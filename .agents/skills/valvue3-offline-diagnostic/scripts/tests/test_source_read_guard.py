"""Anonymous declarations exercise guards only; no model reading is claimed."""
from copy import deepcopy
from pathlib import Path

import pytest

from formal_diagnosis_tools import single_valve_workflow as workflow
from offline_engine.formal_diagnosis import FormalDiagnosisError, load_formal_diagnosis
from offline_engine.source_reads import (
    SOURCE_READ_CONTRACT, source_read_template, validate_source_reads,
    validate_source_read_acceptance,
)
from test_formal_diagnosis_tools import (
    _synthetic_project, _read_json, _write_json, _complete_integrated_result,
    _approve_complete_project_diagnosis, load_offline_experience,
)


def _prepared(tmp_path):
    project, analysis, curve_path = _synthetic_project(tmp_path)
    run_root = project / "work/formal_diagnosis/runs/source-read-test"
    workflow.prepare_project_runs(
        analysis, project_root=project, project_id="PROJECT-001",
        experience_text=load_offline_experience(), run_root=run_root,
        diagnosis_version="source-read-test", parent_run="none",
        revision_reason="Synthetic source read guard regression; no model call.",
    )
    run = run_root / "VALVE-001"
    workflow.prepare_integrated_model_request(run)
    return run, curve_path


@pytest.mark.parametrize("damage", [
    "omitted", "empty", "missing", "duplicate", "wrong_path", "wrong_hash",
    "failed", "unread", "unknown_status", "missing_method", "missing_reference",
    "failure_with_success", "not_list", "extra_field", "not_object",
])
def test_source_read_rejection_is_auditable_and_does_not_advance(tmp_path, damage):
    run, _ = _prepared(tmp_path)
    result = _complete_integrated_result(run)
    reads = result["source_file_reads"]
    if damage == "omitted":
        del result["source_file_reads"]
    elif damage == "empty":
        reads.clear()
    elif damage == "missing":
        reads.pop()
    elif damage == "duplicate":
        reads.append(deepcopy(reads[0]))
    elif damage == "wrong_path":
        reads[0]["relative_path"] = "raw/another-valve/source.pdf"
    elif damage == "wrong_hash":
        reads[0]["sha256"] = "0" * 64
    elif damage in {"failed", "unread", "unknown_status"}:
        reads[0]["status"] = damage
        reads[0]["failure_reason"] = "Synthetic original-file tool failure."
    elif damage in {"missing_method", "missing_reference"}:
        reads[0]["read_method" if damage == "missing_method" else "read_reference"] = ""
    elif damage == "failure_with_success":
        reads[0]["failure_reason"] = "Original could not be opened."
    elif damage == "not_list":
        result["source_file_reads"] = True
    elif damage == "extra_field":
        reads[0]["verified"] = True
    elif damage == "not_object":
        reads[0] = None
    result["whole_valve"]["information_complete"] = False
    result["whole_valve"]["uncertainties"] = ["Original reading was unavailable; not an equipment rating."]
    before = {p: p.read_bytes() for p in (run / "00_run_manifest.json",
              run.parent / "00_project_run_manifest.json")}
    with pytest.raises(ValueError, match="source_file_reads"):
        workflow.submit_integrated_model_result(run, result)
    assert all(p.read_bytes() == value for p, value in before.items())
    assert not (run / "03_observations.json").exists()
    assert not (run / workflow.DIAGNOSIS_DRAFT_FILENAME).exists()
    assert not (run / "05_diagnosis.json").exists()
    assert not (run / "06_freeze.sha256").exists()
    rejections = list((run / "03_source_read_rejections").glob("*.json"))
    assert len(rejections) == 1
    record = _read_json(rejections[0])
    assert record["result"] == result
    assert record["status"] == "rejected"
    assert record["evidence_bundle_sha256"] == workflow._sha256_file(run / "01_evidence_bundle.json")
    # Repeating an identical rejected submission preserves its first audit file.
    original = rejections[0].read_bytes()
    with pytest.raises(ValueError):
        workflow.submit_integrated_model_result(run, result)
    assert rejections[0].read_bytes() == original


def test_source_read_template_starts_unread_and_schema_allows_honest_failure(tmp_path):
    run, _ = _prepared(tmp_path)
    template = _read_json(run / workflow.INTEGRATED_TEMPLATE_FILENAME)
    assert all(row["status"] == "unread" and not row["read_method"]
               and not row["read_reference"] for row in template["source_file_reads"])
    schema = _read_json(run / workflow.INTEGRATED_SCHEMA_FILENAME)
    assert "source_file_reads" in schema["required"]
    props = schema["properties"]["source_file_reads"]["items"]["properties"]
    assert props["status"]["enum"] == ["read", "failed", "unread"]


@pytest.mark.parametrize("kinds", [[], ["PDF"], ["devdata"], ["PDF", "devdata", "specification"]])
def test_read_coverage_uses_registered_originals_not_assumed_file_types(kinds):
    bundle = {"evidence": {"source_files": [
        {"kind": kind, "relative_path": f"raw/input-{index}", "sha256": str(index) * 64}
        for index, kind in enumerate(kinds)
    ]}}
    reads = source_read_template(bundle)
    for row in reads:
        row.update(status="read", read_method="synthetic", read_reference="fixture")
    validate_source_reads(bundle, reads)
    with pytest.raises(ValueError):
        validate_source_reads(bundle, None)
    if reads:
        with pytest.raises(ValueError):
            validate_source_reads(bundle, [])


def test_duplicate_original_source_is_not_normalized_away():
    source = {"relative_path": "raw/input.pdf", "sha256": "a" * 64}
    with pytest.raises(ValueError, match="duplicate"):
        source_read_template({"evidence": {"source_files": [source, source]}})


def test_all_reads_pass_preserve_self_report_and_partial_information(tmp_path):
    run, _ = _prepared(tmp_path)
    result = _complete_integrated_result(run)
    result["whole_valve"]["information_complete"] = False
    result["whole_valve"]["uncertainties"] = ["Application-specific threshold is not supplied."]
    manifest = workflow.submit_integrated_model_result(run, result)
    assert manifest["stage"] == "awaiting_diagnosis"
    observations = _read_json(run / "03_observations.json")
    assert observations["source_file_reads"] == result["source_file_reads"]
    draft = _read_json(run / workflow.DIAGNOSIS_DRAFT_FILENAME)
    assert validate_source_read_acceptance(
        _read_json(run / "01_evidence_bundle.json"), observations, draft, manifest,
    ) == "reported_complete"
    assert draft["whole_valve"]["information_complete"] is False
    assert not (run / "06_freeze.sha256").exists()


def test_old_unsubmitted_request_requires_new_preparation(tmp_path):
    run, _ = _prepared(tmp_path)
    path = run / "00_run_manifest.json"
    manifest = _read_json(path)
    manifest.pop("source_read_contract")
    _write_json(path, manifest)
    before = path.read_bytes()
    with pytest.raises(ValueError, match="prepare a new versioned request"):
        workflow.submit_integrated_model_result(run, _complete_integrated_result(run))
    assert path.read_bytes() == before


def test_pending_old_draft_cannot_use_frozen_legacy_exception():
    manifest = {"stage": "awaiting_diagnosis", "skill_runtime": {"version": "0.1.1"}}
    with pytest.raises(ValueError, match="fresh source reads required"):
        validate_source_read_acceptance({"evidence": {}}, {}, {}, manifest)


def test_old_frozen_artifact_remains_unrecorded_without_mutation():
    manifest = {"stage": "frozen", "skill_runtime": {"version": "0.1.1"}}
    before = deepcopy(manifest)
    assert validate_source_read_acceptance({"evidence": {}}, {}, {}, manifest) == "legacy_unrecorded"
    assert manifest == before
    manifest["skill_runtime"]["version"] = "0.1.2"
    with pytest.raises(ValueError, match="source_read_contract"):
        validate_source_read_acceptance({"evidence": {}}, {}, {}, manifest)


def _tamper_observations_and_rebind(run, *, frozen=False):
    path = run / "03_observations.json"
    observations = _read_json(path)
    observations["source_file_reads"][0].update(status="failed", failure_reason="Unavailable original")
    _write_json(path, observations)
    name = "05_diagnosis.json" if frozen else workflow.DIAGNOSIS_DRAFT_FILENAME
    path = run / name
    diagnosis = _read_json(path)
    diagnosis["diagnosis_basis"] = workflow._diagnosis_basis(run)
    _write_json(path, diagnosis)
    if frozen:
        freeze = run / "06_freeze.sha256"
        names = [line.split("  ", 1)[1] for line in freeze.read_text().splitlines()]
        freeze.write_text("".join(f"{workflow._sha256_file(run / name)}  {name}\n" for name in names))
    return diagnosis


def test_rebound_failed_reads_cannot_enter_review_or_freeze(tmp_path):
    run, _ = _prepared(tmp_path)
    workflow.submit_integrated_model_result(run, _complete_integrated_result(run))
    diagnosis = _tamper_observations_and_rebind(run)
    with pytest.raises(ValueError, match="source_file_reads contains failed"):
        workflow.prepare_diagnosis_review(run.parent)
    with pytest.raises(ValueError, match="source_file_reads contains failed"):
        workflow.submit_diagnosis(run, diagnosis)
    assert not (run / "06_freeze.sha256").exists()


def test_rehashed_frozen_failed_reads_cannot_be_loaded_or_inherited(tmp_path):
    run, curve_path = _prepared(tmp_path)
    workflow.submit_integrated_model_result(run, _complete_integrated_result(run))
    _approve_complete_project_diagnosis(run.parent)
    bundle = _read_json(run / "01_evidence_bundle.json")
    curve_manifest = run.parent / "curve_manifest.json"
    _write_json(curve_manifest, {"images": [{
        "image_key": bundle["evidence"]["curve_images"][0]["image_key"],
        "valve_tag": "VALVE-001", "image_path": str(curve_path), "test_key": "run_step",
    }]})
    loaded = load_formal_diagnosis(run.parent, curve_manifest, required_valve_tags=("VALVE-001",), require_versioned=True)
    assert loaded.source_read_statuses == {"VALVE-001": "reported_complete"}
    _tamper_observations_and_rebind(run, frozen=True)
    with pytest.raises(FormalDiagnosisError, match="source read acceptance failed"):
        load_formal_diagnosis(run.parent, curve_manifest, required_valve_tags=("VALVE-001",), require_versioned=True)
    child, _ = _prepared(tmp_path / "child")
    workflow.submit_integrated_model_result(child, _complete_integrated_result(child))
    with pytest.raises(ValueError, match="source_file_reads contains failed|valid frozen run"):
        workflow.inherit_frozen_diagnosis(run, child)


@pytest.mark.parametrize("source_files", [None, []])
def test_original_list_omission_cannot_hide_registered_sources(source_files):
    source = {"relative_path": "raw/input.pdf", "sha256": "a" * 64}
    evidence = {"structured_evidence": {"source_files": [source]}}
    if source_files is not None:
        evidence["source_files"] = source_files
    with pytest.raises(ValueError, match="registered structured originals"):
        validate_source_reads({"evidence": evidence}, [])


def test_precontract_frozen_loader_reports_legacy_without_rewriting(tmp_path):
    run, curve_path = _prepared(tmp_path)
    workflow.submit_integrated_model_result(run, _complete_integrated_result(run))
    _approve_complete_project_diagnosis(run.parent)
    # Manufacture an anonymous pre-contract fixture, not a production migration.
    for name in ["00_run_manifest.json", "03_observations.json", "05_diagnosis.json"]:
        path = run / name
        payload = _read_json(path)
        payload.pop("source_read_contract", None)
        payload.pop("source_file_reads", None)
        if name == "00_run_manifest.json":
            payload["skill_runtime"]["version"] = "0.1.1"
        _write_json(path, payload)
    diagnosis = _read_json(run / "05_diagnosis.json")
    diagnosis["diagnosis_basis"] = workflow._diagnosis_basis(run)
    _write_json(run / "05_diagnosis.json", diagnosis)
    freeze = run / "06_freeze.sha256"
    names = [line.split("  ", 1)[1] for line in freeze.read_text().splitlines()]
    freeze.write_text("".join(f"{workflow._sha256_file(run / name)}  {name}\n" for name in names))
    bundle = _read_json(run / "01_evidence_bundle.json")
    curve_manifest = run.parent / "curve_manifest.json"
    _write_json(curve_manifest, {"images": [{
        "image_key": bundle["evidence"]["curve_images"][0]["image_key"],
        "valve_tag": "VALVE-001", "image_path": str(curve_path), "test_key": "run_step",
    }]})
    before = {p: p.read_bytes() for p in run.rglob("*") if p.is_file()}
    loaded = load_formal_diagnosis(run.parent, curve_manifest, required_valve_tags=("VALVE-001",), require_versioned=True)
    assert loaded.source_read_statuses == {"VALVE-001": "legacy_unrecorded"}
    assert workflow.get_status(run, require_versioned=True)["freeze_valid"] is True
    assert {p: p.read_bytes() for p in run.rglob("*") if p.is_file()} == before


def test_frozen_legacy_observations_cannot_be_copied_into_new_inheritance(tmp_path):
    parent, _ = _prepared(tmp_path / "parent")
    workflow.submit_integrated_model_result(parent, _complete_integrated_result(parent))
    _approve_complete_project_diagnosis(parent.parent)
    child, _ = _prepared(tmp_path / "child")
    workflow.submit_integrated_model_result(child, _complete_integrated_result(child))
    path = child / "03_observations.json"
    observations = _read_json(path)
    observations.pop("source_read_contract")
    observations.pop("source_file_reads")
    _write_json(path, observations)
    with pytest.raises(ValueError, match="inheritance target requires source read declarations"):
        workflow.inherit_frozen_diagnosis(parent, child)
