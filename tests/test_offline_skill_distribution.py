from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import zipfile
import hashlib

import pytest

from scripts import build_offline_skill_package as builder
from offline_engine.distribution import runtime_identity, verify_package


def test_package_is_portable_and_deterministic(tmp_path: Path) -> None:
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"
    builder.build_package(first)
    builder.build_package(second)
    assert first.read_bytes() == second.read_bytes()
    with pytest.raises(FileExistsError):
        builder.build_package(first)
    installed = tmp_path / "installed"
    with zipfile.ZipFile(first) as archive:
        names = archive.namelist()
        assert all(name.startswith("valvue3-offline-diagnostic/") for name in names)
        assert not any(part in {".git", "__pycache__", ".venv", "projects", "raw", "work", "output"}
                       for name in names for part in Path(name).parts)
        archive.extractall(installed)
    skill = installed / "valvue3-offline-diagnostic"
    assert verify_package(skill)["status"] == "verified"
    assert runtime_identity(skill) == runtime_identity(builder.SKILL)
    checked = subprocess.run([sys.executable, str(skill / "scripts/validate_offline_regressions.py")],
                             cwd=tmp_path, capture_output=True, text=True, check=False)
    assert checked.returncode == 0, checked.stderr + checked.stdout
    assert "PASS" in checked.stdout
    manifest = skill / "package-manifest.json"
    original_manifest = manifest.read_bytes()
    manifest.unlink()
    with pytest.raises(ValueError, match="manifest is missing"):
        verify_package(skill)
    manifest.write_bytes(original_manifest)
    (skill / "assets/project.toml").write_text("tampered")
    with pytest.raises(ValueError, match="differ"):
        verify_package(skill)


def test_package_rejects_undeclared_files(tmp_path: Path) -> None:
    output = tmp_path / "package.zip"
    builder.build_package(output)
    with zipfile.ZipFile(output) as archive:
        archive.extractall(tmp_path)
    skill = tmp_path / "valvue3-offline-diagnostic"
    (skill / "customer-secret.json").write_text("must not be installed here")
    with pytest.raises(ValueError, match="undeclared"):
        verify_package(skill)


def test_repository_export_preserves_source_and_excludes_local_data(tmp_path: Path) -> None:
    output = tmp_path / "repository"
    result = builder.export_repository(output)
    assert result["version"] == runtime_identity(builder.SKILL)["version"]
    assert not (output / "skill").exists()
    for retired in ("Dockerfile", "docker", "compose.yaml", "compose.ocr.yaml",
                    "compose.isolation.yaml", ".dockerignore", ".env.example",
                    ".github/workflows/docker.yml", "docs/DOCKER.md"):
        assert not (output / retired).exists()
    assert (output / ".github/workflows/skill.yml").is_file()
    rebuilt = output / ".scratch/rebuilt.zip"
    checked = subprocess.run([sys.executable, str(output / "scripts/build_offline_skill_package.py"),
                              "--output", str(rebuilt)], cwd=output,
                             capture_output=True, text=True, check=False)
    assert checked.returncode == 0, checked.stderr + checked.stdout
    original = tmp_path / "source.zip"
    builder.build_package(original)
    assert rebuilt.read_bytes() == original.read_bytes()
    assert not (output / ".git").exists()
    assert not (output / ".agents/skills/online-valve-diagnostic").exists()
    for name, expected in result["files"].items():
        assert hashlib.sha256((output / name).read_bytes()).hexdigest() == expected
        assert not any(part in {"projects", "raw", "work", "output", ".runtime", ".codex", ".venv"}
                       for part in Path(name).parts)
    sources = output / ".agents/skills/valvue3-offline-diagnostic"
    assert runtime_identity(sources) == runtime_identity(builder.SKILL)
    assert (output / "README.md").read_bytes() == (builder.ROOT / "docs/offline-repository-readme.txt").read_bytes()
    with pytest.raises(FileExistsError):
        builder.export_repository(output)


def test_repository_export_rejects_symlinked_inputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    secret = tmp_path / "private-auth.json"
    secret.write_text("must not export")
    link = tmp_path / "link.json"
    link.symlink_to(secret)
    monkeypatch.setattr(builder, "REPOSITORY_FILES", {"unsafe.json": str(link)})
    with pytest.raises(ValueError, match="symlinked"):
        builder.export_repository(tmp_path / "repository")
