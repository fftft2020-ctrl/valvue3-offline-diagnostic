#!/usr/bin/env python3
"""Export only explicitly selected shared files, without Git history or local data."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import sys
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / ".agents/skills/valvue3-offline-diagnostic"
sys.path.insert(0, str(SKILL / "scripts"))
from offline_engine.distribution import distribution_files, runtime_identity

SUPPLEMENTAL_FILES = {
    "scripts/validate_offline_regressions.py": "scripts/validate_offline_regressions.py",
    "scripts/offline_regression_fixture.py": "scripts/offline_regression_fixture.py",
    "assets/offline_regressions.json": "tests/fixtures/offline_regressions/offline_regressions.json",
    "assets/offline_regressions.schema.json": "tests/fixtures/offline_regressions/schema.json",
}

REPOSITORY_FILES = {
    name: name for name in (
        "Dockerfile", "compose.yaml", "compose.ocr.yaml", "compose.isolation.yaml", ".env.example",
        ".github/workflows/docker.yml", "pytest.ini",
        "docker/doctor.py", "docker/entrypoint.sh", "docker/import.sh", "docker/valvue3-diagnostic.apparmor",
        "docker/installers/README.md", "docker/isolation_smoke.py", "docker/ocr_server.py",
        "docker/ocr_smoke.py", "docker/package.sh", "docker/requirements-ocr.txt",
        "docker/run.sh", "docker/tests/test_packaging.py",
        "docs/ACCEPTANCE.md", "docs/KNOWN_ISSUES.md", "docs/DOCKER.md",
        "docs/offline-source-workflow.md", "docs/offline-repository-readme.txt",
        "docs/offline-repository-gitignore.txt", "scripts/build_offline_skill_package.py",
        "tests/test_offline_skill_distribution.py",
        *SUPPLEMENTAL_FILES.values(),
    )
}
REPOSITORY_FILES.update({"README.md": "docs/offline-repository-readme.txt",
                         ".gitignore": "docs/offline-repository-gitignore.txt"})


def export_repository(output: Path) -> dict[str, object]:
    """Generate a fresh GitHub/build tree from the one maintained local source."""
    sources = {f".agents/skills/valvue3-offline-diagnostic/{name}":
               f".agents/skills/valvue3-offline-diagnostic/{name}"
               for name in distribution_files(SKILL)}
    sources.update(REPOSITORY_FILES)
    for source in sources.values():
        path = ROOT / source
        if any(part.is_symlink() for part in (path, *path.parents)) or not path.is_file():
            raise ValueError(f"repository file is missing or symlinked: {source}")
    output.mkdir(parents=True, exist_ok=False)
    hashes = {}
    for name, source in sorted(sources.items()):
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / source, target)
        hashes[name] = hashlib.sha256(target.read_bytes()).hexdigest()
    # Explicit files only: future customer data or auth can never enter the context.
    included = {name for name in sources if name.startswith(".agents/skills/")}
    included.update(("Dockerfile", "scripts/build_offline_skill_package.py",
                     *SUPPLEMENTAL_FILES.values(), "docker/doctor.py", "docker/entrypoint.sh",
                     "docker/ocr_server.py", "docker/ocr_smoke.py", "docker/isolation_smoke.py",
                     "docker/requirements-ocr.txt", "docker/installers/README.md",
                     "docker/installers/wps-office.deb"))
    for name in list(included):
        included.update(parent.as_posix() + "/" for parent in Path(name).parents if parent != Path("."))
    ignore = "# Generated explicit build-context allowlist.\n**\n"
    ignore += "".join(f"!{name}\n" for name in sorted(included))
    (output / ".dockerignore").write_text(ignore, encoding="utf-8")
    hashes[".dockerignore"] = hashlib.sha256(ignore.encode()).hexdigest()
    return {"repository": str(output.resolve()), "version": runtime_identity(SKILL)["version"],
            "files": hashes, "note": "生成副本；只修改本机维护源码，不在此目录开发"}


def build_package(output: Path) -> dict[str, object]:
    files = {name: (SKILL / name).read_bytes() for name in distribution_files(SKILL)}
    for name, source in SUPPLEMENTAL_FILES.items():
        path = ROOT / source
        if any(part.is_symlink() for part in (path, *path.parents)) or not path.is_file():
            raise ValueError(f"supplemental file is missing or symlinked: {source}")
        if name in files:
            raise ValueError(f"duplicate package destination: {name}")
        files[name] = path.read_bytes()
    # Reject broken local document links before a receiver discovers them.
    for name, content in files.items():
        if not name.endswith(".md"):
            continue
        for target in re.findall(r"\]\(([^\s)]+)\)", content.decode("utf-8")):
            target = target.split("#", 1)[0]
            if not target or ":" in target:
                continue
            resolved = (SKILL / name).parent.joinpath(target).resolve()
            try:
                relative = resolved.relative_to(SKILL.resolve()).as_posix()
            except ValueError as exc:
                raise ValueError(f"document link escapes package: {name}: {target}") from exc
            if relative not in files:
                raise ValueError(f"document link is not packaged: {name}: {target}")
    release = json.loads(files["release.json"])
    manifest = {"schema_version": 1, "name": release["name"], "version": release["version"],
                "runtime": runtime_identity(SKILL),
                "files": {name: {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
                          for name, data in sorted(files.items())}}
    files["package-manifest.json"] = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(f"{release['name']}/{name}", date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as handle:
        handle.write(buffer.getvalue())
    return {"package": str(output.resolve()), "version": release["version"],
            "files": len(files), "bytes": len(buffer.getvalue()),
            "sha256": hashlib.sha256(buffer.getvalue()).hexdigest()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    destination = parser.add_mutually_exclusive_group(required=True)
    destination.add_argument("--output", type=Path, help="独立 Skill ZIP（新路径）")
    destination.add_argument("--repository-output", type=Path, help="GitHub/Docker 源码副本（新目录）")
    args = parser.parse_args()
    try:
        result = (export_repository(args.repository_output) if args.repository_output
                  else build_package(args.output))
    except (OSError, ValueError) as exc:
        parser.exit(1, f"error: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
