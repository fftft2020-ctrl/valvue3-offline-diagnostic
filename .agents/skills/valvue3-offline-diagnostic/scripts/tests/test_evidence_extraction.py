from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import fitz
from PIL import Image

from init_offline_project import initialize_project
from offline_engine import evidence_extraction
from offline_engine.config import load_project
from offline_engine.generator import (
    SourceFile,
    SpecPage,
    TestRecord as ValveTestRecord,
    ValveReport,
)
from offline_engine.pipeline import (
    approve_project_materials,
    prepare_project_materials,
)


REPORT_SCRIPT = Path(__file__).resolve().parents[1] / "run_offline_report.py"


def _approve_stage_one(project: Path) -> None:
    prepare_project_materials(project)
    approve_project_materials(
        project,
        approval_text="资料确认，可以进入下一阶段。",
    )


def _write_text_pdf(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    document = fitz.open()
    page = document.new_page()
    for index, line in enumerate(lines):
        page.insert_text((36, 40 + index * 14), line, fontsize=8)
    document.save(path)
    document.close()


def _write_multipage_text_pdf(path: Path, pages: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    document = fitz.open()
    for lines in pages:
        page = document.new_page()
        for index, line in enumerate(lines):
            page.insert_text((36, 40 + index * 14), line, fontsize=8)
    document.save(path)
    document.close()


def _write_scanned_pdf(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = io.BytesIO()
    Image.new("RGB", (64, 32), "white").save(image, format="PNG")
    document = fitz.open()
    page = document.new_page()
    page.insert_image(fitz.Rect(36, 36, 164, 100), stream=image.getvalue())
    document.save(path)
    document.close()


def _write_inline_xlsx(path: Path, rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    row_xml = []
    for row_no, row in enumerate(rows, start=1):
        cells = []
        for column_no, value in enumerate(row, start=1):
            column = chr(ord("A") + column_no - 1)
            cells.append(
                f'<c r="{column}{row_no}" t="inlineStr"><is><t>{value}</t></is></c>'
            )
        row_xml.append(f'<row r="{row_no}">{"".join(cells)}</row>')
    worksheet = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(row_xml)}</sheetData></worksheet>'
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '</Types>',
        )
        archive.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            '</Relationships>',
        )
        archive.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            '</Relationships>',
        )
        archive.writestr("xl/worksheets/sheet1.xml", worksheet)


def _write_minimal_docx(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            '</Types>',
        )
        archive.writestr(
            "word/document.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f'<w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>',
        )


def _fake_ocr_environment(tmp_path: Path) -> dict[str, str]:
    fake_bin = tmp_path / "fake-ocr-bin"
    fake_bin.mkdir(exist_ok=True)
    fake_curl = fake_bin / "curl"
    fake_curl.write_text(
        """#!/bin/sh
case "$*" in
  *127.0.0.1:8765/ready*) echo '{"ready":true}' ;;
  *) echo '{"text":"VALVE-008 Device S/N PHOTO008 Positioner Model SVI2AP", "confidence":0.72, "degraded":false, "blocks":[]}' ;;
esac
""",
        encoding="utf-8",
    )
    fake_curl.chmod(0o755)
    environment = os.environ.copy()
    environment["PATH"] = str(fake_bin) + os.pathsep + environment.get("PATH", "")
    return environment


def _fake_scanned_spec_ocr_environment(tmp_path: Path) -> dict[str, str]:
    fake_bin = tmp_path / "fake-spec-ocr-bin"
    fake_bin.mkdir(exist_ok=True)
    fake_curl = fake_bin / "curl"
    fake_curl.write_text(
        """#!/bin/sh
case "$*" in
  *127.0.0.1:8765/ready*) echo '{"ready":true}' ;;
  *) echo '{"pages":[{"text":"Specification Sheet Tag: VALVE-010 Masoneilan Valves 88-21124 Size in 4 Rating ASME CL 600 Fail Position Open Bench Range 10-15 psi g", "confidence":0.72, "degraded":false, "blocks":[], "tables":[]}]}' ;;
esac
""",
        encoding="utf-8",
    )
    fake_curl.chmod(0o755)
    environment = os.environ.copy()
    environment["PATH"] = str(fake_bin) + os.pathsep + environment.get("PATH", "")
    return environment


def _unavailable_ocr_environment(tmp_path: Path) -> dict[str, str]:
    fake_bin = tmp_path / "unavailable-ocr-bin"
    fake_bin.mkdir(exist_ok=True)
    fake_curl = fake_bin / "curl"
    fake_curl.write_text("#!/bin/sh\nexit 7\n", encoding="utf-8")
    fake_curl.chmod(0o755)
    environment = os.environ.copy()
    environment["PATH"] = str(fake_bin) + os.pathsep + environment.get("PATH", "")
    return environment


def test_extract_materials_builds_fresh_per_valve_evidence_and_refreshes_readiness(
    tmp_path: Path,
) -> None:
    project = tmp_path / "evidence-project"
    initialize_project(
        project,
        customer="示例客户",
        report_title="ValVue3 离线诊断报告",
        report_date="2026-08-27",
    )
    valve_dir = project / "raw/VALVE-001"
    valve_dir.mkdir()
    _write_text_pdf(
        valve_dir / "diagnostic.pdf",
        [
            "ValvXpert Execution Report",
            "Device Tag VALVE-001",
            "Device S/N SN001",
            "Device ID 1001",
            "Dev. Type/Rev SVI II AP Polling Address 0",
            "Manufacturer ID Baker Hughes Diagnostics Level Advanced",
            "Diagnostics Level Advanced DTM Version 2.0 Descriptor HART",
            "Firmware Rev 5.1 Message",
            "Air Action ATO",
            "Actuator Type Single Acting",
            "Supply Pressure 70 psi",
            "Strokes (counts) 125",
            "Cycles (counts) 8",
            "Time Open (hours) 10",
            "Time Closed (hours) 20",
            "Time Near Closed (hours) 5",
            "Active Faults None Historical Faults None Raw Data",
        ],
    )
    (valve_dir / "diagnostic.devdata").write_text(
        """
<Root>
  <PositionerInfo>
    <DeviceId>1001</DeviceId>
    <Tag>VALVE-001</Tag>
    <DeviceSerialNumber>SN001</DeviceSerialNumber>
  </PositionerInfo>
  <Tests>
    <Test type="STEP">
      <Header deviceId="1001" deviceTag="VALVE-001" started="2026-08-27T10:00:00" />
      <Step AverageError="1.2" T86="2.3" T63="1.4" Td="0.2" Overshoot="0.4" />
    </Test>
  </Tests>
</Root>
""".strip(),
        encoding="utf-8",
    )
    _write_text_pdf(
        project / "specs/VALVE-001/spec.pdf",
        [
            "Specification Sheet",
            "Tag: VALVE-001",
            "Masoneilan Valves 88-21124",
            "Size in 4 Rating ASME CL 600",
            "End Connection RF Flanges",
            "Rated Cv 120",
            "Leakage Class IV",
            "Fail Position Open",
            "Bench Range 10-15 psi g",
        ],
    )
    stale_summary = project / "work/analysis_summary.json"
    stale_summary.write_text(
        json.dumps([{"folder": "OLD-VALVE", "rating": 1}]),
        encoding="utf-8",
    )
    stale_bytes = stale_summary.read_bytes()

    _approve_stage_one(project)
    completed = subprocess.run(
        [
            sys.executable,
            str(REPORT_SCRIPT),
            "--project",
            str(project),
            "--extract-materials",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    output_dir = project / "work/evidence-extraction"
    assert "status=materials_extracted" in completed.stdout
    assert f"manifest={output_dir / 'project-index.json'}" in completed.stdout
    assert f"report={output_dir / 'extraction.md'}" in completed.stdout
    for relative_path in (
        "project-index.json",
        "extraction.md",
        "supplementary-materials.json",
        "supplementary-materials.md",
        "review-queue.json",
        "run-manifest.json",
        "valves/VALVE-001.json",
    ):
        assert (output_dir / relative_path).is_file()

    valve = json.loads(
        (output_dir / "valves/VALVE-001.json").read_text(encoding="utf-8")
    )
    assert valve["valve_tag"] == "VALVE-001"
    assert valve["fields"]["identity.device_id"]["selected"]["normalized_value"] == "1001"
    assert valve["fields"]["runtime.strokes"]["selected"]["normalized_value"] == 125
    supply_pressure = valve["fields"]["positioner.supply_pressure"]["selected"]
    assert supply_pressure["raw_value"] == "70 psi"
    assert supply_pressure["raw_unit"] == "psi"
    assert supply_pressure["normalized_value"] == 482.633
    assert supply_pressure["normalized_unit"] == "kPa"
    time_open = valve["fields"]["runtime.time_open"]["selected"]
    assert time_open["raw_value"] == "10"
    assert time_open["raw_unit"] == "h"
    assert time_open["normalized_value"] == 36000.0
    assert time_open["normalized_unit"] == "s"
    assert valve["fields"]["metrics.average_error"]["selected"]["normalized_value"] == 1.2
    assert valve["fields"]["specification.model_no"]["selected"]["normalized_value"] == "88-21124"
    assert valve["fields"]["identity.device_id"]["selected"]["source"]["path"] == (
        "raw/VALVE-001/diagnostic.pdf"
    )
    assert "rating" not in valve
    assert "diagnosis" not in valve

    run_manifest = json.loads(
        (output_dir / "run-manifest.json").read_text(encoding="utf-8")
    )
    project_index = json.loads(
        (output_dir / "project-index.json").read_text(encoding="utf-8")
    )
    assert project_index["extractor_version"] == run_manifest["extractor_version"]
    assert project_index["inputs"] == run_manifest["inputs"]
    assert {
        item["path"] for item in project_index["inputs"]
    } >= {
        "raw/VALVE-001/diagnostic.pdf",
        "raw/VALVE-001/diagnostic.devdata",
        "specs/VALVE-001/spec.pdf",
    }
    assert project_index["valves"][0]["report_pages"]["roles"] == {
        "specification": 1,
        "air_piping": 0,
        "accessory": 0,
    }
    assert "analysis_summary" not in json.dumps(run_manifest)
    assert stale_summary.read_bytes() == stale_bytes

    readiness = json.loads(
        (project / "work/material-readiness/readiness.json").read_text(
            encoding="utf-8"
        )
    )
    assert readiness["analysis"]["path"] == "work/evidence-extraction/project-index.json"
    assert readiness["valves"][0]["reports"]["valvxpert"]["status"] == "present"


def test_normalized_sequencer_evidence_preserves_complete_contract(tmp_path: Path) -> None:
    project = tmp_path / "sequencer-contract"
    initialize_project(
        project,
        customer="示例客户",
        report_title="ValVue3 离线诊断报告",
        report_date="2026-08-28",
    )
    source = project / "raw/VALVE-001/sequencer.pdf"
    _write_text_pdf(
        source,
        [
            "Sequencer Execution Report",
            "Start Time 8/26/2026 3:08:53 PM End Time 8/26/2026 3:37:16 PM",
            "Option Config Advanced Diagnostics; Double Acting;",
            "Device Mode Normal Valve Position -0.0 % Manual Setpoint 0.2 %",
            "Input Signal 4.00 mA Board Temp. 31.2 degC Min Temp. -3.9 degC Max Temp. 48.5 degC",
            "Continuous Diagnostics Strokes (counts) 241 Cyeles (counts) 14691",
            "Time Open (hours) 38479 Time Closed (hours) 10811 Time Near Closed (hours) 6319",
            "Active Faults Reset; Historical Faults Reset; Low Power; Raw Data",
            "Task Execution Summary",
            "Run Ramp Test 8/26/2026 3:10:00 PM 8/26/2026 3:20:00 PM 600 Succeeded",
            "Extended Actuator Signature 8/26/2026 3:27:15 PM 8/26/2026 3:36:50 PM 575 Failed",
            "Run Ramp Test Result Hysteresis Dead Band Linearity",
        ],
    )
    valve = ValveReport(folder="VALVE-001")
    valve.files = [SourceFile(name=source.name, kind="sequencer")]
    valve.device_tag = "VALVE-001"
    valve.positioner_report_info = {
        "source": source.name,
        "report_type": "sequencer_execution",
        "start_time": "8/26/2026 3:08:53 PM",
        "end_time": "8/26/2026 3:37:16 PM",
        "report_time": "2026-08-26 15:08:53–15:37:16",
        "option_config": "Advanced Diagnostics; Double Acting;",
        "device_mode": "Normal",
        "valve_position": "-0.0 %",
        "manual_setpoint": "0.2 %",
        "input_signal": "4.00 mA",
        "board_temperature": "31.2 degC",
        "min_temperature": "-3.9 degC",
        "max_temperature": "48.5 degC",
        "strokes": "241",
        "cycles": "14691",
        "time_open": "38479",
        "time_closed": "10811",
        "time_near_closed": "6319",
        "active_faults": [],
        "historical_faults": ["Low Power"],
        "fault_data_status": "present",
        "task_execution": [
            {
                "task_name": "Run Ramp Test",
                "occurrence": 1,
                "start_time": "8/26/2026 3:10:00 PM",
                "end_time": "8/26/2026 3:20:00 PM",
                "duration_seconds": 600,
                "result": "Succeeded",
            },
            {
                "task_name": "Extended Actuator Signature",
                "occurrence": 1,
                "start_time": "8/26/2026 3:27:15 PM",
                "end_time": "8/26/2026 3:36:50 PM",
                "duration_seconds": 575,
                "result": "Failed",
            },
        ],
        "test_execution": [
            {
                "test_key": "extended_signature",
                "test_name": "Extended Actuator Signature",
                "occurrence": 1,
                "status": "Failed",
            }
        ],
    }
    valve.test_execution_issues = [
        {
            "test_key": "extended_signature",
            "test_name": "Extended Actuator Signature",
            "occurrence": 1,
            "status": "Failed",
        }
    ]
    valve.tests = [
        ValveTestRecord(source=source.name, source_type="pdf", test_type="RAMP")
    ]
    valve.all_tests = list(valve.tests)
    valve.metrics = {
        "run_ramp": {
            "hysteresis": {"max": 1.73, "average": 0.87, "min": 0.0},
            "dead_band": {"max": 1.64, "average": 0.88, "min": 0.0},
            "linearity": {"max": 1.06, "average": 0.56, "min": 0.02},
        }
    }

    extracted = evidence_extraction._extract_valve(load_project(project), valve)

    expected_values = {
        "file.report_start_time": "8/26/2026 3:08:53 PM",
        "file.report_end_time": "8/26/2026 3:37:16 PM",
        "positioner.option_config": "Advanced Diagnostics; Double Acting;",
        "positioner.device_mode": "Normal",
        "positioner.valve_position": -0.0,
        "positioner.manual_setpoint": 0.2,
        "positioner.input_signal": 4.0,
        "positioner.board_temperature": 31.2,
        "positioner.min_temperature": -3.9,
        "positioner.max_temperature": 48.5,
    }
    for field_name, expected in expected_values.items():
        assert extracted["fields"][field_name]["selected"]["normalized_value"] == expected
    assert extracted["fields"]["positioner.valve_position"]["selected"]["raw_unit"] == "%"
    assert extracted["fields"]["positioner.input_signal"]["selected"]["raw_unit"] == "mA"
    assert extracted["fields"]["positioner.board_temperature"]["selected"]["raw_unit"] == "degC"
    assert extracted["fields"]["status.active_faults"]["status"] == "extracted"
    assert extracted["fields"]["status.active_faults"]["selected"]["normalized_value"] == []
    assert extracted["fields"]["status.historical_faults"]["selected"]["normalized_value"] == ["Low Power"]
    assert len(extracted["fields"]["status.task_execution"]["selected"]["normalized_value"]) == 2
    test_execution = extracted["fields"]["status.test_execution"]
    assert test_execution["status"] == "extracted"
    assert len(test_execution["selected"]["normalized_value"]) == 2
    assert any(
        item.get("status") == "Failed"
        for item in test_execution["selected"]["normalized_value"]
    )
    assert any(
        item.get("status") == "Succeeded"
        for item in test_execution["selected"]["normalized_value"]
    )
    for family, values in valve.metrics["run_ramp"].items():
        for statistic, expected in values.items():
            field = extracted["fields"][f"metrics.run_ramp.{family}.{statistic}"]
            assert field["selected"]["normalized_value"] == expected
            assert field["selected"]["normalized_unit"] == "%"
    assert extracted["metrics"] == valve.metrics


def test_stage_two_separates_spec_and_device_ownership_and_normalizes_leakage(
    tmp_path: Path,
) -> None:
    project = tmp_path / "stage-two-schema"
    initialize_project(
        project,
        customer="示例客户",
        report_title="ValVue3 离线诊断报告",
        report_date="2026-08-28",
    )
    valve = ValveReport(folder="VALVE-001")
    valve.manufacturer = "Positioner Manufacturer"
    valve.valve_type = "控制阀"
    valve.dtm_info = {
        "source": "dtm.pdf",
        "manufacturer": "Positioner Manufacturer",
        "valve_manufacturer": "Valve Manufacturer",
        "dtm_valve_type": "Rotary",
        "actuator_manufacturer": "Actuator Manufacturer",
    }
    valve.positioner_report_info = {
        "source": "positioner.pdf",
        "manufacturer": "Positioner Manufacturer",
        "device_type": "SVI II AP",
    }
    valve.spec_fields = {
        "manufacturer": "Specification Manufacturer",
        "body_type": "GLOBE",
        "leakage_class": "CLASS 4",
        "actuator_model": "88-21129",
    }

    extracted = evidence_extraction._extract_valve(load_project(project), valve)
    fields = extracted["fields"]

    assert fields["identity.manufacturer"]["selected"]["normalized_value"] == (
        "Positioner Manufacturer"
    )
    assert fields["valve.manufacturer"]["selected"]["normalized_value"] == (
        "Valve Manufacturer"
    )
    assert fields["specification.manufacturer"]["selected"]["normalized_value"] == (
        "Specification Manufacturer"
    )
    assert fields["specification.valve_type"]["selected"]["normalized_value"] == "GLOBE"
    leakage = fields["specification.leakage_class"]["selected"]
    assert leakage["raw_value"] == "CLASS 4"
    assert leakage["normalized_value"] == "IV"
    assert leakage["normalization_rule"] == "leakage_class_to_roman"
    assert fields["actuator.model_full"]["selected"]["normalized_value"] == "88-21129"
    assert fields["actuator.model_display"]["selected"]["normalized_value"] == "88"
    assert fields["actuator.manufacturer"]["selected"]["normalized_value"] == (
        "Actuator Manufacturer"
    )

    valve.spec_fields = {"model_no": "51-21129"}
    from_model_no = evidence_extraction._extract_valve(load_project(project), valve)
    assert from_model_no["fields"]["actuator.model_full"]["status"] == "missing"
    assert from_model_no["fields"]["actuator.model"]["status"] == "missing"
    assert from_model_no["fields"]["actuator.model_display"]["selected"]["normalized_value"] == "51"


def test_stage_two_actuator_display_includes_explicit_specification_size(
    tmp_path: Path,
) -> None:
    project = tmp_path / "actuator-model-size"
    initialize_project(
        project,
        customer="示例客户",
        report_title="ValVue3 离线诊断报告",
        report_date="2026-09-01",
    )
    valve = ValveReport(folder="VALVE-001")
    valve.spec_fields = {
        "actuator_model": "88",
        "actuator_size": "6",
        "actuator_type": "Spring-diaphragm",
    }

    extracted = evidence_extraction._extract_valve(load_project(project), valve)

    display = extracted["fields"]["actuator.model_display"]["selected"]
    assert display["normalized_value"] == "88 / 6"
    assert display["normalization_rule"] == "actuator_model_with_explicit_size"


def test_stage_two_flags_actuator_model_when_source_label_was_not_parsed(
    tmp_path: Path,
) -> None:
    project = tmp_path / "actuator-model-parser-gate"
    initialize_project(
        project,
        customer="示例客户",
        report_title="ValVue3 离线诊断报告",
        report_date="2026-09-01",
    )
    spec_path = project / "specs/VALVE-001/spec.pdf"
    _write_text_pdf(
        spec_path,
        ["Masoneilan Specification Sheet", "Model / Size 88 / 6"],
    )
    valve = ValveReport(folder="VALVE-001")
    valve.spec_page = SpecPage(
        valve="VALVE-001",
        tag="VALVE-001",
        source="VALVE-001/spec.pdf",
        page=1,
        roles=("specification",),
    )

    extracted = evidence_extraction._extract_valve(load_project(project), valve)

    assert extracted["fields"]["actuator.model_display"]["status"] == (
        "source_present_parser_missing"
    )
    assert "actuator.model_display" in extracted["parser_missing_fields"]


def test_stage_two_uses_specification_order_model_without_device_type_conflict(
    tmp_path: Path,
) -> None:
    project = tmp_path / "positioner-order-model"
    initialize_project(
        project,
        customer="示例客户",
        report_title="ValVue3 离线诊断报告",
        report_date="2026-09-01",
    )
    spec_path = project / "specs/VALVE-001/spec.pdf"
    spec_path.parent.mkdir(parents=True)
    spec_path.write_bytes(b"specification")
    valve = ValveReport(folder="VALVE-001")
    valve.positioner_model = "SVI2AP (0xCA / 02)"
    valve.positioner_type = "SVI2AP"
    valve.positioner_report_info = {
        "source": "report.pdf",
        "device_type": "SVI2AP (0xCA / 02)",
    }
    valve.accessory_page = SpecPage(
        valve="VALVE-001",
        tag="VALVE-001",
        source="VALVE-001/spec.pdf",
        page=2,
        roles=("accessory",),
        fields={"positioner_model": "SVI 2 AP-22123121"},
    )

    extracted = evidence_extraction._extract_valve(load_project(project), valve)

    for field_name in ("accessory.positioner_model", "positioner.model"):
        field = extracted["fields"][field_name]
        assert field["status"] == "extracted"
        assert field["conflict"] is False
        assert field["selected"]["raw_value"] == "SVI 2 AP-22123121"
        assert field["selected"]["source_role"] == "specification_accessory"
    assert extracted["fields"]["identity.device_type"]["selected"][
        "raw_value"
    ] == "SVI2AP (0xCA / 02)"


def test_leakage_percent_expression_maps_to_class_v_without_decimal_digit_leakage(
    tmp_path: Path,
) -> None:
    project = initialize_project(
        tmp_path / "leakage-expression",
        customer="示例客户",
        report_title="ValVue3 离线诊断报告",
        report_date="2026-08-28",
    )[0]
    candidate = evidence_extraction._leakage_candidate(
        load_project(project),
        None,
        "0.001%/Max.Diff.Pr.",
    )
    assert candidate is not None
    assert candidate["normalized_value"] == "V"
    assert candidate["normalization_rule"] == "leakage_percent_to_class_v"
    review_item = evidence_extraction._review_item(
        "VALVE-001",
        "specification.leakage_class",
        "ocr_confidence_unavailable",
        candidate,
    )
    assert review_item["candidate_value"] == "0.001%/Max.Diff.Pr."
    assert review_item["normalization_rule"] == "leakage_percent_to_class_v"


def test_confirmed_raw_leakage_review_reapplies_roman_normalization(
    tmp_path: Path,
) -> None:
    project = initialize_project(
        tmp_path / "reviewed-leakage-expression",
        customer="示例客户",
        report_title="ValVue3 离线诊断报告",
        report_date="2026-08-28",
    )[0]
    source = project / "specs/VALVE-001/spec.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"reviewed source")
    valve = evidence_extraction._extract_valve(
        load_project(project),
        ValveReport(folder="VALVE-001"),
    )

    evidence_extraction._apply_supplementary_candidates(
        load_project(project),
        [valve],
        {
            "VALVE-001": [
                {
                    "field": "specification.leakage_class",
                    "value": "0.001%/Max.Diff.Pr.",
                    "path": source,
                    "method": "codex_review",
                    "source_role": "codex_review",
                    "priority": 900,
                    "candidate_status": "codex_reviewed",
                    "normalization_rule": "leakage_percent_to_class_v",
                }
            ]
        },
    )

    leakage = valve["fields"]["specification.leakage_class"]
    assert leakage["status"] == "codex_reviewed"
    assert leakage["selected"]["raw_value"] == "0.001%/Max.Diff.Pr."
    assert leakage["selected"]["normalized_value"] == "V"
    assert leakage["selected"]["normalization_rule"] == (
        "leakage_percent_to_class_v"
    )


def test_continuous_diagnostics_keeps_machine_reason_codes(tmp_path: Path) -> None:
    project = initialize_project(
        tmp_path / "continuous-status",
        customer="示例客户",
        report_title="ValVue3 离线诊断报告",
        report_date="2026-08-28",
    )[0]
    valve = ValveReport(folder="VALVE-001")
    missing = evidence_extraction._extract_valve(load_project(project), valve)
    assert missing["fields"]["status.continuous_diagnostics"]["status"] == "missing"
    assert missing["fields"]["status.continuous_diagnostics"]["selected"]["normalized_value"] == {
        "status": "not_provided"
    }

    source = project / "raw/VALVE-001/continuous.pdf"
    _write_text_pdf(source, ["Continuous Diagnostics"])
    valve.positioner_report_info = {
        "source": source.name,
        "report_type": "sequencer_execution",
    }
    no_data = evidence_extraction._extract_valve(load_project(project), valve)
    assert no_data["fields"]["status.continuous_diagnostics"]["selected"]["normalized_value"] == {
        "status": "no_matching_data"
    }

    _write_text_pdf(source, ["Continuous Diagnostics", "Strokes (counts) 10"])
    with_data = evidence_extraction._extract_valve(load_project(project), valve)
    assert with_data["fields"]["status.continuous_diagnostics"]["selected"]["normalized_value"] == {
        "status": "present"
    }


def test_successful_task_execution_is_not_missing_when_no_failures(tmp_path: Path) -> None:
    project = initialize_project(
        tmp_path / "successful-execution",
        customer="示例客户",
        report_title="ValVue3 离线诊断报告",
        report_date="2026-08-28",
    )[0]
    valve = ValveReport(folder="VALVE-001")
    valve.positioner_report_info = {
        "task_execution": [
            {"task_name": "Run Ramp Test", "result": "Succeeded"},
        ],
    }
    extracted = evidence_extraction._extract_valve(load_project(project), valve)
    execution = extracted["fields"]["status.test_execution"]
    assert execution["status"] == "extracted"
    assert execution["selected"]["normalized_value"][0]["status"] == "Succeeded"


def test_sequencer_task_table_survives_when_another_positioner_report_is_selected(
    tmp_path: Path,
) -> None:
    project = initialize_project(
        tmp_path / "multiple-positioner-reports",
        customer="示例客户",
        report_title="ValVue3 离线诊断报告",
        report_date="2026-08-28",
    )[0]
    valve = ValveReport(folder="VALVE-001")
    valve.positioner_report_info = {
        "source": "valvxpert.pdf",
        "report_type": "valvxpert_execution",
        "device_mode": "Normal",
    }
    valve.positioner_report_infos = [
        dict(valve.positioner_report_info),
        {
            "source": "sequencer.pdf",
            "report_type": "sequencer_execution",
            "task_execution": [
                {"task_name": "Run Ramp Test", "result": "Succeeded"},
            ],
        },
    ]

    extracted = evidence_extraction._extract_valve(load_project(project), valve)

    tasks = extracted["fields"]["status.task_execution"]
    assert tasks["status"] == "extracted"
    assert tasks["selected"]["normalized_value"] == [
        {"task_name": "Run Ramp Test", "result": "Succeeded"}
    ]


def test_runtime_candidates_from_multiple_reports_are_kept_as_a_human_conflict(
    tmp_path: Path,
) -> None:
    project = initialize_project(
        tmp_path / "multiple-runtime-reports",
        customer="示例客户",
        report_title="ValVue3 离线诊断报告",
        report_date="2026-08-28",
    )[0]
    valve = ValveReport(folder="VALVE-001")
    valve.positioner_report_infos = [
        {"report_type": "sequencer_execution", "cycles": "10"},
        {"report_type": "valvxpert_execution", "cycles": "20"},
    ]

    extracted = evidence_extraction._extract_valve(load_project(project), valve)

    cycles = extracted["fields"]["runtime.cycles"]
    assert cycles["status"] == "conflict"
    assert {
        candidate["normalized_value"] for candidate in cycles["candidates"]
    } == {10, 20}
    assert not any(
        item["field"] == "runtime.cycles"
        for item in extracted["review_items"]
    )


def test_source_present_parser_missing_is_an_explicit_extraction_gate(tmp_path: Path) -> None:
    project = tmp_path / "sequencer-parser-gate"
    initialize_project(
        project,
        customer="示例客户",
        report_title="ValVue3 离线诊断报告",
        report_date="2026-08-28",
    )
    source = project / "raw/VALVE-002/sequencer.pdf"
    _write_text_pdf(
        source,
        [
            "Sequencer Execution Report",
            "Positioner Info",
            "Manual Setpoint 12.5 %",
        ],
    )
    valve = ValveReport(folder="VALVE-002")
    valve.files = [SourceFile(name=source.name, kind="sequencer")]
    valve.positioner_report_info = {
        "source": source.name,
        "report_type": "sequencer_execution",
    }

    extracted = evidence_extraction._extract_valve(load_project(project), valve)

    field = extracted["fields"]["positioner.manual_setpoint"]
    assert field["status"] == "source_present_parser_missing"
    assert field["selected"]["raw_value"] is None
    assert field["selected"]["source"]["path"] == "raw/VALVE-002/sequencer.pdf"
    assert "positioner.manual_setpoint" in extracted["parser_missing_fields"]
    assert "positioner.manual_setpoint" not in extracted["missing_fields"]
    assert extracted["status"] == "review_required"
    assert {
        (item["reason"], item["field"])
        for item in extracted["review_items"]
    } >= {("source_present_parser_missing", "positioner.manual_setpoint")}
    markdown = evidence_extraction._render_markdown(
        {
            "project": {"name": project.name, "customer": "示例客户"},
            "summary": {"valve_count": 1},
        },
        [extracted],
    )
    assert "源有值但解析缺失：positioner.manual_setpoint" in markdown


def test_evidence_report_pages_merge_physical_identity_and_keep_roles_distinct(
    tmp_path: Path,
) -> None:
    project = tmp_path / "report-page-contract"
    initialize_project(
        project,
        customer="示例客户",
        report_title="ValVue3 离线诊断报告",
        report_date="2026-08-28",
    )
    source = project / "specs/VALVE-003/spec.pdf"
    _write_multipage_text_pdf(
        source,
        [
            ["Specification Sheet", "Air Piping Diagram"],
            ["Accessory Sheet", "Positioner"],
        ],
    )
    source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
    valve = ValveReport(folder="VALVE-003")
    valve.report_pages = [
        SpecPage(
            valve="VALVE-003",
            tag="VALVE-003",
            source="VALVE-003/spec.pdf",
            source_sha256=source_sha256,
            page=1,
            roles=("specification",),
            image_path="work/spec-page.png",
            match_basis=("directory_tag",),
        ),
        SpecPage(
            valve="VALVE-003",
            tag="VALVE-003",
            source="VALVE-003/spec.pdf",
            source_sha256=source_sha256,
            page=1,
            roles=("air_piping",),
            match_basis=("page_label",),
        ),
        SpecPage(
            valve="VALVE-003",
            tag="VALVE-003",
            source="VALVE-003/spec.pdf",
            source_sha256=source_sha256,
            page=2,
            roles=("accessory",),
            image_path="work/accessory-page.png",
            match_basis=("page_label",),
        ),
    ]
    valve.spec_page = valve.report_pages[0]
    valve.air_piping_page = valve.report_pages[1]
    valve.accessory_page = valve.report_pages[2]

    extracted = evidence_extraction._extract_valve(load_project(project), valve)

    pages = extracted["report_pages"]
    assert len(pages) == 2
    assert pages[0] == {
        "source": "VALVE-003/spec.pdf",
        "source_sha256": source_sha256,
        "page": 1,
        "roles": ["specification", "air_piping"],
        "image_path": "work/spec-page.png",
        "match_basis": ["directory_tag", "page_label"],
        "physical_identity": f"{source_sha256}:1",
    }
    assert pages[1]["roles"] == ["accessory"]
    assert "air_piping" not in pages[1]["roles"]
    assert extracted["material_summary"]["report_pages"] == pages
    assert extracted["material_summary"]["spec_page"]["page"] == 1
    assert extracted["material_summary"]["air_piping_page"]["page"] == 1
    assert extracted["material_summary"]["accessory_page"]["page"] == 2
    assert evidence_extraction._report_page_summary(pages) == {
        "physical_page_count": 2,
        "roles": {"specification": 1, "air_piping": 1, "accessory": 1},
        "pages": [
            {
                "physical_identity": f"{source_sha256}:1",
                "roles": ["specification", "air_piping"],
            },
            {
                "physical_identity": f"{source_sha256}:2",
                "roles": ["accessory"],
            },
        ],
    }


def test_extract_materials_builds_page_level_curve_inventory_without_alias_duplicates(
    tmp_path: Path,
) -> None:
    project = tmp_path / "page-level-curves"
    initialize_project(
        project,
        customer="示例客户",
        report_title="ValVue3 离线诊断报告",
        report_date="2026-08-27",
    )
    valve_dir = project / "raw/VALVE-001"
    valve_dir.mkdir()
    _write_multipage_text_pdf(
        valve_dir / "diagnostic.pdf",
        [
            [
                "ValvXpert Execution Report",
                "Device Tag VALVE-001",
                "Device ID 1",
                "Dev. Type/Rev SVI2-22113121 Polling Address 1",
                "Control Parameters Continuous Diagnostics Strokes (counts) 10",
            ],
            [
                "Run Step Test",
                "Status: Succeeded",
                "Step Type Multiple Steps",
                "Position (%) Set Point (%)",
            ],
            [
                "Run Step Test",
                "Step Type Pattern Steps",
                "Pattern Step Test Status Succeeded",
                "Position (%) Set Point (%)",
            ],
            [
                "Run Step Test Status Succeeded",
                "Step Type Single Step",
                "Run Ramp Test Status Succeeded",
                "Position (%) Set Point (%) Hysteresis (%)",
            ],
            [
                "Extended Act. Signature",
                "Statusi Succeeded",
                "Result: Parameters High Low Mean",
                "Friction 113.4 kPa 88.5 kPa 102.6 kPa",
                "Friction (%) 48.90 % 38.17 % 44.26 %",
                "Run Signature Test Status Succeeded",
                "Position (%) Pressure (kPa)",
            ],
        ],
    )

    _approve_stage_one(project)
    completed = subprocess.run(
        [
            sys.executable,
            str(REPORT_SCRIPT),
            "--project",
            str(project),
            "--extract-materials",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    valve = json.loads(
        (
            project / "work/evidence-extraction/valves/VALVE-001.json"
        ).read_text(encoding="utf-8")
    )
    curve_counts: dict[str, int] = {}
    for curve in valve["curves"]:
        curve_counts[curve["test_key"]] = curve_counts.get(curve["test_key"], 0) + 1
    assert curve_counts == {
        "run_step": 2,
        "pattern_step": 1,
        "run_ramp": 1,
        "extended_signature": 1,
    }
    assert {
        (curve["test_key"], curve["source"]["page"])
        for curve in valve["curves"]
    } == {
        ("run_step", 2),
        ("pattern_step", 3),
        ("run_step", 4),
        ("run_ramp", 4),
        ("extended_signature", 5),
    }

    readiness = json.loads(
        (project / "work/material-readiness/readiness.json").read_text(
            encoding="utf-8"
        )
    )
    curves = readiness["valves"][0]["curves"]
    assert curves["run_step"]["count"] == 2
    assert curves["pattern_step"]["count"] == 1
    assert curves["run_ramp"]["count"] == 1
    assert curves["standard_signature"]["status"] == "missing"
    assert curves["extended_signature"]["count"] == 1


def test_extract_materials_preserves_conflicting_identity_candidates_by_source_priority(
    tmp_path: Path,
) -> None:
    project = tmp_path / "conflicting-identity"
    initialize_project(
        project,
        customer="示例客户",
        report_title="ValVue3 离线诊断报告",
        report_date="2026-08-27",
    )
    valve_dir = project / "raw/VALVE-001"
    valve_dir.mkdir()
    _write_text_pdf(
        valve_dir / "current.pdf",
        [
            "ValvXpert Execution Report",
            "Device Tag VALVE-001",
            "Device S/N CURRENT01",
            "Device ID 2002",
            "Dev. Type/Rev SVI II AP Polling Address 0",
            "Manufacturer ID Baker Hughes Diagnostics Level Advanced",
            "Diagnostics Level Advanced DTM Version 2.0 Descriptor HART",
            "Firmware Rev 5.1 Message",
            "Control Parameters Continuous Diagnostics",
        ],
    )
    (valve_dir / "older.devdata").write_text(
        """
<Root>
  <PositionerInfo>
    <DeviceId>1001</DeviceId>
    <Tag>VALVE-OLD</Tag>
    <DeviceSerialNumber>OLDER01</DeviceSerialNumber>
  </PositionerInfo>
  <Tests />
</Root>
""".strip(),
        encoding="utf-8",
    )

    _approve_stage_one(project)
    completed = subprocess.run(
        [
            sys.executable,
            str(REPORT_SCRIPT),
            "--project",
            str(project),
            "--extract-materials",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    valve = json.loads(
        (
            project
            / "work/evidence-extraction/valves/VALVE-001.json"
        ).read_text(encoding="utf-8")
    )
    device_id = valve["fields"]["identity.device_id"]
    assert device_id["status"] == "conflict"
    assert device_id["selected"]["normalized_value"] == "2002"
    assert device_id["selected"]["source"]["path"] == "raw/VALVE-001/current.pdf"
    assert {
        candidate["normalized_value"] for candidate in device_id["candidates"]
    } == {"1001", "2002"}
    assert any(
        item["field"] == "identity.device_id"
        for item in valve["conflicts"]
    )
    review_queue = json.loads(
        (
            project / "work/evidence-extraction/review-queue.json"
        ).read_text(encoding="utf-8")
    )
    assert not any(
        item["field"] == "identity.device_id"
        and item["reason"] == "source_conflict"
        for item in review_queue["items"]
    )


def test_extract_materials_keeps_every_test_occurrence_and_marks_the_current_one(
    tmp_path: Path,
) -> None:
    project = tmp_path / "repeated-tests"
    initialize_project(
        project,
        customer="示例客户",
        report_title="ValVue3 离线诊断报告",
        report_date="2026-08-27",
    )
    valve_dir = project / "raw/VALVE-001"
    valve_dir.mkdir()
    (valve_dir / "repeated.devdata").write_text(
        """
<Root>
  <PositionerInfo><DeviceId>1001</DeviceId><Tag>VALVE-001</Tag></PositionerInfo>
  <Tests>
    <Test type="STEP">
      <Header deviceId="1001" deviceTag="VALVE-001" started="2026-08-27T09:00:00" />
      <Step AverageError="2.1" T86="3.0" />
    </Test>
    <Test type="STEP">
      <Header deviceId="1001" deviceTag="VALVE-001" started="2026-08-27T10:00:00" />
      <Step AverageError="1.1" T86="2.0" />
    </Test>
  </Tests>
</Root>
""".strip(),
        encoding="utf-8",
    )

    _approve_stage_one(project)
    completed = subprocess.run(
        [
            sys.executable,
            str(REPORT_SCRIPT),
            "--project",
            str(project),
            "--extract-materials",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    valve = json.loads(
        (
            project / "work/evidence-extraction/valves/VALVE-001.json"
        ).read_text(encoding="utf-8")
    )
    step_tests = [item for item in valve["tests"] if item["test_type"] == "STEP"]
    assert [item["occurrence"] for item in step_tests] == [1, 2]
    assert [item["is_current"] for item in step_tests] == [False, True]
    assert [item["started"] for item in step_tests] == [
        "2026-08-27T09:00:00",
        "2026-08-27T10:00:00",
    ]


def test_extract_materials_uses_csv_valve_list_and_records_adopted_supplements(
    tmp_path: Path,
) -> None:
    project = tmp_path / "csv-supplement"
    initialize_project(
        project,
        customer="示例客户",
        report_title="ValVue3 离线诊断报告",
        report_date="2026-08-27",
    )
    (project / "raw/VALVE-001").mkdir()
    (project / "inbound/valve-list.csv").write_text(
        "阀门位号,阀门描述,装置,失效后果,有无副线,现场备注\n"
        "VALVE-001,进料调节阀,示例装置,影响装置稳定运行,无,现场已确认\n",
        encoding="utf-8",
    )

    _approve_stage_one(project)
    completed = subprocess.run(
        [
            sys.executable,
            str(REPORT_SCRIPT),
            "--project",
            str(project),
            "--extract-materials",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    output_dir = project / "work/evidence-extraction"
    valve = json.loads(
        (output_dir / "valves/VALVE-001.json").read_text(encoding="utf-8")
    )
    assert valve["fields"]["context.description"]["selected"]["raw_value"] == "进料调节阀"
    assert valve["fields"]["context.unit"]["selected"]["raw_value"] == "示例装置"
    assert valve["fields"]["context.failure_consequence"]["selected"]["raw_value"] == (
        "影响装置稳定运行"
    )
    assert valve["fields"]["context.bypass"]["selected"]["raw_value"] == "无"
    assert valve["fields"]["context.site_note"]["selected"]["raw_value"] == "现场已确认"
    assert valve["fields"]["context.description"]["selected"]["source"]["path"] == (
        "inbound/valve-list.csv"
    )

    supplements = json.loads(
        (output_dir / "supplementary-materials.json").read_text(encoding="utf-8")
    )
    assert len(supplements["materials"]) == 1
    material = supplements["materials"][0]
    assert material["path"] == "inbound/valve-list.csv"
    assert material["detected_type"] == "valve_list"
    assert material["matched_valves"] == ["VALVE-001"]
    assert material["adopted"] is True
    assert set(material["adopted_fields"]) == {
        "context.description",
        "context.unit",
        "context.failure_consequence",
        "context.bypass",
        "context.site_note",
    }


def test_extract_materials_reads_xlsx_valve_list_without_external_excel_runtime(
    tmp_path: Path,
) -> None:
    project = tmp_path / "xlsx-supplement"
    initialize_project(
        project,
        customer="示例客户",
        report_title="ValVue3 离线诊断报告",
        report_date="2026-08-27",
    )
    (project / "raw/VALVE-002").mkdir()
    _write_inline_xlsx(
        project / "inbound/valve-list.xlsx",
        [
            ["阀门位号", "阀门描述", "装置", "失效后果", "有无副线", "现场备注"],
            ["VALVE-002", "循环调节阀", "示例装置二", "影响循环流量", "有", "待停机检查"],
        ],
    )

    _approve_stage_one(project)
    completed = subprocess.run(
        [
            sys.executable,
            str(REPORT_SCRIPT),
            "--project",
            str(project),
            "--extract-materials",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    output_dir = project / "work/evidence-extraction"
    valve = json.loads(
        (output_dir / "valves/VALVE-002.json").read_text(encoding="utf-8")
    )
    assert valve["fields"]["context.description"]["selected"]["raw_value"] == "循环调节阀"
    assert valve["fields"]["context.bypass"]["selected"]["raw_value"] == "有"
    material = json.loads(
        (output_dir / "supplementary-materials.json").read_text(encoding="utf-8")
    )["materials"][0]
    assert material["detected_type"] == "valve_list"
    assert material["matched_valves"] == ["VALVE-002"]
    assert material["adopted"] is True


def test_extract_materials_safely_unpacks_zip_and_recognizes_its_contents(
    tmp_path: Path,
) -> None:
    project = tmp_path / "zip-supplement"
    initialize_project(
        project,
        customer="示例客户",
        report_title="ValVue3 离线诊断报告",
        report_date="2026-08-27",
    )
    (project / "raw/VALVE-003").mkdir()
    archive_path = project / "inbound/materials.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(
            "valve-list.csv",
            "阀门位号,阀门描述,装置\nVALVE-003,放空调节阀,示例装置三\n",
        )
    original_bytes = archive_path.read_bytes()
    archive_hash = hashlib.sha256(original_bytes).hexdigest()

    _approve_stage_one(project)
    completed = subprocess.run(
        [
            sys.executable,
            str(REPORT_SCRIPT),
            "--project",
            str(project),
            "--extract-materials",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert archive_path.read_bytes() == original_bytes
    extracted = (
        project / f"work/intake-unpacked/{archive_hash}/valve-list.csv"
    )
    assert extracted.is_file()
    output_dir = project / "work/evidence-extraction"
    valve = json.loads(
        (output_dir / "valves/VALVE-003.json").read_text(encoding="utf-8")
    )
    assert valve["fields"]["context.description"]["selected"]["raw_value"] == "放空调节阀"
    materials = json.loads(
        (output_dir / "supplementary-materials.json").read_text(encoding="utf-8")
    )["materials"]
    archive_material = next(
        item for item in materials if item["path"] == "inbound/materials.zip"
    )
    member_material = next(
        item
        for item in materials
        if item["path"] == "inbound/materials.zip!valve-list.csv"
    )
    assert archive_material["detected_type"] == "archive"
    assert archive_material["status"] == "extracted"
    assert member_material["detected_type"] == "valve_list"
    assert member_material["matched_valves"] == ["VALVE-003"]
    assert member_material["adopted"] is True


def test_extract_materials_rejects_zip_members_outside_the_unpack_directory(
    tmp_path: Path,
) -> None:
    project = tmp_path / "unsafe-zip"
    initialize_project(
        project,
        customer="示例客户",
        report_title="ValVue3 离线诊断报告",
        report_date="2026-08-27",
    )
    (project / "raw/VALVE-004").mkdir()
    archive_path = project / "inbound/unsafe.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("../escaped.txt", "must not be written")

    _approve_stage_one(project)
    completed = subprocess.run(
        [
            sys.executable,
            str(REPORT_SCRIPT),
            "--project",
            str(project),
            "--extract-materials",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert not (project / "work/escaped.txt").exists()
    assert not (project / "escaped.txt").exists()
    material = json.loads(
        (
            project / "work/evidence-extraction/supplementary-materials.json"
        ).read_text(encoding="utf-8")
    )["materials"][0]
    assert material["path"] == "inbound/unsafe.zip"
    assert material["detected_type"] == "archive"
    assert material["status"] == "rejected"
    assert "escapes destination" in material["reason"]


def test_extract_materials_classifies_other_files_without_reusing_old_diagnosis(
    tmp_path: Path,
) -> None:
    project = tmp_path / "other-supplements"
    initialize_project(
        project,
        customer="示例客户",
        report_title="ValVue3 离线诊断报告",
        report_date="2026-08-27",
    )
    (project / "raw/VALVE-005").mkdir()
    (project / "raw/VALVE-005/local-note.txt").write_text(
        "VALVE-005 现场补充说明。",
        encoding="utf-8",
    )
    (project / "inbound/acquisition_manifest.json").write_text(
        json.dumps(
            {
                "valve_tag": "VALVE-005",
                "source": "local intake",
                "file_count": 3,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (project / "inbound/note.txt").write_text(
        "VALVE-005 现场资料说明：本批资料由业主提供。",
        encoding="utf-8",
    )
    _write_minimal_docx(
        project / "inbound/old-report.docx",
        "VALVE-005 离线诊断分析报告 健康等级 2级",
    )
    _write_text_pdf(
        project / "inbound/old-report.pdf",
        [
            "VALVE-005",
            "Offline Diagnostic Report",
            "Health Rating 2",
        ],
    )

    _approve_stage_one(project)
    completed = subprocess.run(
        [
            sys.executable,
            str(REPORT_SCRIPT),
            "--project",
            str(project),
            "--extract-materials",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    output_dir = project / "work/evidence-extraction"
    materials = {
        item["path"]: item
        for item in json.loads(
            (output_dir / "supplementary-materials.json").read_text(
                encoding="utf-8"
            )
        )["materials"]
    }
    assert materials["inbound/acquisition_manifest.json"]["detected_type"] == (
        "intake_manifest"
    )
    assert materials["inbound/note.txt"]["detected_type"] == "project_note"
    assert materials["raw/VALVE-005/local-note.txt"]["detected_type"] == (
        "project_note"
    )
    for path in ("inbound/old-report.docx", "inbound/old-report.pdf"):
        assert materials[path]["detected_type"] == "historical_report"
        assert materials[path]["matched_valves"] == ["VALVE-005"]
        assert materials[path]["adopted"] is False
        assert materials[path]["reason"] == "historical_reference_not_enabled"
    valve = json.loads(
        (output_dir / "valves/VALVE-005.json").read_text(encoding="utf-8")
    )
    assert "rating" not in valve
    assert "diagnosis" not in valve


def test_extract_materials_converts_legacy_xls_in_a_temporary_directory(
    tmp_path: Path,
) -> None:
    project = tmp_path / "xls-supplement"
    initialize_project(
        project,
        customer="示例客户",
        report_title="ValVue3 离线诊断报告",
        report_date="2026-08-27",
    )
    (project / "raw/VALVE-006").mkdir()
    legacy_xls = project / "inbound/legacy-list.xls"
    legacy_xls.write_bytes(b"legacy workbook placeholder")
    converted_fixture = tmp_path / "converted-fixture.xlsx"
    _write_inline_xlsx(
        converted_fixture,
        [
            ["阀门位号", "阀门描述", "装置"],
            ["VALVE-006", "蒸汽调节阀", "示例装置六"],
        ],
    )
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_libreoffice = fake_bin / "libreoffice"
    fake_libreoffice.write_text(
        """#!/bin/sh
outdir=""
input=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --outdir) outdir="$2"; shift 2 ;;
    --headless|--convert-to) shift; [ "$1" = "xlsx" ] && shift ;;
    *) input="$1"; shift ;;
  esac
done
stem=$(basename "$input" .xls)
cp "$FAKE_XLSX_SOURCE" "$outdir/$stem.xlsx"
""",
        encoding="utf-8",
    )
    fake_libreoffice.chmod(0o755)
    environment = os.environ.copy()
    environment["PATH"] = str(fake_bin) + os.pathsep + environment.get("PATH", "")
    environment["FAKE_XLSX_SOURCE"] = str(converted_fixture)

    _approve_stage_one(project)
    completed = subprocess.run(
        [
            sys.executable,
            str(REPORT_SCRIPT),
            "--project",
            str(project),
            "--extract-materials",
        ],
        text=True,
        capture_output=True,
        check=False,
        env=environment,
    )

    assert completed.returncode == 0, completed.stderr
    output_dir = project / "work/evidence-extraction"
    valve = json.loads(
        (output_dir / "valves/VALVE-006.json").read_text(encoding="utf-8")
    )
    assert valve["fields"]["context.description"]["selected"]["raw_value"] == "蒸汽调节阀"
    material = json.loads(
        (output_dir / "supplementary-materials.json").read_text(encoding="utf-8")
    )["materials"][0]
    assert material["path"] == "inbound/legacy-list.xls"
    assert material["detected_type"] == "valve_list"
    assert material["adopted"] is True
    assert not list(project.rglob("*.xlsx"))


def test_extract_materials_applies_explicit_project_overrides_without_losing_candidates(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project-overrides"
    initialize_project(
        project,
        customer="示例客户",
        report_title="ValVue3 离线诊断报告",
        report_date="2026-08-27",
    )
    (project / "raw/VALVE-007").mkdir()
    (project / "inbound/valve-list.csv").write_text(
        "阀门位号,阀门描述\nVALVE-007,自动清单描述\n",
        encoding="utf-8",
    )
    override_path = project / "experience/extraction_overrides.json"
    override_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "valves": {
                    "VALVE-007": {
                        "fields": {
                            "context.description": {
                                "value": "人工确认描述",
                                "reason": "业主确认",
                            },
                            "identity.serial_number": {
                                "value": "SN-007",
                                "reason": "铭牌复核",
                            },
                        }
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    _approve_stage_one(project)
    completed = subprocess.run(
        [
            sys.executable,
            str(REPORT_SCRIPT),
            "--project",
            str(project),
            "--extract-materials",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    valve = json.loads(
        (
            project / "work/evidence-extraction/valves/VALVE-007.json"
        ).read_text(encoding="utf-8")
    )
    description = valve["fields"]["context.description"]
    assert description["status"] == "manual_override"
    assert description["selected"]["raw_value"] == "人工确认描述"
    assert description["selected"]["source"]["path"] == (
        "experience/extraction_overrides.json"
    )
    assert description["selected"]["reason"] == "业主确认"
    assert {candidate["raw_value"] for candidate in description["candidates"]} == {
        "自动清单描述",
        "人工确认描述",
    }
    serial_number = valve["fields"]["identity.serial_number"]
    assert serial_number["status"] == "manual_override"
    assert serial_number["selected"]["raw_value"] == "SN-007"
    assert not any(
        item["field"] in {"context.description", "identity.serial_number"}
        for item in valve["review_items"]
    )


def test_extract_materials_routes_low_confidence_image_ocr_to_codex_review(
    tmp_path: Path,
) -> None:
    project = tmp_path / "image-review"
    initialize_project(
        project,
        customer="示例客户",
        report_title="ValVue3 离线诊断报告",
        report_date="2026-08-27",
    )
    (project / "raw/VALVE-008").mkdir()
    image_path = project / "inbound/nameplate.png"
    Image.new("RGB", (64, 32), "white").save(image_path)
    environment = _fake_ocr_environment(tmp_path)

    _approve_stage_one(project)
    completed = subprocess.run(
        [
            sys.executable,
            str(REPORT_SCRIPT),
            "--project",
            str(project),
            "--extract-materials",
        ],
        text=True,
        capture_output=True,
        check=False,
        env=environment,
    )

    assert completed.returncode == 0, completed.stderr
    output_dir = project / "work/evidence-extraction"
    valve = json.loads(
        (output_dir / "valves/VALVE-008.json").read_text(encoding="utf-8")
    )
    serial = valve["fields"]["identity.serial_number"]
    assert serial["status"] == "review_required"
    assert serial["selected"]["raw_value"] == "PHOTO008"
    assert serial["selected"]["confidence"] == 0.72
    assert serial["selected"]["source"]["method"] == "ocr"
    positioner = valve["fields"]["positioner.model"]
    assert positioner["status"] == "review_required"
    assert positioner["selected"]["raw_value"] == "SVI2AP"

    review_queue = json.loads(
        (output_dir / "review-queue.json").read_text(encoding="utf-8")
    )
    serial_review = next(
        item
        for item in review_queue["items"]
        if item["field"] == "identity.serial_number"
    )
    assert serial_review["reason"] == "low_ocr_confidence"
    assert serial_review["source_path"] == "inbound/nameplate.png"
    assert serial_review["image_path"] == "inbound/nameplate.png"
    assert serial_review["review_id"]
    material = json.loads(
        (output_dir / "supplementary-materials.json").read_text(encoding="utf-8")
    )["materials"][0]
    assert material["detected_type"] == "nameplate_image"
    assert material["matched_valves"] == ["VALVE-008"]
    assert material["adopted"] is False
    assert (output_dir / "codex-review-prompt.md").is_file()
    review_schema = json.loads(
        (output_dir / "codex-review-schema.json").read_text(encoding="utf-8")
    )
    assert review_schema["properties"]["schema_version"] == {
        "type": "integer",
        "const": 1,
    }
    review_item_schema = review_schema["properties"]["reviews"]["items"]
    assert set(review_item_schema["required"]) == set(
        review_item_schema["properties"]
    )
    assert review_item_schema["additionalProperties"] is False
    assert review_item_schema["properties"]["status"] == {
        "type": "string",
        "enum": ["confirmed", "corrected", "unresolved"],
    }
    assert review_item_schema["properties"]["value"]["anyOf"]


def test_reliable_new_air_piping_page_is_not_queued_but_uncertain_one_is(
    tmp_path: Path,
) -> None:
    project = tmp_path / "air-piping-review-boundary"
    initialize_project(
        project,
        customer="示例客户",
        report_title="ValVue3 离线诊断报告",
        report_date="2026-08-27",
    )
    image = project / "inbound/air-piping.png"
    Image.new("RGB", (64, 32), "white").save(image)
    config = load_project(project)

    requests = [
        {
            "valve_tag": "VALVE-001",
            "field": "supplementary.image_content",
            "reason": "new_air_piping_page",
            "role": "air_piping",
            "role_reliable": True,
            "confidence": 0.96,
            "path": image,
        },
        {
            "valve_tag": "VALVE-001",
            "field": "supplementary.image_content",
            "reason": "new_air_piping_page",
            "role": "air_piping",
            "role_reliable": False,
            "confidence": 0.96,
            "path": image,
        },
    ]

    items = evidence_extraction._supplementary_review_items(config, requests)

    assert len(items) == 1
    assert items[0]["reason"] == "new_air_piping_page"


def test_extract_materials_applies_current_codex_review_result_and_keeps_ocr_candidate(
    tmp_path: Path,
) -> None:
    project = tmp_path / "codex-review-result"
    initialize_project(
        project,
        customer="示例客户",
        report_title="ValVue3 离线诊断报告",
        report_date="2026-08-27",
    )
    (project / "raw/VALVE-008").mkdir()
    Image.new("RGB", (64, 32), "white").save(
        project / "inbound/nameplate.png"
    )
    environment = _fake_ocr_environment(tmp_path)
    _approve_stage_one(project)
    command = [
        sys.executable,
        str(REPORT_SCRIPT),
        "--project",
        str(project),
        "--extract-materials",
    ]

    first = subprocess.run(
        command,
        text=True,
        capture_output=True,
        check=False,
        env=environment,
    )

    assert first.returncode == 0, first.stderr
    output_dir = project / "work/evidence-extraction"
    queue = json.loads(
        (output_dir / "review-queue.json").read_text(encoding="utf-8")
    )
    serial_review = next(
        item
        for item in queue["items"]
        if item["field"] == "identity.serial_number"
    )
    (output_dir / "codex-review-results.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "reviews": [
                    {
                        "review_id": serial_review["review_id"],
                        "status": "corrected",
                        "value": "PHOTO008-CORRECTED",
                        "reason": "铭牌字符清晰可见",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    second = subprocess.run(
        command,
        text=True,
        capture_output=True,
        check=False,
        env=environment,
    )

    assert second.returncode == 0, second.stderr
    valve = json.loads(
        (output_dir / "valves/VALVE-008.json").read_text(encoding="utf-8")
    )
    serial = valve["fields"]["identity.serial_number"]
    assert serial["status"] == "codex_reviewed"
    assert serial["selected"]["raw_value"] == "PHOTO008-CORRECTED"
    assert serial["selected"]["source"]["method"] == "codex_review"
    assert serial["selected"]["reason"] == "铭牌字符清晰可见"
    assert {candidate["raw_value"] for candidate in serial["candidates"]} == {
        "PHOTO008",
        "PHOTO008-CORRECTED",
    }
    updated_queue = json.loads(
        (output_dir / "review-queue.json").read_text(encoding="utf-8")
    )
    assert not any(
        item["review_id"] == serial_review["review_id"]
        for item in updated_queue["items"]
    )

    Image.new("RGB", (64, 32), "black").save(
        project / "inbound/nameplate.png"
    )
    changed_source = subprocess.run(
        command,
        text=True,
        capture_output=True,
        check=False,
        env=environment,
    )

    assert changed_source.returncode == 2
    assert "approval is stale" in changed_source.stderr
    _approve_stage_one(project)
    changed_source = subprocess.run(
        command,
        text=True,
        capture_output=True,
        check=False,
        env=environment,
    )
    assert changed_source.returncode == 0, changed_source.stderr
    changed_valve = json.loads(
        (output_dir / "valves/VALVE-008.json").read_text(encoding="utf-8")
    )
    changed_serial = changed_valve["fields"]["identity.serial_number"]
    assert changed_serial["status"] == "review_required"
    assert changed_serial["selected"]["raw_value"] == "PHOTO008"
    changed_queue = json.loads(
        (output_dir / "review-queue.json").read_text(encoding="utf-8")
    )
    changed_review = next(
        item
        for item in changed_queue["items"]
        if item["field"] == "identity.serial_number"
    )
    assert changed_review["review_id"] != serial_review["review_id"]


def test_extract_materials_renders_low_confidence_scanned_pdf_page_for_review(
    tmp_path: Path,
) -> None:
    project = tmp_path / "scanned-spec-review"
    initialize_project(
        project,
        customer="示例客户",
        report_title="ValVue3 离线诊断报告",
        report_date="2026-08-27",
    )
    (project / "raw/VALVE-010").mkdir()
    _write_scanned_pdf(project / "specs/VALVE-010/spec.pdf")
    environment = _fake_scanned_spec_ocr_environment(tmp_path)

    _approve_stage_one(project)
    completed = subprocess.run(
        [
            sys.executable,
            str(REPORT_SCRIPT),
            "--project",
            str(project),
            "--extract-materials",
        ],
        text=True,
        capture_output=True,
        check=False,
        env=environment,
    )

    assert completed.returncode == 0, completed.stderr
    output_dir = project / "work/evidence-extraction"
    valve = json.loads(
        (output_dir / "valves/VALVE-010.json").read_text(encoding="utf-8")
    )
    model = valve["fields"]["specification.model_no"]
    assert model["status"] == "review_required"
    assert model["selected"]["raw_value"] == "88-21124"
    assert model["selected"]["source"]["method"] == "ocr"
    assert model["selected"]["confidence"] == 0.72
    body_material = valve["fields"]["specification.body_material"]
    assert body_material["status"] == "missing"
    assert body_material["selected"] is None
    queue = json.loads(
        (output_dir / "review-queue.json").read_text(encoding="utf-8")
    )
    review = next(
        item
        for item in queue["items"]
        if item["field"] == "specification.model_no"
    )
    assert review["reason"] == "low_ocr_confidence"
    assert review["page"] == 1
    assert review["image_path"].startswith("work/evidence-extraction/review/")
    assert (project / review["image_path"]).is_file()
    assert not any(
        item["field"] == "specification.body_material"
        for item in queue["items"]
    )

    (output_dir / "codex-review-results.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "reviews": [
                    {
                        "review_id": review["review_id"],
                        "status": "corrected",
                        "value": "88-21124-C",
                        "reason": "规格页型号字符清晰可见",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    reviewed = subprocess.run(
        [
            sys.executable,
            str(REPORT_SCRIPT),
            "--project",
            str(project),
            "--extract-materials",
        ],
        text=True,
        capture_output=True,
        check=False,
        env=environment,
    )

    assert reviewed.returncode == 0, reviewed.stderr
    reviewed_valve = json.loads(
        (output_dir / "valves/VALVE-010.json").read_text(encoding="utf-8")
    )
    reviewed_model = reviewed_valve["fields"]["specification.model_no"]
    assert reviewed_model["status"] == "codex_reviewed"
    assert reviewed_model["selected"]["raw_value"] == "88-21124-C"
    assert reviewed_model["selected"]["source"]["method"] == "codex_review"
    assert {candidate["raw_value"] for candidate in reviewed_model["candidates"]} == {
        "88-21124",
        "88-21124-C",
    }


def test_codex_review_can_replace_unavailable_image_ocr_with_visible_fields(
    tmp_path: Path,
) -> None:
    project = tmp_path / "unavailable-image-ocr"
    initialize_project(
        project,
        customer="示例客户",
        report_title="ValVue3 离线诊断报告",
        report_date="2026-08-27",
    )
    valve_dir = project / "raw/VALVE-011"
    valve_dir.mkdir()
    Image.new("RGB", (64, 32), "white").save(valve_dir / "nameplate.png")
    environment = _unavailable_ocr_environment(tmp_path)
    _approve_stage_one(project)
    command = [
        sys.executable,
        str(REPORT_SCRIPT),
        "--project",
        str(project),
        "--extract-materials",
    ]

    first = subprocess.run(
        command,
        text=True,
        capture_output=True,
        check=False,
        env=environment,
    )

    assert first.returncode == 0, first.stderr
    output_dir = project / "work/evidence-extraction"
    queue = json.loads(
        (output_dir / "review-queue.json").read_text(encoding="utf-8")
    )
    content_review = next(
        item
        for item in queue["items"]
        if item["field"] == "supplementary.image_content"
    )
    assert content_review["valve_tag"] == "VALVE-011"
    assert content_review["reason"] == "ocr_unavailable"
    assert content_review["image_path"] == "raw/VALVE-011/nameplate.png"
    (output_dir / "codex-review-results.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "reviews": [
                    {
                        "review_id": content_review["review_id"],
                        "status": "corrected",
                        "value": {
                            "valve_tag": "VALVE-011",
                            "fields": [
                                {
                                    "field": "identity.serial_number",
                                    "value": "CODEX011",
                                },
                                {
                                    "field": "positioner.model",
                                    "value": "SVI2AP",
                                },
                            ],
                        },
                        "reason": "铭牌上的位号、序列号和型号清晰可见",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    second = subprocess.run(
        command,
        text=True,
        capture_output=True,
        check=False,
        env=environment,
    )

    assert second.returncode == 0, second.stderr
    valve = json.loads(
        (output_dir / "valves/VALVE-011.json").read_text(encoding="utf-8")
    )
    assert valve["fields"]["identity.serial_number"]["status"] == "codex_reviewed"
    assert valve["fields"]["identity.serial_number"]["selected"]["raw_value"] == (
        "CODEX011"
    )
    assert valve["fields"]["positioner.model"]["selected"]["raw_value"] == "SVI2AP"
    updated_queue = json.loads(
        (output_dir / "review-queue.json").read_text(encoding="utf-8")
    )
    assert not any(
        item["review_id"] == content_review["review_id"]
        for item in updated_queue["items"]
    )
