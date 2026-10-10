import json
import subprocess
from pathlib import Path

import fitz
import pytest

from offline_engine import specifications
from offline_engine.specifications import (
    detect_spec_format,
    normalize_valve_model,
    parse_spec_fields,
)


def test_normalize_valve_model_restores_generic_series_separator() -> None:
    assert normalize_valve_model("8821124") == "88-21124"
    assert normalize_valve_model("5141335") == "51-41335"
    assert normalize_valve_model("8741335AOV") == "87-41335AOV"


def test_normalize_valve_model_normalizes_dash_variants() -> None:
    assert normalize_valve_model("87–41335AOV") == "87-41335AOV"
    assert normalize_valve_model("88=21124") == "88-21124"
    assert normalize_valve_model("88-211 25EB") == "88-21125EB"


def test_normalize_valve_model_rejects_ambiguous_or_short_values() -> None:
    assert normalize_valve_model("88ZI124?") == ""
    assert normalize_valve_model("8821") == ""


CONTROL_VALVE_OCR = """
CONTROL VALVE
SPECIFICATIONS
Model No. [8821124 TT air Set [78-40-316L_ x TT]
Body Type GLOBE
Rating [ASME CLASS 900
Body Size 4in
Rated Cv 49
Flow to OPEN
Fail Position CLOSE
Actuator Type. DIAPHRAGM
"""


def test_control_valve_specifications_adapter_extracts_common_fields() -> None:
    assert detect_spec_format(CONTROL_VALVE_OCR) == "control_valve_specifications"
    assert parse_spec_fields(CONTROL_VALVE_OCR) == {
        "model_no": "88-21124",
        "body_type": "GLOBE",
        "valve_size": "4 in",
        "pressure_class": "ASME CLASS 900",
        "rated_cv": "49",
        "flow_action": "OPEN",
        "fail_position": "CLOSE",
        "actuator_type": "DIAPHRAGM",
    }


def test_control_valve_specifications_adapter_extracts_fractional_body_size() -> None:
    fields = parse_spec_fields(
        "CONTROL VALVE SPECIFICATIONS Model No. [88-21125EB Body Size 1-1/2in"
    )

    assert fields["valve_size"] == "1-1/2 in"


def test_control_valve_specifications_extracts_identity_actuator_and_positioner_fields() -> None:
    fields = parse_spec_fields(
        """
CONTROL VALVE SPECIFICATIONS
Serial No. AB12C3456-78 Tag No. 9000-PV-9002
Model No. 51-41905EL1 Body Type GLOBE Rating ASME CLASS 600
End Connection RTJ Body Size 8in Rated Cv 460 Leakage Spec. CLASS 4
Trim Size HI-CAPACITY (S) Trim Form LO-DB CAGE Characteristic EQ%-LINEAR Rated Cv
Flow to OPEN Fail Position CLOSE
Actuator Type CYLINDER Fittings SWAGELOK Size #12(15) Spring Range
Supply Pressure 0.25 MPa Rated Travel 76.2mm (3")
Positioner Type ELEC/PNEUMA. (HART) Case MAX Model No. SVI2-22113121
"""
    )

    assert fields["serial_number"] == "AB12C3456-78"
    assert fields["end_connection"] == "RTJ"
    assert fields["actuator_size"] == "#12(15)"
    assert fields["actuator_model_note"] == "规格书未提供"
    assert fields["actuator_travel"] == '76.2mm (3")'
    assert fields["spring_range_note"] == "规格书栏位为空"
    assert fields["positioner_model"] == "SVI2-22113121"
    assert fields["leakage_class"] == "CLASS 4"


def test_generic_masoneilan_parser_extracts_actuator_size_travel_and_noisy_end_connection() -> None:
    fields = parse_spec_fields(
        """
Masoneilan Valves 35-35112 Specification Sheet
Size in 3 Rating ASME CL 300 End Connection CL 300 RF Flanges
Model 35 Shutoff Pressure 4.1MPag Type Spring-diaphragm Handwheel Side Mounted
Size 6 Mounting / Installation 1 / Horizontal Line
Bench Range 7-15 psi g Supply Pressure 30 psi g Travel / Stroke Time 50 deg /
"""
    )

    assert fields["valve_size"] == "3"
    assert fields["end_connection"] == "RF Flanges"
    assert fields["actuator_model"] == "35"
    assert fields["actuator_size"] == "6"
    assert fields["actuator_travel"] == "50 deg"


@pytest.mark.parametrize(
    ("source", "model", "size", "fail_position"),
    [
        (
            "Model / Size 88 / 6 Shutoff Pressure / Max 1.6 MPa g "
            "Type Spring-diaphragm Fail Position Closed",
            "88",
            "6",
            "Closed",
        ),
        (
            "Model/Size 51/16 Type Piston (Cylinder) Fail Position Open",
            "51",
            "16",
            "Open",
        ),
        (
            "Model / Size / Fail Position 5A / 12 / Closed",
            "5A",
            "12",
            "Closed",
        ),
    ],
)
def test_generic_parser_extracts_labelled_actuator_model_size_rows(
    source: str,
    model: str,
    size: str,
    fail_position: str,
) -> None:
    fields = parse_spec_fields("Masoneilan Valves 88-21124 Specification Sheet " + source)

    assert fields["actuator_model"] == model
    assert fields["actuator_size"] == size
    assert fields["fail_position"] == fail_position


def test_generic_parser_does_not_treat_positioner_or_valve_model_as_actuator() -> None:
    fields = parse_spec_fields(
        "Masoneilan Valves 88-21124 Specification Sheet "
        "Positioner Model SVI2AP-21113111 Valve Model / Size 88-21124 / 2"
    )

    assert "actuator_model" not in fields
    assert "actuator_size" not in fields


def test_bare_number_is_not_inferred_as_model() -> None:
    fields = parse_spec_fields("CONTROL VALVE SPECIFICATIONS 8821124 Rating ASME CLASS 900")
    assert "model_no" not in fields


def test_model_field_tolerates_table_noise_but_not_an_unlabelled_value() -> None:
    text = """CONTROL VALVE SPECIFICATIONS
| [ModelNo. __ (88-41305ET | Air Set 78-40-316L
Body A494 Gr. CY-40 Class2 | Model No. |WSNF8327B302
"""
    assert parse_spec_fields(text)["model_no"] == "88-41305ET"


def test_model_field_does_not_slice_a_model_from_an_accessory_identifier() -> None:
    text = """CONTROL VALVE SPECIFICATIONS
Model No. |WSNF8327B302 x2
"""
    assert "model_no" not in parse_spec_fields(text)


def test_model_field_does_not_slice_a_model_from_a_prefixed_device_identifier() -> None:
    text = """CONTROL VALVE SPECIFICATIONS
Model No. |SVI2-21113121
"""
    assert "model_no" not in parse_spec_fields(text)


def test_existing_masoneilan_format_remains_supported() -> None:
    fields = parse_spec_fields(
        "Masoneilan Valves 88-21124 Size in 4 Rating ASME CL600 Rated Cv 49"
    )
    assert fields["model_no"] == "88-21124"
    assert fields["valve_size"] == "4"
    assert fields["pressure_class"] == "CL600"
    assert fields["rated_cv"] == "49"


def test_sizing_selection_report_extracts_ordered_model_and_body_size() -> None:
    fields = parse_spec_fields(
        """
Sizing and Selection Report
Status Ordered Model 70-35212
In x Body x Out Size 16x16x16In
Rating Class CL 150
Vave rated CV 3650
Flow Direction / Leakage Class Flow to Open / IV
Actuator Type Piston (Cylinder) Action Double Acting
Fall Pos. / Air Action Closed / Air to Open
Model / Size 5A / 12
Spring Range / Travel 0-0 bar g / 50 deg
Supply / Safety Factor Air, 2.82 bar g / 1
Type Digital HART Model SVI 2 AP - 22113111 Display
Diagnostic Level Standard Capacity Standard
"""
    )

    assert fields["model_no"] == "70-35212"
    assert fields["valve_size"] == "16 in"
    assert fields["pressure_class"] == "CL 150"
    assert fields["rated_cv"] == "3650"
    assert fields["flow_action"] == "Flow to Open"
    assert fields["leakage_class"] == "IV"
    assert fields["actuator_type"] == "Piston (Cylinder)"
    assert fields["actuator_action"] == "Double Acting"
    assert fields["fail_position"] == "Closed"
    assert fields["actuator_model"] == "5A"
    assert fields["actuator_size"] == "12"
    assert fields["bench_range"] == "0-0 bar g"
    assert fields["actuator_travel"] == "50 deg"
    assert fields["supply_pressure"] == "2.82 bar g"
    assert fields["positioner_model"] == "SVI 2 AP - 22113111"
    assert fields["positioner_diagnostics_level"] == "Standard"


def test_masoneilan_paddle_text_extracts_report_equipment_fields() -> None:
    fields = parse_spec_fields(
        """
Masoneilan Valves
52-78463
Specification Sheet
Body
Units / System
Valve Inlet
Valve Body
Valve Outlet
Size
in
4
4
4
Rating
ASME
CL 2500
CL 2500
CL 2500
Actuator
Model
52
Type
Piston (Cylinder)
Bench Range
10-15 psi g
Supply Pressure
52 psi g
Air Action
Air to Close
Fail Position
Open
"""
    )

    assert fields["model_no"] == "52-78463"
    assert fields["valve_size"] == "4"
    assert fields["pressure_class"] == "CL 2500"
    assert fields["fail_position"] == "Open"
    assert fields["bench_range"] == "10-15 psi g"


def test_generic_parser_rejects_accessory_labels_as_model_and_shutoff_as_fail_position() -> None:
    fields = parse_spec_fields(
        """
Masoneilan Valves
Accessory Sheet
Gauge scale psi-kPa-bar
Fail Position
Shutoff Pressure / Max P1
"""
    )

    assert "model_no" not in fields
    assert "fail_position" not in fields


def test_generic_parser_handles_values_before_labels_and_fail_lock() -> None:
    fields = parse_spec_fields(
        """
Specification Sheet
**53-41325
Masoneilan Valves
Body
Size
in
**10
6
**12
Rating
ASME
CL 2500
CL 2500
CL 2500
53
Piston (Cylinder)
20
10-24 psi g
Air to Close
Lock
Tag
"""
    )

    assert fields["model_no"] == "53-41325"
    assert fields["valve_size"] == "6"
    assert fields["pressure_class"] == "CL 2500"
    assert fields["bench_range"] == "10-24 psi g"
    assert fields["fail_position"] == "Lock"


def test_spec_data_sheet_adapter_extracts_alternate_equipment_layout() -> None:
    fields = parse_spec_fields(
        """
Masoneilan Valves
52-71571
Spec Data Sheet
Model No
52-71571
Size (In x Body x Out)
3 x 3 x 4
in
Rating / End Conn /sch
CL 2500 / HUB / 40-40
Actuator
Fail Position
Open
Bench Range / Travel
20-32 psi g / 1.5
"""
    )

    assert fields["model_no"] == "52-71571"
    assert fields["valve_size"] == "3"
    assert fields["pressure_class"] == "CL 2500"
    assert fields["fail_position"] == "Open"
    assert fields["bench_range"] == "20-32 psi g"


def test_assembly_quality_plan_extracts_only_explicit_equipment_fields() -> None:
    text = """
DIRECTIVE 2014/68/UE
Type ：88-78469
Size D.N. :DN 25(1")
Spring range :38-45 psi
Connections :FLANGED CLASS 1500 RTJ
ASSEMBLY QUALITY PLAN
"""

    assert detect_spec_format(text) == "assembly_quality_plan"
    fields = parse_spec_fields(text)
    assert fields["model_no"] == "88-78469"
    assert fields["valve_size"] == "1 in"
    assert fields["pressure_class"] == "CLASS 1500"
    assert fields["bench_range"] == "38-45 psi"
    assert "fail_position" not in fields


class FakePixmap:
    def save(self, path: str) -> None:
        Path(path).write_bytes(b"png")


class FakePage:
    def __init__(self, text: str) -> None:
        self.text = text

    def get_text(self) -> str:
        return self.text

    def get_pixmap(self, **kwargs) -> FakePixmap:
        return FakePixmap()


def _write_pdf(path: Path, page_count: int = 1) -> None:
    document = fitz.open()
    for _ in range(page_count):
        document.new_page()
    document.save(path)
    document.close()
