from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

import check_environment
import run_metered_codex
from offline_engine.distribution import distribution_files, runtime_identity


def test_allowlist_rejects_duplicates_traversal_and_symlinks(tmp_path: Path) -> None:
    target = tmp_path / "selected.txt"
    target.write_text("shared")
    allowlist = tmp_path / "distribution-files.txt"
    for content in ("selected.txt\nselected.txt\n", "../outside.txt\n", "/etc/passwd\n"):
        allowlist.write_text(content)
        with pytest.raises(ValueError):
            distribution_files(tmp_path)
    link = tmp_path / "link.txt"
    link.symlink_to(target)
    allowlist.write_text("link.txt\n")
    with pytest.raises(ValueError, match="symlinked"):
        distribution_files(tmp_path)
    allowlist.write_text("selected.txt\n")
    assert distribution_files(tmp_path) == ["selected.txt"]


def test_runtime_identity_binds_effective_shared_files() -> None:
    identity = runtime_identity()
    assert identity["version"]
    assert all(len(value) == 64 for name, value in identity.items() if name != "version")


def test_environment_distinguishes_prerequisites_from_actual_execution(tmp_path, monkeypatch) -> None:
    auth = tmp_path / "auth"
    auth.mkdir()
    (auth / "auth.json").write_text('{"secret":"must not appear"}')
    (auth / "models_cache.json").write_text(json.dumps({"models": [{"slug": "gpt-6.1-sol"}]}))
    monkeypatch.setenv("CODEX_HOME", str(auth))
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(check_environment.sys, "platform", "linux")
    monkeypatch.setattr(check_environment.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(run_metered_codex, "_codex_executable", lambda: "/usr/bin/codex")
    requirements = dict(line.split("==") for line in (check_environment.SKILL_DIR / "requirements.txt").read_text().splitlines())
    monkeypatch.setattr(check_environment, "version", lambda name: requirements[name])
    commands = []

    def command(args):
        commands.append(args)
        if args[1:] == ["exec", "--help"]:
            return True, "--ignore-user-config --ignore-rules --ephemeral --output-schema"
        return True, "codex-cli test"

    monkeypatch.setattr(check_environment, "_command", command)
    monkeypatch.setattr(run_metered_codex, "_isolation_prefix", lambda *_: ([], {}))
    monkeypatch.setattr(run_metered_codex, "_check_isolation", lambda *_, **__: {"status": "passed"})
    opener = type("Opener", (), {"open": lambda *_, **__: io.BytesIO(b'{"ready":true}')})()
    monkeypatch.setattr(check_environment.urllib.request, "build_opener", lambda *_: opener)
    result = check_environment.check_environment()
    assert result["status"] == "prerequisites_available"
    assert result["checks"]["model_access"]["status"] == "unverified"
    assert result["checks"]["model_access"]["cached_model_present"] is True
    assert result["unverified"]
    assert "must not appear" not in json.dumps(result)
    assert all("exec" not in args or args[1:] == ["exec", "--help"] for args in commands)

    monkeypatch.setattr(check_environment.shutil, "which", lambda name: None if name in {"wps", "bwrap"} else "/usr/bin/" + name)
    result = check_environment.check_environment()
    assert result["stages"]["docx"]["status"] == "prerequisites_available"
    assert result["stages"]["diagnosis"]["status"] == "blocked"
    assert result["stages"]["wps_pdf"]["status"] == "blocked"
