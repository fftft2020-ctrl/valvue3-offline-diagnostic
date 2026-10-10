import hashlib
import json
from pathlib import Path
import shutil

import pytest

from offline_engine import generator
from offline_engine.formal_diagnosis import (
    FormalDiagnosisError,
    _validate_evidence_accounting,
    _validate_evidence_pages,
    _diagnostic_evidence_sha256,
    load_formal_diagnosis,
)
from offline_engine.evidence_extraction import structured_evidence_accounting
from offline_engine.visual_review import CurveInterpretation


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _semantic_evidence_sha256(payload: dict) -> str:
    evidence = json.loads(json.dumps(payload["evidence"]))
    for field in ("curve_images", "specification_images"):
        for image in evidence.get(field) or []:
            image.pop("image_path", None)
    serialized = (
        json.dumps(evidence, ensure_ascii=False, sort_keys=True) + "\n"
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def _build_frozen_run(
    tmp_path: Path,
    *,
    versioned: bool = False,
    audit_capable: bool = False,
) -> tuple[Path, Path]:
    image_path = tmp_path / "report_images/FV-1/run_step.png"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"curve-image")
    image_sha256 = _sha256(image_path)

    curve_manifest_path = tmp_path / "curve_review_manifest.json"
    _write_json(
        curve_manifest_path,
        {
            "images": [
                {
                    "image_key": "FV-1:run_step:stable",
                    "valve_tag": "FV-1",
                    "image_path": str(image_path),
                    "test_key": "run_step",
                }
            ]
        },
    )

    run_root = tmp_path / "formal-run"
    valve_run = run_root / "FV-1"
    valve_run.mkdir(parents=True)
    _write_json(
        run_root / "00_project_run_manifest.json",
        {
            "status": "completed",
            "targets": [
                {
                    "valve_tag": "FV-1",
                    "stage": "frozen",
                    "run_dir": str(valve_run),
                }
            ],
        },
    )
    _write_json(
        valve_run / "00_run_manifest.json",
        {
            "stage": "frozen",
            "target_valve_tag": "FV-1",
        },
    )
    evidence_payload = {
        "target_valve_tag": "FV-1",
        "evidence": {
            "curve_images": [
                {
                    "image_path": str(image_path),
                    "image_sha256": image_sha256,
                    "test_key": "run_step",
                }
            ]
        },
    }
    if versioned:
        evidence_payload["evidence"]["structured_evidence"] = {
            "schema_version": 1,
            "source_record": {
                "folder": "FV-1",
                "status": {
                    "historical_faults": [],
                    "task_execution": [],
                },
            },
            "source_files": [],
            "evidence_pages": [],
            "material_extraction": {"schema_version": 1},
        }
        if audit_capable:
            evidence_payload["evidence"]["structured_evidence"][
                "source_record"
            ].update(
                {
                    "has_test_data": True,
                    "metrics": {},
                    "active_faults": [],
                    "basic_action_failed": False,
                }
            )
    _write_json(
        valve_run / "01_evidence_bundle.json",
        evidence_payload,
    )
    _write_json(valve_run / "02_retrieved_cases.json", [])
    _write_json(
        valve_run / "03_observations.json",
        {
            "curve_observations": [
                {
                    "image_sha256": image_sha256,
                    "test_key": "run_step",
                    "observation": "全行程开向约4秒缓慢爬升后突跳。",
                }
            ]
        },
    )
    _write_json(valve_run / "04_retrieved_rules.json", [])
    diagnosis = {
            "curve_interpretations": [
                {
                    "image_sha256": image_sha256,
                    "test_key": "run_step",
                    "diagnosis": "确认存在明显开向启动迟滞。",
                    "rating": 3,
                    "action": "检查定位器输出和阀杆摩擦。",
                    "confidence": "high",
                    "experience_rule_ids": [],
                }
            ],
            "whole_valve": {
                "information_complete": True,
                "final_rating": 3,
                "rating_conclusion": "阀门动作存在明显迟滞，评为3级。",
                "main_issue": "全行程开向响应严重滞后。",
                "issues": ["全行程开向约4秒缓慢爬升后突跳。"],
                "actions": ["检查定位器输出和阀杆摩擦。"],
                "supporting_evidence": [],
                "counter_evidence": [],
                "uncertainties": [],
                "retrieved_case_ids": [],
                "experience_rule_ids": [],
                "severe_functional_impact": "",
            },
        }
    if versioned:
        diagnosis["diagnosis_basis"] = {
            "mode": "fresh_reanalysis",
            "evidence_semantic_sha256": _semantic_evidence_sha256(
                evidence_payload
            ),
            "evidence_file_sha256": _sha256(
                valve_run / "01_evidence_bundle.json"
            ),
            "retrieved_cases_sha256": _sha256(
                valve_run / "02_retrieved_cases.json"
            ),
            "observations_sha256": _sha256(
                valve_run / "03_observations.json"
            ),
            "retrieved_rules_sha256": _sha256(
                valve_run / "04_retrieved_rules.json"
            ),
        }
    _write_json(valve_run / "05_diagnosis.json", diagnosis)
    frozen_names = [
        "01_evidence_bundle.json",
        "02_retrieved_cases.json",
        "03_observations.json",
        "04_retrieved_rules.json",
        "05_diagnosis.json",
    ]
    if versioned:
        _write_json(
            valve_run / "00_version.json",
            {
                "diagnosis_version": "v2",
                "parent_run": "legacy-v1",
                "revision_reason": "人工校准。",
            },
        )
        frozen_names.insert(0, "00_version.json")
    freeze_lines = [
        f"{_sha256(valve_run / name)}  {name}"
        for name in frozen_names
    ]
    (valve_run / "06_freeze.sha256").write_text(
        "\n".join(freeze_lines) + "\n",
        encoding="utf-8",
    )
    return run_root, curve_manifest_path


def _bind_report_pages(
    run_root: Path,
    tmp_path: Path,
    pages: list[dict],
) -> Path:
    """Bind synthetic frozen pages and return a current analysis summary."""
    run_dir = run_root / "FV-1"
    evidence_path = run_dir / "01_evidence_bundle.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["evidence"]["structured_evidence"]["evidence_pages"] = pages
    _write_json(evidence_path, evidence)

    diagnosis_path = run_dir / "05_diagnosis.json"
    diagnosis = json.loads(diagnosis_path.read_text(encoding="utf-8"))
    basis = diagnosis["diagnosis_basis"]
    basis["evidence_semantic_sha256"] = _diagnostic_evidence_sha256(evidence)
    basis["evidence_file_sha256"] = _sha256(evidence_path)
    _write_json(diagnosis_path, diagnosis)
    freeze_path = run_dir / "06_freeze.sha256"
    frozen_names = (
        "00_version.json",
        "01_evidence_bundle.json",
        "02_retrieved_cases.json",
        "03_observations.json",
        "04_retrieved_rules.json",
        "05_diagnosis.json",
    )

    def refreeze() -> None:
        freeze_path.write_text(
            "\n".join(
                f"{_sha256(run_dir / name)}  {name}" for name in frozen_names
            )
            + "\n",
            encoding="utf-8",
        )

    refreeze()
    summary_path = tmp_path / "analysis_summary.json"
    _write_json(summary_path, [{"folder": "FV-1", "report_pages": pages}])
    return summary_path


def test_load_formal_diagnosis_maps_frozen_curves_and_whole_valve(tmp_path: Path) -> None:
    run_root, curve_manifest_path = _build_frozen_run(tmp_path)

    loaded = load_formal_diagnosis(
        run_root,
        curve_manifest_path,
        required_valve_tags=("FV-1",),
    )

    assert len(loaded.curve_interpretations) == 1
    curve = loaded.curve_interpretations[0]
    assert curve.image_key == "FV-1:run_step:stable"
    assert curve.observation == "全行程开向约4秒缓慢爬升后突跳。"
    assert curve.customer_text == (
        "全行程开向约4秒缓慢爬升后突跳。"
        "确认存在明显开向启动迟滞。"
        "建议检查定位器输出和阀杆摩擦。"
    )
    assert loaded.summary_overrides["FV-1"] == {
        "rating": 3,
        "rating_conclusion": "阀门动作存在明显迟滞，评为3级。",
        "main_issue": "全行程开向响应严重滞后。",
        "issues": ["全行程开向约4秒缓慢爬升后突跳。"],
        "action": "检查定位器输出和阀杆摩擦。",
        "actions": ["检查定位器输出和阀杆摩擦。"],
        "overall_assessment": "阀门动作存在明显迟滞，评为3级。 主要问题：全行程开向响应严重滞后。",
        "health_rating_detail_mode": "summary_only",
    }
    assert loaded.algorithm_audit_statuses == {"FV-1": "legacy_unavailable"}
    assert loaded.algorithm_audit_snapshots == {"FV-1": None}

def test_load_formal_diagnosis_reads_hash_protected_algorithm_audit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    run_root, curve_manifest_path = _build_frozen_run(
        tmp_path, versioned=True, audit_capable=True
    )
    run_dir = run_root / "FV-1"
    run_manifest_path = run_dir / "00_run_manifest.json"
    run_manifest = json.loads(run_manifest_path.read_text(encoding="utf-8"))
    run_manifest["algorithm_audit_contract"] = "deterministic_legacy_rating_v1"
    _write_json(run_manifest_path, run_manifest)
    diagnosis_path = run_dir / "05_diagnosis.json"
    diagnosis = json.loads(diagnosis_path.read_text(encoding="utf-8"))
    source_record = json.loads(
        (run_dir / "01_evidence_bundle.json").read_text(encoding="utf-8")
    )["evidence"]["structured_evidence"]["source_record"]
    interpretation = CurveInterpretation(
        image_key="FV-1:run_step:stable",
        test_key="run_step",
        observation="全行程开向约4秒缓慢爬升后突跳。",
        diagnosis="确认存在明显开向启动迟滞。",
        rating=3,
        action="检查定位器输出和阀杆摩擦。",
        customer_text=(
            "全行程开向约4秒缓慢爬升后突跳。"
            "确认存在明显开向启动迟滞。"
            "建议检查定位器输出和阀杆摩擦。"
        ),
        confidence="high",
        experience_rule_ids=(),
    )
    input_digest = generator.algorithm_audit_input_digest(
        source_record, (interpretation,)
    )
    diagnosis.update(
        {
            "algorithm_audit_contract": "deterministic_legacy_rating_v1",
            "algorithm_audit_status": "available",
                "algorithm_audit_snapshot": {
                    "schema_version": 1,
                    "status": "available",
                    "data_rating": 1,
                    "visual_rating": 3,
                    "algorithm_rating": 2,
                    "rating_inconsistency_reason": None,
                    "rating_evidence": {},
                    "input_digest": input_digest,
                },
        }
    )
    _write_json(diagnosis_path, diagnosis)
    freeze_path = run_dir / "06_freeze.sha256"
    frozen_names = (
        "00_version.json",
        "01_evidence_bundle.json",
        "02_retrieved_cases.json",
        "03_observations.json",
        "04_retrieved_rules.json",
        "05_diagnosis.json",
    )

    def refreeze() -> None:
        freeze_path.write_text(
            "\n".join(
                f"{_sha256(run_dir / name)}  {name}" for name in frozen_names
            )
            + "\n",
            encoding="utf-8",
        )

    freeze_path.write_text(
        "\n".join(
            f"{_sha256(run_dir / name)}  {name}"
            for name in (
                "00_version.json",
                "01_evidence_bundle.json",
                "02_retrieved_cases.json",
                "03_observations.json",
                "04_retrieved_rules.json",
                "05_diagnosis.json",
            )
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        generator,
        "calculate_algorithm_audit",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("report reader must not calculate ratings")
        ),
    )
    monkeypatch.setattr(
        generator,
        "calculate_data_rating",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("report reader must not calculate ratings")
        ),
    )
    monkeypatch.setattr(
        generator,
        "calculate_visual_rating",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("report reader must not calculate ratings")
        ),
    )
    monkeypatch.setattr(
        generator,
        "combine_ratings",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("report reader must not calculate ratings")
        ),
    )
    loaded = load_formal_diagnosis(
        run_root, curve_manifest_path, require_versioned=True
    )

    assert loaded.algorithm_audit_statuses == {"FV-1": "available"}
    assert loaded.algorithm_audit_snapshots["FV-1"]["algorithm_rating"] == 2

    run_manifest.pop("algorithm_audit_contract")
    _write_json(run_manifest_path, run_manifest)
    with pytest.raises(FormalDiagnosisError, match="contract is missing or invalid"):
        load_formal_diagnosis(
            run_root, curve_manifest_path, require_versioned=True
        )
    run_manifest["algorithm_audit_contract"] = "deterministic_legacy_rating_v1"
    _write_json(run_manifest_path, run_manifest)

    downgraded = {
        key: value
        for key, value in diagnosis.items()
        if key
        not in {
            "algorithm_audit_contract",
            "algorithm_audit_status",
            "algorithm_audit_snapshot",
        }
    }
    _write_json(diagnosis_path, downgraded)
    refreeze()
    with pytest.raises(FormalDiagnosisError, match="algorithm audit contract is missing"):
        load_formal_diagnosis(
            run_root, curve_manifest_path, require_versioned=True
        )

    diagnosis["algorithm_audit_status"] = "legacy_unavailable"
    diagnosis["algorithm_audit_snapshot"] = None
    _write_json(diagnosis_path, diagnosis)
    refreeze()
    with pytest.raises(FormalDiagnosisError, match="cannot downgrade"):
        load_formal_diagnosis(
            run_root, curve_manifest_path, require_versioned=True
        )

    diagnosis["algorithm_audit_status"] = "available"
    diagnosis["algorithm_audit_snapshot"] = {
        "schema_version": 1,
        "status": "available",
        "data_rating": 1,
        "visual_rating": 3,
        "algorithm_rating": 2,
        "rating_inconsistency_reason": None,
        "rating_evidence": {},
        "input_digest": "b" * 64,
    }
    _write_json(diagnosis_path, diagnosis)
    refreeze()
    with pytest.raises(FormalDiagnosisError, match="input_digest mismatch"):
        load_formal_diagnosis(
            run_root, curve_manifest_path, require_versioned=True
        )


def test_load_formal_diagnosis_requires_report_action_text(tmp_path: Path) -> None:
    run_root, curve_manifest_path = _build_frozen_run(tmp_path)
    run_dir = run_root / "FV-1"
    diagnosis_path = run_dir / "05_diagnosis.json"
    diagnosis = json.loads(diagnosis_path.read_text(encoding="utf-8"))
    diagnosis["whole_valve"]["actions"] = []
    _write_json(diagnosis_path, diagnosis)
    freeze_path = run_dir / "06_freeze.sha256"
    freeze_path.write_text(
        "\n".join(
            f"{_sha256(run_dir / name)}  {name}"
            for name in (
                "01_evidence_bundle.json",
                "02_retrieved_cases.json",
                "03_observations.json",
                "04_retrieved_rules.json",
                "05_diagnosis.json",
            )
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(FormalDiagnosisError, match="actions must be a text list"):
        load_formal_diagnosis(run_root, curve_manifest_path)


def test_load_formal_diagnosis_accepts_legacy_unavailable_inheritance(
    tmp_path: Path,
) -> None:
    parent_root, curve_manifest_path = _build_frozen_run(
        tmp_path, versioned=True
    )
    parent_dir = parent_root / "FV-1"
    child_root = tmp_path / "formal-child"
    child_dir = child_root / "FV-1"
    shutil.copytree(parent_dir, child_dir)
    _write_json(
        child_root / "00_project_run_manifest.json",
        {
            "targets": [
                {"valve_tag": "FV-1", "stage": "frozen", "run_dir": str(child_dir)}
            ]
        },
    )
    run_manifest_path = child_dir / "00_run_manifest.json"
    run_manifest = json.loads(run_manifest_path.read_text(encoding="utf-8"))
    run_manifest["algorithm_audit_contract"] = "deterministic_legacy_rating_v1"
    _write_json(run_manifest_path, run_manifest)
    diagnosis_path = child_dir / "05_diagnosis.json"
    diagnosis = json.loads(diagnosis_path.read_text(encoding="utf-8"))
    diagnosis["diagnosis_basis"].update(
        {
            "mode": "inherited_frozen",
            "parent_run_dir": str(parent_dir),
            "parent_diagnosis_sha256": _sha256(parent_dir / "05_diagnosis.json"),
            "parent_freeze_sha256": _sha256(parent_dir / "06_freeze.sha256"),
        }
    )
    diagnosis.update(
        {
            "algorithm_audit_contract": "deterministic_legacy_rating_v1",
            "algorithm_audit_status": "legacy_unavailable",
            "algorithm_audit_snapshot": None,
        }
    )
    _write_json(diagnosis_path, diagnosis)
    (child_dir / "06_freeze.sha256").write_text(
        "\n".join(
            f"{_sha256(child_dir / name)}  {name}"
            for name in (
                "00_version.json",
                "01_evidence_bundle.json",
                "02_retrieved_cases.json",
                "03_observations.json",
                "04_retrieved_rules.json",
                "05_diagnosis.json",
            )
        )
        + "\n",
        encoding="utf-8",
    )

    loaded = load_formal_diagnosis(
        child_root, curve_manifest_path, require_versioned=True
    )

    assert loaded.algorithm_audit_statuses == {"FV-1": "legacy_unavailable"}
    assert loaded.algorithm_audit_snapshots == {"FV-1": None}

    grandchild_root = tmp_path / "formal-grandchild"
    grandchild_dir = grandchild_root / "FV-1"
    shutil.copytree(child_dir, grandchild_dir)
    _write_json(
        grandchild_root / "00_project_run_manifest.json",
        {
            "targets": [
                {
                    "valve_tag": "FV-1",
                    "stage": "frozen",
                    "run_dir": str(grandchild_dir),
                }
            ]
        },
    )
    grandchild_diagnosis_path = grandchild_dir / "05_diagnosis.json"
    grandchild_diagnosis = json.loads(
        grandchild_diagnosis_path.read_text(encoding="utf-8")
    )
    grandchild_diagnosis["diagnosis_basis"].update(
        {
            "parent_run_dir": str(child_dir),
            "parent_diagnosis_sha256": _sha256(child_dir / "05_diagnosis.json"),
            "parent_freeze_sha256": _sha256(child_dir / "06_freeze.sha256"),
        }
    )
    _write_json(grandchild_diagnosis_path, grandchild_diagnosis)
    (grandchild_dir / "06_freeze.sha256").write_text(
        "\n".join(
            f"{_sha256(grandchild_dir / name)}  {name}"
            for name in (
                "00_version.json",
                "01_evidence_bundle.json",
                "02_retrieved_cases.json",
                "03_observations.json",
                "04_retrieved_rules.json",
                "05_diagnosis.json",
            )
        )
        + "\n",
        encoding="utf-8",
    )

    grandchild = load_formal_diagnosis(
        grandchild_root, curve_manifest_path, require_versioned=True
    )
    assert grandchild.algorithm_audit_statuses == {
        "FV-1": "legacy_unavailable"
    }


@pytest.mark.parametrize("field", ["issues", "actions"])
@pytest.mark.parametrize("invalid_item", [123, {}, None, "   "])
def test_load_formal_diagnosis_rejects_non_text_summary_list_items(
    tmp_path: Path, field: str, invalid_item: object
) -> None:
    run_root, curve_manifest_path = _build_frozen_run(tmp_path)
    run_dir = run_root / "FV-1"
    diagnosis_path = run_dir / "05_diagnosis.json"
    diagnosis = json.loads(diagnosis_path.read_text(encoding="utf-8"))
    diagnosis["whole_valve"][field] = [invalid_item]
    _write_json(diagnosis_path, diagnosis)
    freeze_path = run_dir / "06_freeze.sha256"
    freeze_path.write_text(
        "\n".join(
            f"{_sha256(run_dir / name)}  {name}"
            for name in (
                "01_evidence_bundle.json",
                "02_retrieved_cases.json",
                "03_observations.json",
                "04_retrieved_rules.json",
                "05_diagnosis.json",
            )
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(FormalDiagnosisError, match=rf"{field} must be a text list"):
        load_formal_diagnosis(run_root, curve_manifest_path)


def test_load_formal_diagnosis_accepts_hash_protected_version_metadata(
    tmp_path: Path,
) -> None:
    run_root, curve_manifest_path = _build_frozen_run(
        tmp_path,
        versioned=True,
    )

    loaded = load_formal_diagnosis(
        run_root,
        curve_manifest_path,
        required_valve_tags=("FV-1",),
    )

    assert len(loaded.curve_interpretations) == 1


def test_load_formal_diagnosis_rejects_versioned_freeze_downgrade(
    tmp_path: Path,
) -> None:
    run_root, curve_manifest_path = _build_frozen_run(
        tmp_path,
        versioned=True,
    )
    freeze_path = run_root / "FV-1/06_freeze.sha256"
    freeze_path.write_text(
        "\n".join(
            line
            for line in freeze_path.read_text(encoding="utf-8").splitlines()
            if not line.endswith("  00_version.json")
        )
        + "\n",
        encoding="utf-8",
    )
    (run_root / "FV-1/00_version.json").unlink()
    run_manifest_path = run_root / "FV-1/00_run_manifest.json"
    run_manifest = json.loads(
        run_manifest_path.read_text(encoding="utf-8")
    )
    for field in ("diagnosis_version", "parent_run", "revision_reason"):
        run_manifest.pop(field, None)
    _write_json(run_manifest_path, run_manifest)

    with pytest.raises(FormalDiagnosisError, match="incomplete"):
        load_formal_diagnosis(
            run_root,
            curve_manifest_path,
            required_valve_tags=("FV-1",),
            require_versioned=True,
        )


def test_load_formal_diagnosis_requires_bound_inputs_for_formal_run(
    tmp_path: Path,
) -> None:
    run_root, curve_manifest_path = _build_frozen_run(
        tmp_path,
        versioned=True,
    )
    diagnosis_path = run_root / "FV-1/05_diagnosis.json"
    diagnosis = json.loads(diagnosis_path.read_text(encoding="utf-8"))
    diagnosis.pop("diagnosis_basis")
    _write_json(diagnosis_path, diagnosis)
    freeze_path = run_root / "FV-1/06_freeze.sha256"
    freeze_path.write_text(
        "\n".join(
            (
                f"{_sha256(diagnosis_path)}  05_diagnosis.json"
                if line.endswith("  05_diagnosis.json")
                else line
            )
            for line in freeze_path.read_text(encoding="utf-8").splitlines()
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(FormalDiagnosisError, match="diagnosis_basis"):
        load_formal_diagnosis(
            run_root,
            curve_manifest_path,
            required_valve_tags=("FV-1",),
            require_versioned=True,
        )


def test_load_formal_diagnosis_requires_structured_evidence(
    tmp_path: Path,
) -> None:
    run_root, curve_manifest_path = _build_frozen_run(
        tmp_path,
        versioned=True,
    )
    run_dir = run_root / "FV-1"
    evidence_path = run_dir / "01_evidence_bundle.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["evidence"].pop("structured_evidence")
    _write_json(evidence_path, evidence)
    diagnosis_path = run_dir / "05_diagnosis.json"
    diagnosis = json.loads(diagnosis_path.read_text(encoding="utf-8"))
    diagnosis["diagnosis_basis"]["evidence_semantic_sha256"] = (
        _semantic_evidence_sha256(evidence)
    )
    diagnosis["diagnosis_basis"]["evidence_file_sha256"] = _sha256(
        evidence_path
    )
    _write_json(diagnosis_path, diagnosis)
    freeze_path = run_dir / "06_freeze.sha256"
    freeze_path.write_text(
        "\n".join(
            (
                f"{_sha256(run_dir / name)}  {name}"
                if name in {"01_evidence_bundle.json", "05_diagnosis.json"}
                else line
            )
            for line in freeze_path.read_text(encoding="utf-8").splitlines()
            for name in [line.partition("  ")[2]]
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(FormalDiagnosisError, match="requires structured_evidence"):
        load_formal_diagnosis(
            run_root,
            curve_manifest_path,
            required_valve_tags=("FV-1",),
            require_versioned=True,
        )


def test_fresh_contract_rejects_unaccounted_structured_evidence(
    tmp_path: Path,
) -> None:
    evidence = {
        "evidence": {
            "structured_evidence": {
                "source_record": {
                    "active_faults": ["Reset"],
                    "status": {
                        "historical_faults": [],
                        "task_execution": [],
                    },
                },
                "material_extraction": {"fields": {}},
            }
        }
    }
    diagnosis = {
        "diagnosis_basis": {
            "mode": "fresh_reanalysis",
            "evidence_contract_version": "2",
        }
    }
    with pytest.raises(FormalDiagnosisError, match="evidence_accounting"):
        _validate_evidence_accounting(
            tmp_path,
            evidence,
            {"evidence_accounting": {"active_faults": []}},
            diagnosis,
        )


def test_load_formal_diagnosis_rejects_tampered_frozen_file(tmp_path: Path) -> None:
    run_root, curve_manifest_path = _build_frozen_run(tmp_path)
    diagnosis_path = run_root / "FV-1/05_diagnosis.json"
    diagnosis_path.write_text("{}\n", encoding="utf-8")

    with pytest.raises(FormalDiagnosisError, match="freeze hash mismatch"):
        load_formal_diagnosis(
            run_root,
            curve_manifest_path,
            required_valve_tags=("FV-1",),
        )


def test_load_formal_diagnosis_rejects_missing_required_valve(tmp_path: Path) -> None:
    run_root, curve_manifest_path = _build_frozen_run(tmp_path)

    with pytest.raises(FormalDiagnosisError, match="missing required valve"):
        load_formal_diagnosis(
            run_root,
            curve_manifest_path,
            required_valve_tags=("FV-1", "FV-2"),
        )


def test_evidence_pages_allow_unrendered_accessory_metadata(tmp_path: Path) -> None:
    identity = f"{'a' * 64}:4"
    _validate_evidence_pages(
        tmp_path,
        {"evidence": {"specification_images": []}},
        {
            "evidence_pages": [
                {"physical_identity": identity, "roles": ["accessory"]}
            ]
        },
    )


def test_evidence_pages_reject_unrendered_technical_page(tmp_path: Path) -> None:
    identity = f"{'a' * 64}:2"
    with pytest.raises(FormalDiagnosisError, match="do not match report pages"):
        _validate_evidence_pages(
            tmp_path,
            {"evidence": {"specification_images": []}},
            {
                "evidence_pages": [
                    {
                        "physical_identity": identity,
                        "roles": ["specification"],
                    }
                ]
            },
        )


def test_versioned_formal_diagnosis_rejects_changed_current_report_page(
    tmp_path: Path,
) -> None:
    run_root, curve_manifest_path = _build_frozen_run(tmp_path, versioned=True)
    frozen_pages = [
        {"source_sha256": "a" * 64, "page": 2, "roles": ["specification"]}
    ]
    summary_path = _bind_report_pages(run_root, tmp_path, frozen_pages)
    current = json.loads(summary_path.read_text(encoding="utf-8"))
    current[0]["report_pages"][0]["source_sha256"] = "b" * 64
    _write_json(summary_path, current)

    with pytest.raises(FormalDiagnosisError, match="do not match frozen evidence_pages"):
        load_formal_diagnosis(
            run_root,
            curve_manifest_path,
            required_valve_tags=("FV-1",),
            require_versioned=True,
            analysis_summary_path=summary_path,
        )


def test_versioned_formal_diagnosis_accepts_same_current_report_pages(
    tmp_path: Path,
) -> None:
    run_root, curve_manifest_path = _build_frozen_run(tmp_path, versioned=True)
    pages = [
        {"source_sha256": "a" * 64, "page": 2, "roles": ["specification"]}
    ]
    summary_path = _bind_report_pages(run_root, tmp_path, pages)

    loaded = load_formal_diagnosis(
        run_root,
        curve_manifest_path,
        required_valve_tags=("FV-1",),
        require_versioned=True,
        analysis_summary_path=summary_path,
    )

    assert len(loaded.curve_interpretations) == 1


def test_current_report_page_roles_are_compared_without_order(
    tmp_path: Path,
) -> None:
    run_root, curve_manifest_path = _build_frozen_run(tmp_path, versioned=True)
    frozen_pages = [
        {
            "source_sha256": "a" * 64,
            "page": 2,
            "roles": ["specification", "air_piping"],
        }
    ]
    summary_path = _bind_report_pages(run_root, tmp_path, frozen_pages)
    current = json.loads(summary_path.read_text(encoding="utf-8"))
    current[0]["report_pages"][0]["roles"] = ["air_piping", "specification"]
    _write_json(summary_path, current)

    load_formal_diagnosis(
        run_root,
        curve_manifest_path,
        required_valve_tags=("FV-1",),
        require_versioned=True,
        analysis_summary_path=summary_path,
    )


def test_current_report_page_presentation_fields_do_not_change_binding(
    tmp_path: Path,
) -> None:
    run_root, curve_manifest_path = _build_frozen_run(tmp_path, versioned=True)
    pages = [
        {
            "source_sha256": "a" * 64,
            "page": 2,
            "roles": ["specification"],
        }
    ]
    summary_path = _bind_report_pages(run_root, tmp_path, pages)
    current = json.loads(summary_path.read_text(encoding="utf-8"))
    current[0]["report_pages"][0].update(
        {
            "image_path": "/different/rendered/page.png",
            "match_basis": ["manual-correction"],
            "insertion_markers": {"specification": "new-marker"},
        }
    )
    _write_json(summary_path, current)

    load_formal_diagnosis(
        run_root,
        curve_manifest_path,
        required_valve_tags=("FV-1",),
        require_versioned=True,
        analysis_summary_path=summary_path,
    )


def test_v2_frozen_diagnosis_rejects_changed_current_structured_evidence(
    tmp_path: Path,
) -> None:
    """A fresh formal run must not mix frozen conclusions with new fields."""
    run_root, curve_manifest_path = _build_frozen_run(tmp_path, versioned=True)
    run_dir = run_root / "FV-1"
    evidence_path = run_dir / "01_evidence_bundle.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    structured = evidence["evidence"]["structured_evidence"]
    observations_path = run_dir / "03_observations.json"
    observations = json.loads(observations_path.read_text(encoding="utf-8"))
    observations["evidence_accounting"] = structured_evidence_accounting(structured)
    _write_json(observations_path, observations)
    diagnosis_path = run_dir / "05_diagnosis.json"
    diagnosis = json.loads(diagnosis_path.read_text(encoding="utf-8"))
    diagnosis["diagnosis_basis"]["evidence_contract_version"] = "2"
    diagnosis["diagnosis_basis"]["observations_sha256"] = _sha256(observations_path)
    _write_json(diagnosis_path, diagnosis)
    freeze_path = run_dir / "06_freeze.sha256"
    freeze_path.write_text(
        "\n".join(
            f"{_sha256(run_dir / name)}  {name}"
            for name in (
                "00_version.json",
                "01_evidence_bundle.json",
                "02_retrieved_cases.json",
                "03_observations.json",
                "04_retrieved_rules.json",
                "05_diagnosis.json",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    pages = [
        {"source_sha256": "a" * 64, "page": 2, "roles": ["specification"]}
    ]
    summary_path = _bind_report_pages(run_root, tmp_path, pages)
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary[0]["structured_evidence"] = json.loads(
        evidence_path.read_text(encoding="utf-8")
    )["evidence"]["structured_evidence"]
    summary[0]["structured_evidence"]["source_record"]["folder"] = "changed"
    _write_json(summary_path, summary)

    with pytest.raises(FormalDiagnosisError, match="structured_evidence"):
        load_formal_diagnosis(
            run_root,
            curve_manifest_path,
            required_valve_tags=("FV-1",),
            require_versioned=True,
            analysis_summary_path=summary_path,
        )


@pytest.mark.parametrize("nested_work", [False, True])
def test_v2_frozen_diagnosis_rejects_stale_project_source_file(
    tmp_path: Path,
    nested_work: bool,
) -> None:
    """A matching-but-stale summary must not hide changed raw evidence."""
    run_root, curve_manifest_path = _build_frozen_run(tmp_path, versioned=True)
    run_dir = run_root / "FV-1"
    evidence_path = run_dir / "01_evidence_bundle.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    project_root = tmp_path / "project"
    source_path = project_root / "raw/FV-1/source.pdf"
    source_path.parent.mkdir(parents=True)
    source_path.write_bytes(b"source-before-freeze")
    structured = evidence["evidence"]["structured_evidence"]
    structured["source_files"] = [
        {
            "relative_path": "raw/FV-1/source.pdf",
            "size_bytes": source_path.stat().st_size,
            "sha256": _sha256(source_path),
        }
    ]
    observations_path = run_dir / "03_observations.json"
    observations = json.loads(observations_path.read_text(encoding="utf-8"))
    observations["evidence_accounting"] = structured_evidence_accounting(structured)
    _write_json(observations_path, observations)
    diagnosis_path = run_dir / "05_diagnosis.json"
    diagnosis = json.loads(diagnosis_path.read_text(encoding="utf-8"))
    diagnosis["diagnosis_basis"]["evidence_contract_version"] = "2"
    diagnosis["diagnosis_basis"]["observations_sha256"] = _sha256(observations_path)
    _write_json(diagnosis_path, diagnosis)
    _write_json(evidence_path, evidence)
    summary_path = _bind_report_pages(run_root, tmp_path, [
        {"source_sha256": "a" * 64, "page": 2, "roles": ["specification"]}
    ])
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary[0]["structured_evidence"] = json.loads(
        evidence_path.read_text(encoding="utf-8")
    )["evidence"]["structured_evidence"]
    project_summary_path = project_root / (
        "work/current-batch/analysis_summary.json"
        if nested_work else "work/analysis_summary.json"
    )
    project_summary_path.parent.mkdir(parents=True)
    _write_json(project_summary_path, summary)

    load_formal_diagnosis(
        run_root,
        curve_manifest_path,
        required_valve_tags=("FV-1",),
        require_versioned=True,
        analysis_summary_path=project_summary_path,
        project_root=project_root if nested_work else None,
    )

    source_path.write_bytes(b"source-after-freeze")

    with pytest.raises(FormalDiagnosisError, match="source file"):
        load_formal_diagnosis(
            run_root,
            curve_manifest_path,
            required_valve_tags=("FV-1",),
            require_versioned=True,
            analysis_summary_path=project_summary_path,
            project_root=project_root if nested_work else None,
        )
