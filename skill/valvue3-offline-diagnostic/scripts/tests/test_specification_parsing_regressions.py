"""Focused regressions for labelled spec parsing and source-present review gates."""
import hashlib
import json
from pathlib import Path

import fitz
import pytest

from init_offline_project import initialize_project
from offline_engine import evidence_extraction
from offline_engine.config import load_project
from offline_engine.generator import SpecPage, ValveReport
from offline_engine.specifications import parse_spec_fields, specification_source_values


@pytest.mark.parametrize("separator", [" ", ": ", "：", ":\n", "\n", " \n: \n"])
def test_supported_spec_labels_accept_colons_spacing_and_newlines(separator):
    lines = [
        "Specification Sheet", "Masoneilan Valves 88-21124",
        *(label + separator + value for label, value in [
            ("Size in", "4"), ("Rating ASME", "CL 600"),
            ("Rated Cv", "120"), ("Leakage Class", "IV"),
            ("Fail Position", "Open"), ("Supply Pressure", "60 psi g"),
        ]),
    ]
    assert parse_spec_fields("\n".join(lines)) == {
        "manufacturer": "Masoneilan", "model_no": "88-21124",
        "valve_size": "4", "pressure_class": "CL 600", "rated_cv": "120",
        "leakage_class": "IV", "fail_position": "Open", "supply_pressure": "60 psi g",
    }


@pytest.mark.parametrize("model", ["21000", "88-21124"])
@pytest.mark.parametrize("separator", [": ", "：", "\n", ":\n"])
def test_verified_generic_aliases_preserve_numeric_model_and_bar_pressure(model, separator):
    text = "\n".join(label + separator + value for label, value in [
        ("Manufacturer", "Fisher"), ("Model Number", model), ("Valve Size", "2 inch"),
        ("Pressure Class", "ANSI 300"), ("Rated Cv", "25.0"),
        ("Fail Position", "Close"), ("Supply Pressure", "6.0 bar"),
    ])
    fields = parse_spec_fields(text)
    assert fields == {
        "manufacturer": "Fisher", "model_no": model, "valve_size": "2 in",
        "pressure_class": "ANSI 300", "rated_cv": "25.0",
        "fail_position": "Close", "supply_pressure": "6.0 bar",
    }
    assert "actuator_model" not in fields
    assert "positioner_model" not in fields


@pytest.mark.parametrize(("raw", "expected"), [
    ('2"', "2 in"), ("2 inches", "2 in"), ("1-1/2 in", "1-1/2 in"),
    ("1 1/2 inch", "1 1/2 in"), ("50 mm", "50 mm"), ("DN50", "DN 50"),
])
def test_standard_size_units_are_explicit_not_guessed(raw, expected):
    assert parse_spec_fields(f"Valve Size: {raw}")["valve_size"] == expected


@pytest.mark.parametrize("raw", ["60 psi", "60 psi g", "6.0 bar", "6 bar g", "600 kPa", "0.6 MPa", "100000 Pa"])
def test_standard_supply_pressure_units_are_retained_without_basis_conversion(raw):
    assert parse_spec_fields(f"Supply Pressure: {raw}")["supply_pressure"] == raw


@pytest.mark.parametrize("separator", [" ", ":", "： ", ":\n", "\n"])
def test_actuator_combo_accepts_optional_colon(separator):
    assert parse_spec_fields(f"Model / Size / Fail Position{separator}87 / 6 / Open") == {
        "actuator_model": "87", "actuator_size": "6", "fail_position": "Open",
    }


@pytest.mark.parametrize("prefix", ["Valve", "Positioner", "Valve ", "Positioner\n"])
def test_ownership_specific_model_rows_do_not_become_actuator_rows(prefix):
    fields = parse_spec_fields(f"{prefix} Model / Size: 87 / 6")
    assert "actuator_model" not in fields
    assert "actuator_size" not in fields


@pytest.mark.parametrize("text", [
    "Model Number: custom-series-X\nValve Size: two furlongs\nPressure Class: proprietary\nRated Cv: unknown\nFail Position: site-defined\nSupply Pressure: 6 widgets",
    "Unknown Item: 21000\nUnknown Diameter: 2 inch\nUnspecified Category: ANSI 300\nUnspecified Value: 25.0\nUnspecified State: Close\nUnspecified Pressure: 6.0 bar",
])
def test_generic_unsupported_inputs_remain_negative(text):
    assert parse_spec_fields(text) == {}


@pytest.mark.parametrize("text", ["Supply Pressure: 6 psi-custom", "Rated Cv: 25unknown", "Rated Cv: 25.0.2", "Valve Size: 2 inch-custom", "Pressure Class: CL 600-custom", "Pressure Class: proprietary CL 600", "Leakage Class: IV-custom", "Leakage Class: VIII", "Model Number: 88-21124???", "Valve Size: 1/0 inch", "Valve Size: 1-2 in", "Valve Size: 1.2/3 inch"])
def test_partial_value_prefixes_are_not_normalized(text):
    assert parse_spec_fields(text) == {}


def _project_with_spec(tmp_path, pages, selected_page=1):
    project = tmp_path / "project"
    initialize_project(project, customer="Anonymous", report_title="Offline", report_date="2026-10-09")
    path = project / "specs/VALVE-001/spec.pdf"
    path.parent.mkdir(parents=True)
    with fitz.open() as document:
        for text in pages:
            page = document.new_page()
            page.insert_textbox(fitz.Rect(36, 36, 550, 800), text, fontsize=10)
        document.save(path)
    valve = ValveReport(folder="VALVE-001")
    valve.spec_page = SpecPage(valve="VALVE-001", tag="VALVE-001", source="VALVE-001/spec.pdf", page=selected_page, roles=("specification",))
    return project, path, valve


UNSUPPORTED = {
    "Model Number": ("custom-series-X", "specification.model_no"),
    "Valve Size": ("two furlongs", "specification.valve_size"),
    "Pressure Class": ("proprietary", "specification.pressure_class"),
    "Rated Cv": ("unknown", "specification.rated_cv"),
    "Fail Position": ("site-defined", "actuator.fail_position"),
    "Supply Pressure": ("6 widgets", "actuator.supply_pressure"),
}


def test_unsupported_present_spec_values_reach_rendered_review_queue(tmp_path):
    text = "Specification Sheet\n" + "\n".join(f"{label}: {value}" for label, (value, _) in UNSUPPORTED.items())
    project, source, valve = _project_with_spec(tmp_path, ["Another valve", text], selected_page=2)
    valve.spec_fields = parse_spec_fields(text)
    evidence_extraction.extract_project_evidence(load_project(project), [valve], ocr_stats={})
    extracted = json.loads((project / "work/evidence-extraction/valves/VALVE-001.json").read_text())
    reviews = json.loads((project / "work/evidence-extraction/review-queue.json").read_text())["items"]
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    for raw, field_name in UNSUPPORTED.values():
        field = extracted["fields"][field_name]
        assert field["status"] == "source_present_parser_missing"
        assert field["selected"]["raw_value"] == raw
        assert field["selected"]["normalized_value"] is None
        assert field["selected"]["source"]["path"] == "specs/VALVE-001/spec.pdf"
        assert field["selected"]["source"]["sha256"] == source_hash
        assert field["selected"]["source"]["page"] == 2
        review = next(item for item in reviews if item["field"] == field_name)
        assert review["reason"] == "source_present_parser_missing"
        assert review["candidate_value"] == raw
        assert review["source_sha256"] == source_hash
        assert review["page"] == 2
        assert (project / review["image_path"]).is_file()


@pytest.mark.parametrize("blank", ["", "-", "N/A", "not provided"])
def test_blank_or_absent_labels_remain_missing_and_other_pages_do_not_leak(tmp_path, blank):
    empty = "Specification Sheet\n" + "\n".join(f"{label}: {blank}" for label in UNSUPPORTED)
    populated = "\n".join(f"{label}: {value}" for label, (value, _) in UNSUPPORTED.items())
    project, _, valve = _project_with_spec(tmp_path, [empty, populated])
    extracted = evidence_extraction._extract_valve(load_project(project), valve)
    assert not extracted["parser_missing_fields"]
    assert not extracted["review_items"]
    for _, field_name in UNSUPPORTED.values():
        assert extracted["fields"][field_name]["status"] == "missing"


def test_single_page_cached_key_values_preserve_unparsed_values_and_provenance(tmp_path, monkeypatch):
    project, source, valve = _project_with_spec(tmp_path, ["Scanned specification"])
    cache = {
        "page_count": 1,
        "pages": [{"page_no": 1, "text": "", "method": "ocr", "confidence": 0.9}],
        "structured_fields": {"key_values": {label: value for label, (value, _) in UNSUPPORTED.items()}},
    }
    monkeypatch.setattr(evidence_extraction, "_ocr_document", lambda *_: cache)
    extracted = evidence_extraction._extract_valve(load_project(project), valve)
    for raw, field_name in UNSUPPORTED.values():
        candidate = extracted["fields"][field_name]["selected"]
        assert candidate["status"] == "source_present_parser_missing"
        assert candidate["raw_value"] == raw
        assert candidate["source"]["page"] == 1
        assert candidate["source"]["method"] == "ocr"
        assert candidate["source"]["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()


def test_unqualified_models_never_borrow_positioner_or_actuator_values():
    assert "model_no" not in parse_spec_fields("Positioner Model Number: 21000")
    assert "model_no" not in parse_spec_fields("Actuator Model Number: 21000")
    assert "model_no" not in specification_source_values("Positioner Type Digital Model Number: 21000")


def test_supported_alias_extraction_preserves_raw_units_and_physical_source(tmp_path):
    text = "Model Number: 21000\nValve Size: 2 inch\nSupply Pressure: 6.0 bar"
    project, source, valve = _project_with_spec(tmp_path, [text])
    valve.spec_fields = parse_spec_fields(text)
    fields = evidence_extraction._extract_valve(load_project(project), valve)["fields"]
    model = fields["specification.model_no"]["selected"]
    assert model["normalized_value"] == "21000"
    size = fields["specification.valve_size"]["selected"]
    assert (size["raw_value"], size["normalized_value"]) == ("2 inch", "2 in")
    assert (size["raw_unit"], size["normalized_unit"]) == ("inch", "in")
    pressure = fields["actuator.supply_pressure"]["selected"]
    assert (pressure["raw_value"], pressure["normalized_value"]) == ("6.0 bar", "6.0 bar")
    assert (pressure["raw_unit"], pressure["normalized_unit"]) == ("bar", "bar")
    for candidate in (model, size, pressure):
        assert candidate["source"]["page"] == 1
        assert candidate["source"]["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
        assert candidate["source"]["path"] == "specs/VALVE-001/spec.pdf"


@pytest.mark.parametrize(("label", "raw", "field_name"), [
    ("Pressure Class", "CL 600-custom", "specification.pressure_class"),
    ("Pressure Class", "proprietary CL 600", "specification.pressure_class"),
    ("Leakage Class", "IV-custom", "specification.leakage_class"),
    ("Valve Size", "2 inch-custom", "specification.valve_size"),
    ("Supply Pressure", "6 psi-custom", "actuator.supply_pressure"),
])
def test_unsupported_recognizable_prefixes_are_reviewed_not_promoted(tmp_path, label, raw, field_name):
    text = f"{label}: {raw}"
    project, _, valve = _project_with_spec(tmp_path, [text])
    valve.spec_fields = parse_spec_fields(text)
    field = evidence_extraction._extract_valve(load_project(project), valve)["fields"][field_name]
    assert field["status"] == "source_present_parser_missing"
    assert field["selected"]["raw_value"] == raw
    assert field["selected"]["normalized_value"] is None


def test_existing_design_pressure_and_temperature_with_colons_are_preserved():
    fields = parse_spec_fields("Design Press: 2 MPa g\nDesign Temp: 100 deg C")
    assert fields["design_pressure"] == "2 MPa g"
    assert fields["design_temperature"] == "100 deg C"


def test_existing_control_template_accepts_colon_separators():
    fields = parse_spec_fields("CONTROL VALVE SPECIFICATIONS\nModel No.: 88-21124\nBody Type: GLOBE\nBody Size: 4in\nRating: ASME CLASS 900\nActuator Type: DIAPHRAGM")
    assert fields["model_no"] == "88-21124"
    assert fields["body_type"] == "GLOBE"
    assert fields["valve_size"] == "4 in"
    assert fields["pressure_class"] == "ASME CLASS 900"
    assert fields["actuator_type"] == "DIAPHRAGM"


def test_unsupported_same_line_value_with_colon_is_not_mistaken_for_blank():
    assert specification_source_values("Model Number: vendor:custom") == {"model_no": "vendor:custom"}
    assert specification_source_values("Model Number:\nOther Field: vendor:custom") == {}


@pytest.mark.parametrize("header", ["Specification Sheet", "CONTROL VALVE SPECIFICATIONS"])
@pytest.mark.parametrize("raw", ["60 psi a", "60 psia", "60 psi g", "60 psig", "0.6 MPa a", "0.6 MPaa", "6 bar a", "6 bara"])
def test_complete_pressure_basis_survives_legacy_template_prefixes(header, raw):
    assert parse_spec_fields(f"{header}\nSupply Pressure: {raw}")["supply_pressure"] == raw


@pytest.mark.parametrize("owner", ["Positioner", "Actuator"])
@pytest.mark.parametrize("separator", [": ", "：", " ", ":\n"])
def test_colon_normalization_never_promotes_owned_model_to_valve(owner, separator):
    text = f"{owner} Model{separator}99-ABC12"
    assert "model_no" not in parse_spec_fields(text)
    assert "model_no" not in specification_source_values(text)


@pytest.mark.parametrize("label", ["Model", "Valve Model", "Model Number"])
def test_unqualified_or_explicit_valve_model_remains_supported(label):
    assert parse_spec_fields(f"{label}: 99-ABC12")["model_no"] == "99-ABC12"


def test_owned_model_before_real_valve_model_does_not_hide_valve_value():
    text = "Positioner Model: 99-ABC12\nValve Model: 88-21124"
    assert parse_spec_fields(text)["model_no"] == "88-21124"


@pytest.mark.parametrize("raw", ["60 psi a", "60 psia", "0.6 MPa a"])
def test_pressure_basis_is_preserved_in_extracted_normalized_value_and_units(tmp_path, raw):
    text = f"CONTROL VALVE SPECIFICATIONS\nSupply Pressure: {raw}"
    project, _, valve = _project_with_spec(tmp_path, [text])
    valve.spec_fields = parse_spec_fields(text)
    candidate = evidence_extraction._extract_valve(load_project(project), valve)["fields"]["actuator.supply_pressure"]["selected"]
    assert candidate["raw_value"] == candidate["normalized_value"] == raw
    assert candidate["raw_unit"] == candidate["normalized_unit"] == raw.split(" ", 1)[1]


@pytest.mark.parametrize("raw", ["- / -", "N/A / N/A", "n/a/n/a", "- / - / Open", "N/A / N/A / Open"])
def test_blank_actuator_combo_columns_do_not_create_parser_missing_reviews(tmp_path, raw):
    text = f"Model / Size / Fail Position: {raw}"
    project, _, valve = _project_with_spec(tmp_path, [text])
    valve.spec_fields = parse_spec_fields(text)
    extracted = evidence_extraction._extract_valve(load_project(project), valve)
    evidence_extraction._refresh_valve_state(extracted)
    assert not extracted["review_items"]
    for name in ("actuator.model_full", "actuator.model", "actuator.model_display", "actuator.size"):
        assert extracted["fields"][name]["status"] == "missing"
    if raw.endswith("Open"):
        assert extracted["fields"]["actuator.fail_position"]["selected"]["normalized_value"] == "Open"
    else:
        assert extracted["fields"]["actuator.fail_position"]["status"] == "missing"


@pytest.mark.parametrize(("raw", "reviewed", "literal"), [
    ("custom-model / -", {"actuator.model_full", "actuator.model", "actuator.model_display"}, "custom-model"),
    ("- / custom-size", {"actuator.size"}, "custom-size"),
    ("N/A / custom-size / Open", {"actuator.size"}, "custom-size"),
    ("- / - / site-defined", {"actuator.fail_position"}, "site-defined"),
])
def test_unsupported_combo_columns_queue_only_the_nonblank_field(tmp_path, raw, reviewed, literal):
    text = f"Model / Size / Fail Position: {raw}"
    project, _, valve = _project_with_spec(tmp_path, [text])
    valve.spec_fields = parse_spec_fields(text)
    extracted = evidence_extraction._extract_valve(load_project(project), valve)
    evidence_extraction._refresh_valve_state(extracted)
    assert {item["field"] for item in extracted["review_items"]} == reviewed
    for name in reviewed:
        candidate = extracted["fields"][name]["selected"]
        assert candidate["status"] == "source_present_parser_missing"
        assert candidate["raw_value"] == literal
        assert candidate["normalized_value"] is None


@pytest.mark.parametrize("header", ["Specification Sheet", "CONTROL VALVE SPECIFICATIONS"])
def test_explicit_pressure_class_outranks_unrelated_end_connection_rating(header):
    text = f"{header}\nPressure Class: ANSI 300\nEnd Connection CL 600 RF Flanges"
    assert parse_spec_fields(text)["pressure_class"] == "ANSI 300"


@pytest.mark.parametrize("prefix", ["Actuator Model: 87", "Positioner Model: 99-ABC12", "Actuator Type: CYLINDER"])
def test_explicit_valve_model_keeps_ownership_on_shared_physical_line(prefix):
    text = f"{prefix} Valve Model Number: 21000"
    assert specification_source_values(text)["model_no"] == "21000"
    assert parse_spec_fields(text)["model_no"] == "21000"


def test_previous_owned_field_does_not_claim_new_unqualified_model_number():
    text = "Actuator Model: 87 Model Number: 21000"
    assert parse_spec_fields(text)["model_no"] == "21000"



def test_partial_cache_never_changes_explicit_specification_page_provenance(tmp_path, monkeypatch):
    project, source, valve = _project_with_spec(tmp_path, ["First valve info", "Model Number: custom-series-X"], selected_page=2)
    cached = {
        "page_count": 2,
        "pages": [{"page_no": 1, "text": "First valve info", "method": "ocr", "confidence": 0.9, "degraded": True}],
    }
    monkeypatch.setattr(evidence_extraction, "_ocr_document", lambda *_: cached)
    extracted = evidence_extraction._extract_valve(load_project(project), valve)
    evidence_extraction._refresh_valve_state(extracted)
    candidate = extracted["fields"]["specification.model_no"]["selected"]
    assert candidate["status"] == "source_present_parser_missing"
    assert candidate["raw_value"] == "custom-series-X"
    assert candidate["source"]["page"] == 2
    assert candidate["source"]["method"] == "native_text"
    assert candidate["source"]["raw_text"] == "Model Number: custom-series-X"
    assert candidate["source"]["degraded"] is False
    assert candidate["confidence"] == 1.0
    assert candidate["source"]["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    review = next(item for item in extracted["review_items"] if item["field"] == "specification.model_no")
    assert review["page"] == 2
    evidence_extraction._render_pdf_review_pages(load_project(project), [review])
    assert review["image_path"].endswith("-p0002.png")
    assert (project / review["image_path"]).is_file()
