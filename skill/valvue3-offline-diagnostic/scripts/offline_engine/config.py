from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class ConfigError(ValueError):
    """Raised when a project configuration is missing or unsafe."""


DEFAULT_FEATURES: dict[str, bool] = {
    "include_specifications": True,
    "include_air_piping_pages": True,
    "include_accessory_pages": False,
}

DEFAULT_PRESENTATION: dict[str, bool] = {
    "include_field_context_overview": False,
    "include_overview_judgement": False,
    "include_overview_priority": False,
    "include_retest_recommendations": False,
}

AIR_PIPING_MIGRATION_NOTICE = (
    "features.include_accessory_pages no longer controls air-piping pages; "
    "set features.include_air_piping_pages explicitly"
)


@dataclass(frozen=True)
class ProjectConfig:
    project_dir: Path
    config_path: Path
    customer: str
    report_title: str
    report_date: str
    report_period_key: str
    report_period_label: str
    raw_dir: Path
    specs_dir: Path
    inbound_dir: Path
    work_dir: Path
    output_dir: Path
    output_docx: Path
    output_pdf: Path
    catalog_file: Path | None = None
    manual_interpretations_file: Path | None = None
    diagnostic_summary_overrides_file: Path | None = None
    formal_diagnosis_run_dir: Path | None = None
    valve_context_file: Path | None = None
    features: dict[str, bool] = field(default_factory=dict)
    presentation: dict[str, Any] = field(default_factory=dict)
    report_layout: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    migration_notices: tuple[str, ...] = ()


def _required_text(section: dict[str, Any], key: str, label: str) -> str:
    value = str(section.get(key, "")).strip()
    if not value:
        raise ConfigError(f"Missing required {label}.{key}")
    return value


def _inside_project(project_dir: Path, value: str, label: str) -> Path:
    candidate = (project_dir / value).resolve()
    try:
        candidate.relative_to(project_dir)
    except ValueError as exc:
        raise ConfigError(f"{label} resolves outside project: {value}") from exc
    return candidate


def _optional_project_path(project_dir: Path, value: Any, label: str) -> Path | None:
    text = str(value or "").strip()
    if not text:
        return None
    return _inside_project(project_dir, text, label)


def load_project(
    project_dir: Path,
    *,
    report_period: str | None = None,
) -> ProjectConfig:
    project_dir = project_dir.resolve()
    config_path = project_dir / "project.toml"
    if not config_path.is_file():
        raise ConfigError(f"Missing project.toml: {config_path}")
    try:
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Invalid project.toml: {exc}") from exc

    project = data.get("project") or {}
    paths = data.get("paths") or {}
    features = data.get("features") or {}
    presentation = data.get("presentation") or {}
    report_layout = data.get("report_layout") or {}
    report_periods = data.get("report_periods") or {}
    report_period_overrides = data.get("report_period_overrides") or {}
    if not all(
        isinstance(section, dict)
        for section in (
            project,
            paths,
            features,
            presentation,
            report_layout,
            report_periods,
            report_period_overrides,
        )
    ):
        raise ConfigError(
            "project, paths, features, presentation, report_layout, report_periods, "
            "and report_period_overrides must be TOML tables"
        )

    customer = _required_text(project, "customer", "project")
    report_title = _required_text(project, "report_title", "project")
    report_date = _required_text(project, "report_date", "project")
    active_period = ""
    report_period_label = ""
    if report_periods:
        active_period = str(report_period or report_periods.get("active") or "").strip()
        if not active_period:
            raise ConfigError("Missing required report_periods.active")
        report_period_label = _required_text(
            report_periods,
            f"{active_period}_label",
            "report_periods",
        )
    elif report_period:
        raise ConfigError(f"Missing required report_periods.{report_period}_label")

    period_override: dict[str, Any] = {}
    if active_period:
        raw_period_override = report_period_overrides.get(active_period) or {}
        if not isinstance(raw_period_override, dict):
            raise ConfigError(
                f"report_period_overrides.{active_period} must be a TOML table"
            )
        period_override = dict(raw_period_override)
    if "report_date" in period_override:
        report_date = _required_text(
            period_override,
            "report_date",
            f"report_period_overrides.{active_period}",
        )
    period_path_keys = {
        "work",
        "manual_interpretations",
        "formal_diagnosis_run",
        "diagnostic_summary_overrides",
        "report_date",
        "features",
        "presentation",
    }
    period_features = period_override.get("features") or {}
    if not isinstance(period_features, dict):
        raise ConfigError(
            f"report_period_overrides.{active_period}.features must be a TOML table"
        )
    effective_features = dict(DEFAULT_FEATURES)
    effective_features.update(features)
    effective_features.update(period_features)
    migration_notices: list[str] = []
    explicitly_configured_feature_keys = set(features) | set(period_features)
    if (
        "include_accessory_pages" in explicitly_configured_feature_keys
        and "include_air_piping_pages" not in explicitly_configured_feature_keys
    ):
        # The historical switch covered both ordinary accessory and air-piping
        # pages.  Treating it as the new air-piping switch would silently insert
        # pages that an old project never classified independently.
        effective_features["include_air_piping_pages"] = False
        migration_notices.append(AIR_PIPING_MIGRATION_NOTICE)
    effective_presentation = dict(DEFAULT_PRESENTATION)
    effective_presentation.update(presentation)
    period_presentation = period_override.get("presentation", {})
    if not isinstance(period_presentation, dict):
        raise ConfigError(f"report_period_overrides.{active_period}.presentation must be a TOML table")
    effective_presentation.update(period_presentation)
    profile = str(effective_presentation.get("profile") or "business_dashboard").strip()
    if profile == "standard":
        raise ConfigError("presentation profile 'standard' has been retired; use 'business_dashboard'")
    if profile != "business_dashboard":
        raise ConfigError(f"unknown presentation profile: {profile}")
    effective_report_layout = dict(report_layout)
    for key, value in period_override.items():
        if key not in period_path_keys:
            effective_report_layout[str(key)] = value
    if "toc_append_label" in effective_report_layout:
        raise ConfigError("report_layout.toc_append_label belongs to the retired standard renderer; remove it")

    raw_dir = _inside_project(project_dir, str(paths.get("raw", "raw")), "paths.raw")
    specs_dir = _inside_project(project_dir, str(paths.get("specs", "specs")), "paths.specs")
    inbound_dir = _inside_project(project_dir, str(paths.get("inbound", "inbound")), "paths.inbound")
    work_dir = _inside_project(
        project_dir,
        str(period_override.get("work", paths.get("work", "work"))),
        (
            f"report_period_overrides.{active_period}.work"
            if "work" in period_override
            else "paths.work"
        ),
    )
    output_dir = _inside_project(project_dir, str(paths.get("output", "output")), "paths.output")
    output_docx_name = _required_text(paths, "output_docx", "paths")
    output_pdf_name = str(paths.get("output_pdf", Path(output_docx_name).with_suffix(".pdf"))).strip()
    if Path(output_docx_name).name != output_docx_name:
        raise ConfigError("paths.output_docx must be a file name")
    if Path(output_pdf_name).name != output_pdf_name:
        raise ConfigError("paths.output_pdf must be a file name")

    catalog_file = _optional_project_path(project_dir, paths.get("catalog"), "paths.catalog")
    manual_file = _optional_project_path(
        project_dir,
        period_override.get(
            "manual_interpretations",
            paths.get("manual_interpretations", "work/manual_curve_interpretations.json"),
        ),
        (
            f"report_period_overrides.{active_period}.manual_interpretations"
            if "manual_interpretations" in period_override
            else "paths.manual_interpretations"
        ),
    )
    diagnostic_summary_file = _optional_project_path(
        project_dir,
        period_override.get(
            "diagnostic_summary_overrides",
            paths.get("diagnostic_summary_overrides"),
        ),
        (
            f"report_period_overrides.{active_period}.diagnostic_summary_overrides"
            if "diagnostic_summary_overrides" in period_override
            else "paths.diagnostic_summary_overrides"
        ),
    )
    formal_diagnosis_run_dir = _optional_project_path(
        project_dir,
        period_override.get(
            "formal_diagnosis_run",
            paths.get("formal_diagnosis_run"),
        ),
        (
            f"report_period_overrides.{active_period}.formal_diagnosis_run"
            if "formal_diagnosis_run" in period_override
            else "paths.formal_diagnosis_run"
        ),
    )
    valve_context_file = _optional_project_path(
        project_dir,
        paths.get("valve_context"),
        "paths.valve_context",
    )

    return ProjectConfig(
        project_dir=project_dir,
        config_path=config_path,
        customer=customer,
        report_title=report_title,
        report_date=report_date,
        report_period_key=active_period,
        report_period_label=report_period_label,
        raw_dir=raw_dir,
        specs_dir=specs_dir,
        inbound_dir=inbound_dir,
        work_dir=work_dir,
        output_dir=output_dir,
        output_docx=output_dir / output_docx_name,
        output_pdf=output_dir / output_pdf_name,
        catalog_file=catalog_file,
        manual_interpretations_file=manual_file,
        diagnostic_summary_overrides_file=diagnostic_summary_file,
        formal_diagnosis_run_dir=formal_diagnosis_run_dir,
        valve_context_file=valve_context_file,
        features={str(key): bool(value) for key, value in effective_features.items()},
        presentation={
            str(key): value for key, value in effective_presentation.items()
        },
        report_layout=effective_report_layout,
        metadata={key: value for key, value in project.items() if key not in {"customer", "report_title", "report_date"}},
        migration_notices=tuple(migration_notices),
    )
