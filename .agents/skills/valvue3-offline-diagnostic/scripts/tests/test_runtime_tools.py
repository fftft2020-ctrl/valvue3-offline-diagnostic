from __future__ import annotations

import json
import shutil
import sys
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import pytest

import export_wps_pdf
import run_metered_codex


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    (project / "project.toml").write_text("[project]\nid='demo'\n")
    return project


def test_wps_export_requires_same_directory_and_stem(tmp_path: Path) -> None:
    source = tmp_path / "report.docx"
    source.write_bytes(b"docx")
    assert export_wps_pdf._validate_paths(
        source, tmp_path / "report.pdf"
    ) == (source.resolve(), (tmp_path / "report.pdf").resolve())
    with pytest.raises(ValueError, match="same directory and stem"):
        export_wps_pdf._validate_paths(source, tmp_path / "other.pdf")


def test_wps_profile_uses_isolated_multicomponent_mode(tmp_path: Path) -> None:
    export_wps_pdf._write_isolated_wps_profile(tmp_path)
    content = (tmp_path / ".config/Kingsoft/Office.conf").read_text()
    assert "AppComponentMode=prome_independ" in content
    assert "AcceptedEULA=true" in content


def test_metered_ledger_builds_compact_stage_summary(tmp_path: Path) -> None:
    project = _project(tmp_path)
    call = {
        "schema_version": 1,
        "event_type": "model_call",
        "call_id": "diag.VALVE-001.attempt-1",
        "stage": "integrated_diagnosis",
        "valve_tag": "VALVE-001",
        "tokens_used": 1234,
        "elapsed_seconds": 12.5,
        "status": "completed",
        "started_at": "2026-09-04T00:00:00+00:00",
        "ended_at": "2026-09-04T00:00:12.500000+00:00",
    }
    summary_path = run_metered_codex._append_record(project, call)
    run_metered_codex._append_record(
        project,
        {
            "schema_version": 1,
            "event_type": "outcome",
            "call_id": call["call_id"],
            "recorded_at": "2026-09-04T00:00:00+00:00",
            "outcome": "accepted",
            "reason": "",
        },
    )
    summary = json.loads(summary_path.read_text())
    stage = summary["stages"]["integrated_diagnosis"]
    assert stage == {
        "started_at": "2026-09-04T00:00:00+00:00",
        "ended_at": "2026-09-04T00:00:12.500000+00:00",
        "calls": 1,
        "valve_count": 1,
        "tokens_used": 1234,
        "unmetered_calls": 0,
        "elapsed_seconds": 12.5,
        "completed": 1,
        "failed": 0,
        "accepted": 1,
        "rejected": 0,
        "pending_review": 0,
        "retry_reasons": {},
    }
    with pytest.raises(ValueError, match="duplicate model call id"):
        run_metered_codex._append_record(project, call)


def test_token_parser_uses_last_reported_total() -> None:
    output = "tokens used\n1,234\nretry\ntokens used\n2,345\n"
    assert run_metered_codex.parse_tokens(output) == 2345
    assert run_metered_codex.parse_tokens("no meter") is None


@pytest.mark.parametrize("returncode", [0, 9])
def test_metered_runner_records_command_result(
    tmp_path: Path, monkeypatch, capsys, returncode: int
) -> None:
    project = _project(tmp_path)
    prompt = tmp_path / "prompt.md"
    image = tmp_path / "image.png"
    prompt.write_text("inspect")
    image.write_bytes(b"image")
    observed = {}
    full_output = "完整模型诊断与过程日志\n" * 1000
    full_error = "完整诊断过程或错误详情\n" * 1000
    args = Namespace(
        project_root=project,
        stage="delivery_visual_review",
        call_id="delivery-review-1",
        valve_tag=None,
        model="gpt-6-sol",
        reasoning_effort="high",
        prompt_file=prompt,
        image=[image],
        output_schema=None,
        output_last_message=tmp_path / "result.json",
        cwd=tmp_path,
        ignore_user_config=True,
        ignore_rules=True,
        sandbox_mode="read-only",
    )

    def fake_run(command, **kwargs):
        observed.update(command=command, kwargs=kwargs)
        # A second caller racing the first must stop before invoking a model.
        with pytest.raises(ValueError, match="log already exists"):
            run_metered_codex._run(args)
        if returncode == 0:
            args.output_last_message.write_text("result", encoding="utf-8")
        return SimpleNamespace(
            returncode=returncode,
            stdout=full_output,
            stderr=full_error + "tokens used\n2,468\n",
        )

    monkeypatch.setattr(run_metered_codex.shutil, "which", lambda _: "/bin/codex")
    monkeypatch.setenv("ALL_PROXY", "socks5://127.0.0.1:12345")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:23456")
    monkeypatch.setattr(run_metered_codex.subprocess, "run", fake_run)
    result = run_metered_codex._run(args)

    assert result == returncode
    assert observed["kwargs"]["env"]["ALL_PROXY"] == "socks5://127.0.0.1:12345"
    assert observed["kwargs"]["env"]["HTTPS_PROXY"] == "http://127.0.0.1:23456"
    assert "--ignore-user-config" in observed["command"]
    assert observed["command"][observed["command"].index("--sandbox") + 1] == (
        "read-only"
    )
    summary = json.loads((project / "work/workflow-summary.json").read_text())
    record = json.loads((project / "work/model-calls/ledger.jsonl").read_text())
    assert record["skill_runtime"] == run_metered_codex.runtime_identity()
    assert summary["tokens_used"] == 2468
    assert summary["stages"][args.stage]["failed"] == (returncode != 0)
    output = capsys.readouterr()
    assert not output.err
    assert "完整" not in output.out
    assert f"MODEL_CALL_RETURN_CODE={returncode}" in output.out
    assert ("MODEL_CALL_RESULT=" in output.out) == (returncode == 0)
    log = project / "work/model-calls/delivery-review-1.log"
    original_log = log.read_text(encoding="utf-8")
    assert original_log == full_output + "\n" + full_error + "tokens used\n2,468\n"
    assert f"MODEL_CALL_LOG={log}" in output.out
    assert len(output.out) < len(original_log) / 10

    monkeypatch.setattr(run_metered_codex.subprocess, "run", lambda *_a, **_k: pytest.fail("duplicate invoked model"))
    with pytest.raises(ValueError, match="duplicate model call id"):
        run_metered_codex._run(args)
    assert log.read_text(encoding="utf-8") == original_log
    # The durable ledger also protects a call whose log was moved away.
    log.unlink()
    with pytest.raises(ValueError, match="duplicate model call id"):
        run_metered_codex._run(args)


def test_read_allowlist_rejects_changed_files_and_symlinks(tmp_path: Path) -> None:
    evidence = tmp_path / "raw.txt"
    evidence.write_text("raw facts")
    denied = tmp_path / "old-answer.txt"
    denied.write_text("not evidence")
    payload = {"schema_version": 1, "files": [
        {"path": str(evidence), "sha256": run_metered_codex._sha256(evidence)}
    ], "denied_paths": [str(denied)]}
    manifest = tmp_path / "allow.json"
    manifest.write_text(json.dumps(payload))
    assert run_metered_codex._read_allowlist(manifest) == payload
    evidence.write_text("changed")
    with pytest.raises(ValueError, match="changed allowed file"):
        run_metered_codex._read_allowlist(manifest)
    alias = tmp_path / "alias"
    alias.symlink_to(evidence)
    payload["files"][0]["path"] = str(alias)
    manifest.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="canonical paths"):
        run_metered_codex._read_allowlist(manifest)


def test_evidence_namespace_hides_answers_and_keeps_inputs_read_only(
    tmp_path: Path, monkeypatch,
) -> None:
    if not shutil.which("bwrap"):
        pytest.skip("namespace integration requires bwrap")
    evidence = tmp_path / "raw.txt"
    evidence.write_text("raw facts")
    answer = tmp_path / "answer.txt"
    answer.write_text("excluded answer")
    auth = tmp_path / "empty-auth.json"
    auth.write_text("{}")
    output = tmp_path / "output"
    output.mkdir()
    original_require = run_metered_codex._require_file
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:34567")
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.setattr(run_metered_codex, "_require_file", lambda path, label:
                        auth if label == "Codex authentication" else original_require(path, label))
    payload = {"files": [{"path": str(evidence),
                           "sha256": run_metered_codex._sha256(evidence)}],
               "denied_paths": [str(answer), str(Path.home() / ".codex/sessions")]}
    prefix, env = run_metered_codex._isolation_prefix(payload, output, sys.executable)
    assert env["https_proxy"] == "http://127.0.0.1:34567"
    assert "HTTP_PROXY" not in env
    check = run_metered_codex._check_isolation(prefix, env, payload, sys.executable)
    assert check["status"] == "passed"
    assert check["allowed_files"] == 1
    assert check["denied_paths"] == 2
    assert evidence.read_text() == "raw facts"
    assert answer.read_text() == "excluded answer"


def test_new_diagnoses_cannot_downgrade_or_omit_isolation(tmp_path: Path) -> None:
    args = Namespace(model="gpt-5.6-sol", reasoning_effort="medium")
    with pytest.raises(ValueError, match="gpt-6-sol / high"):
        run_metered_codex._run(args)
    args = Namespace(model="gpt-6-sol", reasoning_effort="high", stage="independent_replay",
                     read_allowlist=None, prompt_file=tmp_path / "prompt.md")
    with pytest.raises(ValueError, match="diagnostic read allowlist"):
        run_metered_codex._run(args)


def test_codex_runtime_prefers_installed_host_version(tmp_path: Path, monkeypatch) -> None:
    old = tmp_path / "old/codex"
    current = tmp_path / "current/codex"
    for path in (old, current):
        path.parent.mkdir()
        path.write_text("runtime")
        path.chmod(0o755)
    monkeypatch.setattr(run_metered_codex.shutil, "which", lambda _: str(old))
    monkeypatch.setenv("CODEX_VERSION", "2.0-test")
    monkeypatch.setattr(run_metered_codex.os, "get_exec_path", lambda: [str(old.parent), str(current.parent)])
    monkeypatch.setattr(run_metered_codex.subprocess, "run", lambda command, **kwargs:
                        SimpleNamespace(returncode=0, stdout="codex-cli " + ("2.0-test" if command[0] == str(current) else "1.0-test")))
    assert run_metered_codex._codex_executable() == str(current)
    assert run_metered_codex._codex_executable(old) == str(old)
