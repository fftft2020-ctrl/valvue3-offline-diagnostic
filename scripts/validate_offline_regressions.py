#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

try:
    from scripts.offline_regression_fixture import (
        RegressionFixtureError,
        fixture_regression_errors,
    )
except ModuleNotFoundError:
    from offline_regression_fixture import (
        RegressionFixtureError,
        fixture_regression_errors,
    )


SCRIPT_DIR = Path(__file__).resolve().parent
ENGINE_SCRIPTS = (SCRIPT_DIR if (SCRIPT_DIR / "offline_engine").is_dir() else
                  SCRIPT_DIR.parent / ".agents/skills/valvue3-offline-diagnostic/scripts")
if str(ENGINE_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(ENGINE_SCRIPTS))
from offline_engine.config import load_project, ConfigError, ProjectConfig
from offline_engine.project_materials import require_parameter_extraction_approval


DEFAULT_FIXTURE = (
    SCRIPT_DIR.parent / "assets/offline_regressions.json"
    if (SCRIPT_DIR / "offline_engine").is_dir() else
    SCRIPT_DIR.parent / "tests/fixtures/offline_regressions/offline_regressions.json"
)


def _load(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"summary must contain a list: {path}")
    return data


def _sha256_file(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _manifest_file_errors(
    project: Path,
    entries: Any,
    *,
    label: str,
) -> list[str]:
    if not isinstance(entries, list):
        return [f"{label} manifest entries must contain a list"]
    errors: list[str] = []
    project = project.resolve()
    for entry in entries:
        if not isinstance(entry, dict):
            errors.append(f"{label} manifest entry must contain an object")
            continue
        relative = str(entry.get("path") or "").strip()
        expected_sha256 = str(entry.get("sha256") or "").strip()
        if not relative or not expected_sha256:
            errors.append(f"{label} manifest entry requires path and sha256")
            continue
        path = (project / relative).resolve()
        try:
            path.relative_to(project)
        except ValueError:
            errors.append(f"{label} path escapes project: {relative}")
            continue
        if not path.is_file():
            errors.append(f"missing {label} file: {relative}")
        elif _sha256_file(path) != expected_sha256:
            errors.append(f"{label} hash differs: {relative}")
    return errors


def _freeze_errors(run_dir: Path) -> list[str]:
    freeze_path = run_dir / "06_freeze.sha256"
    if not freeze_path.is_file():
        return [f"missing freeze: {freeze_path}"]
    errors: list[str] = []
    entries: set[str] = set()
    for line in freeze_path.read_text(encoding="utf-8").splitlines():
        if "  " not in line:
            errors.append(f"invalid freeze entry: {freeze_path}")
            continue
        expected, filename = line.split("  ", 1)
        if Path(filename).name != filename:
            errors.append(f"freeze path is not local: {filename}")
            continue
        entries.add(filename)
        path = run_dir / filename
        if not path.is_file():
            errors.append(f"missing frozen file: {path}")
        elif _sha256_file(path) != expected:
            errors.append(f"freeze hash differs: {path}")
    required = {
        "01_evidence_bundle.json",
        "03_observations.json",
        "05_diagnosis.json",
    }
    missing = sorted(required - entries)
    if missing:
        errors.append(
            "freeze does not bind required files: " + ", ".join(missing)
        )
    return errors


def _validate_current_project(
    config: ProjectConfig,
    summary_path: Path,
) -> list[str]:
    errors: list[str] = []
    project = config.project_dir
    try:
        # Stage 2 also checks Stage 1; use the runtime gates without rewriting artifacts.
        require_parameter_extraction_approval(config)
    except (RuntimeError, OSError, ValueError) as exc:
        return [f"current runtime approval gate failed: {exc}"]

    summary = _load(summary_path)
    folders = [str(item.get("folder") or "").strip() for item in summary]
    if any(not folder for folder in folders):
        errors.append("current summary contains a blank valve folder")
    if len(folders) != len(set(folders)):
        errors.append("current summary contains duplicate valve folders")
    included = {
        folder
        for folder, item in zip(folders, summary, strict=True)
        if folder and item.get("included_in_report") is not False
    }

    extraction_root = project / "work/evidence-extraction"
    index_path = extraction_root / "project-index.json"
    manifest_path = extraction_root / "run-manifest.json"
    if not index_path.is_file():
        errors.append(f"missing current evidence index: {index_path}")
    if not manifest_path.is_file():
        errors.append(f"missing current extraction manifest: {manifest_path}")
    if errors:
        return errors

    index = json.loads(index_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if index.get("status") != "materials_extracted":
        errors.append("current evidence index is not materials_extracted")
    if manifest.get("status") != "materials_extracted":
        errors.append("current extraction manifest is not materials_extracted")
    indexed_valves = {
        str(item.get("valve_tag") or "").strip()
        for item in index.get("valves") or []
        if isinstance(item, dict)
    }
    missing_valves = sorted(included - indexed_valves)
    if missing_valves:
        errors.append(
            "current evidence index misses valves: "
            + ", ".join(missing_valves)
        )
    for item in index.get("valves") or []:
        if not isinstance(item, dict):
            continue
        relative = str(item.get("path") or "").strip()
        if relative and not (project / relative).is_file():
            errors.append(f"missing per-valve evidence: {relative}")
    errors.extend(
        _manifest_file_errors(
            project,
            manifest.get("inputs"),
            label="extraction input",
        )
    )
    errors.extend(
        _manifest_file_errors(
            project,
            manifest.get("outputs"),
            label="extraction output",
        )
    )

    run_root = config.formal_diagnosis_run_dir
    if run_root is not None:
        project_manifest_path = run_root / "00_project_run_manifest.json"
        if not project_manifest_path.is_file():
            errors.append(
                f"missing configured formal diagnosis: {project_manifest_path}"
            )
        else:
            project_manifest = json.loads(
                project_manifest_path.read_text(encoding="utf-8")
            )
            if project_manifest.get("stage") != "frozen":
                errors.append("configured formal diagnosis is not frozen")
            formal_targets = {
                str(item.get("valve_tag") or "").strip()
                for item in project_manifest.get("targets") or []
                if isinstance(item, dict)
            }
            missing_formal = sorted(included - formal_targets)
            if missing_formal:
                errors.append(
                    "configured formal diagnosis misses valves: "
                    + ", ".join(missing_formal)
                )
            for valve_tag in sorted(formal_targets):
                errors.extend(_freeze_errors(run_root / valve_tag))
    return errors


def validate_fixture_file(path: Path) -> list[str]:
    if not path.is_file():
        return [f"missing anonymous fixture: {path}"]
    try:
        fixture = json.loads(path.read_text(encoding="utf-8"))
        return fixture_regression_errors(fixture)
    except (OSError, json.JSONDecodeError, RegressionFixtureError) as exc:
        return [f"invalid anonymous fixture: {path}: {exc}"]


def validate_project(project: Path) -> list[str]:
    project = project.resolve()
    if not (project / "project.toml").is_file():
        return [f"unsupported legacy project: project.toml is required: {project}"]
    try:
        config = load_project(project)
        current_path = config.work_dir / "analysis_summary.json"
    except (ConfigError, OSError) as exc:
        return [f"invalid project configuration: {exc}"]
    if not current_path.is_file():
        return [f"missing current summary: {current_path}"]
    return _validate_current_project(config, current_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the portable anonymous regression fixture, or explicitly "
            "validate current local projects. Legacy two-summary projects are unsupported."
        )
    )
    parser.add_argument(
        "projects",
        nargs="*",
        type=Path,
        help="Explicit local project directories for high-fidelity integration checks",
    )
    parser.add_argument(
        "--fixture",
        type=Path,
        help="Anonymous fixture to validate instead of the repository default",
    )
    args = parser.parse_args(argv)
    if args.fixture is not None and args.projects:
        parser.error("--fixture cannot be combined with local project directories")

    if not args.projects:
        fixture_path = (args.fixture or DEFAULT_FIXTURE).resolve()
        errors = validate_fixture_file(fixture_path)
        if errors:
            print(f"FAIL {fixture_path}")
            for error in errors:
                print(f"  - {error}")
            return 1
        print(f"PASS {fixture_path}")
        return 0

    failed = False
    for project in args.projects:
        errors = validate_project(project.resolve())
        if errors:
            failed = True
            print(f"FAIL {project}")
            for error in errors:
                print(f"  - {error}")
        else:
            print(f"PASS {project}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
