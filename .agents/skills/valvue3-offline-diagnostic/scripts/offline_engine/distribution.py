from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath


SKILL_DIR = Path(__file__).resolve().parents[2]


def distribution_files(root: Path = SKILL_DIR) -> list[str]:
    files = (root / "distribution-files.txt").read_text(encoding="utf-8").splitlines()
    if not files or len(files) != len(set(files)):
        raise ValueError("distribution allowlist is empty or contains duplicates")
    for name in files:
        relative = PurePosixPath(name)
        if not name or relative.is_absolute() or ".." in relative.parts or str(relative) != name:
            raise ValueError(f"unsafe distribution path: {name}")
        path = root / name
        if any(part.is_symlink() for part in (path, *path.parents)) or not path.is_file():
            raise ValueError(f"distribution file is missing or symlinked: {name}")
    return sorted(files)


def file_identity(root: Path, names: list[str]) -> dict[str, dict[str, object]]:
    return {name: {"sha256": hashlib.sha256((root / name).read_bytes()).hexdigest(),
                   "bytes": (root / name).stat().st_size} for name in sorted(names)}


def runtime_identity(root: Path = SKILL_DIR) -> dict[str, str]:
    release = json.loads((root / "release.json").read_text(encoding="utf-8"))
    files = file_identity(root, distribution_files(root))
    digest = hashlib.sha256(json.dumps(files, sort_keys=True).encode("utf-8")).hexdigest()
    return {"version": release["version"], "files_sha256": digest,
            "experience_sha256": str(files["references/common-offline-experience.md"]["sha256"]),
            "project_template_sha256": str(files["assets/project.toml"]["sha256"])}


def verify_package(root: Path = SKILL_DIR) -> dict[str, object]:
    manifest_path = root / "package-manifest.json"
    if not manifest_path.exists():
        if (root / "scripts/validate_offline_regressions.py").exists():
            raise ValueError("installed package manifest is missing")
        return {"status": "source_tree", "note": "未安装导出的分发包；无法校验发布清单"}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    declared = manifest["files"]
    if not isinstance(declared, dict) or not declared:
        raise ValueError("package manifest requires files")
    for name in declared:
        relative = PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts or str(relative) != name:
            raise ValueError(f"unsafe package path: {name}")
        path = root / name
        if any(part.is_symlink() for part in (path, *path.parents)) or not path.is_file():
            raise ValueError(f"package file is missing or symlinked: {name}")
    if file_identity(root, list(declared)) != declared:
        raise ValueError("package files differ from release manifest")
    actual = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("package contains a symlink")
        if path.is_file() and not any(part in {"__pycache__", ".pytest_cache"} for part in path.parts):
            actual.add(path.relative_to(root).as_posix())
    if actual != set(declared) | {"package-manifest.json"}:
        raise ValueError("package contains undeclared files")
    if manifest["runtime"] != runtime_identity(root):
        raise ValueError("package runtime identity differs")
    return {"status": "verified", "version": manifest["version"], "file_count": len(declared)}
