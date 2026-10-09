from __future__ import annotations

import copy
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from offline_engine.experience import load_offline_experience
from formal_diagnosis_tools import single_valve_workflow
from offline_engine import evidence_extraction
from offline_engine.evidence_extraction import EXTRACTOR_VERSION
from offline_engine.evidence_extraction import (
    build_project_structured_evidence,
    load_material_extraction_evidence,
    project_evidence_pages,
    structured_evidence_accounting,
)
from offline_engine.formal_diagnosis import (
    FormalDiagnosisError,
    load_formal_diagnosis,
)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _synthetic_project(tmp_path: Path) -> tuple[Path, list[dict], Path]:
    project = tmp_path / "PROJECT-001"
    raw_dir = project / "raw/VALVE-001"
    raw_dir.mkdir(parents=True)
    (raw_dir / "source.pdf").write_bytes(b"anonymous-pdf-evidence")
    (raw_dir / "source.devdata").write_bytes(
        b"anonymous-devdata-evidence"
    )
    _write_json(
        project / "work/evidence-extraction/run-manifest.json",
        {
            "schema_version": 1,
            "extractor_version": EXTRACTOR_VERSION,
            "status": "completed",
            "inputs": [
                {
                    "path": path.relative_to(project).as_posix(),
                    "sha256": single_valve_workflow._sha256_file(path),
                    "size_bytes": path.stat().st_size,
                }
                for path in sorted(raw_dir.iterdir())
            ],
        },
    )
    curve_path = project / "work/evidence/VALVE-001/run_step.png"
    curve_path.parent.mkdir(parents=True)
    curve_path.write_bytes(b"anonymous-curve-image")
    report_page_path = project / "work/spec_pages/VALVE-001/spec-and-air.png"
    report_page_path.parent.mkdir(parents=True)
    report_page_path.write_bytes(b"anonymous-specification-and-air-page")
    _write_json(
        project
        / "work/evidence-extraction/valves/VALVE-001.json",
        {
            "schema_version": 1,
            "valve_tag": "VALVE-001",
            "status": "review_required",
            "fields": {
                "runtime.cycles": {
                    "status": "conflict",
                    "selected": {
                        "raw_value": "14691",
                        "normalized_value": 14691,
                        "raw_unit": "counts",
                        "normalized_unit": "counts",
                    },
                    "candidates": [
                        {
                            "raw_value": "14691",
                            "normalized_value": 14691,
                            "status": "extracted",
                        },
                        {
                            "raw_value": "14690",
                            "normalized_value": 14690,
                            "status": "conflicting_candidate",
                        },
                    ],
                    "conflict": True,
                }
            },
            "uncertainties": ["runtime.cycles requires review"],
        },
    )
    analysis = [
        {
            "folder": "VALVE-001",
            "included_in_report": True,
            "has_test_data": True,
            "basic_action_failed": False,
            "device_id": "DEVICE-001",
            "positioner_type": "POSITIONER-001",
            "positioner_report_info": {
                "positioner": {
                    "option_config": "Advanced Diagnostics; Double Acting;",
                    "device_mode": "Normal",
                    "valve_position": {"value": 12.5, "unit": "%"},
                },
                "runtime": {
                    "strokes": {"value": 241, "unit": "counts"},
                    "cycles": {"value": 14691, "unit": "counts"},
                },
                "status": {
                    "historical_faults": ["Historical actuator warning"],
                    "task_execution": [
                        {
                            "task_name": "Run Ramp",
                            "occurrence": 1,
                            "result": "Succeeded",
                        }
                    ],
                },
            },
            "metrics": {
                "Average Error": 1.5,
                "Maximum Hysteresis + Dead Band": 4.0,
                "step_count": 4,
                "run_ramp": {
                    "hysteresis": {
                        "max": {"value": 1.73, "unit": "%"},
                        "average": {"value": 0.87, "unit": "%"},
                        "min": {"value": 0.0, "unit": "%"},
                    },
                    "dead_band": {
                        "max": {"value": 1.64, "unit": "%"},
                        "average": {"value": 0.88, "unit": "%"},
                        "min": {"value": 0.0, "unit": "%"},
                    },
                    "linearity": {
                        "max": {"value": 1.06, "unit": "%"},
                        "average": {"value": 0.56, "unit": "%"},
                        "min": {"value": 0.02, "unit": "%"},
                    },
                },
            },
            "images": [
                {
                    "path": str(curve_path),
                    "test_key": "run_step",
                }
            ],
            "active_faults": [],
            "test_execution_issues": [],
            "report_pages": [
                {
                    "source": "specs/VALVE-001/specification.pdf",
                    "source_sha256": "c" * 64,
                    "page": 2,
                    "roles": ["specification", "air_piping"],
                    "image_path": str(report_page_path),
                    "match_basis": ["directory_tag", "visual_review"],
                }
            ],
        }
    ]
    analysis[0]["structured_evidence"] = build_project_structured_evidence(
        analysis[0],
        project_root=project,
    )
    return project, analysis, curve_path


def _refresh_structured_evidence(project: Path, source: dict) -> None:
    source["structured_evidence"] = build_project_structured_evidence(
        source,
        project_root=project,
    )


def test_formal_evidence_accepts_only_current_shared_extractor_version(
    tmp_path: Path,
) -> None:
    project, _analysis, _curve = _synthetic_project(tmp_path)

    current = load_material_extraction_evidence(
        project,
        "VALVE-001",
    )
    assert current["valve_tag"] == "VALVE-001"

    manifest_path = project / "work/evidence-extraction/run-manifest.json"
    manifest = _read_json(manifest_path)
    manifest["extractor_version"] = EXTRACTOR_VERSION - 1
    _write_json(manifest_path, manifest)
    with pytest.raises(
        ValueError,
        match=rf"extractor_version {EXTRACTOR_VERSION} is required",
    ):
        load_material_extraction_evidence(
            project,
            "VALVE-001",
        )


def test_project_target_binds_complete_structured_evidence(
    tmp_path: Path,
) -> None:
    project, analysis, _ = _synthetic_project(tmp_path)

    target = single_valve_workflow.build_project_target(
        analysis[0],
        project_root=project,
        project_id="PROJECT-001",
    )

    structured = target["evidence"]["structured_evidence"]
    assert structured == analysis[0]["structured_evidence"]
    assert target["evidence"]["source_files"] == structured["source_files"]
    source_record = structured["source_record"]
    assert structured["schema_version"] == 1
    assert source_record["positioner_report_info"]["status"][
        "historical_faults"
    ] == ["Historical actuator warning"]
    assert source_record["positioner_report_info"]["status"][
        "task_execution"
    ][0]["result"] == "Succeeded"
    assert source_record["metrics"]["run_ramp"]["dead_band"]["max"] == {
        "value": 1.64,
        "unit": "%",
    }
    assert structured["source_files"][0]["sha256"]
    assert structured["material_extraction"]["fields"]["runtime.cycles"][
        "conflict"
    ] is True
    assert structured["evidence_pages"] == [
        {
            "match_basis": ["directory_tag", "visual_review"],
            "page": 2,
            "physical_identity": f"{'c' * 64}:2",
            "roles": ["air_piping", "specification"],
            "source": "specs/VALVE-001/specification.pdf",
            "source_sha256": "c" * 64,
        }
    ]
    run_dir = project / "work/formal_diagnosis/runs/v1/VALVE-001"
    single_valve_workflow.prepare_run(
        target,
        load_offline_experience(),
        run_dir,
        diagnosis_version="v1",
        parent_run="none",
        revision_reason="Structured evidence test.",
    )
    bundle = _read_json(run_dir / "01_evidence_bundle.json")
    manifest = _read_json(run_dir / "00_run_manifest.json")
    assert bundle["evidence"]["specification_images"][0]["roles"] == [
        "air_piping",
        "specification",
    ]
    assert manifest["workflow"] == "single_valve_integrated_codex_v3"
    assert manifest["diagnosis_method_required"] == "codex_exec_integrated"
    assert manifest["next_file"] is None
    assert manifest["next_operation"] == "prepare-integrated-diagnosis"
    assert not (run_dir / "03_observation_template.json").exists()


def test_project_target_requires_prepared_structured_evidence(tmp_path: Path) -> None:
    project, analysis, _ = _synthetic_project(tmp_path)
    analysis[0].pop("structured_evidence")

    with pytest.raises(ValueError, match="run prepare-formal-evidence"):
        single_valve_workflow.build_project_target(
            analysis[0], project_root=project, project_id="PROJECT-001"
        )


def test_project_target_does_not_rebuild_prepared_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, analysis, _ = _synthetic_project(tmp_path)
    monkeypatch.setattr(
        evidence_extraction,
        "build_project_structured_evidence",
        lambda *args, **kwargs: pytest.fail("prepare-project rebuilt evidence"),
    )

    target = single_valve_workflow.build_project_target(
        analysis[0], project_root=project, project_id="PROJECT-001"
    )

    assert target["evidence"]["structured_evidence"] == analysis[0]["structured_evidence"]


def test_build_and_validate_preserve_top_level_fault_precedence(tmp_path: Path) -> None:
    project, analysis, _ = _synthetic_project(tmp_path)
    source = analysis[0]
    source["active_faults"] = ["Top fault"]
    source["positioner_report_info"]["active_faults"] = ["Nested fault"]
    _refresh_structured_evidence(project, source)

    target = single_valve_workflow.build_project_target(
        source, project_root=project, project_id="PROJECT-001"
    )

    assert target["evidence"]["structured_evidence"]["source_record"][
        "positioner_report_info"
    ]["active_faults"] == ["Top fault"]


@pytest.mark.parametrize(
    "damage",
    ["schema", "source_record", "source_files", "evidence_pages", "material"],
)
def test_project_target_rejects_damaged_summary_evidence(
    tmp_path: Path, damage: str,
) -> None:
    project, analysis, _ = _synthetic_project(tmp_path)
    structured = analysis[0]["structured_evidence"]
    if damage == "schema":
        structured["schema_version"] = 99
    elif damage == "source_record":
        structured["source_record"]["device_id"] = "tampered"
    elif damage == "source_files":
        structured["source_files"][0]["sha256"] = "0" * 64
    elif damage == "evidence_pages":
        structured["evidence_pages"][0]["roles"] = ["accessory"]
    else:
        structured["material_extraction"]["status"] = "tampered"

    with pytest.raises(ValueError, match="prepare-formal-evidence"):
        single_valve_workflow.build_project_target(
            analysis[0], project_root=project, project_id="PROJECT-001"
        )


def test_project_target_rejects_changed_source_without_rebuilding_summary(
    tmp_path: Path,
) -> None:
    project, analysis, _ = _synthetic_project(tmp_path)
    (project / "raw/VALVE-001/source.pdf").write_bytes(b"changed-evidence")

    with pytest.raises(ValueError, match="prepare-formal-evidence"):
        single_valve_workflow.build_project_target(
            analysis[0], project_root=project, project_id="PROJECT-001"
        )


def test_project_target_keeps_unrendered_accessory_as_structured_evidence_only(
    tmp_path: Path,
) -> None:
    project, analysis, _ = _synthetic_project(tmp_path)
    analysis[0]["report_pages"].append(
        {
            "source": "specs/VALVE-001/accessory.pdf",
            "source_sha256": "d" * 64,
            "page": 4,
            "roles": ["accessory"],
            "image_path": "",
            "match_basis": ["page_label"],
        }
    )
    _refresh_structured_evidence(project, analysis[0])

    target = single_valve_workflow.build_project_target(
        analysis[0],
        project_root=project,
        project_id="PROJECT-001",
    )

    assert len(target["evidence"]["embedded_specification_images"]) == 1
    evidence_pages = target["evidence"]["structured_evidence"][
        "evidence_pages"
    ]
    assert any(page["roles"] == ["accessory"] for page in evidence_pages)


def test_fresh_observation_rejects_review_flags_without_itemized_accounting(
    tmp_path: Path,
) -> None:
    project, analysis, _ = _synthetic_project(tmp_path)
    target = single_valve_workflow.build_project_target(
        analysis[0], project_root=project, project_id="PROJECT-001"
    )
    run_dir = project / "work/formal_diagnosis/runs/v2/VALVE-001"
    single_valve_workflow.prepare_run(
        target,
        load_offline_experience(),
        run_dir,
        diagnosis_version="v2",
        parent_run="none",
        revision_reason="Accounting gate test.",
    )
    observations = _complete_observations(run_dir)
    observations.pop("evidence_accounting")
    with pytest.raises(ValueError, match="evidence_accounting"):
        single_valve_workflow._validate_observations(
            _read_json(run_dir / "01_evidence_bundle.json"),
            observations,
        )


def test_task_execution_accounting_defaults_to_complete_row_count() -> None:
    structured_evidence = {
        "source_record": {
            "positioner_report_info": {
                "status": {
                    "task_execution": [
                        {"task_name": f"Task {index}", "result": "Succeeded"}
                        for index in range(7)
                    ]
                }
            }
        }
    }

    accounting = structured_evidence_accounting(
        structured_evidence
    )

    assert accounting["task_execution"]["expected_count"] == 7


def test_task_execution_accounting_uses_matching_explicit_count() -> None:
    structured_evidence = {
        "source_record": {
            "positioner_report_info": {
                "task_execution_expected_count": 7,
                "task_execution": [
                    {"task_name": f"Task {index}", "result": "Succeeded"}
                    for index in range(7)
                ],
            }
        }
    }

    accounting = structured_evidence_accounting(
        structured_evidence
    )

    assert accounting["task_execution"]["expected_count"] == 7


@pytest.mark.parametrize("tamper", ["delete_row", "change_count"])
def test_task_execution_accounting_gate_rejects_incomplete_or_tampered_count(
    tmp_path: Path,
    tamper: str,
) -> None:
    project, analysis, _ = _synthetic_project(tmp_path)
    analysis[0]["positioner_report_info"]["status"]["task_execution"] = [
        {"task_name": f"Task {index}", "result": "Succeeded"}
        for index in range(7)
    ]
    _refresh_structured_evidence(project, analysis[0])
    target = single_valve_workflow.build_project_target(
        analysis[0], project_root=project, project_id="PROJECT-001"
    )
    run_dir = project / "work/formal_diagnosis/runs/v2/VALVE-001"
    single_valve_workflow.prepare_run(
        target,
        load_offline_experience(),
        run_dir,
        diagnosis_version="v2",
        parent_run="none",
        revision_reason="Task accounting gate test.",
    )
    observations = _complete_observations(run_dir)
    accounting = observations["evidence_accounting"]["task_execution"]
    if tamper == "delete_row":
        accounting["value"] = accounting["value"][:-1]
    else:
        accounting["expected_count"] = 6

    with pytest.raises(ValueError, match="evidence_accounting"):
        single_valve_workflow._validate_observations(
            _read_json(run_dir / "01_evidence_bundle.json"),
            observations,
        )


def test_reset_sentinel_cannot_replace_normalized_empty_active_faults(
    tmp_path: Path,
) -> None:
    project, analysis, _ = _synthetic_project(tmp_path)
    analysis[0]["active_faults"] = ["Reset"]
    target = single_valve_workflow.build_project_target(
        analysis[0], project_root=project, project_id="PROJECT-001"
    )
    assert target["evidence"]["displayed_active_faults"] == []
    assert target["evidence"]["structured_evidence"]["source_record"][
        "active_faults"
    ] == []


def test_report_pages_merge_roles_by_physical_identity_and_check_source_hash(
    tmp_path: Path,
) -> None:
    source = tmp_path / "specs/VALVE-001/spec.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"specification-pdf")
    source_sha256 = single_valve_workflow._sha256_file(source)
    pages = project_evidence_pages(
        {
            "report_pages": [
                {
                    "source": "specs/VALVE-001/spec.pdf",
                    "source_sha256": source_sha256,
                    "page": 2,
                    "roles": ["specification"],
                },
                {
                    "source": "specs/VALVE-001/spec.pdf",
                    "source_sha256": source_sha256,
                    "page": 2,
                    "roles": ["air_piping"],
                },
            ]
        },
        project_root=tmp_path,
    )
    assert len(pages) == 1
    assert pages[0]["roles"] == ["air_piping", "specification"]

    with pytest.raises(ValueError, match="source_sha256"):
        project_evidence_pages(
            {
                "report_pages": [
                    {
                        "source": "specs/VALVE-001/spec.pdf",
                        "source_sha256": "0" * 64,
                        "page": 2,
                        "roles": ["specification"],
                    }
                ]
            },
            project_root=tmp_path,
        )


def test_specification_review_accepts_physical_identity_and_legacy_sha(
    tmp_path: Path,
) -> None:
    project, analysis, _ = _synthetic_project(tmp_path)
    target = single_valve_workflow.build_project_target(
        analysis[0],
        project_root=project,
        project_id="PROJECT-001",
    )
    run_dir = project / "work/formal_diagnosis/runs/v1/VALVE-001"
    single_valve_workflow.prepare_run(
        target,
        load_offline_experience(),
        run_dir,
        diagnosis_version="v1",
        parent_run="none",
        revision_reason="Specification review identity test.",
    )
    bundle = _read_json(run_dir / "01_evidence_bundle.json")
    observations = _complete_observations(run_dir)
    single_valve_workflow._validate_observations(bundle, observations)

    legacy = copy.deepcopy(observations)
    legacy.pop("reviewed_specification_physical_identities")
    single_valve_workflow._validate_observations(bundle, legacy)

    incorrect = copy.deepcopy(observations)
    incorrect["reviewed_specification_physical_identities"] = [
        "wrong-source-sha256:99"
    ]
    with pytest.raises(ValueError, match="review is incomplete"):
        single_valve_workflow._validate_observations(bundle, incorrect)


def test_structured_evidence_hash_is_path_independent_and_field_sensitive(
    tmp_path: Path,
) -> None:
    first_project, first_analysis, _ = _synthetic_project(tmp_path / "one")
    second_project, second_analysis, _ = _synthetic_project(tmp_path / "two")
    first = single_valve_workflow.build_project_target(
        first_analysis[0],
        project_root=first_project,
        project_id="PROJECT-001",
    )
    second = single_valve_workflow.build_project_target(
        second_analysis[0],
        project_root=second_project,
        project_id="PROJECT-001",
    )

    first_hash = single_valve_workflow.diagnostic_evidence_sha256(
        {"evidence": first["evidence"]}
    )
    assert first_hash == single_valve_workflow.diagnostic_evidence_sha256(
        {"evidence": second["evidence"]}
    )

    second["evidence"]["structured_evidence"]["source_record"][
        "positioner_report_info"
    ]["status"]["historical_faults"].append("New historical fault")
    assert first_hash != single_valve_workflow.diagnostic_evidence_sha256(
        {"evidence": second["evidence"]}
    )

    extraction_path = (
        second_project
        / "work/evidence-extraction/valves/VALVE-001.json"
    )
    extraction = _read_json(extraction_path)
    extraction["fields"]["runtime.cycles"]["status"] = "extracted"
    extraction["fields"]["runtime.cycles"]["conflict"] = False
    _write_json(extraction_path, extraction)
    with pytest.raises(ValueError, match="prepare-formal-evidence"):
        single_valve_workflow.build_project_target(
            second_analysis[0],
            project_root=second_project,
            project_id="PROJECT-001",
        )
    _refresh_structured_evidence(second_project, second_analysis[0])
    second = single_valve_workflow.build_project_target(
        second_analysis[0],
        project_root=second_project,
        project_id="PROJECT-001",
    )
    assert first_hash != single_valve_workflow.diagnostic_evidence_sha256(
        {"evidence": second["evidence"]}
    )

    second = single_valve_workflow.build_project_target(
        second_analysis[0],
        project_root=second_project,
        project_id="PROJECT-001",
    )
    second["evidence"]["structured_evidence"]["source_record"][
        "positioner_report_info"
    ]["status"]["task_execution"][0]["result"] = "Failed"
    assert first_hash != single_valve_workflow.diagnostic_evidence_sha256(
        {"evidence": second["evidence"]}
    )

    second = single_valve_workflow.build_project_target(
        second_analysis[0],
        project_root=second_project,
        project_id="PROJECT-001",
    )
    second["evidence"]["structured_evidence"]["source_record"]["metrics"][
        "run_ramp"
    ]["linearity"]["average"]["value"] = 0.57
    assert first_hash != single_valve_workflow.diagnostic_evidence_sha256(
        {"evidence": second["evidence"]}
    )


def test_report_field_manifest_does_not_change_diagnostic_evidence(
    tmp_path: Path,
) -> None:
    project, analysis, _ = _synthetic_project(tmp_path)
    without_manifest = single_valve_workflow.build_project_target(
        analysis[0],
        project_root=project,
        project_id="PROJECT-001",
    )
    source_with_manifest = copy.deepcopy(analysis[0])
    source_with_manifest["report_field_manifest"] = {
        "positioner.device_id": {
            "display_text": "DEVICE-001",
            "source_path": "positioner.device_id",
        }
    }
    with_manifest = single_valve_workflow.build_project_target(
        source_with_manifest,
        project_root=project,
        project_id="PROJECT-001",
    )

    assert "report_field_manifest" not in with_manifest["evidence"][
        "structured_evidence"
    ]["source_record"]
    assert single_valve_workflow.diagnostic_evidence_sha256(
        {"evidence": without_manifest["evidence"]}
    ) == single_valve_workflow.diagnostic_evidence_sha256(
        {"evidence": with_manifest["evidence"]}
    )


def test_inheritance_rejects_changed_material_extraction_evidence(
    tmp_path: Path,
) -> None:
    project, analysis, _ = _synthetic_project(tmp_path)
    common = load_offline_experience()

    parent_root = project / "work/formal_diagnosis/runs/v1"
    single_valve_workflow.prepare_project_runs(
        analysis,
        project_root=project,
        project_id="PROJECT-001",
        experience_text=common,
        run_root=parent_root,
        diagnosis_version="v1",
        parent_run="none",
        revision_reason="Initial diagnosis.",
    )
    parent_dir = parent_root / "VALVE-001"
    _submit_complete_integrated(parent_dir)
    _approve_complete_project_diagnosis(parent_root)

    extraction_path = project / "work/evidence-extraction/valves/VALVE-001.json"
    extraction = _read_json(extraction_path)
    extraction["fields"]["runtime.cycles"]["selected"][
        "normalized_value"
    ] = 14692
    _write_json(extraction_path, extraction)
    child_root = project / "work/formal_diagnosis/runs/v2"
    with pytest.raises(ValueError, match="prepare-formal-evidence"):
        single_valve_workflow.prepare_project_runs(
            analysis,
            project_root=project,
            project_id="PROJECT-001",
            experience_text=common,
            run_root=child_root,
            diagnosis_version="v2",
            parent_run=str(parent_root),
            revision_reason="Updated structured evidence.",
        )


def test_inheritance_rejects_stage_one_reanalyze_all_policy(
    tmp_path: Path,
) -> None:
    project, analysis, _ = _synthetic_project(tmp_path)
    common = load_offline_experience()

    parent_root = project / "work/formal_diagnosis/runs/v1"
    single_valve_workflow.prepare_project_runs(
        analysis,
        project_root=project,
        project_id="PROJECT-001",
        experience_text=common,
        run_root=parent_root,
        diagnosis_version="v1",
        parent_run="none",
        revision_reason="Initial diagnosis.",
    )
    parent_dir = parent_root / "VALVE-001"
    _submit_complete_integrated(parent_dir)
    _approve_complete_project_diagnosis(parent_root)

    child_root = project / "work/formal_diagnosis/runs/v2"
    single_valve_workflow.prepare_project_runs(
        analysis,
        project_root=project,
        project_id="PROJECT-001",
        experience_text=common,
        run_root=child_root,
        diagnosis_version="v2",
        parent_run=str(parent_root),
        revision_reason="User requested full reanalysis.",
    )
    child_dir = child_root / "VALVE-001"
    _submit_complete_integrated(child_dir)
    approval_path = project / "work/stage-gates/materials-approval.json"
    approval_path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(
        approval_path,
        {"unchanged_reuse_policy": "reanalyze_all"},
    )

    with pytest.raises(ValueError, match="full reanalysis"):
        single_valve_workflow.inherit_frozen_diagnosis(
            parent_dir,
            child_dir,
        )


def test_inheritance_preserves_available_algorithm_audit(
    tmp_path: Path,
) -> None:
    project, analysis, _ = _synthetic_project(tmp_path)
    common = load_offline_experience()

    parent_root = project / "work/formal_diagnosis/runs/v1"
    single_valve_workflow.prepare_project_runs(
        analysis,
        project_root=project,
        project_id="PROJECT-001",
        experience_text=common,
        run_root=parent_root,
        diagnosis_version="v1",
        parent_run="none",
        revision_reason="Initial diagnosis.",
    )
    parent_dir = parent_root / "VALVE-001"
    _submit_complete_integrated(parent_dir)
    _approve_complete_project_diagnosis(parent_root)

    child_root = project / "work/formal_diagnosis/runs/v2"
    single_valve_workflow.prepare_project_runs(
        analysis,
        project_root=project,
        project_id="PROJECT-001",
        experience_text=common,
        run_root=child_root,
        diagnosis_version="v2",
        parent_run=str(parent_root),
        revision_reason="Unchanged evidence inheritance.",
    )
    child_dir = child_root / "VALVE-001"
    _submit_complete_integrated(child_dir)

    inherited = single_valve_workflow.inherit_frozen_diagnosis(
        parent_dir, child_dir
    )

    assert inherited["algorithm_audit_status"] == "available"
    assert inherited["algorithm_audit_snapshot"] == _read_json(
        parent_dir / "05_diagnosis.json"
    )["algorithm_audit_snapshot"]
    single_valve_workflow._validate_diagnosis(child_dir, inherited)


def test_inheritance_rejects_refrozen_fresh_parent_downgraded_to_legacy(
    tmp_path: Path,
) -> None:
    project, analysis, _ = _synthetic_project(tmp_path)
    common = load_offline_experience()

    parent_root = project / "work/formal_diagnosis/runs/v1"
    single_valve_workflow.prepare_project_runs(
        analysis,
        project_root=project,
        project_id="PROJECT-001",
        experience_text=common,
        run_root=parent_root,
        diagnosis_version="v1",
        parent_run="none",
        revision_reason="Initial diagnosis.",
    )
    parent_dir = parent_root / "VALVE-001"
    _submit_complete_integrated(parent_dir)
    _approve_complete_project_diagnosis(parent_root)
    parent_diagnosis_path = parent_dir / "05_diagnosis.json"
    parent_diagnosis = _read_json(parent_diagnosis_path)
    parent_diagnosis["algorithm_audit_status"] = "legacy_unavailable"
    parent_diagnosis["algorithm_audit_snapshot"] = None
    _write_json(parent_diagnosis_path, parent_diagnosis)
    (parent_dir / "06_freeze.sha256").write_text(
        "".join(
            f"{single_valve_workflow._sha256_file(parent_dir / name)}  {name}\n"
            for name in (
                "00_experience.md",
                "00_version.json",
                "01_evidence_bundle.json",

                "03_observations.json",

                "05_diagnosis.json",
            )
        ),
        encoding="utf-8",
    )

    child_root = project / "work/formal_diagnosis/runs/v2"
    single_valve_workflow.prepare_project_runs(
        analysis,
        project_root=project,
        project_id="PROJECT-001",
        experience_text=common,
        run_root=child_root,
        diagnosis_version="v2",
        parent_run=str(parent_root),
        revision_reason="Attempted inheritance.",
    )
    child_dir = child_root / "VALVE-001"
    _submit_complete_integrated(child_dir)

    with pytest.raises(FormalDiagnosisError, match="not inherited|cannot be downgraded"):
        single_valve_workflow.inherit_frozen_diagnosis(parent_dir, child_dir)
    assert not (child_dir / "05_diagnosis.json").exists()
    assert _read_json(child_dir / "00_run_manifest.json")["stage"] == (
        "awaiting_diagnosis"
    )


def test_legacy_algorithm_audit_inherits_through_multiple_generations(
    tmp_path: Path,
) -> None:
    project, analysis, _ = _synthetic_project(tmp_path)
    common = load_offline_experience()

    parent_root = project / "work/formal_diagnosis/runs/v1"
    single_valve_workflow.prepare_project_runs(
        analysis,
        project_root=project,
        project_id="PROJECT-001",
        experience_text=common,
        run_root=parent_root,
        diagnosis_version="v1",
        parent_run="none",
        revision_reason="Pre-audit legacy simulation.",
    )
    parent_dir = parent_root / "VALVE-001"
    _submit_complete_integrated(parent_dir)
    _approve_complete_project_diagnosis(parent_root)
    parent_diagnosis_path = parent_dir / "05_diagnosis.json"
    parent_diagnosis = _read_json(parent_diagnosis_path)
    for field in (
        "algorithm_audit_contract",
        "algorithm_audit_status",
        "algorithm_audit_snapshot",
    ):
        parent_diagnosis.pop(field)
    _write_json(parent_diagnosis_path, parent_diagnosis)
    parent_manifest_path = parent_dir / "00_run_manifest.json"
    parent_manifest = _read_json(parent_manifest_path)
    parent_manifest.pop("algorithm_audit_contract")
    _write_json(parent_manifest_path, parent_manifest)
    frozen_names = (
        "00_experience.md",
        "00_version.json",
        "01_evidence_bundle.json",

        "03_observations.json",

        "05_diagnosis.json",
    )
    (parent_dir / "06_freeze.sha256").write_text(
        "".join(
            f"{single_valve_workflow._sha256_file(parent_dir / name)}  {name}\n"
            for name in frozen_names
        ),
        encoding="utf-8",
    )

    def inherit_version(version: str, parent: Path) -> Path:
        run_root = project / f"work/formal_diagnosis/runs/{version}"
        single_valve_workflow.prepare_project_runs(
            analysis,
            project_root=project,
            project_id="PROJECT-001",
            experience_text=common,
            run_root=run_root,
            diagnosis_version=version,
            parent_run=str(parent.parent),
            revision_reason="Unchanged legacy inheritance.",
        )
        run_dir = run_root / "VALVE-001"
        _submit_complete_integrated(run_dir)
        inherited = single_valve_workflow.inherit_frozen_diagnosis(
            parent, run_dir
        )
        assert inherited["algorithm_audit_status"] == "legacy_unavailable"
        _write_json(
            run_dir / single_valve_workflow.DIAGNOSIS_DRAFT_FILENAME,
            inherited,
        )
        single_valve_workflow.prepare_diagnosis_review(run_root)
        single_valve_workflow.approve_project_diagnosis(
            run_root, "同意继承旧版诊断。"
        )
        return run_dir

    child_dir = inherit_version("v2", parent_dir)
    grandchild_dir = inherit_version("v3", child_dir)

    assert _read_json(grandchild_dir / "05_diagnosis.json")[
        "algorithm_audit_status"
    ] == "legacy_unavailable"


def test_project_target_rejects_stale_material_extraction_inputs(
    tmp_path: Path,
) -> None:
    project, analysis, _ = _synthetic_project(tmp_path)
    (project / "raw/VALVE-001/source.pdf").write_bytes(b"changed-source")

    with pytest.raises(ValueError, match="run input is stale"):
        single_valve_workflow.build_project_target(
            analysis[0],
            project_root=project,
            project_id="PROJECT-001",
        )

def _complete_observations(run_dir: Path) -> dict:
    bundle = _read_json(run_dir / "01_evidence_bundle.json")
    observations = {
        "schema_version": 1,
        "displayed_data_reviewed": True,
        "structured_evidence_reviewed": True,
        "active_faults_reviewed": True,
        "test_execution_issues_reviewed": True,
        "curve_observations": [
            {
                "image_sha256": image["image_sha256"],
                "test_key": image["test_key"],
                "observation": "",
                **(
                    {
                        "visual_checks": {
                            key: ""
                            for key in (
                                single_valve_workflow.STEP_VISUAL_CHECKS
                                if image["test_key"]
                                in single_valve_workflow.STEP_TEST_KEYS
                                else single_valve_workflow.SIGNATURE_VISUAL_CHECKS
                            )
                        }
                    }
                    if image["test_key"]
                    in (
                        single_valve_workflow.STEP_TEST_KEYS
                        | single_valve_workflow.SIGNATURE_TEST_KEYS
                    )
                    else {}
                ),
            }
            for image in bundle["evidence"]["curve_images"]
        ],
        "specification_observations": [
            {
                "image_sha256": image["image_sha256"],
                "physical_identity": image.get("physical_identity"),
                "observation": "",
            }
            for image in bundle["evidence"]["specification_images"]
        ],
    }
    observations["reviewed_specification_image_sha256s"] = [
        item["image_sha256"]
        for item in observations["specification_observations"]
    ]
    observations["reviewed_specification_physical_identities"] = [
        item.get("physical_identity") or item["image_sha256"]
        for item in observations["specification_observations"]
    ]
    structured_evidence = bundle["evidence"].get("structured_evidence") or {}
    if structured_evidence:
        observations["evidence_accounting"] = (
            structured_evidence_accounting(
                structured_evidence
            )
        )
    for item in observations["curve_observations"]:
        item["observation"] = (
            "阶跃平台均可到位，换向处存在轻微迟滞。"
        )
        for key in item.get("visual_checks") or {}:
            item["visual_checks"][key] = "absent"
    for item in observations["specification_observations"]:
        item["observation"] = "规格证据已复核。"
    return observations


def _complete_diagnosis(run_dir: Path) -> dict:
    diagnosis = _read_json(
        run_dir / single_valve_workflow.DIAGNOSIS_DRAFT_FILENAME
    )
    for item in diagnosis["curve_interpretations"]:
        item.update(
            {
                "diagnosis": "动作完整，存在轻微调节迟滞。",
                "rating": 2,
                "action": "保持运行并在下次检修时复测。",
                "confidence": "high",
            }
        )
    diagnosis["whole_valve"].update(
        {
            "information_complete": True,
            "final_rating": 2,
            "rating_conclusion": "动作完整但有轻微迟滞，评为2级。",
            "main_issue": "换向处存在轻微迟滞。",
            "issues": ["换向处存在轻微迟滞。"],
            "actions": ["下次检修时复测阶跃和斜坡特性。"],
            "supporting_evidence": ["阶跃平台均可到位。"],
            "counter_evidence": ["未见拒动或行程丢失。"],
            "uncertainties": [],
        }
    )
    if diagnosis.get("algorithm_audit_status") == "available":
        diagnosis["algorithm_audit_snapshot"] = (
            single_valve_workflow._calculate_algorithm_audit(
                run_dir, diagnosis
            )
        )
    return diagnosis


def _complete_integrated_result(run_dir: Path) -> dict:
    result = _read_json(
        run_dir
        / single_valve_workflow.INTEGRATED_TEMPLATE_FILENAME
    )
    result["model_provenance"].update(
        {
            "model": "gpt-6-sol",
            "reasoning_effort": "high",
        }
    )
    for field in result["evidence_review"]:
        result["evidence_review"][field] = True
    for item in result["curve_assessments"]:
        item.update(
            {
                "observation": "阶跃轨迹连续，各平台可到位。",
                "visible_features": ["轨迹连续", "平台到位"],
                "diagnosis": "未见影响主要功能的曲线异常。",
                "rating": 1,
                "action": "保持运行并按周期复测。",
                "confidence": "high",
            }
        )
        for key in item["visual_checks"]:
            item["visual_checks"][key] = "absent"
    for item in result["specification_observations"]:
        item["observation"] = "已核对技术页与当前阀门的归属。"
    result["whole_valve"].update(
        {
            "information_complete": True,
            "final_rating": 1,
            "rating_conclusion": "现有证据未见影响主要功能的异常，评为1级。",
            "main_issue": "未见影响主要功能的异常。",
            "issues": ["未见影响主要功能的异常。"],
            "actions": ["保持运行并按周期复测。"],
            "supporting_evidence": ["全部成功测试均完成主要动作。"],
            "counter_evidence": ["未见拒动、方向性失效或行程丢失。"],
            "uncertainties": [],
        }
    )
    return result


def test_integrated_model_call_preserves_model_rating_and_writes_both_layers(
    tmp_path: Path,
) -> None:
    project, analysis, _ = _synthetic_project(tmp_path)
    common = load_offline_experience()
    run_root = project / "work/formal_diagnosis/runs/v2"
    single_valve_workflow.prepare_project_runs(
        analysis,
        project_root=project,
        project_id="PROJECT-001",
        experience_text=common,
        run_root=run_root,
        diagnosis_version="v2",
        parent_run="none",
        revision_reason="Integrated model diagnosis.",
    )
    run_dir = run_root / "VALVE-001"
    prepared = single_valve_workflow.prepare_integrated_model_request(
        run_dir
    )

    assert prepared["workflow"] == "single_valve_integrated_codex_v3"
    assert prepared["image_paths"]
    assert prepared["worker_model"] == "gpt-6-sol"
    assert prepared["worker_reasoning_effort"] == "high"
    assert prepared["single_call_includes_visual_and_whole_valve_diagnosis"] is True
    request = _read_json(run_dir / prepared["request"])
    prompt = Path(prepared["prompt"]).read_text(encoding="utf-8")
    assert request["legacy_ratings_allowed"] is False
    assert "final_rating" not in request["evidence"]
    assert request["experience_text"] == common
    assert request["report_writing"] == list(single_valve_workflow.HUMAN_WRITING_RULES)
    assert all(rule in prompt for rule in request["report_writing"])
    assert all(rule in prompt for rule in request["required_reasoning"])
    assert all(value in prompt for value in request["rating_contract"].values())
    assert prompt.index('"report_writing":') < prompt.index('"evidence":')
    assert prompt.index('"required_reasoning":') < prompt.index('"evidence":')
    assert "issues 是直接进入报告的正文" in prompt
    assert "复测不是默认建议" in prompt
    assert "每张曲线的 action" in prompt
    assert "条件不足时保留判断限制" in prompt
    assert "本次记录内未关到位，延长记录复测。" not in prompt
    assert "不设固定条数" in prompt
    assert prompt.endswith(common)
    assert prompt.count(common) == 1
    assert prepared["request"] not in prompt
    assert "无需再读取请求 JSON 或经验副本中的相同内容" in prompt
    assert "同时读取 evidence.source_files 列出的全部原始资料" in prompt
    assert json.dumps({key: value for key, value in request.items() if key != "experience_text"}, ensure_ascii=False, separators=(",", ":")) in prompt
    assert request["evidence"] == _read_json(run_dir / "01_evidence_bundle.json")["evidence"]
    assert list(request["evidence"])[:3] == [
        "test_execution_issues", "active_faults", "displayed_operating_context"
    ]
    assert request["source_locator"]["project_root"] == str(project.resolve())
    for field in ("candidate_rules", "candidate_cases", "applicable_rule_ids_by_test_key"):
        assert field not in request
    forbidden = ("experience_review", "experience_rule_ids", "retrieved_case_ids", "not_adopted_reason")
    for name in ("template", "schema"):
        output = Path(prepared[name]).read_text(encoding="utf-8")
        assert all(field not in output for field in forbidden)
    assert any(
        "needs_review 只表示图像缺失、无法辨认、归属错误或具体歧义" in item
        for item in request["required_reasoning"]
    )
    assert "needs_review 只表示图像缺失、无法辨认、归属错误或具体歧义" in prompt
    assert "完全相同的规范名称" in prompt
    assert any(
        "metric_threshold_basis" in item and "Average Error" in item
        for item in request["required_reasoning"]
    )
    template = _read_json(run_dir / prepared["template"])
    assert template["model_provenance"] == {
        "decision_source": "codex_exec_integrated",
        "model": "gpt-6-sol",
        "reasoning_effort": "high",
        "all_evidence_and_images_in_single_call": True,
        "rating_decided_by": "model",
    }

    result = _complete_integrated_result(run_dir)
    single_valve_workflow.submit_integrated_model_result(
        run_dir, result
    )

    observations = _read_json(run_dir / "03_observations.json")
    diagnosis = _read_json(
        run_dir / single_valve_workflow.DIAGNOSIS_DRAFT_FILENAME
    )
    assert observations["curve_observations"][0]["observation"] == (
        result["curve_assessments"][0]["observation"]
    )
    assert diagnosis["curve_interpretations"][0]["diagnosis"] == (
        result["curve_assessments"][0]["diagnosis"]
    )
    assert diagnosis["whole_valve"]["final_rating"] == 1
    assert diagnosis["model_provenance"]["rating_decided_by"] == "model"
    assert diagnosis["human_writing_reference"]["rules"] == request["report_writing"]
    assert "maximum_characters" not in diagnosis["human_writing_reference"]
    assert "forbidden_phrases" not in diagnosis["human_writing_reference"]
    assert diagnosis["algorithm_audit_contract"] == (
        single_valve_workflow.ALGORITHM_AUDIT_CONTRACT
    )
    assert diagnosis["algorithm_audit_status"] == "available"
    audit = diagnosis["algorithm_audit_snapshot"]
    assert audit["status"] == "available"
    assert audit["visual_rating"] == 1
    assert audit["algorithm_rating"] in range(1, 6)
    assert len(audit["input_digest"]) == 64


def test_integrated_model_call_rejects_luna_or_non_high_worker(
    tmp_path: Path,
) -> None:
    project, analysis, _ = _synthetic_project(tmp_path)
    common = load_offline_experience()
    run_root = project / "work/formal_diagnosis/runs/v2"
    single_valve_workflow.prepare_project_runs(
        analysis,
        project_root=project,
        project_id="PROJECT-001",
        experience_text=common,
        run_root=run_root,
        diagnosis_version="v2",
        parent_run="none",
        revision_reason="Integrated model diagnosis.",
    )
    run_dir = run_root / "VALVE-001"
    single_valve_workflow.prepare_integrated_model_request(run_dir)

    result = _complete_integrated_result(run_dir)
    result["model_provenance"]["model"] = "gpt-5.6-luna"
    with pytest.raises(ValueError, match="requires model gpt-6-sol"):
        single_valve_workflow.submit_integrated_model_result(
            run_dir, result
        )
    result = _complete_integrated_result(run_dir)
    result["model_provenance"]["reasoning_effort"] = "medium"
    with pytest.raises(ValueError, match="requires high reasoning effort"):
        single_valve_workflow.submit_integrated_model_result(
            run_dir, result
        )


def test_new_model_contract_and_legacy_manifest_fallback() -> None:
    assert single_valve_workflow._run_model_contract({}) == ("gpt-5.6-sol", "medium")
    with pytest.raises(ValueError, match="gpt-6-sol/high"):
        single_valve_workflow._require_new_model_contract("gpt-5.6-sol", "medium")
    assert single_valve_workflow._run_model_contract({
        "worker_model": "gpt-6-sol",
        "worker_reasoning_effort": "high",
    }) == ("gpt-6-sol", "high")


def test_integrated_request_writes_source_bound_read_allowlist(tmp_path: Path) -> None:
    project, analysis, _ = _synthetic_project(tmp_path)
    (project / "project.toml").write_text("[project]\ncustomer='Demo'\n", encoding="utf-8")
    run_root = project / "work/formal_diagnosis/runs/current"
    single_valve_workflow.prepare_project_runs(
        analysis, project_root=project, project_id="PROJECT-001",
        experience_text=load_offline_experience(), run_root=run_root,
        diagnosis_version="current", parent_run="none", revision_reason="New run.",
        report_period="batch_a",
    )
    run_dir = run_root / "VALVE-001"
    prepared = single_valve_workflow.prepare_integrated_model_request(run_dir)
    allowlist_path = run_dir / "allowed_reads.json"
    assert prepared["read_allowlist"] == str(allowlist_path)
    allowlist = _read_json(allowlist_path)
    allowed = {item["path"] for item in allowlist["files"]}
    assert str((project / "raw/VALVE-001/source.devdata").resolve()) in allowed
    assert str((project / "project.toml").resolve()) not in allowed
    request = _read_json(run_dir / "03_integrated_diagnosis_request.json")
    assert request["model_contract"] == {"model": "gpt-6-sol", "reasoning_effort": "high"}
    assert request["report_period_key"] == "batch_a"
    bundle = _read_json(run_dir / "01_evidence_bundle.json")
    assert "confirmed_mild_defect_required" not in bundle["rating_standard"]["2"]
    assert "单项缺口只限制相关判断" in bundle["rating_standard"]["5"]["boundary"]


def test_diagnosis_review_rejects_tampered_algorithm_audit(
    tmp_path: Path,
) -> None:
    project, analysis, _ = _synthetic_project(tmp_path)
    common = load_offline_experience()
    run_root = project / "work/formal_diagnosis/runs/v2"
    single_valve_workflow.prepare_project_runs(
        analysis,
        project_root=project,
        project_id="PROJECT-001",
        experience_text=common,
        run_root=run_root,
        diagnosis_version="v2",
        parent_run="none",
        revision_reason="Integrated model diagnosis.",
    )
    run_dir = run_root / "VALVE-001"
    _submit_complete_integrated(run_dir)
    draft_path = run_dir / single_valve_workflow.DIAGNOSIS_DRAFT_FILENAME
    draft = _read_json(draft_path)
    draft["algorithm_audit_snapshot"]["algorithm_rating"] = 4
    _write_json(draft_path, draft)

    with pytest.raises(ValueError, match="algorithm audit snapshot"):
        single_valve_workflow.prepare_diagnosis_review(run_root)


@pytest.mark.parametrize("field", ["issues", "actions"])
@pytest.mark.parametrize("invalid_item", [123, {}, None, "   "])
def test_diagnosis_rejects_non_text_summary_list_items(
    tmp_path: Path, field: str, invalid_item: object
) -> None:
    project, analysis, _ = _synthetic_project(tmp_path)
    common = load_offline_experience()
    run_root = project / "work/formal_diagnosis/runs/v2"
    single_valve_workflow.prepare_project_runs(
        analysis,
        project_root=project,
        project_id="PROJECT-001",
        experience_text=common,
        run_root=run_root,
        diagnosis_version="v2",
        parent_run="none",
        revision_reason="Summary list validation.",
    )
    run_dir = run_root / "VALVE-001"
    _submit_complete_integrated(run_dir)
    diagnosis = _complete_diagnosis(run_dir)
    diagnosis["whole_valve"][field] = [invalid_item]

    with pytest.raises(ValueError, match=rf"{field} must be .*text list"):
        single_valve_workflow._validate_diagnosis(run_dir, diagnosis)


def test_integrated_model_call_rejects_template_counterevidence_atomically(
    tmp_path: Path,
) -> None:
    project, analysis, _ = _synthetic_project(tmp_path)
    common = load_offline_experience()
    run_root = project / "work/formal_diagnosis/runs/v2"
    single_valve_workflow.prepare_project_runs(
        analysis,
        project_root=project,
        project_id="PROJECT-001",
        experience_text=common,
        run_root=run_root,
        diagnosis_version="v2",
        parent_run="none",
        revision_reason="Integrated model diagnosis.",
    )
    run_dir = run_root / "VALVE-001"
    single_valve_workflow.prepare_integrated_model_request(run_dir)
    result = _complete_integrated_result(run_dir)
    result["whole_valve"]["counter_evidence"] = [
        "未发现可推翻当前评级的明确反证据。"
    ]

    with pytest.raises(ValueError, match="rejects generic counterevidence"):
        single_valve_workflow.submit_integrated_model_result(
            run_dir, result
        )

    assert not (run_dir / "03_observations.json").exists()
    assert not (
        run_dir / single_valve_workflow.DIAGNOSIS_DRAFT_FILENAME
    ).exists()


@pytest.mark.parametrize("limited_judgment", [
    "Average Error 64.59%和T86/T63缺失受此影响，不能作为严重失效依据。",
    "T86缺少适用阈值，是否偏慢待复核；其余完整曲线支持主要功能仍可完成。",
])
def test_integrated_model_call_preserves_partial_evidence_diagnosis(
    tmp_path: Path, limited_judgment: str,
) -> None:
    project, analysis, _ = _synthetic_project(tmp_path)
    common = load_offline_experience()
    run_root = project / "work/formal_diagnosis/runs/v2"
    single_valve_workflow.prepare_project_runs(
        analysis,
        project_root=project,
        project_id="PROJECT-001",
        experience_text=common,
        run_root=run_root,
        diagnosis_version="v2",
        parent_run="none",
        revision_reason="Integrated model diagnosis.",
    )
    run_dir = run_root / "VALVE-001"
    single_valve_workflow.prepare_integrated_model_request(run_dir)
    result = _complete_integrated_result(run_dir)
    result["whole_valve"].update(
        {
            "final_rating": 3,
            "rating_conclusion": limited_judgment,
            "main_issue": "局部调节偏差；主要行程功能仍可完成。",
            "issues": [limited_judgment],
            "uncertainties": [limited_judgment],
            "metric_threshold_basis": [],
        }
    )

    manifest = single_valve_workflow.submit_integrated_model_result(run_dir, result)
    saved = json.loads((run_dir / single_valve_workflow.DIAGNOSIS_DRAFT_FILENAME).read_text())
    assert manifest["stage"] == "awaiting_diagnosis"
    assert saved["whole_valve"] == result["whole_valve"]
    assert saved["whole_valve"]["final_rating"] == 3
    prompt = (run_dir / single_valve_workflow.INTEGRATED_PROMPT_FILENAME).read_text()
    assert "局部缺测或阈值依据缺失不阻断其他有效诊断" in prompt


def test_integrated_signature_accepts_equivalent_wording_without_rewriting():
    observation = {"test_key": "extended_signature", "image_sha256": "anonymous-image",
        "observation": "两端均有近水平区段，低压力端阀位变化较陡，局部出现折返。",
        "visual_checks": {"local_jaggedness_or_discontinuity": "present", "endpoint_folding_or_jump": "present"}}
    diagnosis = {"model_provenance": {"decision_source": "codex_exec_integrated"},
        "curve_interpretations": [{"test_key": "extended_signature", "image_sha256": "anonymous-image",
                                   "diagnosis": "主体支路连续，两端附近仍可达到；需结合快速行程核实局部变化。"}]}
    original = json.dumps(diagnosis, ensure_ascii=False)
    single_valve_workflow._validate_diagnostic_guardrails(
        {"evidence": {}}, {"curve_observations": [observation]}, diagnosis)
    assert json.dumps(diagnosis, ensure_ascii=False) == original


def test_level_five_rejects_performance_only_gap_when_step_and_ramp_succeeded(
    tmp_path: Path,
) -> None:
    project, analysis, _ = _synthetic_project(tmp_path)
    analysis[0]["positioner_report_info"]["status"]["task_execution"] = [
        {"task_name": "Run Step Test", "result": "Succeeded"},
        {"task_name": "Run Ramp Test", "result": "Succeeded"},
        {
            "task_name": "Extended Actuator Signature",
            "result": "Failed",
        },
        {"task_name": "Change Mode", "result": "Failed"},
    ]
    _refresh_structured_evidence(project, analysis[0])
    run_root = project / "work/formal_diagnosis/runs/v1"
    common = load_offline_experience()
    single_valve_workflow.prepare_project_runs(
        analysis,
        project_root=project,
        project_id="PROJECT-001",
        experience_text=common,
        run_root=run_root,
        diagnosis_version="v1",
        parent_run="none",
        revision_reason="Performance-only data gap boundary test.",
    )
    run_dir = run_root / "VALVE-001"
    _submit_complete_integrated(run_dir)
    diagnosis = _complete_diagnosis(run_dir)
    diagnosis["whole_valve"].update(
        {
            "information_complete": False,
            "final_rating": 5,
            "rating_conclusion": (
                "执行机构性能数据缺失，当前影响未知，评为5级。"
            ),
            "main_issue": "执行机构性能测试未取得数据。",
            "uncertainties": ["执行机构性能数据缺失。"],
        }
    )

    with pytest.raises(
        ValueError,
        match="missing actuator performance data alone cannot receive level five",
    ):
        single_valve_workflow._validate_diagnosis(run_dir, diagnosis)


def test_level_five_boundary_remains_when_step_or_ramp_is_not_successful() -> None:
    bundle = {
        "evidence": {
            "structured_evidence": {
                "source_record": {
                    "basic_action_failed": False,
                    "positioner_report_info": {
                        "status": {
                            "task_execution": [
                                {
                                    "task_name": "Run Step Test",
                                    "result": "Aborted",
                                },
                                {
                                    "task_name": "Run Ramp Test",
                                    "result": "Incomplete",
                                },
                                {
                                    "task_name": "Extended Actuator Signature",
                                    "result": "Failed",
                                },
                            ]
                        }
                    },
                }
            }
        }
    }

    assert (
        single_valve_workflow._step_and_ramp_evidence_precludes_level_five(
            bundle
        )
        is False
    )


def _approve_complete_project_diagnosis(
    run_root: Path,
    *,
    approval_text: str = "同意第三阶段诊断。",
) -> dict:
    manifest = _read_json(run_root / "00_project_run_manifest.json")
    for target in manifest["targets"]:
        run_dir = run_root / target["valve_tag"]
        _write_json(
            run_dir / single_valve_workflow.DIAGNOSIS_DRAFT_FILENAME,
            _complete_diagnosis(run_dir),
        )
    single_valve_workflow.prepare_diagnosis_review(run_root)
    return single_valve_workflow.approve_project_diagnosis(
        run_root,
        approval_text,
    )


def test_resolve_project_analysis_summary_requires_current_or_explicit(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    current = project / "work/analysis_summary.json"
    legacy = project / "work/shared_engine/analysis_summary.json"
    explicit = project / "work/replay-inputs/v1/analysis_summary.json"

    _write_json(legacy, [])
    (project / "project.toml").write_text('[project]\ncustomer="Demo"\nreport_title="Report"\nreport_date="2026-09-16"\n[paths]\noutput_docx="report.docx"')
    with pytest.raises(ValueError, match="current formal evidence snapshot is missing"):
        single_valve_workflow.resolve_project_analysis_summary(project)
    _write_json(current, [])
    assert single_valve_workflow.resolve_project_analysis_summary(project) == current
    _write_json(explicit, [])
    with pytest.raises(ValueError, match="source binding is invalid"):
        single_valve_workflow.resolve_project_analysis_summary(project, explicit)
    _write_json(explicit.with_name("analysis_summary.source.json"), {
        "schema_version": 1,
        "purpose": "formal_evidence_preparation",
        "project_root": str(project.resolve()),
        "report_period_key": "",
        "summary_sha256": hashlib.sha256(explicit.read_bytes()).hexdigest(),
    })
    assert single_valve_workflow.resolve_project_analysis_summary(
        project,
        explicit,
    ) == explicit
    explicit.write_text('[{"changed": true}]\n', encoding="utf-8")
    with pytest.raises(ValueError, match="source binding is invalid"):
        single_valve_workflow.resolve_project_analysis_summary(project, explicit)
    outside = tmp_path / "analysis_summary.json"
    _write_json(outside, [])
    with pytest.raises(ValueError, match="within project work"):
        single_valve_workflow.resolve_project_analysis_summary(project, outside)


def test_prepare_project_cli_uses_current_analysis_summary_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    current = project / "work/analysis_summary.json"
    legacy = project / "work/shared_engine/analysis_summary.json"
    _write_json(
        current,
        [{"folder": "CURRENT-VALVE", "included_in_report": True}],
    )
    _write_json(
        legacy,
        [{"folder": "LEGACY-VALVE", "included_in_report": True}],
    )
    (project / "project.toml").write_text('[project]\ncustomer="Demo"\nreport_title="Report"\nreport_date="2026-09-16"\n[paths]\noutput_docx="report.docx"')
    captured: dict[str, object] = {}
    material_gate: list[Path] = []
    parameter_gate: list[Path] = []

    def fake_prepare_project_runs(analysis: list[dict], **kwargs: object) -> dict:
        captured["analysis"] = analysis
        captured["kwargs"] = kwargs
        return {"stage": "in_progress"}

    monkeypatch.setattr(
        single_valve_workflow,
        "prepare_project_runs",
        fake_prepare_project_runs,
    )
    monkeypatch.setattr(
        single_valve_workflow,
        "require_project_materials_stage",
        lambda project_root: material_gate.append(project_root) or {},
    )
    monkeypatch.setattr(
        single_valve_workflow,
        "require_project_parameters_stage",
        lambda project_root: parameter_gate.append(project_root) or {},
    )

    result = single_valve_workflow.main(
        [
            "prepare-project",
            "--project-root",
            str(project),
            "--project-id",
            "PROJECT-001",
            "--run-root",
            str(project / "work/formal_diagnosis/runs/v1"),
            "--diagnosis-version",
            "v1",
            "--parent-run",
            "none",
            "--revision-reason",
            "CLI path resolution test.",
        ]
    )

    assert result == 0
    assert captured["kwargs"]["experience_text"] == load_offline_experience()
    assert "cases" not in captured["kwargs"]
    assert captured["analysis"] == [
        {"folder": "CURRENT-VALVE", "included_in_report": True}
    ]
    assert material_gate == [project]
    assert parameter_gate == [project]
    assert not (project / "work/formal_diagnosis/runs/v1").exists()


def test_prepare_project_cli_rejects_summary_without_structured_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, analysis, _ = _synthetic_project(tmp_path)
    (project / "project.toml").write_text('[project]\ncustomer="Demo"\nreport_title="Report"\nreport_date="2026-09-16"\n[paths]\noutput_docx="report.docx"')
    analysis[0].pop("structured_evidence")
    _write_json(project / "work/analysis_summary.json", analysis)
    monkeypatch.setattr(single_valve_workflow, "require_project_materials_stage", lambda _: {})
    monkeypatch.setattr(single_valve_workflow, "require_project_parameters_stage", lambda _: {})

    with pytest.raises(ValueError, match="run prepare-formal-evidence"):
        single_valve_workflow.main(
            [
                "prepare-project",
                "--project-root", str(project),
                "--project-id", "PROJECT-001",
                "--run-root", str(project / "work/formal_diagnosis/runs/v1"),
                "--diagnosis-version", "v1",
                "--parent-run", "none",
                "--revision-reason", "Missing evidence contract test.",
            ]
        )


def test_diagnosis_approval_rejects_draft_changed_after_review(
    tmp_path: Path,
) -> None:
    project, analysis, _ = _synthetic_project(tmp_path)
    common = load_offline_experience()
    run_root = project / "work/formal_diagnosis/runs/v1"
    single_valve_workflow.prepare_project_runs(
        analysis,
        project_root=project,
        project_id="PROJECT-001",
        experience_text=common,
        run_root=run_root,
        diagnosis_version="v1",
        parent_run="none",
        revision_reason="Initial anonymous diagnostic run.",
    )
    run_dir = run_root / "VALVE-001"
    _submit_complete_integrated(run_dir)
    draft_path = (
        run_dir / single_valve_workflow.DIAGNOSIS_DRAFT_FILENAME
    )
    draft = _complete_diagnosis(run_dir)
    _write_json(draft_path, draft)
    single_valve_workflow.prepare_diagnosis_review(run_root)

    draft["whole_valve"]["main_issue"] = "换向迟滞需要复测确认。"
    _write_json(draft_path, draft)

    with pytest.raises(
        ValueError,
        match="diagnosis draft changed after review report",
    ):
        single_valve_workflow.approve_project_diagnosis(
            run_root,
            "同意第三阶段诊断。",
        )


def test_frozen_project_run_is_consumed_directly_by_report_loader(
    tmp_path: Path,
) -> None:
    project, analysis, curve_path = _synthetic_project(tmp_path)
    common = load_offline_experience()
    run_root = project / "work/formal_diagnosis/runs/v1"

    project_manifest = single_valve_workflow.prepare_project_runs(
        analysis,
        project_root=project,
        project_id="PROJECT-001",
        experience_text=common,
        run_root=run_root,
        diagnosis_version="v1",
        parent_run="none",
        revision_reason="Initial anonymous diagnostic run.",
    )
    assert project_manifest["stage"] == "in_progress"
    run_dir = run_root / "VALVE-001"

    _submit_complete_integrated(run_dir)
    awaiting = _read_json(run_root / "00_project_run_manifest.json")
    assert awaiting["targets"][0]["stage"] == "awaiting_diagnosis"

    diagnosis = _complete_diagnosis(run_dir)
    with pytest.raises(
        ValueError,
        match="requires user approval before freeze",
    ):
        single_valve_workflow.submit_diagnosis(run_dir, diagnosis)
    _write_json(
        run_dir / single_valve_workflow.DIAGNOSIS_DRAFT_FILENAME,
        diagnosis,
    )
    review = single_valve_workflow.prepare_diagnosis_review(run_root)
    assert review["status"] == "awaiting_user_approval"
    assert review["target_count"] == 1
    review_markdown = (
        project / "work/stage-gates/diagnosis-review-report.md"
    ).read_text(encoding="utf-8")
    assert "## 逐阀结论" in review_markdown
    assert "VALVE-001" in review_markdown
    assert "curve_interpretations" not in review_markdown
    frozen_project = single_valve_workflow.approve_project_diagnosis(
        run_root,
        "同意第三阶段诊断。",
    )
    assert frozen_project["stage"] == "frozen"
    approval = _read_json(
        project / "work/stage-gates/diagnosis-approval.json"
    )
    assert approval["status"] == "approved_and_frozen"
    assert approval["approval_text"] == "同意第三阶段诊断。"
    frozen = _read_json(run_root / "00_project_run_manifest.json")
    assert frozen["stage"] == "frozen"
    assert frozen["frozen_target_count"] == 1
    assert frozen["targets"][0]["stage"] == "frozen"
    assert single_valve_workflow.get_status(
        run_dir,
        require_versioned=True,
    )["freeze_valid"] is True

    curve_manifest_path = project / "work/curve_review_manifest.json"
    frozen_image_key = _read_json(
        run_dir / "01_evidence_bundle.json"
    )["evidence"]["curve_images"][0]["image_key"]
    _write_json(
        curve_manifest_path,
        {
            "images": [
                {
                    "image_key": frozen_image_key,
                    "valve_tag": "VALVE-001",
                    "image_path": str(curve_path),
                    "test_key": "run_step",
                }
            ]
        },
    )
    loaded = load_formal_diagnosis(
        run_root,
        curve_manifest_path,
        required_valve_tags=("VALVE-001",),
        require_versioned=True,
    )
    assert loaded.summary_overrides["VALVE-001"]["rating"] == 2
    assert loaded.curve_interpretations[0].image_key == frozen_image_key

    observations_path = run_dir / "03_observations.json"
    observations_path.write_text("{}\n", encoding="utf-8")
    with pytest.raises(FormalDiagnosisError, match="freeze hash mismatch"):
        load_formal_diagnosis(
            run_root,
            curve_manifest_path,
            required_valve_tags=("VALVE-001",),
            require_versioned=True,
        )


def _submit_complete_integrated(run_dir):
    single_valve_workflow.prepare_integrated_model_request(run_dir)
    result = _complete_integrated_result(run_dir)
    single_valve_workflow.submit_integrated_model_result(run_dir, result)


@pytest.mark.parametrize("command", ["prepare", "submit-observations"])
def test_retired_split_commands_are_not_available(command, capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        single_valve_workflow.main([command])
    assert exc.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_complete_prose_is_snapshotted_without_filtering_or_version_selection(tmp_path: Path, monkeypatch) -> None:
    project, analysis, _ = _synthetic_project(tmp_path)
    # Hundreds of topics, including material unrelated to the present test family.
    common = load_offline_experience() + "\n" + "\n".join(
        f"### 通用经验 {i}\n现象：低温。判断：核对实际工况。适用：有原始证据。建议：按规格复核。"
        for i in range(500)
    ) + "\n全文末尾也必须进入模型上下文。\n"
    live = tmp_path / "common.md"
    live.write_text(common, encoding="utf-8")
    _write_json(project / "experience/project_experience.json", {"rules": [{"id": "must-not-load"}]})
    root = project / "work/formal_diagnosis/runs/prose"
    single_valve_workflow.prepare_project_runs(analysis, project_root=project, project_id="PROJECT-001",
        experience_text=load_offline_experience(live), run_root=root,
        diagnosis_version="prose", parent_run="none", revision_reason="Full prose input.")
    run = root / "VALVE-001"
    live.write_text("# ValVue3 离线诊断通用经验\n后续修改。\n", encoding="utf-8")
    prepared = single_valve_workflow.prepare_integrated_model_request(run)
    assert _read_json(Path(prepared["request"]))["experience_text"] == common
    assert Path(prepared["prompt"]).read_text(encoding="utf-8").endswith(common)
    assert (run / "00_experience.md").read_text(encoding="utf-8") == common
    # Exercise the actual metered transport; replace only the external model process.
    (project / "project.toml").write_text("[project]\n", encoding="utf-8")
    import run_metered_codex
    import subprocess
    captured = {}

    def capture_model(command, **kwargs):
        captured["command"] = command
        captured["stdin"] = kwargs["input"]
        return subprocess.CompletedProcess(command, 0, "tokens used\n123", "")

    monkeypatch.setattr(run_metered_codex.shutil, "which", lambda _: "/test/codex")
    with monkeypatch.context() as transport:
        transport.setattr(run_metered_codex.subprocess, "run", capture_model)
        assert run_metered_codex.main([
            "run", "--project-root", str(project), "--stage", "test_transport",
            "--call-id", "full-prose.test", "--prompt-file", prepared["prompt"],
            "--output-schema", prepared["schema"],
            *[arg for image in prepared["image_paths"] for arg in ("--image", image)],
        ]) == 0
    assert captured["stdin"] == Path(prepared["prompt"]).read_text(encoding="utf-8")
    assert captured["stdin"].endswith(common)
    assert captured["stdin"].count(common) == 1
    assert prepared["request"] not in captured["stdin"]
    request = _read_json(Path(prepared["request"]))
    inline_evidence = json.dumps(
        {key: value for key, value in request.items() if key != "experience_text"},
        ensure_ascii=False, separators=(",", ":"),
    )
    assert captured["stdin"].count(inline_evidence) == 1
    assert captured["command"].count("-i") == len(prepared["image_paths"])
    for image in prepared["image_paths"]:
        assert image in captured["command"]
    assert not (run / "02_retrieved_cases.json").exists()
    assert not (run / "04_retrieved_rules.json").exists()
    result = _complete_integrated_result(run)
    single_valve_workflow.submit_integrated_model_result(run, result)
    _approve_complete_project_diagnosis(root)
    assert single_valve_workflow._verify_freeze(run)
    assert (run / "00_experience.md").read_text(encoding="utf-8") == common
    (run / "00_experience.md").write_text(common + "篡改", encoding="utf-8")
    assert not single_valve_workflow._verify_freeze(run)
    with pytest.raises(ValueError, match="snapshot changed"):
        single_valve_workflow._run_experience(run)


def test_changed_experience_requires_fresh_diagnosis_and_preserves_parent(tmp_path: Path) -> None:
    project, analysis, _ = _synthetic_project(tmp_path)
    common = load_offline_experience()
    roots = [project / "work/formal_diagnosis/runs" / version for version in ("parent", "child")]
    for root, text in zip(roots, (common, common + "\n补充：核对当前检修后的独立复测条件。\n")):
        single_valve_workflow.prepare_project_runs(analysis, project_root=project, project_id="PROJECT-001",
            experience_text=text, run_root=root, diagnosis_version=root.name,
            parent_run="none" if root == roots[0] else str(roots[0]), revision_reason="Experience update test.")
        _submit_complete_integrated(root / "VALVE-001")
        if root == roots[0]:
            _approve_complete_project_diagnosis(root)
    parent, child = [root / "VALVE-001" for root in roots]
    frozen_before = {name: (parent / name).read_bytes() for name in ("00_experience.md", "05_diagnosis.json", "06_freeze.sha256")}
    with pytest.raises(ValueError, match="common experience changed"):
        single_valve_workflow.inherit_frozen_diagnosis(parent, child)
    assert all((parent / name).read_bytes() == content for name, content in frozen_before.items())
    assert single_valve_workflow._verify_freeze(parent)


def _freeze_recovery_project(tmp_path: Path, *, inherited: bool = False) -> Path:
    project, analysis, _ = _synthetic_project(tmp_path)
    shutil.copytree(project / "raw/VALVE-001", project / "raw/VALVE-002")
    extraction_path = project / "work/evidence-extraction/run-manifest.json"
    extraction = _read_json(extraction_path)
    extraction["inputs"] += [
        dict(item, path=item["path"].replace("VALVE-001", "VALVE-002"))
        for item in extraction["inputs"]
    ]
    _write_json(extraction_path, extraction)
    evidence = _read_json(project / "work/evidence-extraction/valves/VALVE-001.json")
    evidence["valve_tag"] = "VALVE-002"
    _write_json(project / "work/evidence-extraction/valves/VALVE-002.json", evidence)
    second = copy.deepcopy(analysis[0])
    second["folder"] = "VALVE-002"
    analysis.append(second)
    for source in analysis:
        _refresh_structured_evidence(project, source)
    run_root = project / "work/formal_diagnosis/runs/v1"
    single_valve_workflow.prepare_project_runs(
        analysis, project_root=project, project_id="PROJECT-001",
        experience_text=load_offline_experience(), run_root=run_root,
        diagnosis_version="v1", parent_run="none", revision_reason="Anonymous recovery check",
    )
    for source in analysis:
        _submit_complete_integrated(run_root / source["folder"])
    single_valve_workflow.prepare_diagnosis_review(run_root)
    if inherited:
        parent_root = run_root
        single_valve_workflow.approve_project_diagnosis(parent_root, "同意匿名父版诊断。")
        run_root = parent_root.parent / "v2"
        single_valve_workflow.prepare_project_runs(
            analysis, project_root=project, project_id="PROJECT-001",
            experience_text=load_offline_experience(), run_root=run_root,
            diagnosis_version="v2", parent_run=str(parent_root),
            revision_reason="Unchanged anonymous evidence inheritance",
        )
        for source in analysis:
            child = run_root / source["folder"]
            _submit_complete_integrated(child)
            diagnosis = single_valve_workflow.inherit_frozen_diagnosis(
                parent_root / source["folder"], child,
            )
            _write_json(child / single_valve_workflow.DIAGNOSIS_DRAFT_FILENAME, diagnosis)
        single_valve_workflow.prepare_diagnosis_review(run_root)
    return run_root


@pytest.mark.parametrize("inherited", [False, True])
@pytest.mark.parametrize("boundary", ["second_valve", "after_05", "after_06", "after_manifest"])
def test_project_freeze_resumes_same_approval_after_interruption(tmp_path, monkeypatch, boundary, inherited):
    run_root = _freeze_recovery_project(tmp_path, inherited=inherited)
    workflow = single_valve_workflow
    original_submit = workflow.submit_diagnosis
    original_write = workflow._write_json
    original_open = Path.open

    def interrupted_submit(run_dir, diagnosis):
        if boundary == "second_valve" and run_dir.name == "VALVE-002":
            raise OSError("injected interruption")
        return original_submit(run_dir, diagnosis)

    def interrupted_write(path, payload):
        if (boundary == "after_06" and path.name == "00_run_manifest.json"
                and payload.get("stage") == "frozen"):
            raise OSError("injected interruption")
        if (boundary == "after_manifest" and path.name == "00_project_run_manifest.json"
                and payload.get("frozen_target_count")):
            raise OSError("injected interruption")
        return original_write(path, payload)

    def interrupted_open(path, mode="r", *args, **kwargs):
        if boundary == "after_05" and path.name == "06_freeze.sha256" and mode == "x":
            raise OSError("injected interruption")
        return original_open(path, mode, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(workflow, "submit_diagnosis", interrupted_submit)
        patch.setattr(workflow, "_write_json", interrupted_write)
        patch.setattr(Path, "open", interrupted_open)
        with pytest.raises(OSError, match="injected interruption"):
            workflow.approve_project_diagnosis(run_root, "同意匿名测试诊断。")
    approval_path = workflow._diagnosis_stage_gate_paths(run_root)["approval"]
    approval = _read_json(approval_path)
    assert approval["status"] == "approved_freeze_incomplete"
    protected = {
        path: (path.read_bytes(), path.stat().st_mtime_ns)
        for name in ("05_diagnosis.json", "06_freeze.sha256")
        for path in run_root.glob(f"*/{name}")
    }
    if boundary == "second_valve":
        pending = run_root / "VALVE-002"
        workflow.submit_diagnosis(pending, _read_json(pending / workflow.DIAGNOSIS_DRAFT_FILENAME))
    result = workflow.approve_project_diagnosis(run_root)
    assert result["stage"] == "frozen"
    assert result["frozen_target_count"] == 2
    resumed_approval = _read_json(approval_path)
    assert resumed_approval["approved_at"] == approval["approved_at"]
    assert resumed_approval["approval_text"] == approval["approval_text"]
    assert resumed_approval["status"] == "approved_and_frozen"
    for path, expected in protected.items():
        assert (path.read_bytes(), path.stat().st_mtime_ns) == expected
    for tag in ("VALVE-001", "VALVE-002"):
        assert workflow.get_status(run_root / tag, require_versioned=True)["freeze_valid"]
    assert workflow.approve_project_diagnosis(run_root) == result
    if inherited:
        child = run_root / "VALVE-001"
        manifest_path = child / "00_run_manifest.json"
        before = (manifest_path.read_bytes(), manifest_path.stat().st_mtime_ns)
        with pytest.raises(ValueError, match="child run is not awaiting diagnosis"):
            workflow.inherit_frozen_diagnosis(run_root.parent / "v1/VALVE-001", child)
        assert (manifest_path.read_bytes(), manifest_path.stat().st_mtime_ns) == before
        freeze_path = child / "06_freeze.sha256"
        freeze_path.write_text(freeze_path.read_text() + "corrupt\n")
        with pytest.raises(ValueError, match="child diagnosis must be a valid frozen run"):
            workflow.approve_project_diagnosis(run_root)


@pytest.mark.parametrize("changed_file", [
    "05_diagnosis.json", "06_freeze.sha256", "05_diagnosis_draft.json",
    "03_observations.json", "00_experience.md",
])
def test_freeze_recovery_rejects_changed_evidence(tmp_path, monkeypatch, changed_file):
    run_root = _freeze_recovery_project(tmp_path)
    workflow = single_valve_workflow
    original_submit = workflow.submit_diagnosis

    def interrupted(run_dir, diagnosis):
        if run_dir.name == "VALVE-002":
            raise OSError("injected interruption")
        return original_submit(run_dir, diagnosis)

    with monkeypatch.context() as patch:
        patch.setattr(workflow, "submit_diagnosis", interrupted)
        with pytest.raises(OSError):
            workflow.approve_project_diagnosis(run_root, "同意匿名测试诊断。")
    path = run_root / "VALVE-001" / changed_file
    if path.suffix == ".json":
        payload = _read_json(path)
        payload["unexpected_change"] = True
        _write_json(path, payload)
    else:
        path.write_text(path.read_text() + "changed\n")
    changed_bytes = path.read_bytes()
    with pytest.raises(ValueError):
        workflow.approve_project_diagnosis(run_root)
    assert path.read_bytes() == changed_bytes
    assert not (run_root / "VALVE-002/05_diagnosis.json").exists()
