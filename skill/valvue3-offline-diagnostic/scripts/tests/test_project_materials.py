from __future__ import annotations

import json
import hashlib
import subprocess
import sys
from pathlib import Path

import fitz
import pytest

from init_offline_project import initialize_project
from offline_engine.config import load_project
from offline_engine.evidence_extraction import EXTRACTOR_VERSION
from offline_engine.pipeline import (
    approve_project_materials,
    prepare_project_materials,
    require_project_materials_approval,
)
from offline_engine.project_materials import write_material_readiness
from offline_engine.project_materials import (
    approve_parameter_extraction,
    parameter_extraction_report_fingerprint,
    require_parameter_extraction_approval,
    write_parameter_extraction_report,
)


REPORT_SCRIPT = Path(__file__).resolve().parents[1] / "run_offline_report.py"


def _initialize(project: Path, *, with_context: bool = False) -> None:
    initialize_project(
        project,
        customer="示例客户",
        report_title="ValVue3 离线诊断报告",
        report_date="2026-08-26",
    )
    if with_context:
        config = project / "project.toml"
        text = config.read_text(encoding="utf-8")
        config.write_text(
            text.replace(
                'manual_interpretations = "work/manual_curve_interpretations.json"',
                'manual_interpretations = "work/manual_curve_interpretations.json"\n'
                'valve_context = "experience/valve_context.json"',
            ),
            encoding="utf-8",
        )


def _write_context(project: Path, tags: tuple[str, ...]) -> None:
    payload = {
        tag: {
            "description": f"{tag} 调节阀",
            "unit": "示例装置",
            "failure_consequence": "影响装置稳定运行",
            "bypass": "无",
            "note": "现场资料已确认",
        }
        for tag in tags
    }
    (project / "experience/valve_context.json").write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )


def _enable_accessory_pages(project: Path) -> None:
    config = project / "project.toml"
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            "include_accessory_pages = false",
            "include_accessory_pages = true",
        ),
        encoding="utf-8",
    )


def _write_frozen_baseline(
    project: Path,
    *,
    valve_tag: str,
    raw_file: Path,
    spec_file: Path,
    material_extraction: dict | None = None,
) -> None:
    config = project / "project.toml"
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            'manual_interpretations = "work/manual_curve_interpretations.json"',
            'manual_interpretations = "work/manual_curve_interpretations.json"\n'
            'formal_diagnosis_run = "work/formal_diagnosis/runs/v1"',
        ),
        encoding="utf-8",
    )
    run_root = project / "work/formal_diagnosis/runs/v1"
    valve_run = run_root / valve_tag
    valve_run.mkdir(parents=True)
    manifest = {
        "schema_version": 1,
        "stage": "frozen",
        "project_id": "baseline-project",
        "diagnosis_version": "v1",
        "targets": [{"valve_tag": valve_tag, "stage": "frozen"}],
    }
    (run_root / "00_project_run_manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    raw_sha = hashlib.sha256(raw_file.read_bytes()).hexdigest()
    spec_sha = hashlib.sha256(spec_file.read_bytes()).hexdigest()
    bundle = {
        "evidence": {
            "source_files": [
                {
                    "relative_path": raw_file.relative_to(project).as_posix(),
                    "size_bytes": raw_file.stat().st_size,
                    "sha256": raw_sha,
                }
            ],
            "specification_images": [
                {"source_sha256": spec_sha, "page": 1}
            ],
            "structured_evidence": {
                "material_extraction": material_extraction or {
                    "valve_tag": valve_tag,
                    "fields": {},
                }
            },
        }
    }
    (valve_run / "01_evidence_bundle.json").write_text(
        json.dumps(bundle), encoding="utf-8"
    )


def _payload(project: Path) -> dict:
    return json.loads(
        (project / "work/material-readiness/readiness.json").read_text(
            encoding="utf-8"
        )
    )


def test_first_inventory_does_not_parse_pdf_and_marks_types_pending(
    tmp_path: Path,
) -> None:
    project = tmp_path / "first-inventory"
    _initialize(project, with_context=True)
    _write_context(project, ("VALVE-001",))
    (project / "raw/VALVE-001").mkdir()
    (project / "raw/VALVE-001/report.pdf").write_bytes(b"not parsed")
    (project / "raw/VALVE-001/source.devdata").write_bytes(b"not parsed")
    (project / "specs/VALVE-001").mkdir()
    (project / "specs/VALVE-001/spec.pdf").write_bytes(b"not matched")

    result = prepare_project_materials(project)

    assert result.status == "materials_reported"
    payload = _payload(project)
    assert payload["analysis"]["status"] == "archive_scanned"
    valve = payload["valves"][0]
    assert valve["reports"]["devdata"] == {
        "status": "present",
        "count": 1,
        "detail": "",
    }
    assert valve["reports"]["sequencer"]["status"] == "pending"
    assert valve["reports"]["valvxpert"]["status"] == "pending"
    assert valve["curves"]["run_step"]["status"] == "pending"
    assert valve["materials"]["specification"]["status"] == "pending"
    assert valve["materials"]["air_piping"]["status"] == "pending"
    assert valve["materials"]["accessory"] == {
        "status": "disabled",
        "count": 0,
        "detail": "未启用/不纳入报告",
    }
    assert all(
        value["status"] == "present"
        for value in valve["materials"]["context"].values()
    )
    assert valve["overall"]["status"] == "pending"
    assert result.report_path is not None
    markdown = result.report_path.read_text(encoding="utf-8")
    assert "报告与原始数据" in markdown
    assert "曲线数量与执行状态" in markdown
    assert "不执行 OCR" in markdown
    assert "等待用户明确批准" in markdown


def test_existing_analysis_builds_per_valve_report_curve_and_material_matrix(
    tmp_path: Path,
) -> None:
    project = tmp_path / "analyzed-project"
    tags = ("VALVE-SEQ", "VALVE-XPERT")
    _initialize(project, with_context=True)
    _write_context(project, tags)
    (project / "specs").mkdir(exist_ok=True)
    (project / "specs/spec.pdf").write_bytes(b"spec")

    raw_files = {
        "VALVE-SEQ": {
            "active.pdf": b"active",
            "sequence.pdf": b"sequence",
            "source.devdata": b"devdata-seq",
        },
        "VALVE-XPERT": {
            "valvxpert.pdf": b"valvxpert",
            "source.devdata": b"devdata-xpert",
        },
    }
    for tag, files in raw_files.items():
        folder = project / "raw" / tag
        folder.mkdir()
        for name, content in files.items():
            (folder / name).write_bytes(content)

    summaries = [
        {
            "folder": "VALVE-SEQ",
            "files": [
                {"name": name, "kind": "PDF" if name.endswith(".pdf") else "devdata"}
                for name in sorted(raw_files["VALVE-SEQ"])
            ],
            "source_report_types": {
                "dtm_active": 1,
                "sequencer": 1,
                "devdata": 1,
            },
            "dtm_data_report_count": 1,
            "images": [
                {"test_key": "run_step"},
                {"test_key": "run_step"},
                {"test_key": "run_ramp"},
            ],
            "test_execution_issues": [
                {"test_key": "run_ramp", "status": "Failed"}
            ],
            "spec_page": {"source": "spec.pdf", "page": 1},
            "air_piping_page": {"source": "spec.pdf", "page": 2},
            "accessory_page": {"source": "spec.pdf", "page": 2},
            "data_note": "",
            "unreadable": [],
        },
        {
            "folder": "VALVE-XPERT",
            "files": [
                {"name": name, "kind": "PDF" if name.endswith(".pdf") else "devdata"}
                for name in sorted(raw_files["VALVE-XPERT"])
            ],
            "dtm_info": {},
            "positioner_report_info": {
                "source": "valvxpert.pdf",
                "report_type": "valvxpert_execution",
                "dtm_source_status": "present",
                "dtm_parser_status": "parsed",
            },
            "images": [
                {"test_key": "run_step", "source": "valvxpert.pdf"}
            ],
            "test_execution_issues": [],
            "spec_page": {"source": "spec.pdf", "page": 3},
            "air_piping_page": {"source": "spec.pdf", "page": 4},
            "accessory_page": {"source": "spec.pdf", "page": 4},
            "data_note": "",
            "unreadable": [],
        },
    ]
    (project / "work/analysis_summary.json").write_text(
        json.dumps(summaries, ensure_ascii=False),
        encoding="utf-8",
    )

    result = write_material_readiness(load_project(project))

    payload = _payload(project)
    assert payload["analysis"]["status"] == "available"
    by_tag = {item["valve_tag"]: item for item in payload["valves"]}
    sequencer = by_tag["VALVE-SEQ"]
    assert sequencer["reports"]["sequencer"]["count"] == 1
    assert sequencer["reports"]["test_report"]["status"] == "present"
    assert sequencer["reports"]["dtm_data"]["status"] == "present"
    assert sequencer["curves"]["run_step"]["count"] == 2
    assert sequencer["curves"]["run_ramp"] == {
        "status": "failed",
        "count": 1,
        "detail": "Failed",
    }
    assert sequencer["curves"]["standard_signature"]["status"] == "missing"
    assert sequencer["overall"]["status"] == "complete"

    valvxpert = by_tag["VALVE-XPERT"]
    assert valvxpert["reports"]["sequencer"]["status"] == "missing"
    assert valvxpert["reports"]["valvxpert"]["count"] == 1
    assert valvxpert["reports"]["test_report"]["status"] == "present"
    assert valvxpert["reports"]["dtm_data"]["status"] == "present"
    assert valvxpert["overall"]["status"] == "complete"

    markdown = result.markdown_path.read_text(encoding="utf-8")
    assert "✅ 2张" in markdown
    assert "⚠ 1张 / Failed" in markdown
    assert "❌ 缺失" in markdown
    assert "✅ 必需资料齐全" in markdown


def test_report_pages_separate_roles_and_deduplicate_physical_pages(
    tmp_path: Path,
) -> None:
    project = tmp_path / "page-role-project"
    _initialize(project, with_context=True)
    _enable_accessory_pages(project)
    _write_context(project, ("VALVE-001",))
    (project / "raw/VALVE-001").mkdir()
    (project / "raw/VALVE-001/report.pdf").write_bytes(b"report")
    (project / "specs/VALVE-001").mkdir()
    (project / "specs/VALVE-001/spec.pdf").write_bytes(b"spec")
    summary = {
        "folder": "VALVE-001",
        "files": [{"name": "report.pdf", "kind": "PDF"}],
        "source_report_types": {"sequencer": 1},
        "dtm_data_report_count": 0,
        "images": [],
        "test_execution_issues": [],
        "report_pages": [
            {
                "source": "VALVE-001/spec.pdf",
                "source_sha256": "same-page",
                "page": 2,
                "roles": ["specification", "air_piping"],
            },
            {
                "source": "VALVE-001/spec.pdf",
                "source_sha256": "same-page",
                "page": 2,
                "roles": ["air_piping", "specification"],
            },
            {
                "source": "VALVE-001/spec.pdf",
                "source_sha256": "accessory-page",
                "page": 3,
                "roles": ["accessory"],
            },
        ],
        "data_note": "",
        "unreadable": [],
    }
    (project / "work/analysis_summary.json").write_text(
        json.dumps([summary], ensure_ascii=False), encoding="utf-8"
    )

    result = write_material_readiness(load_project(project))

    materials = _payload(project)["valves"][0]["materials"]
    assert materials["specification"] == {
        "status": "present",
        "count": 1,
        "detail": "",
    }
    assert materials["air_piping"] == {
        "status": "present",
        "count": 1,
        "detail": "",
    }
    assert materials["accessory"] == {
        "status": "present",
        "count": 1,
        "detail": "",
    }
    markdown = result.markdown_path.read_text(encoding="utf-8")
    assert "| 阀门 | Specification | 气路页 | 普通附件 |" in markdown


def test_legacy_accessory_does_not_satisfy_air_piping(
    tmp_path: Path,
) -> None:
    project = tmp_path / "legacy-page-project"
    _initialize(project, with_context=True)
    _write_context(project, ("VALVE-001",))
    (project / "raw/VALVE-001").mkdir()
    (project / "raw/VALVE-001/report.pdf").write_bytes(b"report")
    (project / "specs/VALVE-001").mkdir()
    (project / "specs/VALVE-001/spec.pdf").write_bytes(b"spec")
    summary = {
        "folder": "VALVE-001",
        "files": [{"name": "report.pdf", "kind": "PDF"}],
        "source_report_types": {"sequencer": 1},
        "dtm_data_report_count": 0,
        "images": [],
        "test_execution_issues": [],
        "spec_page": {"source": "spec.pdf", "page": 1},
        "accessory_page": {"source": "spec.pdf", "page": 2},
        "data_note": "",
        "unreadable": [],
    }
    (project / "work/analysis_summary.json").write_text(
        json.dumps([summary], ensure_ascii=False), encoding="utf-8"
    )

    write_material_readiness(load_project(project))

    valve = _payload(project)["valves"][0]
    assert valve["materials"]["specification"]["status"] == "present"
    assert valve["materials"]["air_piping"]["status"] == "unmatched"
    assert valve["materials"]["accessory"] == {
        "status": "disabled",
        "count": 0,
        "detail": "未启用/不纳入报告",
    }
    assert "匹配气路页" in valve["overall"]["missing"]
    assert "匹配普通附件" not in valve["overall"]["missing"]
    markdown = (
        project / "work/material-readiness/readiness.md"
    ).read_text(encoding="utf-8")
    assert "— 未启用/不纳入报告" in markdown


def test_cli_prints_both_material_report_paths(tmp_path: Path) -> None:
    project = tmp_path / "cli-project"
    _initialize(project)

    completed = subprocess.run(
        [
            sys.executable,
            str(REPORT_SCRIPT),
            "--project",
            str(project),
            "--prepare-materials",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "status=materials_reported" in completed.stdout
    assert "manifest=" + str(
        project / "work/stage-gates/material-archive-report.json"
    ) in completed.stdout
    assert "report=" + str(
        project / "work/stage-gates/material-archive-report.md"
    ) in completed.stdout
    html_path = project / "work/stage-gates/material-archive-report.html"
    assert "web_report=" + str(html_path) in completed.stdout
    html_report = html_path.read_text(encoding="utf-8")
    assert "第一阶段资料归档报告" in html_report
    assert "网页是审核界面，不是诊断报告" in html_report
    assert "VALVE-001" not in html_report


def test_material_archive_approval_binds_report_and_current_inputs(
    tmp_path: Path,
) -> None:
    project = tmp_path / "approved-project"
    _initialize(project)
    (project / "raw/VALVE-001").mkdir()
    (project / "raw/VALVE-001/source.devdata").write_bytes(b"source-v1")

    prepared = prepare_project_materials(project)
    assert prepared.web_report_path is not None
    web_report = prepared.web_report_path.read_text(encoding="utf-8")
    assert "VALVE-001" in web_report
    assert "source.devdata" not in web_report
    assert 'data-label="Run Step"' in web_report
    assert 'class="nowrap">资料归档审核' in web_report
    approval = approve_project_materials(
        project,
        approval_text="资料确认，可以进入下一阶段。",
    )

    payload = json.loads(approval.manifest_path.read_text(encoding="utf-8"))
    assert payload["status"] == "approved"
    assert payload["approval_text"] == "资料确认，可以进入下一阶段。"
    assert len(payload["input_fingerprint"]) == 64
    assert len(payload["report_fingerprint"]) == 64
    assert payload["report_html"] == "work/stage-gates/material-archive-report.html"
    assert len(payload["report_html_sha256"]) == 64
    assert require_project_materials_approval(project)["status"] == "approved"

    config = project / "project.toml"
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            'report_date = "2026-08-26"',
            'report_date = "2026-08-28"',
        ),
        encoding="utf-8",
    )
    assert require_project_materials_approval(project)["status"] == "approved"

    (project / "raw/VALVE-001/new.pdf").write_bytes(b"new material")
    with pytest.raises(RuntimeError, match="approval is stale"):
        require_project_materials_approval(project)


def test_stage_one_requires_reuse_decision_for_unchanged_frozen_valve(
    tmp_path: Path,
) -> None:
    project = tmp_path / "reuse-decision-project"
    _initialize(project)
    raw_dir = project / "raw/VALVE-001"
    spec_dir = project / "specs/VALVE-001"
    raw_dir.mkdir()
    spec_dir.mkdir()
    raw_file = raw_dir / "source.devdata"
    spec_file = spec_dir / "spec.pdf"
    raw_file.write_bytes(b"unchanged-source")
    spec_file.write_bytes(b"unchanged-spec")
    _write_frozen_baseline(
        project,
        valve_tag="VALVE-001",
        raw_file=raw_file,
        spec_file=spec_file,
    )

    prepared = prepare_project_materials(project)
    report = json.loads(prepared.manifest_path.read_text(encoding="utf-8"))
    assert report["reuse_review"]["decision_required"] is True
    assert [
        item["valve_tag"] for item in report["reuse_review"]["unchanged_valves"]
    ] == ["VALVE-001"]
    markdown = prepared.report_path.read_text(encoding="utf-8")
    assert "上一版本变化与复用选择" in markdown
    assert "复用未变化内容" in markdown

    with pytest.raises(RuntimeError, match="unchanged material"):
        approve_project_materials(
            project,
            approval_text="批准当前归档。",
        )

    approved = approve_project_materials(
        project,
        approval_text="批准当前归档，复用未变化内容。",
        unchanged_reuse_policy="reuse_unchanged",
    )
    approval = json.loads(approved.manifest_path.read_text(encoding="utf-8"))
    assert approval["unchanged_reuse_policy"] == "reuse_unchanged"
    assert approval["unchanged_valves"][0]["valve_tag"] == "VALVE-001"
    assert require_project_materials_approval(project)[
        "unchanged_reuse_policy"
    ] == "reuse_unchanged"


def test_stage_one_allows_approval_without_policy_when_source_changed(
    tmp_path: Path,
) -> None:
    project = tmp_path / "changed-baseline-project"
    _initialize(project)
    raw_dir = project / "raw/VALVE-001"
    spec_dir = project / "specs/VALVE-001"
    raw_dir.mkdir()
    spec_dir.mkdir()
    raw_file = raw_dir / "source.devdata"
    spec_file = spec_dir / "spec.pdf"
    raw_file.write_bytes(b"baseline-source")
    spec_file.write_bytes(b"baseline-spec")
    _write_frozen_baseline(
        project,
        valve_tag="VALVE-001",
        raw_file=raw_file,
        spec_file=spec_file,
    )
    raw_file.write_bytes(b"changed-source")

    prepared = prepare_project_materials(project)
    report = json.loads(prepared.manifest_path.read_text(encoding="utf-8"))
    assert report["reuse_review"]["decision_required"] is False
    assert report["reuse_review"]["changed_or_new_valves"][0][
        "valve_tag"
    ] == "VALVE-001"

    approved = approve_project_materials(
        project,
        approval_text="批准当前归档。",
    )
    approval = json.loads(approved.manifest_path.read_text(encoding="utf-8"))
    assert approval["unchanged_reuse_policy"] == "not_applicable"


def test_material_archive_native_scan_lists_curves_and_execution_status(
    tmp_path: Path,
) -> None:
    project = tmp_path / "native-curve-project"
    _initialize(project)
    valve_dir = project / "raw/VALVE-001"
    valve_dir.mkdir()
    pdf = fitz.open()
    task_page = pdf.new_page()
    task_page.insert_text(
        (72, 72),
        "Task Execution Summary\n"
        "Run Ramp Test 8/26/2026 3:10:00 PM "
        "8/26/2026 3:20:00 PM 600 Failed",
    )
    ramp_page = pdf.new_page()
    ramp_page.insert_text(
        (72, 72),
        "Run Ramp Test\nTask Parameters\nMaximum Hysteresis\nPosition (%)",
    )
    signature_page = pdf.new_page()
    signature_page.insert_text(
        (72, 72),
        "Extended Act. Signature\nFriction\nPosition (%)",
    )
    pdf.save(valve_dir / "sequencer.pdf")
    pdf.close()

    prepare_project_materials(project)

    payload = _payload(project)
    valve = payload["valves"][0]
    assert payload["analysis"]["status"] == "archive_scanned"
    assert valve["reports"]["sequencer"]["status"] == "present"
    assert valve["curves"]["run_ramp"] == {
        "status": "failed",
        "count": 1,
        "detail": "Failed",
    }
    assert valve["curves"]["extended_signature"] == {
        "status": "present",
        "count": 1,
        "detail": "",
    }
    assert "metrics" not in valve


def test_material_archive_prefers_explicit_valvxpert_title_over_curve_content(
    tmp_path: Path,
) -> None:
    project = tmp_path / "valvxpert-archive-project"
    _initialize(project)
    valve_dir = project / "raw/VALVE-001"
    valve_dir.mkdir()
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text(
        (72, 72),
        "ValvXpert Report\n"
        "Task Execution Summary\n"
        "Run Ramp Test 8/26/2026 3:10:00 PM "
        "8/26/2026 3:20:00 PM 600 Success\n"
        "Run Ramp Test\nTask Parameters\nPosition (%)",
    )
    pdf.save(valve_dir / "valvxpert.pdf")
    pdf.close()

    prepare_project_materials(project)

    valve = _payload(project)["valves"][0]
    assert valve["reports"]["valvxpert"] == {
        "status": "present",
        "count": 1,
        "detail": "",
    }
    assert valve["reports"]["sequencer"]["status"] == "missing"
    assert valve["reports"]["dtm_data"] == {
        "status": "pending",
        "count": 0,
        "detail": "参数待第二阶段提取",
    }


def test_cli_blocks_stage_two_until_material_archive_is_approved(
    tmp_path: Path,
) -> None:
    project = tmp_path / "gated-project"
    _initialize(project)

    blocked = subprocess.run(
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
    assert blocked.returncode == 2
    assert "Stage 1 material archive approval is required" in blocked.stderr

    prepare_project_materials(project)
    approved = subprocess.run(
        [
            sys.executable,
            str(REPORT_SCRIPT),
            "--project",
            str(project),
            "--approve-materials",
            "资料确认，可以进入下一阶段。",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert approved.returncode == 0, approved.stderr
    assert "status=materials_approved" in approved.stdout


def _write_evidence_snapshot(project: Path, *, value: str = "Normal") -> None:
    output = project / "work/evidence-extraction"
    valve_dir = output / "valves"
    valve_dir.mkdir(parents=True, exist_ok=True)
    valve = {
        "schema_version": 1,
        "valve_tag": "VALVE-001",
        "status": "extracted_with_gaps",
        "fields": {
            "identity.valve_tag": {
                "status": "extracted",
                "selected": {
                    "raw_value": "VALVE-001",
                    "normalized_value": "VALVE-001",
                    "raw_unit": "",
                    "normalized_unit": "",
                    "confidence": 1.0,
                    "source": {"path": "raw/VALVE-001/source.devdata", "page": None},
                },
                "candidates": [],
                "conflict": False,
            },
            "positioner.device_mode": {
                "status": "extracted",
                "selected": {
                    "raw_value": value,
                    "normalized_value": value,
                    "raw_unit": "",
                    "normalized_unit": "",
                    "confidence": 1.0,
                    "source": {"path": "raw/VALVE-001/report.pdf", "page": 1},
                },
                "candidates": [],
                "conflict": False,
            },
            "specification.model_no": {
                "status": "missing",
                "selected": None,
                "candidates": [],
                "conflict": False,
            },
        },
        "reports": {"counts": {"sequencer": 1}},
        "tests": [{"test_type": "RAMP", "status": "Failed"}],
        "curves": [],
        "report_pages": [],
        "missing_fields": ["specification.model_no"],
        "parser_missing_fields": [],
        "conflicts": [],
        "review_items": [],
        "source_files": [],
        "material_summary": {"description": "示例调节阀"},
    }
    valve_path = valve_dir / "VALVE-001.json"
    valve_path.write_text(json.dumps(valve, ensure_ascii=False), encoding="utf-8")
    index = {
        "schema_version": 1,
        "extractor_version": EXTRACTOR_VERSION,
        "created_at": "2026-08-28T00:00:00+00:00",
        "status": "materials_extracted",
        "project": {"name": project.name, "customer": "示例客户"},
        "summary": {"valve_count": 1},
        "inputs": [],
        "valves": [
            {
                "valve_tag": "VALVE-001",
                "path": "work/evidence-extraction/valves/VALVE-001.json",
            }
        ],
    }
    (output / "project-index.json").write_text(
        json.dumps(index, ensure_ascii=False), encoding="utf-8"
    )
    (output / "supplementary-materials.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "materials": [
                    {
                        "path": "inbound/notes.pdf",
                        "detected_type": "现场资料",
                        "adopted": False,
                        "adopted_fields": [],
                        "status": "unmatched",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (output / "review-queue.json").write_text(
        json.dumps({"schema_version": 1, "items": []}, ensure_ascii=False),
        encoding="utf-8",
    )


def _approve_stage_one_for_parameter_report(project: Path) -> None:
    prepare_project_materials(project)
    approve_project_materials(project, approval_text="资料归档确认，可以提取参数。")


def test_stage_two_confirms_parameter_equality_for_reuse_candidates(
    tmp_path: Path,
) -> None:
    project = tmp_path / "parameter-reuse-project"
    _initialize(project)
    raw_file = project / "raw/VALVE-001/source.devdata"
    spec_file = project / "specs/VALVE-001/spec.pdf"
    raw_file.parent.mkdir(parents=True)
    spec_file.parent.mkdir(parents=True)
    raw_file.write_bytes(b"unchanged-source")
    spec_file.write_bytes(b"unchanged-spec")
    _write_evidence_snapshot(project, value="Normal")
    baseline_extraction = json.loads(
        (project / "work/evidence-extraction/valves/VALVE-001.json").read_text(
            encoding="utf-8"
        )
    )
    _write_frozen_baseline(
        project,
        valve_tag="VALVE-001",
        raw_file=raw_file,
        spec_file=spec_file,
        material_extraction=baseline_extraction,
    )
    prepare_project_materials(project)
    approve_project_materials(
        project,
        approval_text="批准归档并复用未变化内容。",
        unchanged_reuse_policy="reuse_unchanged",
    )

    first = write_parameter_extraction_report(load_project(project))
    assert first.payload["reuse_review"] == {
        "policy": "reuse_unchanged",
        "status": "confirmed",
        "parameter_unchanged_valves": ["VALVE-001"],
        "parameter_changed_valves": [],
        "reuse_scope": "允许复用参数事实；客观观察和整阀结论仍受各自严格哈希门禁约束。",
    }
    assert "参数完全未变化：1 台" in first.markdown_path.read_text(
        encoding="utf-8"
    )

    _write_evidence_snapshot(project, value="Manual")
    second = write_parameter_extraction_report(load_project(project))
    assert second.payload["reuse_review"]["parameter_unchanged_valves"] == []
    assert second.payload["reuse_review"]["parameter_changed_valves"] == [
        {
            "valve_tag": "VALVE-001",
            "reason": "参数值、状态、单位、冲突或技术页绑定发生变化",
        }
    ]


def test_parameter_extraction_report_contains_matrix_and_full_evidence(
    tmp_path: Path,
) -> None:
    project = tmp_path / "parameter-report-project"
    _initialize(project)
    source = project / "raw/VALVE-001/source.devdata"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"source")
    _approve_stage_one_for_parameter_report(project)
    _write_evidence_snapshot(project)

    result = write_parameter_extraction_report(load_project(project))
    payload = json.loads(result.json_path.read_text(encoding="utf-8"))
    valve = payload["valves"][0]

    assert result.status == "parameter_extraction_reported"
    assert result.markdown_path == project / "work/stage-gates/parameter-extraction-report.md"
    html_path = project / "work/stage-gates/parameter-extraction-report.html"
    assert html_path.is_file()
    assert payload["stage"]["name"] == "parameter_extraction"
    assert valve["fields"]["positioner.device_mode"]["display_value"] == "Normal"
    assert valve["fields"]["specification.model_no"]["display_value"] == "无数据"
    assert valve["fields"]["status.continuous_diagnostics"]["display_value"] == "无数据"
    assert valve["fields"]["identity.valve_tag"]["raw_value"] == "VALVE-001"
    assert valve["fields"]["positioner.device_mode"]["display_label"] == "设备模式"
    assert "specification.design_pressure" in valve["extra_fields"]
    assert valve["full_evidence"]["valve_tag"] == "VALVE-001"
    assert payload["summary"]["missing_count"] >= 1
    markdown = result.markdown_path.read_text(encoding="utf-8")
    assert "逐阀关键参数" in markdown
    assert "逐阀审核状态" in markdown
    assert "需要你决定" in markdown
    assert "内部明细" in markdown
    assert "逐阀主参数矩阵" not in markdown
    assert "| 阀门 | 参数 | 值 | 状态 | 来源 |" not in markdown
    assert "inbound/notes.pdf" not in markdown
    assert "当前没有待确认项或冲突" in markdown
    assert "接受以上明确资料缺口" in markdown
    web_report = html_path.read_text(encoding="utf-8")
    assert "第二阶段参数审核报告" in web_report
    assert "VALVE-001" in web_report
    assert "不包含曲线判读" in web_report
    assert "批准第二阶段，进入第三阶段" in web_report
    assert "<table>" in web_report
    assert "阀门规格" in web_report
    assert "定位器" in web_report
    assert "执行机构" in web_report
    assert "关键检测指标" in web_report
    assert "UNRESOLVED CONFLICTS" not in web_report


def test_parameter_web_report_renders_data_status_in_plain_chinese(
    tmp_path: Path,
) -> None:
    project = tmp_path / "plain-status-project"
    _initialize(project)
    source = project / "raw/VALVE-001/source.devdata"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"source")
    _approve_stage_one_for_parameter_report(project)
    _write_evidence_snapshot(project)
    valve_path = project / "work/evidence-extraction/valves/VALVE-001.json"
    valve = json.loads(valve_path.read_text(encoding="utf-8"))
    valve["fields"]["status.continuous_diagnostics"] = {
        "status": "extracted",
        "selected": {
            "raw_value": "Continuous Diagnostics",
            "normalized_value": {"status": "present"},
            "raw_unit": "",
            "normalized_unit": "",
            "confidence": 1.0,
            "source": {"path": "raw/VALVE-001/source.devdata", "page": 1},
        },
        "candidates": [],
        "conflict": False,
    }
    valve["fields"]["status.task_execution"] = {
        "status": "extracted",
        "selected": {
            "raw_value": [{"task_name": "Run Ramp", "result": "Succeeded"}],
            "normalized_value": [
                {"task_name": "Run Ramp", "result": "Succeeded"}
            ],
            "raw_unit": "",
            "normalized_unit": "",
            "confidence": 1.0,
            "source": {"path": "raw/VALVE-001/source.devdata", "page": 1},
        },
        "candidates": [],
        "conflict": False,
    }
    valve_path.write_text(json.dumps(valve, ensure_ascii=False), encoding="utf-8")

    write_parameter_extraction_report(load_project(project))

    web_report = (
        project / "work/stage-gates/parameter-extraction-report.html"
    ).read_text(encoding="utf-8")
    assert "连续诊断数据</small>有数据" in web_report
    assert "测试结果</small><b class=\"\">无异常" in web_report
    assert "当前活动故障</small>未发现" in web_report
    assert "{&quot;status&quot;" not in web_report
    assert "{'status': 'present'}" not in web_report


def test_parameter_report_shows_confirmed_replacement_only_in_approval_surface(
    tmp_path: Path,
) -> None:
    project = tmp_path / "positioner-replacement-project"
    _initialize(project)
    source = project / "raw/VALVE-001/source.devdata"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"source")
    _approve_stage_one_for_parameter_report(project)
    _write_evidence_snapshot(project)
    valve_path = project / "work/evidence-extraction/valves/VALVE-001.json"
    valve = json.loads(valve_path.read_text(encoding="utf-8"))
    valve["fields"]["positioner.model"] = {
        "status": "extracted",
        "selected": {
            "raw_value": "SVI 2 AP-22123121",
            "normalized_value": "SVI 2 AP-22123121",
            "raw_unit": "",
            "normalized_unit": "",
            "confidence": 1.0,
            "source": {"path": "specs/VALVE-001/spec.pdf", "page": 2},
        },
        "candidates": [],
        "conflict": False,
    }
    valve_path.write_text(json.dumps(valve, ensure_ascii=False), encoding="utf-8")
    replacement_path = project / "experience/positioner_replacement_status.json"
    replacement_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "valves": {
                    "VALVE-001": {
                        "status": "replaced",
                        "display": "已更换",
                        "reason": "用户确认",
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = write_parameter_extraction_report(load_project(project))

    markdown = result.markdown_path.read_text(encoding="utf-8")
    html_report = (
        project / "work/stage-gates/parameter-extraction-report.html"
    ).read_text(encoding="utf-8")
    assert "SVI 2 AP-22123121" in markdown
    assert "SVI 2 AP-22123121" in html_report
    assert "更换状态：已更换" in markdown
    assert "已更换" in html_report
    assert "positioner_replacement" not in result.payload["valves"][0][
        "full_evidence"
    ]

    approve_parameter_extraction(load_project(project), approval_text="参数确认。")
    replacement = json.loads(replacement_path.read_text(encoding="utf-8"))
    replacement["valves"]["VALVE-001"]["display"] = "现场已更换"
    replacement_path.write_text(
        json.dumps(replacement, ensure_ascii=False), encoding="utf-8"
    )
    with pytest.raises(RuntimeError, match="parameter extraction approval is stale"):
        require_parameter_extraction_approval(load_project(project))


def test_parameter_report_lists_low_confidence_items_not_only_their_count(
    tmp_path: Path,
) -> None:
    project = tmp_path / "parameter-low-confidence-project"
    _initialize(project)
    source = project / "raw/VALVE-001/source.devdata"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"source")
    _approve_stage_one_for_parameter_report(project)
    _write_evidence_snapshot(project)
    valve_path = project / "work/evidence-extraction/valves/VALVE-001.json"
    valve = json.loads(valve_path.read_text(encoding="utf-8"))
    valve["fields"]["positioner.device_mode"]["status"] = "review_required"
    valve["review_items"] = [
        {
            "field": "positioner.device_mode",
            "reason": "low_ocr_confidence",
            "candidate_value": "Normal",
            "confidence": 0.72,
        }
    ]
    valve_path.write_text(json.dumps(valve, ensure_ascii=False), encoding="utf-8")

    result = write_parameter_extraction_report(load_project(project))

    assert any(
        item["field"] == "positioner.device_mode"
        and "low_ocr_confidence" in item["message"]
        for item in result.payload["attention"]
    )
    markdown = result.markdown_path.read_text(encoding="utf-8")
    assert "设备模式" in markdown
    assert "候选值 Normal" in markdown
    assert "positioner.device_mode" not in markdown
    assert "low_ocr_confidence" not in markdown
    assert "接受以上缺口和未解决项" in markdown


def test_parameter_extraction_fingerprint_ignores_generation_time(
    tmp_path: Path,
) -> None:
    project = tmp_path / "parameter-fingerprint-project"
    _initialize(project)
    (project / "raw/VALVE-001").mkdir()
    (project / "raw/VALVE-001/source.devdata").write_bytes(b"source")
    _approve_stage_one_for_parameter_report(project)
    _write_evidence_snapshot(project)

    first = write_parameter_extraction_report(load_project(project))
    first_payload = json.loads(first.json_path.read_text(encoding="utf-8"))
    first_fp = parameter_extraction_report_fingerprint(first_payload)
    first_payload["generated_at"] = "2099-01-01T00:00:00+00:00"
    second_fp = parameter_extraction_report_fingerprint(first_payload)
    assert first_fp == second_fp


def test_parameter_report_rejects_stale_extractor_schema(tmp_path: Path) -> None:
    project = tmp_path / "parameter-stale-extractor-project"
    _initialize(project)
    source = project / "raw/VALVE-001/source.devdata"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"source")
    _approve_stage_one_for_parameter_report(project)
    _write_evidence_snapshot(project)
    index_path = project / "work/evidence-extraction/project-index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    index["extractor_version"] = EXTRACTOR_VERSION - 1
    index_path.write_text(json.dumps(index), encoding="utf-8")

    with pytest.raises(RuntimeError, match="evidence extraction is stale"):
        write_parameter_extraction_report(load_project(project))


def test_parameter_extraction_approval_binds_snapshot_and_invalidates_on_change(
    tmp_path: Path,
) -> None:
    project = tmp_path / "parameter-approval-project"
    _initialize(project)
    source = project / "raw/VALVE-001/source.devdata"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"source")
    _approve_stage_one_for_parameter_report(project)
    _write_evidence_snapshot(project)
    write_parameter_extraction_report(load_project(project))

    approval_path = approve_parameter_extraction(
        load_project(project), approval_text="参数确认，可以进入逐图观察。"
    )
    approval = json.loads(approval_path.read_text(encoding="utf-8"))
    assert approval["status"] == "approved"
    assert approval["report_html"] == "work/stage-gates/parameter-extraction-report.html"
    assert len(approval["report_html_sha256"]) == 64
    assert require_parameter_extraction_approval(load_project(project))["status"] == "approved"

    html_path = project / "work/stage-gates/parameter-extraction-report.html"
    original_html = html_path.read_text(encoding="utf-8")
    html_path.write_text(original_html + "\n<!-- changed -->\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="parameter extraction approval is stale"):
        require_parameter_extraction_approval(load_project(project))
    html_path.write_text(original_html, encoding="utf-8")

    evidence_path = project / "work/evidence-extraction/valves/VALVE-001.json"
    changed = json.loads(evidence_path.read_text(encoding="utf-8"))
    changed["fields"]["positioner.device_mode"]["selected"]["normalized_value"] = "Manual"
    evidence_path.write_text(json.dumps(changed, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(RuntimeError, match="parameter extraction approval is stale"):
        require_parameter_extraction_approval(load_project(project))


def test_parameter_approval_reuses_only_period_key_difference(tmp_path: Path) -> None:
    project = tmp_path / "period-approval-project"
    _initialize(project)
    config_path = project / "project.toml"
    config_path.write_text(config_path.read_text(encoding="utf-8") +
        '\n[report_periods]\nactive="first"\nfirst_label="First"\nsecond_label="Second"\n',
        encoding="utf-8")
    source = project / "raw/VALVE-001/source.devdata"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"source")
    _approve_stage_one_for_parameter_report(project)
    _write_evidence_snapshot(project)
    write_parameter_extraction_report(load_project(project, report_period="first"))
    approval_path = approve_parameter_extraction(
        load_project(project, report_period="first"), approval_text="原批次参数已审核。"
    )
    original_approval = approval_path.read_bytes()
    assert require_parameter_extraction_approval(load_project(project, report_period="second"))["status"] == "approved"
    assert approval_path.read_bytes() == original_approval
    evidence_path = project / "work/evidence-extraction/valves/VALVE-001.json"
    changed = json.loads(evidence_path.read_text(encoding="utf-8"))
    changed["fields"]["positioner.device_mode"]["selected"]["normalized_value"] = "Manual"
    evidence_path.write_text(json.dumps(changed, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(RuntimeError, match="parameter extraction approval is stale"):
        require_parameter_extraction_approval(load_project(project, report_period="second"))


def test_cli_records_parameter_approval_verbatim(tmp_path: Path) -> None:
    project = tmp_path / "parameter-approval-cli-project"
    _initialize(project)
    source = project / "raw/VALVE-001/source.devdata"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"source")
    _approve_stage_one_for_parameter_report(project)
    _write_evidence_snapshot(project)
    write_parameter_extraction_report(load_project(project))

    completed = subprocess.run(
        [
            sys.executable,
            str(REPORT_SCRIPT),
            "--project",
            str(project),
            "--approve-parameters",
            "参数确认，可以进入逐图观察。",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "status=parameters_approved" in completed.stdout
    approval = json.loads(
        (project / "work/stage-gates/parameters-approval.json").read_text(
            encoding="utf-8"
        )
    )
    assert approval["approval_text"] == "参数确认，可以进入逐图观察。"


def test_parameter_extraction_approval_survives_identical_snapshot_regeneration(
    tmp_path: Path,
) -> None:
    project = tmp_path / "parameter-approval-stable-project"
    _initialize(project)
    source = project / "raw/VALVE-001/source.devdata"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"source")
    _approve_stage_one_for_parameter_report(project)
    _write_evidence_snapshot(project)
    write_parameter_extraction_report(load_project(project))
    approve_parameter_extraction(
        load_project(project), approval_text="参数确认，可以进入逐图观察。"
    )

    write_parameter_extraction_report(load_project(project))

    assert require_parameter_extraction_approval(load_project(project))["status"] == "approved"


def test_parameter_extraction_approval_invalidates_on_extraction_feature_change(
    tmp_path: Path,
) -> None:
    project = tmp_path / "parameter-approval-feature-project"
    _initialize(project)
    source = project / "raw/VALVE-001/source.devdata"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"source")
    _approve_stage_one_for_parameter_report(project)
    _write_evidence_snapshot(project)
    write_parameter_extraction_report(load_project(project))
    approve_parameter_extraction(
        load_project(project), approval_text="参数确认，可以进入逐图观察。"
    )

    project_toml = project / "project.toml"
    project_toml.write_text(
        project_toml.read_text(encoding="utf-8").replace(
            "include_air_piping_pages = true",
            "include_air_piping_pages = false",
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="parameter extraction approval is stale"):
        require_parameter_extraction_approval(load_project(project))


def test_parameter_extraction_approval_binds_ocr_semantics_not_timestamp(
    tmp_path: Path, monkeypatch,
) -> None:
    project = tmp_path / "parameter-approval-ocr-project"
    _initialize(project)
    source = project / "raw/VALVE-001/source.devdata"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"source")
    _approve_stage_one_for_parameter_report(project)
    _write_evidence_snapshot(project)
    ocr_document = project / "work/ocr/documents/source.json"
    ocr_document.parent.mkdir(parents=True)
    ocr_document.write_text(
        json.dumps({"created_at": "2026-08-28T00:00:00Z", "pages": [{"text": "A"}]}),
        encoding="utf-8",
    )
    write_parameter_extraction_report(load_project(project))
    approve_parameter_extraction(
        load_project(project), approval_text="参数确认，可以进入逐图观察。"
    )

    ocr_document.write_text(
        json.dumps({"created_at": "2099-01-01T00:00:00Z", "pages": [{"text": "A"}]}),
        encoding="utf-8",
    )
    assert require_parameter_extraction_approval(load_project(project))["status"] == "approved"

    from offline_engine import document_ocr_cache
    with monkeypatch.context() as changed_contract:
        changed_contract.setattr(document_ocr_cache, "SCHEMA_VERSION", document_ocr_cache.SCHEMA_VERSION + 1)
        with pytest.raises(RuntimeError, match="parameter extraction approval is stale"):
            require_parameter_extraction_approval(load_project(project))

    ocr_document.write_text(
        json.dumps({"created_at": "2099-01-01T00:00:00Z", "pages": [{"text": "B"}]}),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="parameter extraction approval is stale"):
        require_parameter_extraction_approval(load_project(project))
