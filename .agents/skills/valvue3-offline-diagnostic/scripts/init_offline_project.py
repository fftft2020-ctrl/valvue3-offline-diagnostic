from __future__ import annotations

import argparse
import json
import re
import tomllib
from datetime import date
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = SKILL_DIR / "assets/project.toml"
DIRECTORIES = (
    "raw",
    "specs",
    "inbound",
    "experience/feedback_inbox",
    "work",
    "output",
)
EXTRACTION_OVERRIDES = {
    "schema_version": 1,
    "valves": {},
}
PLACEHOLDERS = {
    "__CUSTOMER__": "customer",
    "__REPORT_TITLE__": "report title",
    "__REPORT_DATE__": "report date",
}


class ProjectInitializationError(ValueError):
    """Raised when a new project cannot be initialized safely."""


def _required_text(value: str, label: str) -> str:
    text = value.strip()
    if not text:
        raise ProjectInitializationError(f"{label} must not be empty")
    return text


def _iso_date(value: str) -> str:
    text = _required_text(value, "report date")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        raise ProjectInitializationError("report date must use YYYY-MM-DD")
    try:
        date.fromisoformat(text)
    except ValueError as exc:
        raise ProjectInitializationError(
            "report date must be a valid calendar date"
        ) from exc
    return text


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _render_project_toml(
    *,
    customer: str,
    report_title: str,
    report_date: str,
) -> str:
    if not TEMPLATE_PATH.is_file():
        raise ProjectInitializationError(
            f"project template is missing: {TEMPLATE_PATH}"
        )
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    values = {
        "__CUSTOMER__": _toml_string(customer),
        "__REPORT_TITLE__": _toml_string(report_title),
        "__REPORT_DATE__": _toml_string(report_date),
    }
    for placeholder, label in PLACEHOLDERS.items():
        count = template.count(placeholder)
        if count != 1:
            raise ProjectInitializationError(
                f"project template must contain one {label} placeholder"
            )
        template = template.replace(placeholder, values[placeholder])
    try:
        parsed = tomllib.loads(template)
    except tomllib.TOMLDecodeError as exc:
        raise ProjectInitializationError(
            f"rendered project.toml is invalid: {exc}"
        ) from exc
    paths = parsed.get("paths") or {}
    if "formal_diagnosis_run" in paths:
        raise ProjectInitializationError(
            "new project template must not configure paths.formal_diagnosis_run"
        )
    return template if template.endswith("\n") else template + "\n"


def initialize_project(
    project: Path,
    *,
    customer: str,
    report_title: str,
    report_date: str,
) -> tuple[Path, tuple[Path, ...]]:
    customer = _required_text(customer, "customer")
    report_title = _required_text(report_title, "report title")
    report_date = _iso_date(report_date)
    project_dir = project.expanduser().resolve()
    if project_dir.parent == project_dir:
        raise ProjectInitializationError(
            "project directory must not be the filesystem root"
        )
    if project_dir.exists() and not project_dir.is_dir():
        raise ProjectInitializationError(
            f"project path exists and is not a directory: {project_dir}"
        )

    config_path = project_dir / "project.toml"
    experience_path = project_dir / "experience/project_experience.json"
    overrides_path = project_dir / "experience/extraction_overrides.json"
    log_path = project_dir / "experience/experience_log.md"
    managed_files = (config_path, experience_path, overrides_path, log_path)
    conflicts = [path for path in managed_files if path.exists()]
    if conflicts:
        conflict_list = ", ".join(str(path) for path in conflicts)
        raise ProjectInitializationError(
            f"refusing to overwrite existing project files: {conflict_list}"
        )

    config_text = _render_project_toml(
        customer=customer,
        report_title=report_title,
        report_date=report_date,
    )
    overrides_text = (
        json.dumps(EXTRACTION_OVERRIDES, ensure_ascii=False, indent=2) + "\n"
    )

    project_dir.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    for relative in DIRECTORIES:
        directory = project_dir / relative
        directory.mkdir(parents=True, exist_ok=True)
        created.append(directory)
    with overrides_path.open("x", encoding="utf-8") as stream:
        stream.write(overrides_text)
    created.append(overrides_path)
    with config_path.open("x", encoding="utf-8") as stream:
        stream.write(config_text)
    created.append(config_path)
    return project_dir, tuple(created)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Initialize a ValVue3 offline diagnostic project."
    )
    parser.add_argument(
        "--project",
        required=True,
        type=Path,
        help="Explicit directory to create or initialize",
    )
    parser.add_argument("--customer", required=True, help="Customer name")
    parser.add_argument("--report-title", required=True, help="Report title")
    parser.add_argument(
        "--report-date",
        required=True,
        help="Report date in YYYY-MM-DD format",
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    try:
        project_dir, created = initialize_project(
            args.project,
            customer=args.customer,
            report_title=args.report_title,
            report_date=args.report_date,
        )
    except (OSError, ProjectInitializationError) as exc:
        parser.exit(1, f"error: {exc}\n")

    print("status=initialized")
    print(f"project={project_dir}")
    for path in created:
        print(f"created={path.relative_to(project_dir)}")
    print(
        "next=place incoming files under inbound/, then run "
        "run_offline_report.py --prepare-materials; submit the Stage 1 "
        "archive report and wait for explicit user approval"
    )


if __name__ == "__main__":
    main()
