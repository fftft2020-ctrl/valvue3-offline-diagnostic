from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import business_report, generator
from .config import ProjectConfig, load_project
from .distribution import runtime_identity
from .document_ocr_cache import prepare_project_pdf_cache, load_specification_pages
from .curve_identity import curve_image_identity, curve_image_key
from .evidence_extraction import extract_project_evidence
from .formal_diagnosis import (
    FormalDiagnosis,
    FormalDiagnosisError,
    load_formal_diagnosis,
)
from .format_revision import (
    compare_format_only_content,
    format_revision_manifest_path,
)
from .project_materials import (
    approve_material_archive,
    approve_parameter_extraction,
    material_archive_report_paths,
    material_archive_web_report_path,
    require_material_archive_approval,
    require_parameter_extraction_approval,
    write_parameter_extraction_report,
    write_material_readiness,
)
from .visual_review import CurveReviewReadiness, curve_review_readiness
from .wps_delivery import inspect_wps_pdf_layout
from .unit_reports import validate_unit_report_pdf
from .review_workflow import (
    _atomic_write_json,
    ReportArtifact,
    ReviewImport,
    allocate_report_artifact,
    append_run_history,
    finalize_human_review_document,
    ingest_review_return,
    latest_review_import,
)
from validate_valvue3_report import check_required_information


SKILL_DIR = Path(__file__).resolve().parents[2]
VALIDATOR_PATH = SKILL_DIR / "scripts/validate_valvue3_report.py"


@dataclass(frozen=True)
class PipelineResult:
    config: ProjectConfig
    manifest_path: Path
    status: str
    missing_image_keys: tuple[str, ...] = ()
    report_path: Path | None = None
    web_report_path: Path | None = None


def _configured_only_valves(config: ProjectConfig) -> tuple[str, ...]:
    return tuple(
        str(tag).strip()
        for tag in (config.report_layout.get("only_valves") or [])
        if str(tag).strip()
    )


def _select_report_valves(
    config: ProjectConfig,
    valves: list[generator.ValveReport],
    *,
    include_data_insufficient: bool | None = None,
) -> list[generator.ValveReport]:
    return generator.select_report_valves(
        valves,
        include_data_insufficient=(
            config.features.get("include_data_insufficient_valves", True)
            if include_data_insufficient is None
            else include_data_insufficient
        ),
        only_valves=_configured_only_valves(config),
        append_valves=tuple(
            str(tag).strip()
            for tag in (config.report_layout.get("append_valves") or [])
            if str(tag).strip()
        ),
    )


def _render_prepared_report(
    config: ProjectConfig, valves: list[generator.ValveReport]
) -> None:
    presentation = config.presentation
    profile = str(presentation.get("profile") or "business_dashboard").strip()
    if profile == "standard":
        raise ValueError(
            "presentation profile 'standard' has been retired; use 'business_dashboard'"
        )
    if profile != "business_dashboard":
        raise ValueError(f"unknown presentation profile: {profile}")

    data_only = bool(presentation.get("data_only", False))
    if "overall_conclusion_override" in presentation and not str(
        presentation["overall_conclusion_override"]
    ).strip():
        raise ValueError("overall_conclusion_override must be non-empty text")
    if "recommendation_overrides" in presentation:
        configured = presentation["recommendation_overrides"]
        if not isinstance(configured, list) or not configured or any(
            not isinstance(item, str) or not item.strip() for item in configured
        ):
            raise ValueError(
                "recommendation_overrides must be a non-empty list of text"
            )
    rating_conclusions = (
        {}
        if data_only
        else {valve.folder: valve.rating_conclusion_override for valve in valves}
    )
    counts = Counter() if data_only else Counter(int(valve.rating) for valve in valves)
    overall_conclusion = (
        ""
        if data_only
        else str(presentation.get("overall_conclusion_override") or "")
        or (
            f"本批共诊断{len(valves)}台阀门："
            + "、".join(f"{grade}级{counts[grade]}台" for grade in range(1, 6))
            + "。各阀评级依据、主要问题及处置要求详见优先处理清单和对应单阀章节。"
        )
    )
    configured_recommendations = presentation.get("recommendation_overrides")
    if isinstance(configured_recommendations, list):
        recommendations = [str(item) for item in configured_recommendations]
    elif data_only:
        recommendations = []
    else:
        recommendations = [
            "按优先处理清单及对应单阀章节落实排查、维护和复测要求；"
            "每项措施的适用位号与实施条件以该阀建议为准。"
        ]
    optional: dict[str, Any] = {}
    for key in (
        "include_field_context_overview",
        "include_overview_judgement",
        "include_overview_priority",
        "include_retest_recommendations",
        "omit_retest_recommendations",
    ):
        if key in presentation:
            optional[key] = bool(presentation[key])
    for key in ("term_replacements", "recommendations_heading"):
        if key in presentation:
            optional[key] = presentation[key]

    business_report.generate_business_dashboard_docx(
        valves,
        output_path=config.output_docx,
        project_name=config.customer,
        report_name=config.report_title,
        report_date=config.report_date,
        unit_name=str(config.metadata.get("unit_name") or config.customer),
        rating_conclusions=rating_conclusions,
        overall_conclusion=overall_conclusion,
        recommendations=recommendations,
        report_period_label=str(presentation.get("report_period_label") or ""),
        review_label=str(presentation.get("review_label") or ""),
        data_only=data_only,
        blank_ratings=bool(presentation.get("blank_ratings", False)),
        blank_conclusions=bool(presentation.get("blank_conclusions", False)),
        blank_actions=bool(presentation.get("blank_actions", False)),
        include_specifications=config.features.get("include_specifications", True),
        include_air_piping_pages=config.features.get("include_air_piping_pages", True),
        include_accessory_pages=config.features.get("include_accessory_pages", False),
        **optional,
    )


def convert_docx_to_pdf(config: ProjectConfig) -> Path:
    """Render an internal LibreOffice check PDF outside the delivery directory."""
    if not config.output_docx.is_file():
        raise RuntimeError(f"DOCX output is missing: {config.output_docx}")
    libreoffice = shutil.which("libreoffice") or shutil.which("soffice")
    if not libreoffice:
        raise RuntimeError("LibreOffice is required for PDF conversion")
    check_dir = config.work_dir / "pdf-checks"
    check_dir.mkdir(parents=True, exist_ok=True)
    converted = check_dir / config.output_docx.with_suffix(".pdf").name
    if converted.exists():
        converted.unlink()
    result = subprocess.run(
        [
            libreoffice,
            "--headless",
            "--convert-to",
            "pdf",
            "--outdir",
            str(check_dir),
            str(config.output_docx),
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0 or not converted.is_file():
        detail = result.stderr.strip() or result.stdout.strip() or "no converted PDF was created"
        raise RuntimeError(f"LibreOffice PDF conversion failed: {detail}")
    check_pdf = check_dir / f"{config.output_docx.stem}.libreoffice-check.pdf"
    if check_pdf.exists():
        check_pdf.unlink()
    converted.replace(check_pdf)
    return check_pdf


def validate_report(
    config: ProjectConfig,
    *,
    require_pdf: bool = True,
    require_finalized_curve_review: bool = False,
    pdf_path: Path | None = None,
    information_only: bool = True,
    raise_on_failure: bool = True,
) -> dict[str, Any]:
    summary_path = config.work_dir / "analysis_summary.json"
    command = [
        sys.executable,
        str(VALIDATOR_PATH),
        "--project",
        str(config.project_dir),
        "--docx",
        str(config.output_docx),
        "--summary",
        str(summary_path),
        "--min-figure-interpretations",
        "1",
        "--json",
    ]
    if config.report_period_key:
        command.extend(["--report-period", config.report_period_key])
    if require_pdf:
        command.extend(["--pdf", str(pdf_path or config.output_pdf)])
    if require_finalized_curve_review:
        command.append("--require-finalized-curve-review")
    if not config.features.get("include_specifications", True):
        command.append("--allow-no-spec-match")
    if information_only:
        # Keep the exact period and in-memory report variant used by generation.
        validation = check_required_information(
            config.project_dir,
            config.output_docx,
            summary_path,
            pdf=(pdf_path or config.output_pdf) if require_pdf else None,
            config=config,
            report_period=config.report_period_key or None,
        )
        returncode, stderr = 0, ""
    else:
        result = subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        try:
            validation = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            detail = result.stderr.strip() or result.stdout.strip() or "validator returned no JSON"
            raise RuntimeError(f"Report validation failed: {detail}") from exc
        returncode, stderr = result.returncode, result.stderr
    validation_path = config.work_dir / "validation.json"
    validation_path.parent.mkdir(parents=True, exist_ok=True)
    validation_path.write_text(json.dumps(validation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if raise_on_failure and (returncode != 0 or not validation.get("ok")):
        failures = "; ".join(validation.get("failures", [])) or stderr.strip()
        if information_only:
            failures = (
                "; ".join(validation.get("summary_issues", []))
                or "; ".join(validation.get("issues", []))
                or failures
            )
        raise RuntimeError(f"Report validation failed: {failures}")
    return validation


def check_report_information(
    project_dir: Path,
    *,
    docx: Path,
    pdf: Path | None = None,
    report_period: str | None = None,
) -> PipelineResult:
    """Run the single compact acceptance check for customer-required content."""
    config = load_project(project_dir, report_period=report_period)
    docx = docx.resolve()
    pdf = pdf.resolve() if pdf is not None else None
    variant = replace(
        config,
        output_dir=docx.parent,
        output_docx=docx,
        output_pdf=pdf or config.output_pdf,
    )
    validation = validate_report(
        variant,
        require_pdf=pdf is not None,
        require_finalized_curve_review=False,
        pdf_path=pdf,
        information_only=True,
        raise_on_failure=False,
    )
    acceptance_dir = config.work_dir / "information-acceptance"
    acceptance_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = acceptance_dir / f"{docx.stem}.json"
    manifest_path.write_text(
        json.dumps(validation, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return PipelineResult(
        config=variant,
        manifest_path=manifest_path,
        status=(
            "information_complete"
            if validation.get("ok")
            else "information_missing"
        ),
        report_path=docx,
    )


def _sha256(path: Path | None) -> str:
    if path is None or not path.is_file():
        return ""
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=SKILL_DIR,
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def _git_dirty() -> bool | None:
    try:
        return bool(subprocess.run(
            ["git", "status", "--porcelain", "--", "."],
            cwd=SKILL_DIR,
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip())
    except (OSError, subprocess.CalledProcessError):
        return None


def _tree_sha256(root: Path, files: list[Path] | None = None) -> str:
    selected = files if files is not None else sorted(
        path for path in root.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    )
    digest = hashlib.sha256()
    for path in selected:
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(_sha256(path).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _input_summary(raw_dir: Path) -> dict[str, Any]:
    files = sorted(path for path in raw_dir.rglob("*") if path.is_file()) if raw_dir.is_dir() else []
    return {
        "file_count": len(files),
        "total_bytes": sum(path.stat().st_size for path in files),
        "extensions": sorted({path.suffix.lower() for path in files}),
        "tree_sha256": _tree_sha256(raw_dir, files),
    }


def _write_manifest(
    config: ProjectConfig,
    *,
    status: str,
    validation: dict[str, Any] | None = None,
    libreoffice_check_pdf: Path | None = None,
    delivery_pdf: Path | None = None,
) -> Path:
    manifest_path = config.work_dir / "run_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": status,
        "engine": {
            "skill": "valvue3-offline-diagnostic",
            "release": runtime_identity(),
            "git_commit": _git_commit(),
            "git_dirty": _git_dirty(),
            "tree_sha256": _tree_sha256(SKILL_DIR),
        },
        "project": {
            "customer": config.customer,
            "config": str(config.config_path),
            "config_sha256": _sha256(config.config_path),
        },
        "configuration": {
            "features": dict(config.features),
            "presentation": dict(config.presentation),
            "migration_notices": list(config.migration_notices),
        },
        "inputs": _input_summary(config.raw_dir),
        "outputs": {
            "docx": str(config.output_docx),
            "docx_sha256": _sha256(config.output_docx),
            "pdf": str(delivery_pdf) if delivery_pdf else "",
            "pdf_sha256": _sha256(delivery_pdf),
            "delivery_pdf": str(delivery_pdf) if delivery_pdf else "",
            "delivery_pdf_sha256": _sha256(delivery_pdf),
            "libreoffice_check_pdf": (
                str(libreoffice_check_pdf)
                if libreoffice_check_pdf
                else ""
            ),
            "libreoffice_check_pdf_sha256": _sha256(
                libreoffice_check_pdf
            ),
        },
        "validation": validation or {},
    }
    if (
        config.formal_diagnosis_run_dir is not None
        and not config.presentation.get("data_only")
    ):
        manifest["formal_diagnosis"] = {
            "run_dir": str(config.formal_diagnosis_run_dir),
            "tree_sha256": _tree_sha256(config.formal_diagnosis_run_dir),
            "project_manifest_sha256": _sha256(
                config.formal_diagnosis_run_dir / "00_project_run_manifest.json"
            ),
        }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest_path


_curve_image_identity = curve_image_identity
_curve_image_key = curve_image_key


def _related_metrics(metrics: dict[str, Any], test_key: str) -> dict[str, Any]:
    if test_key in {"run_step", "pattern_step"}:
        return {
            key: metrics.get(key)
            for key in ("Average Error", "T86", "T63", "Td", "Overshoot", "step_count")
        }
    if test_key == "run_ramp":
        run_ramp = metrics.get("run_ramp")
        if isinstance(run_ramp, dict):
            return {
                "run_ramp": {
                    family: {
                        statistic: values.get(statistic)
                        for statistic in ("max", "average", "min")
                    }
                    for family in ("hysteresis", "dead_band", "linearity")
                    if isinstance((values := run_ramp.get(family)), dict)
                }
            }
        # Compatibility for analysis snapshots created before Run Ramp was
        # split into its three independent measurement families.
        return {
            key: metrics.get(key)
            for key in ("Maximum Hysteresis + Dead Band", "Average Hysteresis + Dead Band", "Average Error")
        }
    if test_key in {"standard_signature", "extended_signature"}:
        return {
            key: metrics.get(key)
            for key in ("Max Friction", "Min Friction", "Mean Friction", "Response Time Open", "Response Time Close", "Supply Pressure")
        }
    return {"test_types": metrics.get("test_types"), "step_count": metrics.get("step_count")}


def prepare_project_materials(
    project_dir: Path,
    *,
    report_period: str | None = None,
) -> PipelineResult:
    """Create the frozen Stage 1 archive report before parameter extraction."""
    config = load_project(project_dir, report_period=report_period)
    readiness = write_material_readiness(config, freeze_stage_report=True)
    stage_json, stage_markdown = material_archive_report_paths(config)
    stage_html = material_archive_web_report_path(config)
    return PipelineResult(
        config=config,
        manifest_path=stage_json,
        report_path=stage_markdown,
        web_report_path=stage_html,
        status=readiness.status,
    )


def approve_project_materials(
    project_dir: Path,
    *,
    approval_text: str,
    unchanged_reuse_policy: str | None = None,
    report_period: str | None = None,
) -> PipelineResult:
    """Record the user's explicit approval of the frozen Stage 1 report."""
    config = load_project(project_dir, report_period=report_period)
    approval_path = approve_material_archive(
        config,
        approval_text,
        unchanged_reuse_policy=unchanged_reuse_policy,
    )
    append_run_history(
        config.project_dir,
        {
            "event": "material_archive_approved",
            "approval": str(approval_path.relative_to(config.project_dir)),
        },
    )
    return PipelineResult(
        config=config,
        manifest_path=approval_path,
        status="materials_approved",
    )


def approve_project_parameters(
    project_dir: Path,
    *,
    approval_text: str,
    report_period: str | None = None,
) -> PipelineResult:
    """Record explicit user approval of the Stage 2 parameter snapshot."""
    config = load_project(project_dir, report_period=report_period)
    require_material_archive_approval(config)
    # The Stage 2 implementation also checks the current Stage 1 approval and
    # the report/evidence fingerprints.  Keeping that binding in
    # project_materials.py prevents the CLI from creating an unbound approval.
    approval_path = approve_parameter_extraction(
        config,
        approval_text=approval_text,
    )
    append_run_history(
        config.project_dir,
        {
            "event": "parameter_extraction_approved",
            "approval": str(approval_path.relative_to(config.project_dir)),
        },
    )
    return PipelineResult(
        config=config,
        manifest_path=approval_path,
        status="parameters_approved",
    )


def require_project_materials_approval(
    project_dir: Path,
    *,
    report_period: str | None = None,
) -> dict[str, Any]:
    config = load_project(project_dir, report_period=report_period)
    return require_material_archive_approval(config)


def require_project_parameters_approval(
    project_dir: Path,
    *,
    report_period: str | None = None,
) -> dict[str, Any]:
    config = load_project(project_dir, report_period=report_period)
    return require_parameter_extraction_approval(config)


def _evidence_preparation_config(
    config: ProjectConfig, evidence_work_dir: Path | None,
) -> ProjectConfig:
    if evidence_work_dir is None:
        return config
    destination = evidence_work_dir.resolve()
    work_root = (config.project_dir / "work").resolve()
    if not destination.is_relative_to(work_root) or destination in (work_root, config.work_dir.resolve()):
        raise ValueError("evidence-work-dir must be a separate directory inside project/work")
    if (destination / "analysis_summary.json").exists():
        raise ValueError("versioned evidence already exists; choose a new evidence-work-dir")
    return replace(config, work_dir=destination)


def prepare_curve_review(
    project_dir: Path,
    *,
    report_period: str | None = None,
    evidence_work_dir: Path | None = None,
) -> PipelineResult:
    """Render curve crops from raw inputs and write a review-only manifest.

    This deliberately stops before report generation, PDF conversion, validation, or
    loading manual curve interpretations.
    """
    config = _evidence_preparation_config(
        load_project(project_dir, report_period=report_period), evidence_work_dir)
    generator.configure(config, load_formal_structured_evidence=False,
                        load_parameter_structured_evidence=False, load_summary_overrides=False)
    valves = _select_report_valves(
        config,
        generator.collect_curve_review_valves(render_assets=False),
        include_data_insufficient=True,
    )
    for valve in valves:
        generator.render_curve_review_assets(valve)
    images: list[dict[str, Any]] = []
    image_identities: dict[str, str] = {}
    for valve in valves:
        valve_images = sorted(
            valve.images,
            key=lambda item: (
                str(item.get("test_key") or "other"),
                str(item.get("source") or ""),
                str(item.get("page") or ""),
                str(item.get("bbox") or ""),
            ),
        )
        for image in valve_images:
            test_key = str(image.get("test_key") or "other")
            image_path = Path(str(image.get("path") or "")).resolve()
            image_key = _curve_image_key(valve.folder, image)
            identity = _curve_image_identity(valve.folder, image)
            if image_key in image_identities:
                raise RuntimeError(
                    f"duplicate curve review image identity for {image_key}: "
                    f"{image_identities[image_key]!r} and {identity!r}"
                )
            image_identities[image_key] = identity
            images.append(
                {
                    "image_key": image_key,
                    # The project folder is the report-facing valve identifier and
                    # remains unique when device-internal tags omit the unit prefix.
                    "valve_tag": str(valve.folder),
                    "image_path": str(image_path),
                    "test_key": test_key,
                    "related_metrics": _related_metrics(valve.metrics, test_key),
                }
            )
    manifest_path = config.work_dir / "curve_review_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                "status": "curve_review_required",
                "images": images,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return PipelineResult(config=config, manifest_path=manifest_path, status="curve_review_required")


def prepare_formal_evidence(
    project_dir: Path,
    *,
    report_period: str | None = None,
    evidence_work_dir: Path | None = None,
) -> PipelineResult:
    """Build the period-filtered analysis snapshot used by formal diagnosis runs."""
    config = _evidence_preparation_config(
        load_project(project_dir, report_period=report_period), evidence_work_dir)
    spec_page_text_cache = load_specification_pages(config.project_dir, config.specs_dir)
    generator.configure(
        config,
        spec_page_text_cache=spec_page_text_cache,
        # This stage reconstructs evidence from raw/extracted project inputs.
        # The configured formal run is an output binding for report generation,
        # never an input to a fresh evidence snapshot.
        load_formal_structured_evidence=False,
        load_parameter_structured_evidence=False,
        load_summary_overrides=False,
    )
    only_valves = set(_configured_only_valves(config))
    all_valves = generator.prepare_valve_data(
    )
    report_valves = _select_report_valves(config, all_valves)
    generator.write_summary(
        all_valves,
        included_folders={valve.folder for valve in report_valves},
        structured_evidence_project_root=config.project_dir,
        report_field_manifest_builder=business_report.report_field_manifest,
        include_diagnosis=False,
    )
    summary_path = config.work_dir / "analysis_summary.json"
    (config.work_dir / "analysis_summary.source.json").write_text(
        json.dumps({"schema_version": 1, "purpose": "formal_evidence_preparation",
                    "project_root": str(config.project_dir.resolve()),
                    "report_period_key": config.report_period_key,
                    "summary_sha256": hashlib.sha256(summary_path.read_bytes()).hexdigest()},
                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return PipelineResult(
        config=config,
        manifest_path=summary_path,
        status="formal_evidence_prepared",
    )


def prepare_project_ocr(
    project_dir: Path,
    *,
    report_period: str | None = None,
    retry_failed_ocr: bool = False,
) -> PipelineResult:
    """Prepare the project-local PDF text/OCR cache without generating a report."""
    config = load_project(project_dir, report_period=report_period)
    cache_options = {"retry_failed_ocr": True} if retry_failed_ocr else {}
    stats = prepare_project_pdf_cache(config.project_dir, **cache_options)
    manifest_path = config.project_dir / "work" / "ocr" / "manifest.json"
    if not manifest_path.is_file():
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps({"schema_version": 1, "documents": {}, "stats": stats}, ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )
    append_run_history(config.project_dir, {"event": "ocr_prepared", **stats})
    return PipelineResult(config=config, manifest_path=manifest_path, status="ocr_prepared")


def extract_project_materials(
    project_dir: Path,
    *,
    report_period: str | None = None,
    retry_failed_ocr: bool = False,
) -> PipelineResult:
    """Extract fresh structured project evidence without diagnosing valves."""
    config = load_project(project_dir, report_period=report_period)
    cache_options = {"retry_failed_ocr": True} if retry_failed_ocr else {}
    stats = prepare_project_pdf_cache(config.project_dir, **cache_options)
    spec_page_text_cache = load_specification_pages(config.project_dir, config.specs_dir)

    from . import generator

    generator.configure(
        config,
        spec_page_text_cache=spec_page_text_cache,
        load_parameter_structured_evidence=False,
        load_formal_structured_evidence=False,
        load_summary_overrides=False,
    )
    valves = generator.collect_structured_evidence()
    extraction = extract_project_evidence(config, valves, ocr_stats=stats)
    # The evidence extractor is the machine/source layer; the Stage 2 gate
    # snapshot is produced from that same evidence in one pass so the user
    # approves exactly what downstream diagnosis will consume.
    parameter_report = write_parameter_extraction_report(config)
    write_material_readiness(config)
    append_run_history(
        config.project_dir,
        {
            "event": "materials_extracted",
            "valve_count": len(valves),
            **stats,
        },
    )
    return PipelineResult(
        config=config,
        # Preserve the established evidence-extraction CLI contract while the
        # Stage 2 snapshot is additionally written under stage-gates.  The
        # caller prints the new snapshot paths explicitly below.
        manifest_path=extraction.index_path,
        report_path=extraction.markdown_path,
        # Keep the historical CLI status for compatibility with existing
        # evidence-extraction consumers; the returned paths now point at the
        # evidence layer, while parameter_report is the approval snapshot.
        status=extraction.status,
    )


def validate_wps_delivery(
    project_dir: Path,
    *,
    docx: Path,
    wps_pdf: Path,
    approve_visual: bool = False,
    visual_review_note: str = "",
    report_period: str | None = None,
    format_only_source: Path | None = None,
) -> PipelineResult:
    """Validate a WPS-exported PDF before registering it as a delivery file."""
    config = load_project(project_dir, report_period=report_period)
    docx = docx.resolve()
    wps_pdf = wps_pdf.resolve()
    for path, label in ((docx, "DOCX"), (wps_pdf, "WPS PDF")):
        try:
            path.relative_to(config.project_dir)
        except ValueError as exc:
            raise RuntimeError(f"{label} must stay inside the project: {path}") from exc
    expected_pdf = docx.with_suffix(".pdf")
    if wps_pdf != expected_pdf:
        raise RuntimeError(
            "WPS delivery PDF must use the same stem and directory "
            f"as its DOCX: {expected_pdf}"
        )

    validation_dir = config.project_dir / "work" / "wps-validation" / docx.stem
    manifest_path = validation_dir / "delivery_manifest.json"
    if manifest_path.is_file():
        previous = manifest_path.read_bytes()
        history_path = validation_dir / "history" / f"{hashlib.sha256(previous).hexdigest()}.json"
        history_path.parent.mkdir(parents=True, exist_ok=True)
        if not history_path.exists():
            history_path.write_bytes(previous)
    payload: dict[str, Any] = {
        "schema_version": 1,
        "status": "wps_delivery_validation_in_progress",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "report_period": config.report_period_key,
        "inputs": {"docx": str(docx), "pdf": str(wps_pdf)},
        "automatic_layout": {},
        "content_validation": {},
        "format_only_validation": None,
        "visual_review": {"approved": False, "note": ""},
        "delivery": {},
    }
    # Revoke the current success marker before any check can raise or be interrupted.
    # Earlier manifests and their contact sheets remain available as historical evidence.
    _atomic_write_json(manifest_path, payload)
    variant = replace(config, output_dir=docx.parent, output_docx=docx, output_pdf=wps_pdf)
    try:
        for path, label in ((docx, "DOCX"), (wps_pdf, "WPS PDF")):
            if not path.is_file():
                raise RuntimeError(f"{label} is missing: {path}")
        inputs = {
            "docx": str(docx),
            "docx_sha256": _sha256(docx),
            "pdf": str(wps_pdf),
            "pdf_sha256": _sha256(wps_pdf),
        }
        payload["inputs"] = inputs
        _atomic_write_json(manifest_path, payload)
        prepared_manifest_path = format_revision_manifest_path(config.project_dir, docx)
        prepared_manifest: dict[str, Any] = {}
        if prepared_manifest_path.is_file():
            prepared_manifest = json.loads(prepared_manifest_path.read_text(encoding="utf-8"))
            prepared_source = Path(str(prepared_manifest.get("parent_docx") or ""))
            if Path(str(prepared_manifest.get("candidate_docx") or "")).resolve() != docx:
                raise RuntimeError("format-only candidate does not match the prepared revision")
            if format_only_source is None:
                format_only_source = prepared_source
            elif format_only_source.resolve() != prepared_source.resolve():
                raise RuntimeError("format-only source does not match the prepared revision parent")

        if format_only_source is not None:
            format_only_source = format_only_source.resolve()
            try:
                format_only_source.relative_to(config.project_dir)
            except ValueError as exc:
                raise RuntimeError(
                    f"format-only source must stay inside the project: {format_only_source}"
                ) from exc
            if prepared_manifest and (
                not prepared_manifest.get("parent_sha256")
                or not format_only_source.is_file()
                or _sha256(format_only_source) != prepared_manifest["parent_sha256"]
            ):
                raise RuntimeError("format-only parent changed after revision preparation")
            comparison = compare_format_only_content(format_only_source, docx)
            payload["format_only_validation"] = comparison
            if not comparison["ok"]:
                payload["status"] = "format_only_content_mismatch"
                raise RuntimeError("format-only content mismatch: " + ", ".join(comparison["differences"]))

        note = visual_review_note.strip()
        if approve_visual and not note:
            raise RuntimeError("WPS visual review note is required for delivery approval")
        # A new attempt must never replace contact sheets referenced by an older manifest.
        contact_sheet = Path(tempfile.mkdtemp(prefix="check-", dir=validation_dir)) / "contact-sheet.png"
        automatic = inspect_wps_pdf_layout(wps_pdf, contact_sheet=contact_sheet)
        payload["automatic_layout"] = automatic
        content_validation = validate_unit_report_pdf(config.project_dir, docx, wps_pdf)
        if content_validation is None:
            content_validation = validate_report(
                variant,
                require_pdf=True,
                require_finalized_curve_review=True,
                pdf_path=wps_pdf,
                raise_on_failure=False,
            )
        payload["content_validation"] = content_validation
        if not automatic["ok"] or not content_validation.get("ok"):
            failures = list(automatic.get("failures") or [])
            failures.extend(
                content_validation.get("summary_issues")
                or content_validation.get("failures")
                or content_validation.get("issues")
                or []
            )
            raise RuntimeError("WPS delivery validation failed: " + "; ".join(map(str, failures)))
        if _sha256(docx) != inputs["docx_sha256"] or _sha256(wps_pdf) != inputs["pdf_sha256"]:
            raise RuntimeError("WPS delivery inputs changed during validation")
        status = "wps_delivery_validated" if approve_visual else "wps_layout_review_required"
        payload.update(
            status=status,
            visual_review={
                "approved": approve_visual,
                "note": note,
                "approved_at": datetime.now(timezone.utc).isoformat() if approve_visual else "",
            },
            delivery={**inputs, "pdf_source": "WPS 文字"} if approve_visual else {},
        )
        if approve_visual:
            append_run_history(
                config.project_dir,
                {
                    "event": "wps_delivery_validated",
                    "docx": str(docx.relative_to(config.project_dir)),
                    "docx_sha256": inputs["docx_sha256"],
                    "pdf": str(wps_pdf.relative_to(config.project_dir)),
                    "pdf_sha256": inputs["pdf_sha256"],
                    "layout_manifest": str(manifest_path.relative_to(config.project_dir)),
                    "format_only_source": (
                        str(format_only_source.relative_to(config.project_dir))
                        if format_only_source is not None else ""
                    ),
                },
            )
        _atomic_write_json(manifest_path, payload)
    except Exception as exc:
        if payload["status"] != "format_only_content_mismatch":
            payload["status"] = "wps_delivery_validation_failed"
        payload["error"] = str(exc)
        payload["visual_review"] = {"approved": False, "note": ""}
        payload["delivery"] = {}
        _atomic_write_json(manifest_path, payload)
        raise
    return PipelineResult(config=variant, manifest_path=manifest_path, status=status)


def prepare_format_only_revision(
    project_dir: Path,
    *,
    source_docx: Path,
    report_period: str | None = None,
) -> PipelineResult:
    """Copy a confirmed DOCX into a new version without regenerating content."""
    base_config = load_project(project_dir, report_period=report_period)
    source_docx = source_docx.resolve()
    if not source_docx.is_file() or source_docx.suffix.lower() != ".docx":
        raise RuntimeError(
            f"format-only source DOCX is missing or invalid: {source_docx}"
        )
    try:
        relative_source = source_docx.relative_to(
            base_config.project_dir / "output"
        )
    except ValueError as exc:
        raise RuntimeError(
            "format-only source must be an existing project output DOCX"
        ) from exc
    if len(relative_source.parts) < 2:
        if source_docx != base_config.output_docx.resolve():
            raise RuntimeError(
                "format-only root source must be the configured output DOCX"
            )
        kind = "business"
    else:
        kind = relative_source.parts[0]
    if kind not in {"review", "business", "data"}:
        raise RuntimeError(
            "format-only source must be under output/review, "
            "output/business, or output/data"
        )

    artifact = allocate_report_artifact(
        base_config.project_dir,
        kind,
        base_config.project_dir.name,
        period_label=base_config.report_period_label,
    )
    artifact.docx.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_docx, artifact.docx)
    comparison = compare_format_only_content(
        source_docx,
        artifact.docx,
    )
    if not comparison["ok"]:
        raise RuntimeError(
            "format-only staging changed confirmed report content"
        )
    manifest_path = format_revision_manifest_path(
        base_config.project_dir,
        artifact.docx,
    )
    payload = {
        "schema_version": 1,
        "status": "format_revision_prepared",
        "kind": kind,
        "version": artifact.version,
        "parent_docx": str(source_docx),
        "parent_sha256": _sha256(source_docx),
        "candidate_docx": str(artifact.docx.resolve()),
        "candidate_sha256": _sha256(artifact.docx),
        "content_identity": comparison,
        "allowed_changes": [
            "page and section layout",
            "paragraph pagination and spacing",
            "table row height, cell margin, and width",
            "font formatting without text changes",
        ],
        "forbidden_operations": [
            "report regeneration",
            "diagnosis or rating recalculation",
            "OCR or specification rematching",
            "curve, text, image, bookmark, or attachment changes",
        ],
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    append_run_history(
        base_config.project_dir,
        {
            "event": "format_revision_prepared",
            "version": artifact.version,
            "kind": kind,
            "parent_docx": str(source_docx.relative_to(base_config.project_dir)),
            "candidate_docx": str(
                artifact.docx.relative_to(base_config.project_dir)
            ),
            "content_identity_sha256": comparison["source_identity"][
                "semantic_sha256"
            ],
        },
    )
    variant = replace(
        base_config,
        output_dir=artifact.docx.parent,
        output_docx=artifact.docx,
        output_pdf=artifact.pdf,
    )
    return PipelineResult(
        config=variant,
        manifest_path=manifest_path,
        status="format_revision_prepared",
    )


def _archive_generated_report(
    config: ProjectConfig, artifact: ReportArtifact, result: PipelineResult,
) -> PipelineResult:
    if result.status == "curve_review_required":
        return result
    artifact.work_dir.mkdir(parents=True, exist_ok=True)
    snapshot_source = config.work_dir / "analysis_summary.json"
    snapshot_path = artifact.work_dir / "diagnostic_snapshot.json"
    if artifact.kind != "data" and snapshot_source.is_file():
        shutil.copy2(snapshot_source, snapshot_path)
    version_manifest = artifact.work_dir / "run_manifest.json"
    if result.manifest_path.is_file():
        shutil.copy2(result.manifest_path, version_manifest)
    event = f"{artifact.kind}_generated"
    append_run_history(
        config.project_dir,
        {
            "event": event,
            "version": artifact.version,
            **({"period": config.report_period_label} if artifact.kind == "data" else {}),
            "docx": str(artifact.docx.relative_to(config.project_dir)),
            "pdf": str(artifact.pdf.relative_to(config.project_dir)) if artifact.pdf.is_file() else "",
            **(
                {"snapshot_sha256": _sha256(snapshot_path)}
                if artifact.kind != "data"
                else {}
            ),
        },
    )
    return PipelineResult(config=config, manifest_path=version_manifest, status=event)


def generate_review_report(
    project_dir: Path,
    *,
    skip_pdf: bool = False,
    report_period: str | None = None,
) -> PipelineResult:
    """Generate the next non-overwriting formal-layout report for human review."""
    base_config = load_project(project_dir, report_period=report_period)
    artifact = allocate_report_artifact(
        base_config.project_dir,
        "review",
        base_config.project_dir.name,
        period_label=base_config.report_period_label,
    )
    variant = replace(
        base_config,
        output_dir=artifact.docx.parent,
        output_docx=artifact.docx,
        output_pdf=artifact.pdf,
        manual_interpretations_file=None,
        diagnostic_summary_overrides_file=None,
        presentation={
            **base_config.presentation,
            "profile": "business_dashboard",
            "review_label": "待人工校验版",
            "report_period_label": base_config.report_period_label,
            "blank_ratings": False,
            "blank_conclusions": bool(
                base_config.presentation.get("blank_conclusions", False)
            ),
            "blank_actions": False,
        },
    )
    result = run_project(
        base_config.project_dir,
        skip_pdf=skip_pdf,
        finalize_curve_review=True,
        config_override=variant,
    )
    return _archive_generated_report(variant, artifact, result)


def generate_data_report(
    project_dir: Path,
    *,
    skip_pdf: bool = False,
    report_period: str | None = None,
) -> PipelineResult:
    """Generate the next non-overwriting factual-data report."""
    base_config = load_project(project_dir, report_period=report_period)
    artifact = allocate_report_artifact(
        base_config.project_dir,
        "data",
        base_config.project_dir.name,
        period_label=base_config.report_period_label,
    )
    variant = replace(
        base_config,
        report_title="Masoneilan ValVue3 离线检测数据报告",
        output_dir=artifact.docx.parent,
        output_docx=artifact.docx,
        output_pdf=artifact.pdf,
        presentation={
            **base_config.presentation,
            "profile": "business_dashboard",
            "review_label": "纯数据版",
            "report_period_label": base_config.report_period_label,
            "data_only": True,
            "blank_ratings": True,
            "blank_conclusions": True,
            "blank_actions": True,
        },
    )
    result = run_project(
        base_config.project_dir,
        skip_pdf=skip_pdf,
        finalize_curve_review=False,
        config_override=variant,
    )
    return _archive_generated_report(variant, artifact, result)


def ingest_review_report(project_dir: Path, returned_docx: Path, *, review_base: Path | None = None, report_period: str | None = None) -> ReviewImport:
    config = load_project(project_dir, report_period=report_period)
    valve_tags = {
        path.name for path in config.raw_dir.iterdir()
        if path.is_dir()
    } if config.raw_dir.is_dir() else set()
    return ingest_review_return(
        config.project_dir,
        returned_docx,
        valve_tags=valve_tags,
        review_base=review_base,
        expected_binding=_current_review_binding(config),
        report_period=config.report_period_key,
        curve_manifest_path=config.work_dir / "curve_review_manifest.json",
        base_interpretations_path=config.work_dir / "codex_curve_interpretations.json",
    )


def _review_import_blocker(change_list: dict[str, Any]) -> str:
    unresolved = change_list.get("unresolved_changes")
    if isinstance(unresolved, list) and unresolved:
        return "unmapped human changes"
    comments = change_list.get("comments")
    if isinstance(comments, list) and comments:
        return "unresolved Word comments"
    return ""


def _current_review_binding(config: ProjectConfig) -> dict[str, str]:
    run_root = config.formal_diagnosis_run_dir
    if run_root is None:
        raise RuntimeError("human review is not bound to a configured formal diagnosis run")
    if json.loads((run_root / "00_project_run_manifest.json").read_text(encoding="utf-8")).get("stage") != "frozen":
        raise RuntimeError("review base requires a frozen formal diagnosis run")
    return {"tree_sha256": _tree_sha256(run_root),
            "project_manifest_sha256": _sha256(run_root / "00_project_run_manifest.json")}


def _validate_review_formal_binding(
    config: ProjectConfig,
    change_list: dict[str, Any],
) -> None:
    run_root = config.formal_diagnosis_run_dir
    binding = change_list.get("formal_diagnosis")
    if run_root is None or not isinstance(binding, dict):
        raise RuntimeError(
            "human review is not bound to the configured formal diagnosis run"
        )
    expected = {
        "tree_sha256": _tree_sha256(run_root),
        "project_manifest_sha256": _sha256(
            run_root / "00_project_run_manifest.json"
        ),
    }
    mismatched = [
        field
        for field, value in expected.items()
        if str(binding.get(field) or "") != value
    ]
    if mismatched:
        raise RuntimeError(
            "human review formal diagnosis binding does not match the current run: "
            + ", ".join(mismatched)
        )


def _validate_human_overlay_binding(
    config: ProjectConfig,
    overlay_path: Path | None,
    *,
    label: str,
) -> None:
    if overlay_path is None or not overlay_path.is_file():
        return
    candidates = (
        overlay_path.parent / "change_list.json",
        overlay_path.parent / "run_manifest.json",
    )
    binding: dict[str, Any] | None = None
    for path in candidates:
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"invalid {label} binding: {path}") from exc
        candidate = payload.get("formal_diagnosis") if isinstance(payload, dict) else None
        if isinstance(candidate, dict):
            binding = candidate
            break
    if binding is None:
        raise RuntimeError(
            f"{label} has no registered formal diagnosis binding: "
            f"{overlay_path}"
        )
    _validate_review_formal_binding(
        config,
        {"formal_diagnosis": binding},
    )


def _validate_diagnostic_overlay_binding(config: ProjectConfig) -> None:
    _validate_human_overlay_binding(
        config,
        config.diagnostic_summary_overrides_file,
        label="diagnostic summary overlay",
    )


def _validate_curve_overlay_binding(config: ProjectConfig) -> None:
    _validate_human_overlay_binding(
        config,
        config.manual_interpretations_file,
        label="manual curve overlay",
    )


def generate_business_report(
    project_dir: Path,
    *,
    skip_pdf: bool = False,
    allow_empty: bool = False,
    report_period: str | None = None,
    review_return: Path | None = None,
) -> PipelineResult:
    """Generate a business report from the uniquely bound human return."""
    base_config = load_project(project_dir, report_period=report_period)
    change_list_path = latest_review_import(base_config.project_dir, binding=_current_review_binding(base_config) if base_config.formal_diagnosis_run_dir else None, review_return=review_return, report_period=base_config.report_period_key)
    try:
        change_list = json.loads(change_list_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"invalid review change list: {change_list_path}") from exc
    blocker = _review_import_blocker(change_list)
    if blocker:
        raise RuntimeError(
            f"{blocker} must be resolved before business generation: {change_list_path}"
        )
    _validate_review_formal_binding(base_config, change_list)
    overrides_path = change_list_path.parent / "diagnostic_overrides.json"
    try:
        overrides = json.loads(overrides_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"invalid human diagnostic overrides: {overrides_path}") from exc
    artifact = allocate_report_artifact(
        base_config.project_dir,
        "business",
        base_config.project_dir.name,
        period_label=base_config.report_period_label,
    )
    unresolved = change_list.get("unresolved_changes")
    if isinstance(unresolved, list) and (
        unresolved or (change_list_path.parent / "manual_curve_interpretations.json").is_file()
    ):
        returned_docx = base_config.project_dir / str(change_list.get("returned_docx") or "")
        if not returned_docx.is_file():
            raise RuntimeError(f"human review DOCX is missing: {returned_docx}")
        if _sha256(returned_docx) != str(change_list.get("returned_sha256") or ""):
            raise RuntimeError("human review DOCX changed after ingestion")
        sync = finalize_human_review_document(
            returned_docx,
            artifact.docx,
            report_title=base_config.report_title,
            report_date=base_config.report_date,
            overrides=overrides,
        )
        variant = replace(
            base_config,
            output_dir=artifact.docx.parent,
            output_docx=artifact.docx,
            output_pdf=artifact.pdf,
        )
        check_pdf = None if skip_pdf else convert_docx_to_pdf(variant)
        validation = validate_report(
            variant,
            require_pdf=check_pdf is not None,
            pdf_path=check_pdf,
            information_only=True,
            raise_on_failure=False,
        )
        artifact.work_dir.mkdir(parents=True, exist_ok=True)
        version_manifest = artifact.work_dir / "run_manifest.json"
        version_manifest.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "status": "business_generated_from_human_return",
                    "review_version": int(change_list.get("review_version") or 0),
                    "returned_docx": str(returned_docx.relative_to(base_config.project_dir)),
                    "returned_sha256": _sha256(returned_docx),
                    "formal_diagnosis": change_list["formal_diagnosis"],
                    "output_docx": str(artifact.docx.relative_to(base_config.project_dir)),
                    "output_sha256": _sha256(artifact.docx),
                    "synchronization": sync,
                    "information_validation": validation,
                },
                ensure_ascii=False,
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )
        append_run_history(
            variant.project_dir,
            {
                "event": "business_generated",
                "version": artifact.version,
                "review_version": int(change_list.get("review_version") or 0),
                "returned_sha256": _sha256(returned_docx),
                "docx": str(artifact.docx.relative_to(variant.project_dir)),
                "pdf": "",
            },
        )
        return PipelineResult(
            config=variant,
            manifest_path=version_manifest,
            status="business_generated",
        )
    presentation = {
        key: value
        for key, value in base_config.presentation.items()
        if key not in {"review_label", "blank_ratings", "blank_conclusions", "blank_actions"}
    }
    presentation["profile"] = "business_dashboard"
    presentation["report_period_label"] = base_config.report_period_label
    report_overrides_path = change_list_path.parent / "report_overrides.json"
    if report_overrides_path.is_file():
        try:
            report_overrides = json.loads(report_overrides_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"invalid report-level human overrides: {report_overrides_path}") from exc
        if isinstance(report_overrides, dict):
            conclusion_present = "overall_conclusion" in report_overrides
            raw_conclusion = report_overrides.get("overall_conclusion")
            conclusion = raw_conclusion.strip() if isinstance(raw_conclusion, str) else ""
            recommendations = report_overrides.get("recommendations")
            if conclusion_present and not conclusion:
                raise RuntimeError("human overall_conclusion must be non-empty text")
            if conclusion:
                presentation["overall_conclusion_override"] = conclusion
            if isinstance(recommendations, list):
                if not recommendations or any(
                    not isinstance(item, str) or not item.strip()
                    for item in recommendations
                ):
                    raise RuntimeError(
                        "human recommendations must be a non-empty list of text"
                    )
                clean_recommendations = [item.strip() for item in recommendations]
                presentation["recommendation_overrides"] = clean_recommendations
            elif "recommendations" in report_overrides:
                raise RuntimeError(
                    "human recommendations must be a non-empty list of text"
                )
    variant = replace(
        base_config,
        output_dir=artifact.docx.parent,
        output_docx=artifact.docx,
        output_pdf=artifact.pdf,
        presentation=presentation,
        diagnostic_summary_overrides_file=overrides_path,
        manual_interpretations_file=(
            change_list_path.parent / "manual_curve_interpretations.json"
            if (change_list_path.parent / "manual_curve_interpretations.json").is_file()
            else None
        ),
    )
    result = run_project(
        base_config.project_dir,
        skip_pdf=skip_pdf,
        finalize_curve_review=True,
        config_override=variant,
    )
    if result.status == "curve_review_required":
        return result
    artifact.work_dir.mkdir(parents=True, exist_ok=True)
    snapshot_source = variant.work_dir / "analysis_summary.json"
    snapshot_path = artifact.work_dir / "diagnostic_snapshot.json"
    if snapshot_source.is_file():
        shutil.copy2(snapshot_source, snapshot_path)
    version_manifest = artifact.work_dir / "run_manifest.json"
    if result.manifest_path.is_file():
        shutil.copy2(result.manifest_path, version_manifest)
    append_run_history(
        variant.project_dir,
        {
            "event": "business_generated",
            "version": artifact.version,
            "review_version": int(change_list.get("review_version") or 0),
            "returned_sha256": str(change_list.get("returned_sha256") or ""),
            "snapshot_sha256": _sha256(snapshot_path),
            "docx": str(artifact.docx.relative_to(variant.project_dir)),
            "pdf": str(artifact.pdf.relative_to(variant.project_dir)) if artifact.pdf.is_file() else "",
        },
    )
    return PipelineResult(config=variant, manifest_path=version_manifest, status="business_generated")


def curve_review_finalize_status(
    project_dir: Path,
    *,
    report_period: str | None = None,
) -> CurveReviewReadiness:
    """Check the review gate without starting customer report generation."""
    config = load_project(project_dir, report_period=report_period)
    return curve_review_readiness(
        config.work_dir / "curve_review_manifest.json",
        codex_path=config.work_dir / "codex_curve_interpretations.json",
        manual_path=config.manual_interpretations_file,
    )


def run_project(
    project_dir: Path,
    *,
    dry_run: bool = False,
    skip_pdf: bool = False,
    validate_only: bool = False,
    finalize_curve_review: bool = False,
    config_override: ProjectConfig | None = None,
    report_period: str | None = None,
) -> PipelineResult:
    config = config_override or load_project(
        project_dir,
        report_period=report_period,
    )
    data_only = bool(config.presentation.get("data_only"))
    if data_only and finalize_curve_review:
        raise ValueError("data-only report cannot finalize diagnostic curve review")
    curve_review_manifest = config.work_dir / "curve_review_manifest.json"
    curve_interpretations = ()
    formal_diagnosis: FormalDiagnosis | None = None
    formal_curve_entries: list[dict[str, Any]] = []
    prior_has_test_data_folders: set[str] | None = None
    if (
        config.formal_diagnosis_run_dir is not None
        and not dry_run
        and not validate_only
        and not data_only
    ):
        analysis_summary_path = config.work_dir / "analysis_summary.json"
        if not analysis_summary_path.is_file():
            raise FormalDiagnosisError(
                "current analysis_summary.json is missing; run "
                "prepare-formal-evidence before formal report generation: "
                f"{analysis_summary_path}"
            )
        try:
            prior_summary = json.loads(analysis_summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise FormalDiagnosisError(
                f"invalid current analysis summary: {analysis_summary_path}"
            ) from exc
        prior_has_test_data_folders = {
            str(item.get("folder") or "")
            for item in prior_summary
            if isinstance(item, dict) and "has_test_data" in item
        }
        required_valves = tuple(
            str(tag).strip()
            for tag in (config.report_layout.get("only_valves") or [])
            if str(tag).strip()
        )
        formal_diagnosis = load_formal_diagnosis(
            config.formal_diagnosis_run_dir,
            curve_review_manifest,
            project_root=config.project_dir,
            required_valve_tags=required_valves,
            require_versioned=True,
            analysis_summary_path=analysis_summary_path,
        )
        formal_curve_entries = [
            asdict(interpretation)
            for interpretation in formal_diagnosis.curve_interpretations
        ]
        _validate_diagnostic_overlay_binding(config)
        _validate_curve_overlay_binding(config)
    elif (
        not data_only
        and not dry_run
        and not validate_only
        and config.formal_diagnosis_run_dir is None
    ):
        raise FormalDiagnosisError(
            "formal diagnosis binding is required for a diagnostic report"
        )
    if finalize_curve_review and (dry_run or validate_only):
        raise ValueError("curve review finalization requires report generation")
    if (
        not dry_run
        and not validate_only
        and not data_only
        and (finalize_curve_review or curve_review_manifest.is_file())
    ):
        readiness = curve_review_readiness(
            curve_review_manifest,
            codex_path=None,
            formal_interpretations=formal_curve_entries,
            manual_path=config.manual_interpretations_file,
        )
        if readiness.status == "curve_review_required":
            return PipelineResult(
                config=config,
                manifest_path=curve_review_manifest,
                status=readiness.status,
                missing_image_keys=readiness.missing_image_keys,
            )
        curve_interpretations = readiness.interpretations
    validation: dict[str, Any] | None = None
    libreoffice_check_pdf: Path | None = None
    if dry_run:
        status = "dry_run"
    elif validate_only:
        validation = validate_report(config, require_pdf=False)
        status = "validated"
    else:
        spec_page_text_cache = load_specification_pages(config.project_dir, config.specs_dir)
        generator.configure(
            config,
            curve_interpretations=curve_interpretations,
            formal_summary_overrides=(
                formal_diagnosis.summary_overrides
                if formal_diagnosis is not None
                else None
            ),
            spec_page_text_cache=spec_page_text_cache,
        )
        only_valves = set(_configured_only_valves(config))
        all_valves = generator.prepare_valve_data(
            curve_review_required_folders=only_valves or None,
        )
        scoped_valves = _select_report_valves(
            config,
            all_valves,
            include_data_insufficient=True,
        )
        if data_only:
            report_valves = scoped_valves
        else:
            assert formal_diagnosis is not None
            for valve in scoped_valves:
                if valve.folder not in formal_diagnosis.summary_overrides:
                    raise FormalDiagnosisError(
                        f"{valve.folder} is missing from the validated frozen diagnosis"
                    )
                generator.apply_report_diagnosis_summary(valve)
                valve.algorithm_audit_status = (
                    formal_diagnosis.algorithm_audit_statuses.get(
                        valve.folder,
                        "legacy_unavailable",
                    )
                )
                valve.algorithm_audit_snapshot = (
                    formal_diagnosis.algorithm_audit_snapshots.get(valve.folder)
                )
                if valve.algorithm_audit_snapshot is not None:
                    audit = valve.algorithm_audit_snapshot
                    valve.data_rating = audit["data_rating"]
                    valve.visual_rating = audit["visual_rating"]
                    valve.algorithm_rating = audit["algorithm_rating"]
                    valve.rating_inconsistency_reason = audit[
                        "rating_inconsistency_reason"
                    ]
                    valve.rating_evidence = dict(audit["rating_evidence"])
            report_valves = _select_report_valves(config, all_valves)
        generator.write_summary(
            all_valves,
            included_folders={valve.folder for valve in report_valves},
            report_field_manifest_builder=business_report.report_field_manifest,
            include_diagnosis=not data_only,
            diagnosis_folders=(
                {valve.folder for valve in scoped_valves}
                if formal_diagnosis is not None
                else None
            ),
            include_has_test_data_folders=(
                prior_has_test_data_folders
                if formal_diagnosis is not None
                else None
            ),
        )
        _render_prepared_report(config, report_valves)
        if skip_pdf or not config.features.get("convert_pdf", True):
            validation = validate_report(
                config,
                require_pdf=False,
                require_finalized_curve_review=finalize_curve_review or bool(curve_interpretations),
            )
            status = "generated"
        else:
            libreoffice_check_pdf = convert_docx_to_pdf(config)
            validation = validate_report(
                config,
                require_pdf=True,
                require_finalized_curve_review=finalize_curve_review or bool(curve_interpretations),
                pdf_path=libreoffice_check_pdf,
            )
            status = "generated_and_internal_validated"
    manifest_path = _write_manifest(
        config,
        status=status,
        validation=validation,
        libreoffice_check_pdf=libreoffice_check_pdf,
    )
    return PipelineResult(config=config, manifest_path=manifest_path, status=status)
