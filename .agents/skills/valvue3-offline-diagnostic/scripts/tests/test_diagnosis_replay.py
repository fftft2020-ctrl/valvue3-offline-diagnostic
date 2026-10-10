import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import run_diagnosis_replay as replay
import run_metered_codex as meter


def test_formal_allowlist_keeps_raw_and_images_but_excludes_answers(tmp_path: Path):
    project = tmp_path / "project"
    run = tmp_path / "run"
    run.mkdir()
    raw = project / "raw/FV-001/test.pdf"
    raw.parent.mkdir(parents=True)
    raw.write_bytes(b"original test")
    old = project / "output/old.docx"
    old.parent.mkdir()
    old.write_bytes(b"old diagnosis")
    image = project / "work/image.png"
    image.parent.mkdir()
    image.write_bytes(b"curve")
    request = {"valve_tag": "FV-001", "source_locator": {"project_root": str(project)},
        "evidence": {"source_files": [{"relative_path": "raw/FV-001/test.pdf", "sha256": replay.digest(raw)}],
                     "curve_images": [{"image_path": str(image), "image_sha256": replay.digest(image)}],
                     "specification_images": [], "trace_only": {"path": str(old)}}}
    for name in ("00_experience.md", "01_evidence_bundle.json", "03_integrated_diagnosis_prompt.md",
                 "03_integrated_diagnosis_output_schema.json", "03_integrated_diagnosis_result_template.json"):
        (run / name).write_text("{}")
    (run / "03_integrated_diagnosis_request.json").write_text(json.dumps(request))
    allow = meter.build_diagnosis_allowlist(run, project)
    included = {entry["path"] for entry in allow["files"]}
    assert str(raw) in included and str(image) in included
    assert str(old) not in included
    assert str(project / "output") in allow["denied_paths"]
    raw.write_bytes(b"changed source")
    with pytest.raises(ValueError, match="source identity changed"):
        meter.build_diagnosis_allowlist(run, project)


def test_compare_requires_untampered_seal_before_reading_references(tmp_path: Path):
    with pytest.raises(FileNotFoundError, match="sealed.json"):
        replay.compare(SimpleNamespace(run_root=tmp_path, reference=tmp_path / "not-yet-readable.json"))
    result = tmp_path / "result.json"
    result.write_text("prediction")
    replay.write_new(tmp_path / "sealed.json", {"status": "sealed_for_comparison",
                                               "files": {"result.json": replay.digest(result)}})
    result.write_text("post-comparison revision")
    with pytest.raises(ValueError, match="sealed evidence changed"):
        replay.compare(SimpleNamespace(run_root=tmp_path, reference=tmp_path / "not-yet-readable.json"))


def test_snapshot_refuses_incomplete_diagnosis(tmp_path: Path, monkeypatch):
    run = tmp_path / "FV-001"
    run.mkdir()
    (run / "00_experience.md").write_text("experience")
    replay.write_new(run / "execution.json", {"status": "rejected"})
    replay.write_new(tmp_path / "replay.json", {"code_sha256": {}, "experience_sha256": replay.digest(run / "00_experience.md"),
                                               "entries": [{"run_dir": str(run), "tag": "FV-001"}]})
    monkeypatch.setattr(replay, "code_hashes", lambda: {})
    with pytest.raises(ValueError, match="cannot seal incomplete"):
        replay.snapshot(SimpleNamespace(run_root=tmp_path, allow_partial=False))
    assert not (tmp_path / "sealed.json").exists()


def test_unbound_specification_is_reported_before_model_call(tmp_path: Path):
    project = tmp_path / "project"
    run = tmp_path / "run"
    run.mkdir()
    source = project / "specs/FV-001/quality.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"available but not bound")
    (run / "03_integrated_diagnosis_request.json").write_text(json.dumps({
        "valve_tag": "FV-001", "source_locator": {"project_root": str(project)},
        "evidence": {"source_files": [], "curve_images": [], "specification_images": []}}))
    with pytest.raises(ValueError, match="unbound specification originals"):
        meter.build_diagnosis_allowlist(run, project)


def test_snapshot_rejects_changed_accepted_prediction(tmp_path: Path, monkeypatch):
    run = tmp_path / "FV-001"
    run.mkdir()
    (run / "00_experience.md").write_text("experience")
    result = run / "03_integrated_diagnosis_result.json"
    result.write_text("original")
    replay.write_new(run / "execution.json", {"status": "accepted", "result_sha256": replay.digest(result)})
    replay.write_new(tmp_path / "replay.json", {"code_sha256": {}, "experience_sha256": replay.digest(run / "00_experience.md"),
                                               "entries": [{"run_dir": str(run), "tag": "FV-001"}]})
    result.write_text("changed")
    monkeypatch.setattr(replay, "code_hashes", lambda: {})
    with pytest.raises(ValueError, match="accepted prediction changed"):
        replay.snapshot(SimpleNamespace(run_root=tmp_path, allow_partial=False))


def test_replay_runs_serially_and_stops_on_transport_failure(tmp_path: Path, monkeypatch):
    entries = []
    for tag in ("FV-001", "FV-002", "FV-003"):
        run = tmp_path / tag
        run.mkdir()
        (run / "00_experience.md").write_text("experience")
        entries.append({"tag": tag, "run_dir": str(run)})
    replay.write_new(tmp_path / "replay.json", {
        "code_sha256": {}, "entries": entries,
        "experience_sha256": replay.digest(tmp_path / "FV-001/00_experience.md")})
    monkeypatch.setattr(replay, "code_hashes", lambda: {})
    calls = []

    def call_one(_root, _replay, entry):
        calls.append(entry["tag"])
        return {"tag": entry["tag"], "status": "rejected", "failure_phase": "transport"}

    monkeypatch.setattr(replay, "_call_one", call_one)
    with pytest.raises(ValueError, match="serially"):
        replay.run(SimpleNamespace(run_root=tmp_path, jobs=2))
    assert calls == []
    replay.run(SimpleNamespace(run_root=tmp_path, jobs=1))
    assert calls == ["FV-001"]
    assert replay.read(tmp_path / "execution_summary.json")["pending"] == 2
