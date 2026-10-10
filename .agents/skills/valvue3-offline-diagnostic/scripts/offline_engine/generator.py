from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

import fitz
from .config import ProjectConfig
from .curve_identity import (
    TEST_ORDER,
    curve_image_key,
    curve_review_marker,
    sort_test_images,
    spec_insertion_marker,
)
from .presentation import (
    TEST_LABELS,
    clean_fault,
    format_friction_metric,
    format_number as fmt,
)
from .specifications import detect_spec_format, iter_spec_pdfs, parse_spec_fields
from .visual_review import CurveInterpretation, combine_ratings


BASE_DIR = Path(__file__).resolve().parent
RAW_DIR = BASE_DIR / "raw"
SPECS_DIR = BASE_DIR / "specs"
WORK_DIR = BASE_DIR / "work"
IMAGE_DIR = WORK_DIR / "report_images"
SPEC_IMAGE_DIR = WORK_DIR / "spec_pages"
SUMMARY_JSON = WORK_DIR / "analysis_summary.json"
CURVE_INTERPRETATIONS_BY_IMAGE_KEY: dict[str, CurveInterpretation] = {}
DIAGNOSTIC_SUMMARY_OVERRIDES: dict[str, dict[str, Any]] = {}
VALVE_CONTEXT_BY_TAG: dict[str, dict[str, str]] = {}
STRUCTURED_EVIDENCE_BY_TAG: dict[str, dict[str, Any]] = {}


QUALITY_SPEC_BY_FOLDER = {}
EXPLICIT_SPEC_FILE_BY_TAG: dict[str, str] = {}
DIRECT_SPEC_PAGE_BY_TAG: dict[str, dict[str, Any]] = {}
DIRECT_AIR_PIPING_PAGE_BY_TAG: dict[str, dict[str, Any]] = {}
DIRECT_ACCESSORY_PAGE_BY_TAG: dict[str, dict[str, Any]] = {}
CURVE_IMAGE_EXCLUSIONS: frozenset[str] = frozenset()
CURVE_TEST_KEY_OVERRIDES: dict[str, str] = {}
INCLUDE_SPECIFICATIONS = True
INCLUDE_AIR_PIPING_PAGES = True
INCLUDE_ACCESSORY_PAGES = False
ALLOW_LOOSE_PDF_METRICS = True
INCLUDE_SIGNATURE_METRICS = True
EXCLUDE_FULL_TRAVEL_SINGLE_STEP = False
EXCLUDED_SOURCE_FILES: frozenset[str] = frozenset()
SPEC_PAGES_BY_VALVE: dict[str, "SpecPage"] = {}
AIR_PIPING_PAGES_BY_VALVE: dict[str, "SpecPage"] = {}
ACCESSORY_PAGES_BY_VALVE: dict[str, "SpecPage"] = {}
SPEC_PAGE_TEXT_CACHE: dict[tuple[str, int], str] = {}
SPEC_FIELD_OVERRIDES_BY_TAG: dict[str, dict[str, str]] = {}
FRICTION_MEASUREMENT_OVERRIDES_BY_TAG: dict[str, dict[str, Any]] = {}
HISTORICAL_RUNTIME_BY_TAG: dict[str, dict[str, Any]] = {}

TEST_EXECUTION_TASKS = (
    ("Run Step Test", "run_step"),
    ("Pattern Step Test", "pattern_step"),
    ("Run Ramp Test", "run_ramp"),
    ("Standard Signature", "standard_signature"),
    ("Run Signature Test", "standard_signature"),
    ("Extended Actuator Signature", "extended_signature"),
    ("Extended Act. Signature", "extended_signature"),
    ("Performance Check", "performance_check"),
    ("Change Mode", "change_mode"),
)
TEST_EXECUTION_FAILURE_STATUSES = {"failed", "cancelled", "aborted", "incomplete", "not completed"}
def _load_project_json(name: str, default: Any) -> Any:
    path = BASE_DIR / name
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def _load_formal_structured_evidence(path: Path | None) -> dict[str, dict[str, Any]]:
    """Read frozen structured evidence for report binding without importing formal tools."""
    if path is None or not path.is_dir():
        return {}
    evidence_by_tag: dict[str, dict[str, Any]] = {}
    for valve_dir in sorted(path.iterdir()):
        if not valve_dir.is_dir():
            continue
        bundle_path = valve_dir / "01_evidence_bundle.json"
        try:
            bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(bundle, dict):
            continue
        evidence = bundle.get("evidence")
        if not isinstance(evidence, dict):
            continue
        structured = evidence.get("structured_evidence")
        if isinstance(structured, dict):
            evidence_by_tag[valve_dir.name] = structured
    return evidence_by_tag


def _load_parameter_structured_evidence(
    project_dir: Path,
) -> dict[str, dict[str, Any]]:
    """Load the approved-parameter projection used by factual report rows.

    The CLI gate validates approval before report generation.  This loader only
    supplies the already extracted values; it never reruns OCR or parsing.
    """
    valve_dir = project_dir / "work/evidence-extraction/valves"
    if not valve_dir.is_dir():
        return {}
    evidence_by_tag: dict[str, dict[str, Any]] = {}
    for path in sorted(valve_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        valve_tag = str(payload.get("valve_tag") or "").strip()
        if valve_tag:
            evidence_by_tag[valve_tag] = {"material_extraction": payload}
    return evidence_by_tag


def configure(
    config: ProjectConfig,
    *,
    curve_interpretations: tuple[CurveInterpretation, ...] = (),
    formal_summary_overrides: dict[str, dict[str, Any]] | None = None,
    spec_page_text_cache: dict[tuple[str, int], str] | None = None,
    load_formal_structured_evidence: bool = True,
    load_parameter_structured_evidence: bool = True,
    load_summary_overrides: bool = True,
) -> None:
    global BASE_DIR, RAW_DIR, SPECS_DIR, WORK_DIR, IMAGE_DIR, SPEC_IMAGE_DIR
    global SUMMARY_JSON
    global CURVE_INTERPRETATIONS_BY_IMAGE_KEY
    global DIAGNOSTIC_SUMMARY_OVERRIDES, VALVE_CONTEXT_BY_TAG
    global STRUCTURED_EVIDENCE_BY_TAG
    global QUALITY_SPEC_BY_FOLDER, EXPLICIT_SPEC_FILE_BY_TAG, DIRECT_SPEC_PAGE_BY_TAG, DIRECT_AIR_PIPING_PAGE_BY_TAG, DIRECT_ACCESSORY_PAGE_BY_TAG
    global CURVE_IMAGE_EXCLUSIONS, CURVE_TEST_KEY_OVERRIDES
    global INCLUDE_SPECIFICATIONS, INCLUDE_AIR_PIPING_PAGES, INCLUDE_ACCESSORY_PAGES
    global ALLOW_LOOSE_PDF_METRICS, INCLUDE_SIGNATURE_METRICS, EXCLUDE_FULL_TRAVEL_SINGLE_STEP
    global EXCLUDED_SOURCE_FILES
    global SPEC_FIELD_OVERRIDES_BY_TAG
    global FRICTION_MEASUREMENT_OVERRIDES_BY_TAG
    global HISTORICAL_RUNTIME_BY_TAG
    global SPEC_PAGE_TEXT_CACHE

    BASE_DIR = config.project_dir
    RAW_DIR = config.raw_dir
    SPECS_DIR = config.specs_dir
    WORK_DIR = config.work_dir
    IMAGE_DIR = WORK_DIR / "report_images"
    SPEC_IMAGE_DIR = WORK_DIR / "spec_pages"
    SUMMARY_JSON = WORK_DIR / "analysis_summary.json"
    CURVE_INTERPRETATIONS_BY_IMAGE_KEY = {item.image_key: item for item in curve_interpretations}
    SPEC_PAGE_TEXT_CACHE = dict(spec_page_text_cache or {})
    formal_overrides = {
        tag: dict(override)
        for tag, override in (formal_summary_overrides or {}).items()
    }
    human_overrides = (
        {}
        if config.presentation.get("data_only") or not load_summary_overrides
        else load_diagnostic_summary_overrides(config.diagnostic_summary_overrides_file)
    )
    DIAGNOSTIC_SUMMARY_OVERRIDES = merge_diagnostic_summary_overrides(
        formal_overrides,
        human_overrides,
    )
    VALVE_CONTEXT_BY_TAG = load_valve_context(config.valve_context_file)
    parameter_evidence = (
        _load_parameter_structured_evidence(config.project_dir)
        if load_parameter_structured_evidence
        else {}
    )
    formal_evidence = (
        _load_formal_structured_evidence(config.formal_diagnosis_run_dir)
        if (
            load_formal_structured_evidence
            and config.formal_diagnosis_run_dir
            and not config.presentation.get("data_only")
        )
        else {}
    )
    STRUCTURED_EVIDENCE_BY_TAG = {**parameter_evidence, **formal_evidence}

    QUALITY_SPEC_BY_FOLDER = _load_project_json("quality_spec_mapping.json", {})
    EXPLICIT_SPEC_FILE_BY_TAG = _load_project_json("spec_file_mapping.json", {})
    DIRECT_SPEC_PAGE_BY_TAG = _load_project_json("spec_page_mapping.json", {})
    DIRECT_AIR_PIPING_PAGE_BY_TAG = _load_project_json("air_piping_page_mapping.json", {})
    DIRECT_ACCESSORY_PAGE_BY_TAG = _load_project_json("accessory_page_mapping.json", {})
    curve_review_overrides = _load_project_json("curve_review_overrides.json", {})
    if not isinstance(curve_review_overrides, dict):
        raise ValueError("curve_review_overrides.json must contain an object")
    configured_curve_exclusions = curve_review_overrides.get("exclude") or []
    configured_test_key_overrides = curve_review_overrides.get("test_key_overrides") or {}
    if not isinstance(configured_curve_exclusions, list):
        raise ValueError("curve_review_overrides.exclude must contain a list")
    if not isinstance(configured_test_key_overrides, dict):
        raise ValueError("curve_review_overrides.test_key_overrides must contain an object")
    curve_exclusions: set[str] = set()
    for value in configured_curve_exclusions:
        relative_path = Path(str(value).strip())
        if relative_path.is_absolute() or ".." in relative_path.parts or len(relative_path.parts) != 2:
            raise ValueError(
                "curve_review_overrides.exclude must contain <valve-tag>/<image-name>: "
                f"{value}"
            )
        curve_exclusions.add(relative_path.as_posix())
    curve_test_key_overrides: dict[str, str] = {}
    for value, test_key in configured_test_key_overrides.items():
        relative_path = Path(str(value).strip())
        normalized_test_key = str(test_key).strip()
        if relative_path.is_absolute() or ".." in relative_path.parts or len(relative_path.parts) != 2:
            raise ValueError(
                "curve_review_overrides.test_key_overrides keys must contain "
                f"<valve-tag>/<image-name>: {value}"
            )
        if normalized_test_key not in TEST_LABELS:
            raise ValueError(
                "curve_review_overrides.test_key_overrides contains an unsupported test key: "
                f"{test_key}"
            )
        curve_test_key_overrides[relative_path.as_posix()] = normalized_test_key
    CURVE_IMAGE_EXCLUSIONS = frozenset(curve_exclusions)
    CURVE_TEST_KEY_OVERRIDES = curve_test_key_overrides
    INCLUDE_SPECIFICATIONS = config.features.get("include_specifications", True)
    INCLUDE_AIR_PIPING_PAGES = config.features.get("include_air_piping_pages", True)
    INCLUDE_ACCESSORY_PAGES = config.features.get("include_accessory_pages", False)
    ALLOW_LOOSE_PDF_METRICS = config.features.get("allow_loose_pdf_metrics", True)
    INCLUDE_SIGNATURE_METRICS = config.features.get("include_signature_metrics", True)
    EXCLUDE_FULL_TRAVEL_SINGLE_STEP = bool(
        config.report_layout.get("exclude_full_travel_single_step", False)
    )
    configured_excluded_sources = config.report_layout.get("exclude_source_files") or []
    excluded_sources: set[str] = set()
    for value in configured_excluded_sources:
        relative_path = Path(str(value).strip())
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise ValueError(f"report_layout.exclude_source_files must contain project-relative paths: {value}")
        excluded_sources.add(relative_path.as_posix())
    EXCLUDED_SOURCE_FILES = frozenset(excluded_sources)
    SPEC_FIELD_OVERRIDES_BY_TAG = _load_project_json("spec_field_overrides.json", {})
    FRICTION_MEASUREMENT_OVERRIDES_BY_TAG = _load_project_json(
        "friction_measurement_overrides.json", {}
    )
    HISTORICAL_RUNTIME_BY_TAG = _load_project_json("historical_runtime_notes.json", {})


def load_diagnostic_summary_overrides(path: Path | None) -> dict[str, dict[str, Any]]:
    """Load customer-project diagnosis summaries without embedding project facts in the engine."""
    if path is None or not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid diagnostic summary overrides JSON: {path}") from exc
    if not isinstance(raw, dict):
        raise ValueError("diagnostic summary overrides must be an object keyed by valve tag")

    allowed = {
        "rating",
        "main_issue",
        "action",
        "issues",
        "actions",
        "summary_issues",
        "summary_actions",
        "overall_assessment",
        "rating_conclusion",
        "health_rating_detail_mode",
        "human_verification_status",
    }
    validated: dict[str, dict[str, Any]] = {}
    for raw_tag, raw_override in raw.items():
        tag = str(raw_tag).strip()
        if not tag or not isinstance(raw_override, dict):
            raise ValueError(f"diagnostic summary override requires an object for valve tag: {raw_tag}")
        unknown = set(raw_override) - allowed
        if unknown:
            raise ValueError(f"{tag} contains unsupported diagnostic summary fields: {', '.join(sorted(unknown))}")
        override: dict[str, Any] = {}
        if "rating" in raw_override:
            rating = raw_override["rating"]
            if isinstance(rating, bool) or not isinstance(rating, int) or rating not in range(1, 6):
                raise ValueError(f"{tag} rating must be an integer from 1 to 5")
            override["rating"] = rating
        for field_name in ("main_issue", "action", "overall_assessment", "rating_conclusion"):
            if field_name not in raw_override:
                continue
            value = raw_override[field_name]
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{tag} {field_name} must be non-empty text")
            override[field_name] = value.strip()
        if "health_rating_detail_mode" in raw_override:
            detail_mode = raw_override["health_rating_detail_mode"]
            if detail_mode not in {"detailed", "summary_only"}:
                raise ValueError(
                    f"{tag} health_rating_detail_mode must be 'detailed' or 'summary_only'"
                )
            override["health_rating_detail_mode"] = detail_mode
        if "human_verification_status" in raw_override:
            status = raw_override["human_verification_status"]
            if not isinstance(status, str) or not status.strip():
                raise ValueError(
                    f"{tag} human_verification_status must be non-empty text"
                )
            override["human_verification_status"] = status.strip()
        for field_name in ("issues", "actions", "summary_issues", "summary_actions"):
            if field_name not in raw_override:
                continue
            values = raw_override[field_name]
            empty_allowed = field_name in {"issues", "summary_issues"}
            if not isinstance(values, list) or (not values and not empty_allowed) or any(
                not isinstance(value, str) or not value.strip() for value in values
            ):
                qualifier = "a list of text" if empty_allowed else "a non-empty list of text"
                raise ValueError(f"{tag} {field_name} must be {qualifier}")
            if field_name.startswith("summary_") and len(values) > 3:
                raise ValueError(f"{tag} {field_name} must contain 1 to 3 text bullets")
            override[field_name] = [value.strip() for value in values]
        validated[tag] = override
    return validated


def merge_diagnostic_summary_overrides(
    formal: dict[str, dict[str, Any]],
    human: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Merge registered human fields over one validated frozen summary."""
    merged = {tag: dict(summary) for tag, summary in formal.items()}
    for tag, overlay in human.items():
        summary = {**merged.get(tag, {}), **overlay}
        if "issues" in overlay and "summary_issues" not in overlay:
            summary["summary_issues"] = list(overlay["issues"])
        elif "summary_issues" in overlay and "issues" not in overlay:
            summary["issues"] = list(overlay["summary_issues"])
        if "action" in overlay and "actions" not in overlay:
            summary["actions"] = [overlay["action"]]
        elif "summary_actions" in overlay and "actions" not in overlay:
            summary["actions"] = list(overlay["summary_actions"])
        if "actions" in overlay or "action" in overlay or "summary_actions" in overlay:
            if "summary_actions" not in overlay:
                summary["summary_actions"] = list(summary["actions"])
            if "action" not in overlay:
                summary["action"] = _joined_actions(summary["actions"])
        if (
            ("rating_conclusion" in overlay or "main_issue" in overlay)
            and "overall_assessment" not in overlay
        ):
            conclusion = str(summary.get("rating_conclusion") or "").strip()
            main_issue = str(summary.get("main_issue") or "").strip()
            if conclusion and main_issue:
                summary["overall_assessment"] = (
                    f"{conclusion} 主要问题：{main_issue}"
                )
        merged[tag] = summary
    return merged


def _joined_actions(actions: list[Any]) -> str:
    clean = [str(item).strip().rstrip("。；") for item in actions if str(item).strip()]
    return "；".join(clean) + ("。" if clean else "")


def report_diagnosis_summary(valve_tag: str) -> dict[str, Any]:
    """Return a complete report summary without consulting algorithm defaults."""
    summary = DIAGNOSTIC_SUMMARY_OVERRIDES.get(valve_tag)
    if not isinstance(summary, dict):
        raise ValueError(f"{valve_tag} validated formal diagnosis summary is missing")
    missing: list[str] = []
    rating = summary.get("rating")
    if isinstance(rating, bool) or not isinstance(rating, int) or rating not in range(1, 6):
        missing.append("rating")
    for field_name in (
        "rating_conclusion",
        "main_issue",
        "action",
        "overall_assessment",
    ):
        if not isinstance(summary.get(field_name), str) or not str(summary[field_name]).strip():
            missing.append(field_name)
    issues = summary.get("issues")
    if not isinstance(issues, list) or not all(
        isinstance(item, str) and item.strip() for item in issues
    ):
        missing.append("issues")
    actions = summary.get("actions")
    if not isinstance(actions, list) or not actions or not all(
        isinstance(item, str) and item.strip() for item in actions
    ):
        missing.append("actions")
    if missing:
        raise ValueError(
            f"{valve_tag} validated formal diagnosis summary has missing or empty "
            f"required fields: {', '.join(missing)}"
        )
    return summary


def apply_report_diagnosis_summary(valve: ValveReport) -> None:
    """Apply only validated frozen and human report fields to a valve."""
    summary = report_diagnosis_summary(valve.folder)
    valve.rating = summary["rating"]
    valve.final_rating = summary["rating"]
    valve.rating_conclusion_override = summary["rating_conclusion"].strip()
    valve.main_issue = summary["main_issue"].strip()
    valve.action = summary["action"].strip()
    valve.issues = list(summary["issues"])
    valve.actions = list(summary["actions"])
    valve.summary_issues = list(summary.get("summary_issues", summary["issues"]))
    valve.summary_actions = list(summary.get("summary_actions", summary["actions"]))
    valve.overall_assessment = summary["overall_assessment"].strip()
    valve.health_rating_detail_mode = str(
        summary.get("health_rating_detail_mode") or "summary_only"
    )
    valve.human_verification_status = str(
        summary.get("human_verification_status") or ""
    )


def load_valve_context(path: Path | None) -> dict[str, dict[str, str]]:
    """Load customer-provided operating context keyed by valve tag."""
    if path is None or not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid valve context JSON: {path}") from exc
    if not isinstance(raw, dict):
        raise ValueError("valve context must be an object keyed by valve tag")
    allowed = {"description", "unit", "failure_consequence", "bypass", "note"}
    validated: dict[str, dict[str, str]] = {}
    for raw_tag, raw_context in raw.items():
        tag = str(raw_tag).strip()
        if not tag or not isinstance(raw_context, dict):
            raise ValueError(f"valve context requires an object for valve tag: {raw_tag}")
        unknown = set(raw_context) - allowed
        if unknown:
            raise ValueError(f"{tag} contains unsupported valve context fields: {', '.join(sorted(unknown))}")
        context: dict[str, str] = {}
        for field_name in allowed:
            value = raw_context.get(field_name)
            if value is None:
                continue
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{tag} {field_name} must be non-empty text")
            context[field_name] = value.strip()
        validated[tag] = context
    return validated


def fnum(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.upper() in {"N/A", "NA", "NONE", "NULL"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def max_num(values: list[Any]) -> float | None:
    nums = [fnum(v) for v in values]
    nums = [v for v in nums if v is not None]
    return max(nums) if nums else None


def min_num(values: list[Any]) -> float | None:
    nums = [fnum(v) for v in values]
    nums = [v for v in nums if v is not None]
    return min(nums) if nums else None


def avg_num(values: list[Any]) -> float | None:
    nums = [fnum(v) for v in values]
    nums = [v for v in nums if v is not None]
    return sum(nums) / len(nums) if nums else None


def run_pdftotext(path: Path) -> str:
    from .document_ocr_cache import load_cached_pdf_text

    cached = load_cached_pdf_text(BASE_DIR, path)
    if cached is not None:
        return cached
    try:
        result = subprocess.run(
            ["pdftotext", "-layout", str(path), "-"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return result.stdout or ""
    except FileNotFoundError:
        return ""


def run_pdftotext_pages(path: Path) -> list[str]:
    """Read prepared PDF text without discarding physical page boundaries."""
    from .document_ocr_cache import load_cached_pdf_pages

    cached = load_cached_pdf_pages(BASE_DIR, path)
    if cached is not None:
        return cached
    return _pdf_text_pages(run_pdftotext(path))


def clean_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


@dataclass
class TestRecord:
    source: str
    source_type: str
    test_type: str
    device_id: str = ""
    device_tag: str = ""
    started: str = ""
    occurrence: int = 1
    attrs: dict[str, Any] = field(default_factory=dict)
    steps: list[dict[str, Any]] = field(default_factory=list)
    supply_pressures: list[float] = field(default_factory=list)


@dataclass
class SourceFile:
    name: str
    kind: str
    pages: int | None = None
    readable: bool = True
    note: str = ""


def record_source_report(
    valve: "ValveReport",
    report_type: str,
    *,
    info: dict[str, Any] | None = None,
) -> None:
    """Retain report-type counts without exposing parsed customer content."""
    info = info or {}
    valve.source_report_types[report_type] = (
        valve.source_report_types.get(report_type, 0) + 1
    )
    if (
        report_type in {"dtm_active", "valvxpert"}
        and info.get("dtm_source_status") == "present"
        and info.get("dtm_parser_status") == "parsed"
    ):
        valve.dtm_data_report_count += 1


@dataclass
class SpecPage:
    valve: str
    tag: str
    source: str
    page: int
    source_sha256: str = ""
    roles: tuple[str, ...] = ()
    image_path: str = ""
    match_basis: tuple[str, ...] = ()
    model_no: str = ""
    valve_spec: str = ""
    fields: dict[str, str] = field(default_factory=dict)

    @property
    def physical_identity(self) -> str:
        source_identity = self.source_sha256 or self.source
        return f"{source_identity}:{self.page}"


SPEC_CONTENT_PADDING_PT = 2.0


def _rect_area(rect: fitz.Rect) -> float:
    return max(0.0, rect.width) * max(0.0, rect.height)


def _expand_page_rect(
    rect: fitz.Rect,
    page_rect: fitz.Rect,
    padding: float = SPEC_CONTENT_PADDING_PT,
) -> fitz.Rect:
    """Keep a small margin around the source form without restoring page furniture."""
    expanded = fitz.Rect(
        rect.x0 - padding,
        rect.y0 - padding,
        rect.x1 + padding,
        rect.y1 + padding,
    )
    expanded.intersect(page_rect)
    return expanded


def _text_block_rects(blocks: list[dict[str, Any]]) -> list[fitz.Rect]:
    """Return bounds for actual text in PyMuPDF's dictionary-format blocks."""
    rects = []
    for block in blocks:
        if block.get("type") != 0:
            continue
        text = block.get("text", "")
        if not text:
            text = "".join(
                span.get("text", "")
                for line in block.get("lines", [])
                for span in line.get("spans", [])
            )
        if str(text).strip():
            rects.append(fitz.Rect(block["bbox"]))
    return rects


def effective_spec_content_rect(page: fitz.Page) -> fitz.Rect:
    """Locate the data-sheet body, excluding report headers, footers and page margins.

    ValVue source PDFs commonly place the actual Specification/Accessory form in a
    raster image block and add the report header/footer as separate PDF objects. A
    block-based crop preserves the form border and all table fields while removing
    those unrelated report objects. Native/vector PDFs use their body drawings or
    text as a conservative fallback.
    """
    page_rect = page.rect
    blocks = page.get_text("dict").get("blocks", [])
    image_rects = []
    min_image_area = _rect_area(page_rect) * 0.05
    for block in blocks:
        if block.get("type") != 1:
            continue
        rect = fitz.Rect(block["bbox"])
        if _rect_area(rect) >= min_image_area:
            image_rects.append(rect)
    if image_rects:
        # A form may be split into several adjacent raster strips. Group strips
        # by vertical continuity first, while keeping a separate report header
        # or footer image out of the form cluster.
        clusters: list[list[fitz.Rect]] = []
        for rect in sorted(image_rects, key=lambda item: (item.y0, item.x0)):
            for cluster in clusters:
                cluster_bottom = max(item.y1 for item in cluster)
                if rect.y0 <= cluster_bottom + 6:
                    cluster.append(rect)
                    break
            else:
                clusters.append([rect])
        content_cluster = max(
            clusters,
            key=lambda cluster: sum(_rect_area(item) for item in cluster),
        )
        content_rect = fitz.Rect(
            min(rect.x0 for rect in content_cluster),
            min(rect.y0 for rect in content_cluster),
            max(rect.x1 for rect in content_cluster),
            max(rect.y1 for rect in content_cluster),
        )
        # Technical pages can mix a central raster table with vector header and
        # footer fields. Keeping the raster alone silently removes those fields.
        for rect in _text_block_rects(blocks):
            content_rect.include_rect(rect)
        return _expand_page_rect(content_rect, page_rect)

    # For native/vector forms, exclude the top report header and bottom footer,
    # then choose the densest body drawing group as the form boundary.
    body_rects = []
    top_cut = page_rect.height * 0.10
    bottom_cut = page_rect.height * 0.92
    for drawing in page.get_drawings():
        rect = fitz.Rect(drawing.get("rect", (0, 0, 0, 0)))
        if rect.width < 1 and rect.height < 1:
            continue
        if rect.y1 <= top_cut or rect.y0 >= bottom_cut:
            continue
        body_rects.append(rect)
    if body_rects:
        return _expand_page_rect(
            fitz.Rect(
                min(rect.x0 for rect in body_rects),
                min(rect.y0 for rect in body_rects),
                max(rect.x1 for rect in body_rects),
                max(rect.y1 for rect in body_rects),
            ),
            page_rect,
        )

    text_rects = []
    for rect in _text_block_rects(blocks):
        if rect.y0 > top_cut and rect.y1 < bottom_cut:
            text_rects.append(rect)
    if text_rects:
        return _expand_page_rect(
            fitz.Rect(
                min(rect.x0 for rect in text_rects),
                min(rect.y0 for rect in text_rects),
                max(rect.x1 for rect in text_rects),
                max(rect.y1 for rect in text_rects),
            ),
            page_rect,
        )
    return page_rect


@dataclass
class ValveReport:
    folder: str
    description: str = "未提供"
    unit: str = ""
    failure_consequence: str = "未提供"
    bypass: str = "未提供"
    site_note: str = "未提供"
    files: list[SourceFile] = field(default_factory=list)
    source_report_types: dict[str, int] = field(default_factory=dict)
    dtm_data_report_count: int = 0
    tests: list[TestRecord] = field(default_factory=list)
    all_tests: list[TestRecord] = field(default_factory=list)
    curve_inventory: list[dict[str, Any]] = field(default_factory=list)
    active_faults: list[str] = field(default_factory=list)
    device_id: str = ""
    device_tag: str = ""
    serial_number: str = ""
    positioner_model: str = "未提供"
    positioner_type: str = ""
    firmware: str = ""
    detection_date: str = "未提供"
    manufacturer: str = "Masoneilan / Baker Hughes"
    actuator_type: str = "未提供"
    spring_range: str = "未提供"
    valve_model: str = "未提供"
    valve_spec: str = "未提供"
    spec_fields: dict[str, str] = field(default_factory=dict)
    dtm_info: dict[str, str] = field(default_factory=dict)
    positioner_report_info: dict[str, Any] = field(default_factory=dict)
    positioner_report_infos: list[dict[str, Any]] = field(default_factory=list)
    spec_page: SpecPage | None = None
    spec_image: str = ""
    accessory_page: SpecPage | None = None
    accessory_image: str = ""
    air_piping_page: SpecPage | None = None
    air_piping_image: str = ""
    report_pages: list[SpecPage] = field(default_factory=list)
    valve_type: str = "未提供"
    data_note: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)
    basic_action_failed: bool = False
    test_execution_issues: list[dict[str, Any]] = field(default_factory=list)
    curve_interpretations: tuple[CurveInterpretation, ...] = ()
    data_rating: int | None = None
    visual_rating: int | None = None
    final_rating: int = 5
    rating_inconsistency_reason: str | None = None
    rating_evidence: dict[str, Any] = field(default_factory=dict)
    rating: int = 5
    algorithm_rating: int | None = None
    algorithm_audit_status: str = ""
    algorithm_audit_snapshot: dict[str, Any] | None = None
    main_issue: str = "数据不足"
    action: str = "补充数据并复测"
    issues: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    summary_issues: list[str] = field(default_factory=list)
    summary_actions: list[str] = field(default_factory=list)
    basis: list[str] = field(default_factory=list)
    curve_summary: list[str] = field(default_factory=list)
    overall_assessment: str = ""
    rating_conclusion_override: str = ""
    health_rating_detail_mode: str = "detailed"
    human_verification_status: str = ""
    rating_override_note: str = ""
    experience_actions: list[str] = field(default_factory=list)
    images: list[dict[str, str]] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)
    structured_evidence: dict[str, Any] | None = None


_NO_STRUCTURED_PARAMETER = object()


def _structured_parameter_value(
    valve: ValveReport,
    path: str,
    *,
    prefer_raw: bool = False,
) -> Any:
    structured = valve.structured_evidence
    if not isinstance(structured, dict):
        return _NO_STRUCTURED_PARAMETER
    material = structured.get("material_extraction")
    fields = material.get("fields") if isinstance(material, dict) else None
    field = fields.get(path) if isinstance(fields, dict) else None
    if not isinstance(field, dict) or str(field.get("status") or "") in {
        "",
        "missing",
        "conflict",
        "review_required",
        "source_present_parser_missing",
    }:
        return _NO_STRUCTURED_PARAMETER
    selected = field.get("selected")
    if not isinstance(selected, dict):
        return _NO_STRUCTURED_PARAMETER
    keys = ("raw_value", "normalized_value") if prefer_raw else (
        "normalized_value",
        "raw_value",
    )
    for key in keys:
        if key in selected and selected[key] is not None and selected[key] != "":
            return selected[key]
    return _NO_STRUCTURED_PARAMETER


def apply_structured_parameter_projection(valve: ValveReport) -> None:
    """Make approved/frozen Stage 2 values authoritative for report rows."""
    if not isinstance(valve.structured_evidence, dict):
        return
    valve.spec_fields = dict(valve.spec_fields)

    # Once Stage 2 evidence is bound, absence of a specification order model
    # must not fall back to a DTM family/revision under the "model" label.
    # The report row will instead use the separately retained positioner type.
    positioner_model = _structured_parameter_value(valve, "positioner.model")
    if positioner_model is _NO_STRUCTURED_PARAMETER:
        valve.positioner_model = "未提供"

    attribute_paths = {
        "identity.device_tag": "device_tag",
        "identity.device_id": "device_id",
        "identity.serial_number": "serial_number",
        "identity.device_type": "positioner_type",
        "identity.manufacturer": "manufacturer",
        "identity.firmware_version": "firmware",
        "positioner.model": "positioner_model",
        "actuator.type": "actuator_type",
        "actuator.bench_range": "spring_range",
        "specification.model_no": "valve_model",
        "valve.type": "valve_type",
        "context.description": "description",
        "context.unit": "unit",
        "context.failure_consequence": "failure_consequence",
        "context.bypass": "bypass",
        "context.site_note": "site_note",
    }
    for path, attribute in attribute_paths.items():
        value = _structured_parameter_value(valve, path)
        if value is not _NO_STRUCTURED_PARAMETER and not isinstance(
            value, (dict, list)
        ):
            setattr(valve, attribute, str(value))

    specification_paths = {
        "specification.manufacturer": "manufacturer",
        "specification.model_no": "model_no",
        "specification.serial_number": "serial_number",
        "specification.valve_size": "valve_size",
        "specification.pressure_class": "pressure_class",
        "specification.end_connection": "end_connection",
        "specification.design_pressure": "design_pressure",
        "specification.design_temperature": "design_temperature",
        "specification.body_material": "body_material",
        "specification.body_type": "body_type",
        "specification.rated_cv": "rated_cv",
        "specification.leakage_class": "leakage_class",
        "specification.flow_action": "flow_action",
        "specification.trim_form": "trim_form",
        "specification.trim_size": "trim_size",
        "specification.characteristic": "characteristic",
        "actuator.type": "actuator_type",
        "actuator.model_full": "actuator_model_full",
        "actuator.model_display": "actuator_model",
        "actuator.size": "actuator_size",
        "actuator.action": "actuator_action",
        "actuator.fail_position": "fail_position",
        "actuator.bench_range": "bench_range",
        "actuator.supply_pressure": "supply_pressure",
        "actuator.travel": "actuator_travel",
        "accessory.positioner_model": "positioner_model",
        "accessory.positioner_type": "positioner_type",
        "accessory.diagnostics_level": "positioner_diagnostics_level",
    }
    for path, key in specification_paths.items():
        value = _structured_parameter_value(valve, path)
        if value is not _NO_STRUCTURED_PARAMETER and not isinstance(
            value, (dict, list)
        ):
            valve.spec_fields[key] = str(value)

    info_paths = {
        "identity.dtm_version": "diagnostic_software_version",
        "file.report_type": "report_type",
        "file.report_start_time": "start_time",
        "file.report_end_time": "end_time",
        "file.report_time": "report_time",
        "positioner.diagnostics_level": "diagnostics_level",
        "positioner.air_action": "air_action",
        "positioner.actuator_style": "actuator_style",
        "positioner.supply_pressure": "supply_pressure",
        "positioner.option_config": "option_config",
        "positioner.device_mode": "device_mode",
        "positioner.valve_position": "valve_position",
        "positioner.manual_setpoint": "manual_setpoint",
        "positioner.input_signal": "input_signal",
        "positioner.board_temperature": "board_temperature",
        "positioner.min_temperature": "min_temperature",
        "positioner.max_temperature": "max_temperature",
        "runtime.strokes": "strokes",
        "runtime.cycles": "cycles",
        "runtime.time_open": "time_open",
        "runtime.time_closed": "time_closed",
        "runtime.time_near_closed": "time_near_closed",
        "runtime.historical_runtime": "historical_runtime",
        "status.active_faults": "active_faults",
        "status.historical_faults": "historical_faults",
        "status.task_execution": "task_execution",
        "status.test_execution": "test_execution",
    }
    valve.positioner_report_info = dict(valve.positioner_report_info)
    for path, key in info_paths.items():
        value = _structured_parameter_value(
            valve,
            path,
            prefer_raw=path in {
                "positioner.supply_pressure",
                "runtime.time_open",
                "runtime.time_closed",
                "runtime.time_near_closed",
            },
        )
        if value is not _NO_STRUCTURED_PARAMETER:
            valve.positioner_report_info[key] = value

    active_faults = _structured_parameter_value(valve, "status.active_faults")
    if active_faults is not _NO_STRUCTURED_PARAMETER:
        values = active_faults if isinstance(active_faults, list) else [active_faults]
        valve.active_faults = [str(value) for value in values]
    tasks = _structured_parameter_value(valve, "status.task_execution")
    if isinstance(tasks, list):
        valve.test_execution_issues = task_execution_issues(tasks)

    metric_paths = {
        "metrics.average_error": "Average Error",
        "metrics.t86": "T86",
        "metrics.t63": "T63",
        "metrics.td": "Td",
        "metrics.overshoot": "Overshoot",
        "metrics.max_friction": "Max Friction",
        "metrics.min_friction": "Min Friction",
        "metrics.mean_friction": "Mean Friction",
        "metrics.response_time_open": "Response Time Open",
        "metrics.response_time_close": "Response Time Close",
        "metrics.supply_pressure": "Supply Pressure",
    }
    for path, key in metric_paths.items():
        value = _structured_parameter_value(valve, path)
        if value is not _NO_STRUCTURED_PARAMETER:
            valve.metrics[key] = value
    run_ramp = dict(valve.metrics.get("run_ramp") or {})
    for family in ("hysteresis", "dead_band", "linearity"):
        family_values = dict(run_ramp.get(family) or {})
        for statistic in ("max", "average", "min"):
            value = _structured_parameter_value(
                valve,
                f"metrics.run_ramp.{family}.{statistic}",
            )
            if value is not _NO_STRUCTURED_PARAMETER:
                family_values[statistic] = value
        if family_values:
            run_ramp[family] = family_values
    if run_ramp:
        valve.metrics["run_ramp"] = run_ramp

    valve.valve_spec = format_valve_spec(valve.spec_fields) or valve.valve_spec


def apply_valve_context(valve: ValveReport) -> None:
    context = VALVE_CONTEXT_BY_TAG.get(valve.folder, {})
    valve.description = context.get("description", valve.description)
    valve.unit = context.get("unit", valve.unit)
    valve.failure_consequence = context.get("failure_consequence", valve.failure_consequence)
    valve.bypass = context.get("bypass", valve.bypass)
    valve.site_note = context.get("note", valve.site_note)


def apply_specification_identity_fallback(valve: ValveReport) -> None:
    """Fill identity fields from a matched spec only when raw data is absent."""
    if not valve.serial_number and valve.spec_fields.get("serial_number"):
        valve.serial_number = clean_spaces(valve.spec_fields["serial_number"])


def parse_devdata(path: Path) -> tuple[list[TestRecord], dict[str, str]]:
    records: list[TestRecord] = []
    info: dict[str, str] = {}
    try:
        root = ET.parse(path).getroot()
    except Exception as exc:
        raise RuntimeError(f"XML解析失败: {exc}") from exc

    pos = root.find(".//PositionerInfo")
    if pos is not None:
        info = {child.tag: (child.text or "") for child in list(pos)}

    for test in root.findall(".//Tests/Test"):
        header = test.find("Header")
        hdr = header.attrib if header is not None else {}
        rec = TestRecord(
            source=path.name,
            source_type="devdata",
            test_type=test.get("type", "UNKNOWN"),
            device_id=hdr.get("deviceId", info.get("DeviceId", "")),
            device_tag=hdr.get("deviceTag", info.get("Tag", "")),
            started=hdr.get("started", ""),
        )
        general = test.find("GeneralResults")
        if general is not None:
            rec.attrs.update(general.attrib)
        settings = test.find("SignatureSettings")
        if settings is not None:
            rec.attrs.update({f"setting_{k}": v for k, v in settings.attrib.items()})
            sp = fnum(settings.attrib.get("supplyPres"))
            if sp is not None:
                rec.supply_pressures.append(sp)
            if settings.attrib.get("isSingle") == "1":
                rec.attrs["actuator_type"] = "Single Acting"
            elif settings.attrib.get("isSingle") == "0":
                rec.attrs["actuator_type"] = "Double Acting"
        for step in test.findall("Step"):
            rec.steps.append(dict(step.attrib))
        records.append(rec)
    return records, info


def parse_spring_range(text: str) -> str:
    spring_match = re.search(
        r"Spring Range\s+([-+]?\d+(?:\.\d+)?)\s*(psi|kPa|bar|MPa)?\s+"
        r"([-+]?\d+(?:\.\d+)?)\s*(psi|kPa|bar|MPa)\b",
        clean_spaces(text),
        re.I,
    )
    if not spring_match:
        return ""
    first_unit = spring_match.group(2)
    final_unit = spring_match.group(4)
    if first_unit and first_unit.lower() != final_unit.lower():
        return ""
    first = float(spring_match.group(1))
    second = float(spring_match.group(3))
    low, high = sorted((first, second))
    return f"{low:g}–{high:g} {final_unit}"


_EXTENDED_SIGNATURE_TITLE_RE = re.compile(
    r"\bExtended\s+Act(?:uator)?\.?\s+"
    r"(?:Signature|Status\s*:\s*Signature)\b",
    re.I,
)
_STANDARD_SIGNATURE_TITLE_RE = re.compile(
    r"\bStandard\s+Signature\b",
    re.I,
)
_GENERIC_SIGNATURE_TITLE_RE = re.compile(
    r"\bRun\s+Signature\s+Test\b",
    re.I,
)
_ORDINARY_STEP_TYPE_RE = re.compile(
    r"\b(?:Multiple|Muti\s*plo|Single)\s+St(?:e|o|a)ps?\b",
    re.I,
)
_PATTERN_STEP_TYPE_RE = re.compile(
    r"\bPatte(?:rn|m)\s+St[eoa]p[a-z]\b",
    re.I,
)
_PATTERN_STEP_TITLE_RE = re.compile(
    r"\bPatte(?:rn|m)\s+St[eoa]p\s+Test\b",
    re.I,
)
_CURVE_TEST_TYPES = {
    "run_step": "STEP",
    "pattern_step": "PATTERN-STEP",
    "run_ramp": "SIG-POS",
    "standard_signature": "SIG-S",
    "extended_signature": "SIG-EXT",
}
_CURVE_TITLES = {
    "run_step": "Run Step Test",
    "pattern_step": "Pattern Step Test",
    "run_ramp": "Run Ramp Test",
    "standard_signature": "Standard Signature",
    "extended_signature": "Extended Actuator Signature",
}
_FRICTION_NUMBER_RE = r"([-+]?\d+(?:\.\d+)?)"


def _pdf_text_pages(text: str) -> list[str]:
    pages = text.split("\f")
    while len(pages) > 1 and not pages[-1].strip():
        pages.pop()
    return pages or [text]


def _step_section_counts(text: str) -> tuple[int, int]:
    """Count source Run Step sections, excluding customer status captions."""
    compact = clean_spaces(text)
    titles = list(re.finditer(r"\bRun\s+Step\s+Test\b", compact, re.I))
    ordinary = 0
    pattern = 0
    untyped = 0
    for index, title in enumerate(titles):
        end = titles[index + 1].start() if index + 1 < len(titles) else len(compact)
        section = compact[title.start() : end]
        if not re.search(
            r"\b(?:Task|Taak|Tack)\s+P\w*|"
            r"\b(?:Step|Stop|Stap|Stce)\s+Ty\w*|"
            r"\bResult\s*:",
            section,
            re.I,
        ):
            continue
        if (
            _PATTERN_STEP_TYPE_RE.search(section)
            or _PATTERN_STEP_TITLE_RE.search(section)
        ):
            pattern += 1
        elif _ORDINARY_STEP_TYPE_RE.search(section):
            ordinary += 1
        else:
            untyped += 1
    if pattern == 0 and untyped and _PATTERN_STEP_TITLE_RE.search(compact):
        pattern = 1
        untyped -= 1
    ordinary += untyped
    return ordinary, pattern


def page_curve_test_keys(text: str) -> tuple[str, ...]:
    """Classify physical curve tests on one PDF page from specific evidence.

    Customer report captions may repeat a generic English test name after the
    source test title.  Explicit test semantics therefore take precedence over
    generic aliases, while genuinely different explicit tests may coexist.
    """
    compact = clean_spaces(text)
    if not compact or "Task Execution Summary" in compact:
        return ()

    keys: set[str] = set()
    ordinary_step_types = list(_ORDINARY_STEP_TYPE_RE.finditer(compact))
    pattern_step_types = list(_PATTERN_STEP_TYPE_RE.finditer(compact))
    ordinary_sections, pattern_sections = _step_section_counts(compact)
    has_explicit_pattern = bool(
        pattern_sections
        or pattern_step_types
        or _PATTERN_STEP_TITLE_RE.search(compact)
    )
    has_pattern = has_explicit_pattern or (
        not ordinary_step_types and is_pattern_step(compact)
    )
    has_run_step = bool(re.search(r"\bRun\s+Step\s+Test\b", compact, re.I))
    if has_pattern:
        keys.add("pattern_step")
    if ordinary_sections or ordinary_step_types or (has_run_step and not has_pattern):
        keys.add("run_step")

    if re.search(
        r"\bRun\s+Ramp\s+Test\b|\bRamp\s+Test\s+Information\b|\bHysteresis\b",
        compact,
        re.I,
    ):
        keys.add("run_ramp")

    has_extended = bool(_EXTENDED_SIGNATURE_TITLE_RE.search(compact))
    has_standard = bool(_STANDARD_SIGNATURE_TITLE_RE.search(compact))
    has_generic_signature = bool(_GENERIC_SIGNATURE_TITLE_RE.search(compact))
    if has_standard:
        keys.add("standard_signature")
    if has_extended:
        keys.add("extended_signature")
    if has_generic_signature and not (has_standard or has_extended):
        keys.add("standard_signature")

    return tuple(key for key in TEST_ORDER if key in keys and key != "other")


def parse_curve_inventory(text: str, source: str) -> list[dict[str, Any]]:
    """Return one stable curve record per physical page and curve type."""
    records: list[dict[str, Any]] = []
    for page_number, page_text in enumerate(_pdf_text_pages(text), start=1):
        compact = clean_spaces(page_text)
        ordinary_sections, pattern_sections = _step_section_counts(compact)
        ordinary_step_count = max(
            ordinary_sections,
            len(_ORDINARY_STEP_TYPE_RE.findall(compact)),
        )
        pattern_step_count = max(
            pattern_sections,
            len(_PATTERN_STEP_TYPE_RE.findall(compact)),
        )
        for test_key in page_curve_test_keys(page_text):
            count = {
                "run_step": max(ordinary_step_count, 1),
                "pattern_step": max(pattern_step_count, 1),
            }.get(test_key, 1)
            strong = {
                "run_step": ordinary_sections > 0
                or ordinary_step_count > 0
                or bool(
                    re.search(
                        r"\bRun\s+Step\s+Test\b.*?\bTask\s+Param\w*",
                        compact,
                        re.I,
                    )
                ),
                "pattern_step": pattern_sections > 0 or pattern_step_count > 0,
                "run_ramp": bool(
                    re.search(
                        r"\bRamp\s+Test\s+Information\b|"
                        r"\bRun\s+Ramp\s+Test\b.*?\bTask\s+Param\w*|"
                        r"\bMaximum\s+Hysteresis\b|"
                        r"\bHyst(?:eresis|ersis)\s*&\s*Deadband\b",
                        compact,
                        re.I,
                    )
                ),
                "standard_signature": bool(
                    _STANDARD_SIGNATURE_TITLE_RE.search(compact)
                ),
                "extended_signature": bool(
                    _EXTENDED_SIGNATURE_TITLE_RE.search(compact)
                ),
            }.get(test_key, False)
            for _ in range(count):
                candidate = {
                    "source": source,
                    "page": page_number,
                    "test_key": test_key,
                    "test_type": _CURVE_TEST_TYPES[test_key],
                    "title": _CURVE_TITLES[test_key],
                    "status": "completed",
                    "_strong": strong,
                }
                previous_index = next(
                    (
                        index
                        for index in range(len(records) - 1, -1, -1)
                        if records[index]["test_key"] == test_key
                        and records[index]["page"] == page_number - 1
                    ),
                    None,
                )
                if previous_index is not None:
                    previous = records[previous_index]
                    if previous["_strong"] and not strong:
                        continue
                    if strong and not previous["_strong"]:
                        records[previous_index] = candidate
                        continue
                    if not strong and not previous["_strong"]:
                        continue
                records.append(candidate)
    for record in records:
        record.pop("_strong", None)
    totals = Counter(str(record["test_key"]) for record in records)
    seen: Counter[str] = Counter()
    for record in records:
        key = str(record["test_key"])
        seen[key] += 1
        record["occurrence"] = seen[key]
        record["is_current"] = seen[key] == totals[key]
    return records


def _signature_title_events(page_text: str) -> list[tuple[int, str]]:
    explicit = [
        (match.start(), test_type)
        for pattern, test_type in (
            (_STANDARD_SIGNATURE_TITLE_RE, "SIG-S"),
            (_EXTENDED_SIGNATURE_TITLE_RE, "SIG-EXT"),
        )
        for match in pattern.finditer(page_text)
    ]
    if explicit:
        return sorted(explicit)
    return [
        (match.start(), "SIG-S")
        for match in _GENERIC_SIGNATURE_TITLE_RE.finditer(page_text)
    ]


def _parse_signature_friction_block(block: str) -> dict[str, Any]:
    """Parse one signature result block without mixing its units.

    ValVue3 has two layouts: newer reports print labelled Max/Mean/Min rows,
    while older Run Signature reports print one ``Friction Max Min Average``
    row.  The returned values deliberately keep percent and pressure channels
    separate; comparing those numbers would produce a misleading valve-level
    maximum.
    """
    values: dict[str, dict[str, float]] = {"max": {}, "min": {}, "mean": {}}
    # The customer PDFs contain several OCR spellings of psi (psí, píi,
    # pí, psl, pai).  Normalize only these bounded unit tokens before reading
    # the High/Low/Mean table; do not alter arbitrary words or numbers.
    normalized = re.sub(
        r"(?<![A-Za-z])(?:psi|psí|píi|pí|psl|pai|pıi)(?![A-Za-z])",
        "psi",
        block,
        flags=re.I,
    )
    normalized = re.sub(
        r"(?<!Max )(?<!Min )(?<!Mean )\bFriction\s*\(\s*%\s*\)",
        "Friction",
        normalized,
        flags=re.I,
    )
    unit_re = r"(?:\(\s*(%|psi|kPa|bar|MPa)\s*\)|(%|psi|kPa|bar|MPa))"
    for label, key in (("Max", "max"), ("Min", "min"), ("Mean", "mean")):
        for match in re.finditer(
            rf"\b{label}\s+Friction\s*{unit_re}\s*:?\s*{_FRICTION_NUMBER_RE}",
            normalized,
            re.I,
        ):
            unit = (match.group(1) or match.group(2) or "").lower()
            value = fnum(match.group(3))
            if value is not None:
                values[key][unit] = value

    # Legacy table layout: Parameter / Max / Min / Average followed by one
    # percent row and, in some reports, one pressure row.
    row_re = re.compile(
        rf"\bFriction\s+{_FRICTION_NUMBER_RE}\s*(%|psi|kPa|bar|MPa)\s+"
        rf"{_FRICTION_NUMBER_RE}\s*\2\s+{_FRICTION_NUMBER_RE}\s*\2",
        re.I,
    )
    for match in row_re.finditer(normalized):
        unit = match.group(2).lower()
        row_values = [fnum(match.group(1)), fnum(match.group(3)), fnum(match.group(4))]
        if all(value is not None for value in row_values):
            limit = 100.0 if unit == "%" else 200.0 if unit == "psi" else 2000.0
            if all(abs(value) <= limit for value in row_values):
                values["max"].setdefault(unit, row_values[0])
                values["min"].setdefault(unit, row_values[1])
                values["mean"].setdefault(unit, row_values[2])
    return values


def parse_signature_friction_records(text: str, source: str) -> list[dict[str, Any]]:
    """Return one friction result per Standard/Extended Signature occurrence."""
    records: list[dict[str, Any]] = []
    occurrence_by_type: Counter[str] = Counter()
    for page_text in _pdf_text_pages(text):
        compact = clean_spaces(page_text)
        events = _signature_title_events(compact)
        for index, (start, test_type) in enumerate(events):
            end = events[index + 1][0] if index + 1 < len(events) else len(compact)
            block = compact[start:end]
            occurrence_by_type[test_type] += 1
            records.append(
                {
                    "source": source,
                    "occurrence": occurrence_by_type[test_type],
                    "test_type": test_type,
                    "values": _parse_signature_friction_block(block),
                }
            )
    return records


def _report_faults(compact: str, section: str, following: str) -> list[str]:
    match = re.search(rf"{section}\s*(.*?)\s*{following}", compact, re.I)
    if not match:
        return []
    return [
        value.strip()
        for value in match.group(1).split(";")
        if value.strip().lower() not in {"", "reset", "no error", "none"}
    ]


def parse_sequence_pdf(path: Path) -> tuple[list[TestRecord], dict[str, Any]]:
    text = "\f".join(run_pdftotext_pages(path))
    compact = clean_spaces(text)

    info = {
        "source": path.name,
        "report_type": "sequencer_execution",
        "device_tag": first_regex_value(
            compact,
            [
                r"Positioner Tag:\s*([A-Za-z0-9\-]+)",
                r"Positioner Tag\s+([A-Za-z0-9\-]+)",
                r"Device Tag\s+([A-Za-z0-9\-]+)",
            ],
        ),
        "serial_number": first_regex_value(
            compact,
            [
                r"Serial Number\s*:?\s*([A-Z0-9]+)",
                r"Device S/N\s+([A-Z0-9]+)",
                r"序列号\s*[:：]?\s*([A-Z0-9]+)",
            ],
        ),
        "device_id": first_regex_value(
            compact,
            [
                r"Device ID:\s*([0-9_]+)",
                r"Device ID\s+([0-9_]+)",
            ],
        ).strip("_"),
        "device_type": first_regex_value(
            compact,
            [
                r"Device Type ID\s+(.+?)\s+Address",
                r"Device Type\s+(.+?)\s+Address",
                r"Dev\. Type/Rev\s+(.+?)(?=\s+Polling Address)",
            ],
        ),
        "firmware": first_regex_value(
            compact,
            [r"Firmware Version\s+([0-9.]+)", r"Firmware Rev(?:ision)?\s+([0-9.]+(?:\s*/\s*[0-9]+)?)"],
        ),
        "diagnostics_level": first_regex_value(
            compact,
            [
                r"Diagnostic Level\s+(.+?)\s+Position Unit",
                r"Diagnostics Level\s+(.+?)\s+Position Unit",
                r"Diagnostics Level\s+(.+?)(?=\s+DTM Version)",
            ],
        ),
        "manufacturer": first_regex_value(
            compact,
            [
                r"Manufacturer ID\s+(.+?)(?=\s+Diagnostics Level)",
                r"Manufacturer ID\s+(.+?)\s+(?:Start Time|开始时间|Configuration|配置)",
            ],
        ),
        "diagnostic_software_version": first_regex_value(
            compact,
            [r"DTM Version\s+(.+?)(?=\s+Descriptor|\s+Firmware Rev)"],
        ),
        "device_address": first_regex_value(compact, [r"Address\s*:?\s*([0-9]+)"]),
        "start_time": first_regex_value(
            compact,
            [
                r"Start Time:\s*([0-9/:\sAPM]+?)\s+End Time:",
                r"Start Time\s+([0-9/:\sAPM]+?)\s+End Time",
            ],
        ),
        "end_time": first_regex_value(
            compact,
            [
                r"End Time:\s*([0-9/:\sAPM]+?)\s+Total Task Count:",
                r"End Time\s+([0-9/:\sAPM]+?)\s+Total Duration",
            ],
        ),
        "air_action": first_regex_value(
            compact,
            [r"Air Action\s+(Air To Open|Air To Close|ATC|ATO|气动打开|气动关闭)"],
        ),
        "actuator_type": regex_value(compact, r"Actuator Type\s+(Single|Double)"),
        "supply_pressure": first_regex_value(
            compact,
            [r"Supply Pressure\s+([-+]?\d+(?:\.\d+)?\s*(?:psi|kPa))"],
        ),
        "option_config": first_regex_value(
            compact, [r"Option Config\s+(.+?)(?=\s+(?:Configuration|Operating Data))"]
        ),
        "device_mode": first_regex_value(compact, [r"Device Mode\s+([A-Za-z]+)"]),
        "valve_position": first_regex_value(
            compact, [r"Valve Position\s+([-+]?\d+(?:\.\d+)?\s*%)"]
        ),
        "manual_setpoint": first_regex_value(
            compact, [r"Manual Setpoint\s+([-+]?\d+(?:\.\d+)?\s*%)"]
        ),
        "input_signal": first_regex_value(
            compact, [r"Input Signal\s+([-+]?\d+(?:\.\d+)?\s*mA)"]
        ),
        "board_temperature": first_regex_value(
            compact, [r"Board Temp\.?\s+([-+]?\d+(?:\.\d+)?\s*degC)"]
        ),
        "min_temperature": first_regex_value(
            compact, [r"Min Temp\.?\s+([-+]?\d+(?:\.\d+)?\s*degC)"]
        ),
        "max_temperature": first_regex_value(
            compact, [r"Max Temp\.?\s+([-+]?\d+(?:\.\d+)?\s*degC)"]
        ),
        "strokes": first_regex_value(compact, [r"Strokes\s*\(counts\)\s+(\d+)"]),
        "cycles": first_regex_value(compact, [r"Cy(?:cl|el)es\s*\(counts\)\s+(\d+)"]),
        "time_open": first_regex_value(compact, [r"Time Open\s*\(hours\)\s+(\d+(?:\.\d+)?)"]),
        "time_closed": first_regex_value(compact, [r"Time Closed\s*\(hours\)\s+(\d+(?:\.\d+)?)"]),
        "time_near_closed": first_regex_value(compact, [r"Time Near Closed\s*\(hours\)\s+(\d+(?:\.\d+)?)"]),
        "active_faults": _report_faults(compact, "Active Faults", "Historical Faults"),
        "historical_faults": _report_faults(compact, "Historical Faults", "Raw Data"),
        "task_execution": parse_task_execution(text),
        "task_execution_expected_count": (
            int(value)
            if (value := first_regex_value(compact, [r"Total Task Count\s*:?\s*(\d+)"]))
            else None
        ),
        "fault_data_status": (
            "present"
            if "Active Faults" in compact and "Historical Faults" in compact
            else "source_missing"
        ),
        "task_execution_status": (
            "present" if "Task Execution Summary" in compact else "source_missing"
        ),
        "report_time": parse_report_execution_window(text),
        "spring_range": "",
        "dtm_source_status": "present" if "Positioner Info" in compact else "source_missing",
        "dtm_parser_status": "parsed" if "Positioner Info" in compact else "not_applicable",
    }
    info["test_execution_issues"] = task_execution_issues(info["task_execution"])
    expected_task_count = info["task_execution_expected_count"]
    if info["task_execution_status"] == "source_missing":
        info["task_execution_parser_status"] = "not_applicable"
    elif expected_task_count is not None and len(info["task_execution"]) != expected_task_count:
        info["task_execution_parser_status"] = "source_present_parser_missing"
    elif not info["task_execution"]:
        info["task_execution_parser_status"] = "source_present_parser_missing"
    else:
        info["task_execution_parser_status"] = "parsed"
    info["spring_range"] = parse_spring_range(compact)
    records: list[TestRecord] = []

    if "Run Step Test" in compact:
        rec = TestRecord(
            source=path.name,
            source_type="pdf",
            test_type="STEP",
            device_id=info.get("device_id", ""),
            device_tag=info.get("device_tag", ""),
        )
        rec.steps = parse_step_table_rows(text)
        number_or_na = r"(N/A|[-+]?\d+(?:\.\d+)?)"
        if not rec.steps:
            if ALLOW_LOOSE_PDF_METRICS:
                avg_errors = regex_values(compact, rf"Average Error(?: \(%\))?:\s*{number_or_na}%?")
                t86s = regex_values(compact, rf"T86(?: \(s\))?:\s*{number_or_na}\s*s?")
                t63s = regex_values(compact, rf"T63(?: \(s\))?:\s*{number_or_na}\s*s?")
                tds = regex_values(compact, rf"Td(?: \(s\))?:\s*{number_or_na}\s*s?")
                overs = regex_values(compact, rf"Max(?:imum)?\s+Over\s*Shoot:?\s*{number_or_na}%?")
                if not overs:
                    overs = regex_values(compact, rf"Overshoot \( %\):\s*{number_or_na}")
            else:
                avg_errors = regex_values(compact, rf"Average Error \(%\):\s*{number_or_na}")
                t86s = regex_values(compact, rf"T86 \(s\):\s*{number_or_na}")
                t63s = regex_values(compact, rf"T63 \(s\):\s*{number_or_na}")
                tds = regex_values(compact, rf"Td \(s\):\s*{number_or_na}")
                overs = regex_values(compact, rf"Overshoot \( %\):\s*{number_or_na}")
            count = max(len(avg_errors), len(t86s), len(t63s), len(tds), len(overs))
            for i in range(count):
                rec.steps.append(
                    {
                        "AverageError": avg_errors[i] if i < len(avg_errors) else "",
                        "T86": t86s[i] if i < len(t86s) else "",
                        "T63": t63s[i] if i < len(t63s) else "",
                        "Td": tds[i] if i < len(tds) else "",
                        "MaxOverShoot": overs[i] if i < len(overs) else "",
                    }
                )
        if not rec.steps and ALLOW_LOOSE_PDF_METRICS:
            t86_row = table_metric_values(compact, "T86")
            t63_row = table_metric_values(compact, "T63")
            td_row = table_metric_values(compact, "Td")
            overshoot_row = table_metric_values(compact, "Overshoot")
            error_row = table_metric_values(compact, "Error")
            if any([t86_row, t63_row, td_row, overshoot_row, error_row]):
                rec.steps.append(
                    {
                        "AverageError": max_text_value(error_row),
                        "T86": max_text_value(t86_row),
                        "T63": max_text_value(t63_row),
                        "Td": max_text_value(td_row),
                        "MaxOverShoot": max_text_value(overshoot_row),
                    }
                )
        if rec.steps:
            rec.supply_pressures.extend(extract_supply_pressures_from_text(text))
        records.append(rec)

    if "Run Ramp Test" in compact:
        rec = TestRecord(
            source=path.name,
            source_type="pdf",
            test_type="SIG-POS",
            device_id=info.get("device_id", ""),
            device_tag=info.get("device_tag", ""),
        )
        rec.attrs.update(
            {
                "MaxHysteresis": first_regex_value(
                    compact,
                    [
                        r"Maximum Hysteresis Dead Band \( %\):\s*([-+]?\d+(?:\.\d+)?)",
                        r"Hystersis & Deadband:\s*Max:\s*([-+]?\d+(?:\.\d+)?)%?",
                        r"Hysteresis & Deadband:\s*Max:\s*([-+]?\d+(?:\.\d+)?)%?",
                    ],
                ),
                "MinHysteresis": first_regex_value(
                    compact,
                    [
                        r"Minimum Hysteresis Dead Band \( %\):\s*([-+]?\d+(?:\.\d+)?)",
                        r"Hystersis & Deadband:\s*Max:\s*[-+]?\d+(?:\.\d+)?%?\s*Min:\s*([-+]?\d+(?:\.\d+)?)%?",
                        r"Hysteresis & Deadband:\s*Max:\s*[-+]?\d+(?:\.\d+)?%?\s*Min:\s*([-+]?\d+(?:\.\d+)?)%?",
                    ],
                ),
                "AverageHysteresis": first_regex_value(
                    compact,
                    [
                        r"Average Hysteresis Dead Band \( %\):\s*([-+]?\d+(?:\.\d+)?)",
                        r"Hystersis & Deadband:.*?Average:\s*([-+]?\d+(?:\.\d+)?)%?",
                        r"Hysteresis & Deadband:.*?Average:\s*([-+]?\d+(?:\.\d+)?)%?",
                    ],
                ),
                "MaxErrorUp": first_regex_value(
                    compact,
                    [
                        r"Maximum Error Up \( %\):\s*([-+]?\d+(?:\.\d+)?)",
                        r"Ramp#1:.*?Maximum Error:\s*([-+]?\d+(?:\.\d+)?)%?",
                    ],
                ),
                "AverageErrorUp": first_regex_value(
                    compact,
                    [
                        r"Average Error Up \( %\):\s*([-+]?\d+(?:\.\d+)?)",
                        r"Ramp#1:.*?Average Error:\s*([-+]?\d+(?:\.\d+)?)%?",
                    ],
                ),
                "MaxErrorDown": first_regex_value(
                    compact,
                    [
                        r"Maximum Error Down \( %\):\s*([-+]?\d+(?:\.\d+)?)",
                        r"Ramp#2:.*?Maximum Error:\s*([-+]?\d+(?:\.\d+)?)%?",
                    ],
                ),
                "AverageErrorDown": first_regex_value(
                    compact,
                    [
                        r"Average Error Down \( %\):\s*([-+]?\d+(?:\.\d+)?)",
                        r"Ramp#2:.*?Average Error:\s*([-+]?\d+(?:\.\d+)?)%?",
                    ],
                ),
            }
        )
        if not ALLOW_LOOSE_PDF_METRICS:
            rec.attrs.update(
                {
                    "MaxErrorUp": regex_value(compact, r"Maximum Error Up \( %\):\s*([-+]?\d+(?:\.\d+)?)"),
                    "AverageErrorUp": regex_value(compact, r"Average Error Up \( %\):\s*([-+]?\d+(?:\.\d+)?)"),
                    "MaxErrorDown": regex_value(compact, r"Maximum Error Down \( %\):\s*([-+]?\d+(?:\.\d+)?)"),
                    "AverageErrorDown": regex_value(compact, r"Average Error Down \( %\):\s*([-+]?\d+(?:\.\d+)?)"),
                }
            )
        rec.attrs.update({key: value for key, value in parse_hysteresis_dead_band(text).items() if value})
        if ALLOW_LOOSE_PDF_METRICS and not rec.attrs.get("MaxErrorUp"):
            ramp_error = table_metric_values(compact, "Error")
            if ramp_error:
                rec.attrs["MaxErrorUp"] = max_text_value(ramp_error)
                rec.attrs["AverageErrorUp"] = avg_text_value(ramp_error)
        if ALLOW_LOOSE_PDF_METRICS:
            rec.supply_pressures.extend(extract_supply_pressures_from_text(text))
        records.append(rec)

    if INCLUDE_SIGNATURE_METRICS:
        for friction in parse_signature_friction_records(text, path.name):
            values = friction["values"]
            rec = TestRecord(
                source=path.name,
                source_type="pdf",
                test_type=friction["test_type"],
                device_id=info.get("device_id", ""),
                device_tag=info.get("device_tag", ""),
                occurrence=friction["occurrence"],
            )
            # Keep the historical scalar attribute names for callers while
            # retaining every unit and the source identity for provenance.
            rec.attrs.update(
                {
                    "maxFric": values["max"].get("%"),
                    "minFric": values["min"].get("%"),
                    "aveFric": values["mean"].get("%"),
                    "maxFric_pres": values["max"].get("psi"),
                    "minFric_pres": values["min"].get("psi"),
                    "aveFric_pres": values["mean"].get("psi"),
                    "friction_source": path.name,
                    "friction_occurrence": friction["occurrence"],
                    "friction_values": values,
                }
            )
            rec.supply_pressures.extend(extract_supply_pressures_from_text(text))
            records.append(rec)

    return records, info


def parse_test_execution_issues(text: str) -> list[dict[str, Any]]:
    """Read non-success task results from the Task Execution Summary table."""
    tasks = parse_task_execution(text)
    if tasks:
        return task_execution_issues(tasks)
    occurrences: Counter[str] = Counter()
    issues: list[dict[str, Any]] = []
    display_names = {
        "run_step": "Run Step Test",
        "pattern_step": "Pattern Step Test",
        "run_ramp": "Run Ramp Test",
        "standard_signature": "Standard Signature",
        "extended_signature": "Extended Actuator Signature",
        "performance_check": "Performance Check",
        "change_mode": "Change Mode",
    }
    status_pattern = re.compile(
        r"\b(Succeeded|Normal|Failed|Cancelled|Aborted|Incomplete|Not Completed)\s*$",
        re.I,
    )
    for page_text in _pdf_text_pages(text):
        page_keys = set(page_curve_test_keys(page_text))
        page_results: list[tuple[str, str, str, int]] = []
        for raw_line in page_text.splitlines():
            line = clean_spaces(raw_line)
            for test_name, default_key in TEST_EXECUTION_TASKS:
                if not line.lower().startswith(test_name.lower()):
                    continue
                status_match = status_pattern.search(line)
                if not status_match:
                    break
                test_key = default_key
                specificity = 1
                if test_name == "Run Signature Test":
                    signature_keys = page_keys & {
                        "standard_signature",
                        "extended_signature",
                    }
                    if len(signature_keys) == 1:
                        test_key = next(iter(signature_keys))
                    specificity = 0
                elif (
                    test_name == "Run Step Test"
                    and "pattern_step" in page_keys
                    and "run_step" not in page_keys
                ):
                    test_key = "pattern_step"
                    specificity = 0
                status = status_match.group(1).title()
                alias_index = next(
                    (
                        index
                        for index, result in enumerate(page_results)
                        if result[0] == test_key
                        and result[1] == status
                        and result[2] != test_name
                    ),
                    None,
                )
                candidate = (test_key, status, test_name, specificity)
                if alias_index is None:
                    page_results.append(candidate)
                elif specificity > page_results[alias_index][3]:
                    page_results[alias_index] = candidate
                break
        for test_key, status, _test_name, _specificity in page_results:
            occurrences[test_key] += 1
            if status.lower() in TEST_EXECUTION_FAILURE_STATUSES:
                issues.append(
                    {
                        "test_key": test_key,
                        "test_name": display_names.get(test_key, test_key),
                        "occurrence": occurrences[test_key],
                        "status": status,
                    }
                )
    return issues


def parse_task_execution(text: str) -> list[dict[str, Any]]:
    """Preserve every row from Task Execution Summary with timing provenance."""
    names = sorted(TEST_EXECUTION_TASKS, key=lambda item: len(item[0]), reverse=True)
    timestamp = r"\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2}:\d{2}\s+[AP]M"
    result_pattern = r"Succeeded|Success|Setup|Normal|Failed|Cancelled|Aborted|Incomplete|Not Completed"
    occurrences: Counter[str] = Counter()
    tasks: list[dict[str, Any]] = []
    canonical_names = {
        "run_step": "Run Step Test",
        "pattern_step": "Pattern Step Test",
        "run_ramp": "Run Ramp Test",
        "standard_signature": "Standard Signature",
        "extended_signature": "Extended Actuator Signature",
        "performance_check": "Performance Check",
        "change_mode": "Change Mode",
    }
    name_pattern = "|".join(re.escape(source_name) for source_name, _ in names)
    key_by_name = {source_name.lower(): test_key for source_name, test_key in names}
    compact = clean_spaces(text)
    for match in re.finditer(
        rf"({name_pattern})\s+({timestamp})\s+({timestamp})\s+"
        rf"(\d+(?:\.\d+)?)\s+({result_pattern})",
        compact,
        re.I,
    ):
        source_name = match.group(1)
        test_key = key_by_name[source_name.lower()]
        occurrences[test_key] += 1
        duration = float(match.group(4))
        tasks.append({
            "task_name": canonical_names.get(test_key, source_name),
            "occurrence": occurrences[test_key],
            "start_time": match.group(2),
            "end_time": match.group(3),
            "duration_seconds": int(duration) if duration.is_integer() else duration,
            "result": "Succeeded" if match.group(5).lower() == "success" else match.group(5).title(),
            "test_key": test_key,
        })
    return tasks


def attach_curve_execution_times(
    images: list[dict[str, Any]],
    tasks: list[dict[str, Any]],
) -> None:
    """Bind each rendered curve to its chronological task execution window."""
    task_index = 0
    for image in images:
        image_key = str(image.get("test_key") or "other")
        compatible_keys = {image_key}
        # ValvXpert records Pattern Step as a Run Step Test in the task summary.
        if image_key == "pattern_step":
            compatible_keys.add("run_step")
        for index in range(task_index, len(tasks)):
            task = tasks[index]
            if str(task.get("test_key") or "") not in compatible_keys:
                continue
            image["test_start_time"] = str(task.get("start_time") or "")
            image["test_end_time"] = str(task.get("end_time") or "")
            image["test_result"] = str(task.get("result") or "")
            task_index = index + 1
            break


def task_execution_issues(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the report-facing non-success view without losing full tasks."""
    return [
        {
            "test_key": str(task.get("test_key") or ""),
            "test_name": str(task.get("task_name") or ""),
            "occurrence": int(task.get("occurrence") or 1),
            "status": str(task.get("result") or ""),
        }
        for task in tasks
        if str(task.get("result") or "").lower() in TEST_EXECUTION_FAILURE_STATUSES
    ]


def table_metric_values(text: str, label: str) -> list[str]:
    values: list[str] = []
    number_with_unit = r"([-+]?\d+(?:\.\d+)?)\s*(?:%|s|psi)?"
    pattern = rf"\b{re.escape(label)}\s+{number_with_unit}(?:\s+{number_with_unit})?(?:\s+{number_with_unit})?"
    for match in re.finditer(pattern, text, re.I):
        values.extend(group for group in match.groups() if group)
    return values


def max_text_value(values: list[str]) -> str:
    nums = [v for v in (fnum(value) for value in values) if v is not None]
    return str(max(nums)) if nums else ""


def avg_text_value(values: list[str]) -> str:
    nums = [v for v in (fnum(value) for value in values) if v is not None]
    return str(nums[-1]) if nums else ""


def extract_supply_pressures_from_text(text: str) -> list[float]:
    pressures: list[float] = []
    for match in re.finditer(r"Supply Pressure[:\s]+([-+]?\d+(?:\.\d+)?)\s*kPa", text, re.I):
        value = fnum(match.group(1))
        if value is not None:
            pressure_psi = value * 0.1450377377
            if 0 < pressure_psi <= 200:
                pressures.append(pressure_psi)
    psi_patterns = [
        r"Supply Pressure\s+\(psi\):\s*([-+]?\d+(?:\.\d+)?)",
        r"Supply Pressure[:\s]+([-+]?\d+(?:\.\d+)?)\s*p(?:si|sí|ai|í)\b",
    ]
    for pattern in psi_patterns:
        for match in re.finditer(pattern, text, re.I):
            value = fnum(match.group(1))
            if value is not None and 0 < value <= 200:
                pressures.append(value)
    return pressures


def parse_step_table_rows(text: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    row_re = re.compile(
        r"^\s*([-+]?\d+(?:\.\d+)?)\s*-\s*>\s*([-+]?\d+(?:\.\d+)?)\s+"
        r"(N/A|[-+]?\d+(?:\.\d+)?)\s+"
        r"(N/A|[-+]?\d+(?:\.\d+)?)\s+"
        r"(N/A|[-+]?\d+(?:\.\d+)?)\s+"
        r"(N/A|[-+]?\d+(?:\.\d+)?)\s+"
        r"(N/A|[-+]?\d+(?:\.\d+)?)\s*$",
        re.I,
    )
    for line in text.splitlines():
        match = row_re.match(line)
        if match:
            rows.append(
                {
                    "StartPosition": match.group(1),
                    "EndPosition": match.group(2),
                    "AverageError": match.group(3),
                    "T86": match.group(4),
                    "T63": match.group(5),
                    "Td": match.group(6),
                    "MaxOverShoot": match.group(7),
                }
            )
    if rows:
        return rows

    lines = [clean_spaces(line) for line in text.splitlines()]
    arrow_re = re.compile(
        r"^([-+]?\d+(?:\.\d+)?)\s*-\s*>\s*([-+]?\d+(?:\.\d+)?)$",
        re.I,
    )
    value_re = re.compile(r"^(?:N/A|[-+]?\d+(?:\.\d+)?)$", re.I)
    for index, line in enumerate(lines):
        match = arrow_re.match(line)
        if not match:
            continue
        values: list[str] = []
        for candidate in lines[index + 1:index + 12]:
            if arrow_re.match(candidate):
                break
            if not value_re.match(candidate):
                if values:
                    break
                continue
            values.append(candidate)
            if len(values) == 5:
                break
        if len(values) != 5:
            continue
        normalized_values: list[str] = []
        for value in values:
            normalized = value
            if re.fullmatch(r"0\d{2,}", normalized):
                normalized = "0." + normalized[1:]
            number = fnum(normalized)
            if number is not None and abs(number) > 100:
                normalized = ""
            normalized_values.append(normalized)
        rows.append(
            {
                "StartPosition": match.group(1),
                "EndPosition": match.group(2),
                "AverageError": normalized_values[0],
                "T86": normalized_values[1],
                "T63": normalized_values[2],
                "Td": normalized_values[3],
                "MaxOverShoot": normalized_values[4],
            }
        )
    return rows


def parse_hysteresis_dead_band(text: str) -> dict[str, str]:
    """Parse independent Run Ramp statistics; never synthesize their sum."""
    for line in text.splitlines():
        match = re.search(
            r"Hysteresis Dead Band \(\%\)\s+([-+]?\d+(?:\.\d+)?)\s+"
            r"([-+]?\d+(?:\.\d+)?)\s+([-+]?\d+(?:\.\d+)?)",
            line,
            re.I,
        )
        if match:
            return {
                "MaxHysteresis": match.group(1),
                "MinHysteresis": match.group(2),
                "AverageHysteresis": match.group(3),
            }
    lines = [clean_spaces(line) for line in text.splitlines()]
    compact = clean_spaces(text)
    if re.search(r"\b(?:Maximum|Max)\s+(?:Average|Mean)\s+(?:Minimum|Min)\b", compact, re.I):
        column_order = ("max", "average", "min")
    else:
        column_order = ("max", "min", "average")

    def triplet(label: str) -> dict[str, float | None]:
        for index, line in enumerate(lines):
            if not re.fullmatch(rf"{label}\s*\(%\)", line, re.I):
                continue
            values: list[float | None] = []
            for candidate in lines[index + 1:index + 8]:
                if not re.fullmatch(r"[-+]?\d+(?:\.\d+)?", candidate):
                    if values:
                        break
                    continue
                value = float(candidate)
                values.append(value if abs(value) <= 100 else None)
                if len(values) == 3:
                    return dict(zip(column_order, values))
        inline = re.search(
            rf"{label}\s*\(%\)\s+([-+]?\d+(?:\.\d+)?)\s+"
            rf"([-+]?\d+(?:\.\d+)?)\s+([-+]?\d+(?:\.\d+)?)",
            compact,
            re.I,
        )
        return (
            dict(zip(column_order, (float(value) for value in inline.groups())))
            if inline else {}
        )

    result: dict[str, str] = {}
    for label, prefix in (
        ("Hysteresis", "Hysteresis"),
        (r"Dead\s+Band", "DeadBand"),
        ("Linearity", "Linearity"),
    ):
        values = triplet(label)
        if len(values) != 3:
            continue
        for statistic, suffix in (("max", "Max"), ("min", "Min"), ("average", "Average")):
            value = values.get(statistic)
            if value is not None:
                result[prefix + suffix] = f"{value:g}"
    return result


def is_svi_ii_ap_report_text(text: str) -> bool:
    compact = clean_spaces(text)
    return (
        "SVI II AP Report" in compact and "Smart Valve Interface Results" in compact
    ) or ("SVI II AP报表" in compact and "智能阀门接口结果" in compact)


EXECUTION_TIMESTAMP_RE = re.compile(
    r"\b\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2}:\d{2}\s+[AP]M\b",
    re.I,
)


def parse_report_execution_window(text: str) -> str:
    start_match = re.search(
        r"Start\s+Time\s*[:\-]?\s*(\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2}:\d{2}\s+[AP]M)",
        text,
        re.I,
    )
    if not start_match:
        return ""
    try:
        start = datetime.strptime(start_match.group(1), "%m/%d/%Y %I:%M:%S %p")
    except ValueError:
        return ""
    end_match = re.search(
        r"End\s+Time\s*[:\-]?\s*(\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2}:\d{2}\s+[AP]M)",
        text,
        re.I,
    )
    timestamp_source = text.split("Task Execution Summary", 1)[1] if "Task Execution Summary" in text else ""
    timestamp_values = [end_match.group(1)] if end_match else EXECUTION_TIMESTAMP_RE.findall(timestamp_source)
    timestamps: list[datetime] = []
    for value in timestamp_values:
        try:
            timestamps.append(datetime.strptime(value, "%m/%d/%Y %I:%M:%S %p"))
        except ValueError:
            continue
    end = max((value for value in timestamps if value >= start), default=start)
    start_text = start.strftime("%Y-%m-%d %H:%M:%S")
    if end.date() == start.date():
        return f"{start_text}–{end.strftime('%H:%M:%S')}"
    return f"{start_text}–{end.strftime('%Y-%m-%d %H:%M:%S')}"


def parse_active_report_pdf(path: Path) -> dict[str, Any]:
    text = "\f".join(run_pdftotext_pages(path))
    compact = clean_spaces(text)
    test_execution_issues = parse_test_execution_issues(text)
    report_time = parse_report_execution_window(text)
    # A few ValvXpert exports lose the first-page heading but retain the DTM
    # sections. Keep source/parser provenance explicit; missing source values
    # must not be replaced with guessed values.
    source_has_dtm_sections = bool(
        re.search(
            r"(?:DTM\s+Version|SVI\s+(?:II\s+AP|FF)|Control\s+Parameters|"
            r"Continuous\s+Diagnostics|Strokes\s+\(counts\))",
            compact,
            re.I,
        )
    )

    def dtm_provenance(parser_status: str) -> dict[str, str]:
        return {
            "dtm_source_status": "present" if source_has_dtm_sections else "source_missing",
            "dtm_parser_status": parser_status if source_has_dtm_sections else "not_applicable",
        }

    if is_svi_ii_ap_report_text(text):
        faults: list[str] = []
        match = re.search(
            r"(?:Active Faults|当前故障)\s*(.*?)\s*(?:Historical Faults|历史故障)",
            compact,
            re.I,
        )
        if match:
            faults = [
                value.strip()
                for value in re.split(r";", match.group(1))
                if value.strip()
                and value.strip().lower() not in {"reset", "重置"}
                and "I/P Current" not in value
            ]
        return {
            "source": path.name,
            "is_dtm_report": True,
            "dtm_report_type": "svi_ii_ap",
            "device_tag": first_regex_value(compact, [r"Device Tag\s*:?\s*([A-Za-z0-9-]+)", r"设备位号\s*([A-Za-z0-9-]+)"]),
            "device_id": first_regex_value(compact, [r"Device ID\s*:?\s*([0-9_]+)", r"设备识别码\s*([0-9_]+)"]).strip("_"),
            "serial_number": first_regex_value(compact, [r"Device S/N\s*:?\s*([A-Z0-9]+)", r"设备序列号\s*[:：]?\s*([A-Z0-9]+)"]),
            "date_time": first_regex_value(compact, [r"Time\s*:\s*([0-9]{4}-[0-9]{2}-[0-9]{2}\s+[0-9:]+)", r"时间\s*[：:]\s*([0-9]{4}-[0-9]{2}-[0-9]{2}\s+[0-9:]+)"]),
            "firmware": first_regex_value(compact, [r"Firmware Rev\s+([0-9.]+)", r"固件版本\s+([0-9.]+(?:\s*/\s*[0-9]+)?)"]),
            "dtm_revision": first_regex_value(compact, [r"DTM Rev\s+([0-9.]+(?:\s+BuildID:\s*[A-Z0-9]+)?)", r"DTM 版本\s+([0-9.]+(?:\s+BuildID:\s*[A-Z0-9]+)?)"]),
            "device_type": "SVI II AP",
            "option_config": first_regex_value(compact, [r"Option Config\s+(.+?;)\s+Configuration Information", r"可选功能配置\s+(.+?;)\s+配置信息"]),
            "air_action": first_regex_value(compact, [r"Air Action\s+(Air To Open|Air To Close)", r"气开/气关设置\s+(气动打开|气动关闭)"]),
            "actuator_style": first_regex_value(compact, [r"Actuator Type\s+(Single Acting|Double Acting)", r"执行机构类型\s+(单作用|双作用)"]),
            "supply_pressure": first_regex_value(compact, [r"Supply Pressure\s+([-+]?\d+(?:\.\d+)?\s*(?:psi|kPa))", r"供给压力\s+([-+]?\d+(?:\.\d+)?\s*(?:psi|kPa))"]),
            "valve_position": first_regex_value(compact, [r"Valve Position\s+([-+]?\d+(?:\.\d+)?\s*%)", r"阀位\s+([-+]?\d+(?:\.\d+)?\s*%)"]),
            "input_signal": first_regex_value(compact, [r"Input Signal\s+([-+]?\d+(?:\.\d+)?\s*mA)", r"输入信号\s+([-+]?\d+(?:\.\d+)?\s*mA)"]),
            "device_mode": first_regex_value(compact, [r"Device Mode\s+([A-Za-z ]+?)\s+Min Temp\.", r"设备模式\s+(.+?)\s+最低温度"]),
            "min_temperature": first_regex_value(compact, [r"Min Temp\.\s+([-+]?\d+(?:\.\d+)?\s*degC)", r"最低温度\s+([-+]?\d+(?:\.\d+)?\s*degC)"]),
            "max_temperature": first_regex_value(compact, [r"Max Temp\.\s+([-+]?\d+(?:\.\d+)?\s*degC)", r"最高温度\s+([-+]?\d+(?:\.\d+)?\s*degC)"]),
            "strokes": first_regex_value(compact, [r"Strokes \(counts\)\s+(\d+)", r"冲程（计数）\s+(\d+)"]),
            "cycles": first_regex_value(compact, [r"Cy(?:cl|el)es \(counts\)\s+(\d+)", r"循环（计数）\s+(\d+)"]),
            "time_open": first_regex_value(compact, [r"Time Open \(hours\)\s+(\d+)", r"开启时间（小时）\s+(\d+)"]),
            "time_closed": first_regex_value(compact, [r"Time Closed \(hours\)\s+(\d+)", r"闭合时间（小时）\s+(\d+)"]),
            "time_near_closed": first_regex_value(compact, [r"Time Near Closed \(hours\)\s+(\d+)", r"临近全关时间（小时）\s+(\d+)"]),
            "faults": faults,
            "fault_data_status": "present" if match else "source_missing",
            "test_execution_issues": test_execution_issues,
            "report_time": report_time,
            **dtm_provenance("parsed"),
        }
    has_valvxpert_heading = "ValvXpert Execution Report" in compact
    has_partial_valvxpert_sections = (
        not has_valvxpert_heading
        and "Control Parameters" in compact
        and "Continuous Diagnostics" in compact
        and bool(re.search(r"(?:Run Step Test|Run Ramp Test|Task Execution Summary)", compact, re.I))
    )
    if has_valvxpert_heading or has_partial_valvxpert_sections:
        actuator_style = first_regex_value(
            compact,
            [
                r"(?:Actuator|Astuator) Type\s+(Single|Double)(?=\s+(?:Open Stop Adj\.|P\b|Continuous)|\s*$)",
                r"(?:Actuator|Astuator) Type\s+(Single Acting|Double Acting)",
            ],
        )
        if actuator_style in {"Single", "Double"}:
            actuator_style += " Acting"
        return {
            "source": path.name,
            "is_dtm_report": False,
            "report_type": "valvxpert_execution",
            "device_tag": regex_value(compact, r"Device Tag\s+([A-Za-z0-9-]+)"),
            "serial_number": regex_value(compact, r"Device S/N\s+([A-Z0-9]+)"),
            "device_id": regex_value(compact, r"Device ID\s+([0-9_]+)").strip("_"),
            "device_type": regex_value(compact, r"Dev\. Type/Rev\s+(.+?)(?=\s+Polling Address)"),
            "manufacturer": regex_value(compact, r"Manufacturer ID\s+(.+?)(?=\s+Diagnostics Level)"),
            "diagnostics_level": regex_value(compact, r"Diagnostics Level\s+(.+?)(?=\s+DTM Version)"),
            "diagnostic_software_version": regex_value(compact, r"DTM Version\s+(.+?)(?=\s+Descriptor|\s+Firmware Rev)"),
            "firmware": regex_value(compact, r"Firmware Rev\s+(.+?)(?=\s+(?:Message|Valve S/N|Option Config))"),
            "option_config": regex_value(compact, r"Option Config\s+(.+?)(?=\s+Configuration(?:\s|$))"),
            "air_action": regex_value(compact, r"Air Action\s+(ATC|ATO|Air To Close|Air To Open)"),
            "actuator_style": actuator_style,
            "device_mode": regex_value(compact, r"Device Mode\s+(.+?)(?=\s+Valve Position)"),
            "valve_position": regex_value(compact, r"Valve Position\s+([-+]?\d+(?:\.\d+)?\s*%)"),
            "manual_setpoint": regex_value(compact, r"Manual Setpoint\s+([-+]?\d+(?:\.\d+)?\s*%)"),
            "input_signal": regex_value(compact, r"Input Signal\s+([-+]?\d+(?:\.\d+)?\s*mA)"),
            "bias": regex_value(compact, r"\bBias\s+([-+]?\d+(?:\.\d+)?)"),
            "board_temperature": regex_value(compact, r"Board Temp\.\s+([-+]?\d+(?:\.\d+)?\s*degC)"),
            "min_temperature": regex_value(compact, r"Min Temp\.\s+([-+]?\d+(?:\.\d+)?\s*degC)"),
            "max_temperature": regex_value(compact, r"Max Temp\.\s+([-+]?\d+(?:\.\d+)?\s*degC)"),
            "supply_pressure": regex_value(compact, r"Supply Pressure\s+([-+]?\d+(?:\.\d+)?\s*(?:psi|kPa))"),
            "strokes": regex_value(compact, r"Strokes \(counts\)\s+(\d+)"),
            "cycles": regex_value(compact, r"Cy(?:cl|el)es \(counts\)\s+(\d+)"),
            "time_open": regex_value(compact, r"Time Open \(hours\)\s+(\d+)"),
            "time_closed": regex_value(compact, r"Time Closed \(hours\)\s+(\d+)"),
            "time_near_closed": regex_value(compact, r"Time Near Closed \(hours\)\s+(\d+)"),
            "spring_range": parse_spring_range(compact),
            "fault_data_status": "present"
            if "Active Faults" in compact or "Historical Faults" in compact
            else "source_missing",
            "test_execution_issues": test_execution_issues,
            "report_time": report_time,
            **dtm_provenance("parsed"),
            **(
                {
                    "active_faults": _report_faults(compact, "Active Faults", "Historical Faults"),
                    "historical_faults": _report_faults(compact, "Historical Faults", "Raw Data"),
                }
                if "Active Faults" in compact or "Historical Faults" in compact
                else {}
            ),
        }
    if "SVI FF Configuration Report" not in compact:
        return {
            "source": path.name,
            "is_dtm_report": False,
            "test_execution_issues": test_execution_issues,
            "report_time": report_time,
            **dtm_provenance("parser_missing" if source_has_dtm_sections else "not_applicable"),
        }
    faults: list[str] = []
    m = re.search(r"Active Faults\s*(.*?)\s*Historical Faults", compact)
    if m:
        raw = clean_spaces(m.group(1))
        for part in re.split(r";", raw):
            part = part.strip()
            if part and "I/P Current" not in part:
                faults.append(part)
    info = {
        "source": path.name,
        "is_dtm_report": True,
        "device_tag": regex_value(compact, r"Device Tag:?\s+([A-Za-z0-9\-]+)"),
        "device_id": regex_value(compact, r"Device ID:?\s+([0-9_]+)").strip("_"),
        "serial_number": regex_value(compact, r"Device S/N\s+([A-Z0-9]+)"),
        "date_time": first_regex_value(
            compact,
            [
                r"Date and Time:\s*([A-Za-z]{3,9}\s+\d{1,2},\s+\d{4},\s+[0-9:]+\s+[AP]M)",
                r"Time\s*:\s*([0-9]{4}-[0-9]{2}-[0-9]{2}\s+[0-9:]+)",
            ],
        ),
        "firmware": first_regex_value(compact, [r"Firmware Revision\s+([0-9.]+\s+[A-Z0-9]+)", r"Firmware Rev\s+([0-9.]+\s*/\s*[0-9]+)"]),
        "device_address": regex_value(compact, r"Device Address\s+([0-9]+)"),
        "device_type": regex_value(compact, r"Device Type\s+([^ ]+\s+-\s+SVI FF)"),
        "actuator_manufacturer": first_regex_value(compact, [r"Actuator Manufacturer ID\s+([^ ]+(?:\s+MASONEILAN)?)", r"Manufacturer ID\s+0x004745 - GE Masoneilan\s+Manufacturer ID\s+([^ ]+(?:\s+MASONEILAN)?)"]),
        "actuator_model": first_regex_value(compact, [r"Device Type\s+0x0008 - SVI FF\s+Model Number\s+([0-9A-Z-]+)", r"Actuator.*?Model Number\s+([0-9A-Z-]+)"]),
        "actuator_serial": regex_value(compact, r"Serial Number\s+(I-[0-9A-Z-]+)"),
        "actuator_style": regex_value(compact, r"Style\s+(Single Acting|Double Acting)"),
        "fail_action": regex_value(compact, r"Fail Action\s+(.+?\(air to open\))"),
        "max_supply_pr": regex_value(compact, r"Max Supply Pr\.\s+([-+]?\d+(?:\.\d+)?\s*psi)"),
        "pr_control_high": regex_value(compact, r"Pr\. Control High\s+([-+]?\d+(?:\.\d+)?\s*psi)"),
        "pr_control_low": regex_value(compact, r"Pr\. Control Low\s+([-+]?\d+(?:\.\d+)?\s*psi)"),
        "display": regex_value(compact, r"Display\s+(.+?)\s+Pr\. Control Low"),
        "housing": regex_value(compact, r"Housing\s+(.+?)\s+Message"),
        "options": regex_value(compact, r"Options\s+(.+?)\s+Spec Sheet"),
        "other_agencies": regex_value(compact, r"Other Agencies\s+([A-Z0-9, ]+?)\s+Valve"),
        "valve_manufacturer": first_regex_value(compact, [r"Valve Manufacturer ID\s+([^ ]+(?:\s+MASONEILAN)?)", r"Other Agencies.*?Valve Manufacturer ID\s+([^ ]+(?:\s+MASONEILAN)?)"]),
        "dtm_valve_model": first_regex_value(compact, [r"Booster\s+Model Number\s+([0-9A-Z-]+)", r"Valve.*?Model Number\s+([0-9A-Z-]+)"]),
        "dtm_valve_serial": regex_value(compact, r"Valve.*?Serial Number\s+(I-[0-9A-Z-]+)"),
        "dtm_valve_type": regex_value(compact, r"Type\s+(Linear|Rotary)"),
        "spec_sheet": regex_value(compact, r"Spec Sheet\s+(I-[0-9A-Z-]+)"),
        "faults": faults,
        "test_execution_issues": test_execution_issues,
        "supply_pressure": regex_value(compact, r"Supply Pressure\s+([-+]?\d+(?:\.\d+)?)\s*psi"),
        "valve_position": regex_value(compact, r"Valve Position\s+([-+]?\d+(?:\.\d+)?)\s*%"),
        "strokes": regex_value(compact, r"Strokes \(counts\)\s+(\d+)"),
        "cycles": regex_value(compact, r"Cy(?:cl|el)es \(counts\)\s+(\d+)"),
        "time_open": regex_value(compact, r"Time Open \(hours\)\s+(\d+)"),
        "time_closed": regex_value(compact, r"Time Closed \(hours\)\s+(\d+)"),
        "failed_active": regex_value(compact, r"Failed Active\s+([0-9]+)"),
        "off_spec_active": regex_value(compact, r"Off Specification Active\s+([0-9]+)"),
        "maintenance_active": regex_value(compact, r"Maintenance Active\s+([0-9]+)"),
        "check_function_active": regex_value(compact, r"Check Function Active\s+([0-9]+)"),
        "travel_range": regex_value(compact, r"Travel Range\s+([-+]?\d+(?:\.\d+)?\s*%)"),
        "working_sp_max": regex_value(compact, r"Working SP Max\s+([-+]?\d+(?:\.\d+)?\s*%)"),
        "working_sp_min": regex_value(compact, r"Working SP Min\s+([-+]?\d+(?:\.\d+)?\s*%)"),
        "supply_pressure_max": regex_value(compact, r"Supply Pressure Max\s+([-+]?\d+(?:\.\d+)?\s*psi)"),
        "supply_pressure_min": regex_value(compact, r"Supply Pressure Min\s+([-+]?\d+(?:\.\d+)?\s*psi)"),
        "actuator_a_max": regex_value(compact, r"Actuator A Max\s+([-+]?\d+(?:\.\d+)?\s*psi)"),
        "actuator_a_min": regex_value(compact, r"Actuator A Min\s+([-+]?\d+(?:\.\d+)?\s*psi)"),
        "actuator_b_max": regex_value(compact, r"Actuator B Max\s+([-+]?\d+(?:\.\d+)?\s*psi)"),
        "actuator_b_min": regex_value(compact, r"Actuator B Min\s+([-+]?\d+(?:\.\d+)?\s*psi)"),
        "temperature_max": regex_value(compact, r"Temperature Max\s+([-+]?\d+(?:\.\d+)?\s*°C)"),
        "temperature_min": regex_value(compact, r"Temperature Min\s+([-+]?\d+(?:\.\d+)?\s*°C)"),
        "travel_accumulation_a": first_regex_value(compact, [r"Travel Accumulation A Alert.*?Travel Accumulation\s+([-+]?\d+(?:\.\d+)?%)"]),
        "cycle_counter_a": first_regex_value(compact, [r"Cycle Counter A Alert.*?Cycle Counter\s+([0-9]+)"]),
        **dtm_provenance("parsed"),
    }
    for key in ["actuator_model", "dtm_valve_model", "actuator_manufacturer", "valve_manufacturer", "display", "housing", "options", "other_agencies"]:
        if info.get(key) in {"Device", "Manufacturer", "Model", "Number", "Valve", "Booster"}:
            info[key] = ""
    return info


def regex_value(text: str, pattern: str) -> str:
    m = re.search(pattern, text, re.I)
    return m.group(1).strip() if m else ""


def regex_values(text: str, pattern: str) -> list[str]:
    return [m.strip() for m in re.findall(pattern, text, re.I)]


def first_regex_value(text: str, patterns: list[str]) -> str:
    for pattern in patterns:
        value = regex_value(text, pattern)
        if value:
            return value
    return ""


def normalize_date_text(text: str) -> str:
    text = clean_spaces(text)
    match = re.match(r"([A-Za-z]+)\s+(\d{1,2}),\s+(\d{4})", text)
    if match:
        months = {
            "jan": "01",
            "feb": "02",
            "mar": "03",
            "apr": "04",
            "may": "05",
            "jun": "06",
            "jul": "07",
            "aug": "08",
            "sep": "09",
            "oct": "10",
            "nov": "11",
            "dec": "12",
        }
        month = months.get(match.group(1)[:3].lower())
        if month:
            return f"{match.group(3)}-{month}-{int(match.group(2)):02d}"
    return text.split()[0] if text else ""


def normalize_spec_tag(tag: str) -> str:
    text = re.sub(r"[^A-Z0-9]", "", tag.upper())
    text = re.sub(r"^0203", "", text)
    if text.startswith("L") and len(text) > 1 and text[1] == "V":
        text = text[1:]
    if text and text[0].isdigit():
        text = text[1:]
    return text


def spec_match_keys(valve_tag: str) -> set[str]:
    base = normalize_spec_tag(valve_tag)
    keys = {base}
    if base.startswith("V"):
        keys.add("L" + base)
    return keys


def extract_spec_tag(page_text: str) -> str:
    direct = re.search(r"\b0203-[A-Z]{1,2}V?\d{5}[A-Z]?\b", page_text, re.I)
    if direct:
        return direct.group(0).strip()
    for line in page_text.splitlines():
        match = re.search(r"\bTag\s*:?\s+([A-Z0-9-]{5,})\b", line, re.I)
        if match and match.group(1).upper() not in {"GENERAL", "SERVICE"}:
            return match.group(1).strip()
    return ""


def extract_spec_tags(page_text: str) -> set[str]:
    tags: set[str] = set()
    grouped_pattern = re.compile(
        r"\b\d{4}-[A-Z]{1,4}-\d{4,6}[A-Z]?(?:/[A-Z0-9-]+)+\b",
        re.I,
    )
    for match in grouped_pattern.finditer(page_text):
        parts = match.group(0).upper().split("/")
        primary = parts[0]
        tags.add(primary)
        prefix, tag_number = primary.rsplit("-", 1)
        for suffix in parts[1:]:
            if len(suffix) == 1 and suffix.isalpha() and tag_number[-1:].isalpha():
                tags.add(f"{prefix}-{tag_number[:-1]}{suffix}")
            elif re.fullmatch(r"\d{4,6}[A-Z]?", suffix):
                tags.add(f"{prefix}-{suffix}")

    standalone_pattern = re.compile(r"\b\d{4}-[A-Z]{1,4}-\d{4,6}[A-Z]?\b", re.I)
    tags.update(match.group(0).upper() for match in standalone_pattern.finditer(page_text))
    if not tags:
        tag = extract_spec_tag(page_text)
        if tag:
            tags.add(tag.upper())
    return tags


def page_supports_spec_tag(page_text: str, candidate: str) -> bool:
    page_tags = extract_spec_tags(page_text)
    if not page_tags:
        return True
    normalized_candidate = normalize_spec_tag(candidate)
    normalized_page_tags = {normalize_spec_tag(tag) for tag in page_tags}
    if normalized_candidate in normalized_page_tags:
        return True
    if candidate[-1:].isalpha() and normalize_spec_tag(candidate[:-1]) in normalized_page_tags:
        return True
    return False


def parse_accessory_fields(text: str) -> dict[str, str]:
    if not is_accessory_sheet(text):
        return {}
    patterns = (
        r"Model\s+Positioner\s+(SVI\s*[0-9I]+\s*AP\s*[-–—]?\s*[A-Z0-9-]+)",
        r"Positioner\s+Model\s+(SVI\s*[0-9I]+\s*AP\s*[-–—]?\s*[A-Z0-9-]+)",
        r"\bPOSITIONER\s+(SVI\s*2\s*[-–—]?\s*[0-9]{8})\b",
        r"\bPOSITIONER\s+(SVI\s*2\s*AP\s*[-–—]?\s*[0-9]{8})\b",
        r"\b(SVI\s*[0-9I]+\s*AP\s*[-–—]\s*[A-Z0-9-]+)\b",
    )
    compact = clean_spaces(text)
    for pattern in patterns:
        match = re.search(pattern, compact, re.I)
        if match:
            return {"positioner_model": clean_spaces(match.group(1))}
    return {}


def page_text_with_ocr(pdf: Path, page: fitz.Page, page_no: int) -> str:
    key = (str(pdf.resolve()), page_no)
    if key not in SPEC_PAGE_TEXT_CACHE:
        raise RuntimeError(
            f"specification OCR cache miss for {pdf.name} page {page_no}; run --extract-materials"
        )
    return SPEC_PAGE_TEXT_CACHE[key]


def normalized_page_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def is_specification_sheet(text: str) -> bool:
    normalized = normalized_page_text(text)
    return (
        "specification sheet" in normalized
        or "specffication sheet" in normalized
        or "spec data sheet" in normalized
        or bool(re.search(r"\bcontrol\s*valve\s+specifications\b", normalized))
        or detect_spec_format(text) == "assembly_quality_plan"
    )


def is_accessory_sheet(text: str) -> bool:
    normalized = normalized_page_text(text)
    # Exported page headers often repeat "Specification Sheet" above the
    # actual Accessory Sheet.  The explicit accessory label is authoritative.
    return "accessory sheet" in normalized


def is_air_piping_page(text: str) -> bool:
    normalized = normalized_page_text(text)
    return bool(
        re.search(r"\bair\s+piping(?:\s+diagram)?\b", normalized)
        or re.search(r"\bpneumatic\s+(?:hookup|hook-up|schematic|diagram)\b", normalized)
        or "气路图" in normalized
    )


def classify_spec_page_roles(text: str) -> tuple[str, ...]:
    """Classify attachment semantics without conflating air piping/accessories."""
    roles: list[str] = []
    if is_specification_sheet(text):
        roles.append("specification")
    if is_air_piping_page(text):
        roles.append("air_piping")
    if is_accessory_sheet(text) and "air_piping" not in roles:
        roles.append("accessory")
    return tuple(roles)


def file_sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def merge_spec_page_references(pages: list[SpecPage]) -> list[SpecPage]:
    """Merge roles only for the same source hash and one-based physical page."""
    merged: dict[str, SpecPage] = {}
    order: list[str] = []
    role_order = {"specification": 0, "air_piping": 1, "accessory": 2}
    for page in pages:
        identity = page.physical_identity
        if identity not in merged:
            merged[identity] = SpecPage(**page.__dict__)
            order.append(identity)
            continue
        current = merged[identity]
        current.roles = tuple(
            sorted(set(current.roles) | set(page.roles), key=lambda role: role_order.get(role, 99))
        )
        current.match_basis = tuple(dict.fromkeys((*current.match_basis, *page.match_basis)))
        current.image_path = current.image_path or page.image_path
        current.fields.update(page.fields)
        current.model_no = current.model_no or page.model_no
        current.valve_spec = current.valve_spec or page.valve_spec
    return [merged[identity] for identity in order]


def report_pages_for_valve(valve: ValveReport) -> list[SpecPage]:
    """Return the normalized page list, projecting legacy singular fields once."""
    if valve.report_pages:
        return valve.report_pages
    projected: list[SpecPage] = []
    for page, role, image_path in (
        (valve.spec_page, "specification", valve.spec_image),
        (valve.air_piping_page, "air_piping", valve.air_piping_image),
        (valve.accessory_page, "accessory", valve.accessory_image),
    ):
        if page is None:
            continue
        clone = SpecPage(**page.__dict__)
        clone.roles = clone.roles or (role,)
        clone.image_path = clone.image_path or image_path
        projected.append(clone)
    valve.report_pages = merge_spec_page_references(projected)
    return valve.report_pages


def filename_spec_tag(pdf: Path) -> str:
    filename_match = re.search(r"\(([^)]+)\)", pdf.name)
    return filename_match.group(1).strip() if filename_match else ""


def spec_source_path(pdf: Path) -> str:
    return pdf.relative_to(SPECS_DIR).as_posix()


def configured_spec_source_matches(
    configured_source: str,
    pdf: Path,
    all_pdfs: list[Path],
    specs_dir: Path | None = None,
) -> bool:
    root = specs_dir or SPECS_DIR
    configured = str(configured_source).replace("\\", "/").strip("/")
    relative = pdf.relative_to(root).as_posix()
    if "/" in configured:
        return configured == relative
    same_basename = [candidate for candidate in all_pdfs if candidate.name == configured]
    if len(same_basename) > 1:
        raise ValueError(
            f"ambiguous specification source {configured!r}; use a path relative to specs/"
        )
    return len(same_basename) == 1 and pdf == same_basename[0]


def directory_spec_tag(pdf: Path) -> str:
    relative = pdf.relative_to(SPECS_DIR)
    return pdf.parent.name if len(relative.parts) > 1 else ""


def spec_fields_from_text(text: str, tag: str = "") -> dict[str, str]:
    fields = parse_spec_fields(text)
    if tag in SPEC_FIELD_OVERRIDES_BY_TAG:
        fields.update(SPEC_FIELD_OVERRIDES_BY_TAG[tag])
    return fields


def format_valve_spec(fields: dict[str, str]) -> str:
    parts = []
    if fields.get("valve_size"):
        parts.append(fields["valve_size"])
    if fields.get("pressure_class"):
        parts.append(fields["pressure_class"].replace(" ", ""))
    if fields.get("end_connection"):
        parts.append(fields["end_connection"])
    if fields.get("design_pressure"):
        parts.append("Design Press " + fields["design_pressure"])
    if fields.get("rated_cv"):
        parts.append("Rated Cv " + fields["rated_cv"])
    if fields.get("leakage_class"):
        parts.append("Leakage Class " + fields["leakage_class"])
    return "；".join(parts)


def normalize_positioner_model(device_type: str) -> str:
    text = clean_spaces(device_type)
    if not text:
        return ""
    if re.search(r"\bSVI\s*FF\b", text, re.I):
        return "SVI FF"
    if re.search(r"\bSVI\s*II\s*AP\b", text, re.I):
        return "SVI II AP"
    if re.search(r"\bSVI\s*2\s*AP\b", text, re.I):
        return "SVI2AP"
    return text


def execution_report_matches_folder(folder_name: str, info: dict[str, Any]) -> bool:
    folder = clean_spaces(folder_name).upper()
    device_tag = clean_spaces(str(info.get("device_tag") or "")).upper()
    return bool(device_tag and (folder == device_tag or folder.endswith("-" + device_tag)))


def report_selection_key(info: dict[str, Any]) -> tuple[str, str]:
    report_time = clean_spaces(str(info.get("report_time") or info.get("start_time") or ""))
    return report_time[:19], clean_spaces(str(info.get("source") or ""))


def select_positioner_report(infos: list[dict[str, Any]], chosen_device_id: str) -> dict[str, Any] | None:
    trusted = (
        [info for info in infos if info.get("device_id") == chosen_device_id]
        if chosen_device_id
        else list(infos)
    )
    return max(trusted, key=report_selection_key) if trusted else None


def apply_positioner_report(valve: ValveReport, info: dict[str, Any]) -> bool:
    if valve.device_id and info.get("device_id") != valve.device_id:
        return False
    valve.positioner_report_info = {
        key: value
        for key, value in info.items()
        if not isinstance(value, list) and value not in {None, "", False}
    }
    for key in ("active_faults", "historical_faults", "task_execution", "test_execution_issues"):
        if key in info:
            valve.positioner_report_info[key] = [
                dict(item) if isinstance(item, dict) else item for item in info[key]
            ]
    valve.device_tag = valve.device_tag or info.get("device_tag", "")
    valve.serial_number = valve.serial_number or info.get("serial_number", "")
    valve.firmware = valve.firmware or info.get("firmware", "")
    valve.actuator_type = info.get("actuator_style") or valve.actuator_type
    valve.spring_range = info.get("spring_range") or valve.spring_range
    valve.positioner_type = normalize_positioner_model(info.get("device_type", ""))
    valve.active_faults.extend(info.get("active_faults") or [])
    return True


def sequence_positioner_report_info(info: dict[str, Any]) -> dict[str, Any]:
    """Normalize Sequencer Positioner Info into the shared DTM evidence shape."""
    keys = (
        "source",
        "report_type",
        "device_tag",
        "device_id",
        "serial_number",
        "device_type",
        "firmware",
        "diagnostics_level",
        "diagnostic_software_version",
        "manufacturer",
        "device_address",
        "air_action",
        "actuator_type",
        "supply_pressure",
        "option_config",
        "device_mode",
        "valve_position",
        "manual_setpoint",
        "input_signal",
        "board_temperature",
        "min_temperature",
        "max_temperature",
        "strokes",
        "cycles",
        "time_open",
        "time_closed",
        "time_near_closed",
        "active_faults",
        "historical_faults",
        "task_execution",
        "test_execution_issues",
        "fault_data_status",
        "task_execution_status",
        "task_execution_expected_count",
        "task_execution_parser_status",
        "start_time",
        "end_time",
        "report_time",
        "dtm_source_status",
        "dtm_parser_status",
    )
    normalized = {
        key: info[key]
        for key in keys
        if info.get(key) is not None
        and info.get(key) != ""
        and info.get(key) is not False
    } | ({"actuator_style": info["actuator_type"]} if info.get("actuator_type") else {})
    for key in ("active_faults", "historical_faults", "task_execution", "test_execution_issues"):
        if key in info:
            normalized[key] = [
                dict(item) if isinstance(item, dict) else item for item in info[key]
            ]
    return normalized


def apply_sequence_positioner_fallback(
    valve: ValveReport,
    sequence_infos: list[dict[str, Any]],
    chosen_device_id: str,
) -> None:
    """Use Sequencer metadata only when no stronger DTM/report evidence exists."""
    if valve.positioner_report_info or valve.dtm_info:
        return
    selected = next(
        (
            info
            for info in reversed(sequence_infos)
            if not chosen_device_id
            or not info.get("device_id")
            or info.get("device_id") == chosen_device_id
        ),
        None,
    )
    if selected:
        valve.positioner_report_info = sequence_positioner_report_info(selected)
        if selected.get("report_time"):
            valve.detection_date = str(selected["report_time"])


def format_actuator_info(fields: dict[str, str], fallback: str = "") -> str:
    if not fields:
        return fallback or "未提供"
    parts = []
    if fields.get("actuator_model"):
        parts.append("Model " + fields["actuator_model"])
    if fields.get("actuator_type"):
        parts.append(fields["actuator_type"])
    if fields.get("supply_pressure"):
        parts.append("Supply " + fields["supply_pressure"])
    if fields.get("fail_position"):
        parts.append("Fail " + fields["fail_position"])
    return " / ".join(parts) or fallback or "未提供"


def load_spec_pages() -> dict[str, SpecPage]:
    specs: dict[str, SpecPage] = {}
    spec_pdfs = iter_spec_pdfs(SPECS_DIR)
    for pdf in spec_pdfs:
        source = spec_source_path(pdf)
        source_hash = file_sha256(pdf)
        direct_items = [
            (tag, item)
            for tag, item in DIRECT_SPEC_PAGE_BY_TAG.items()
            if isinstance(item, dict)
            and configured_spec_source_matches(str(item.get("source", "")), pdf, spec_pdfs)
        ]
        explicit_tags = [
            tag
            for tag, filename in EXPLICIT_SPEC_FILE_BY_TAG.items()
            if configured_spec_source_matches(filename, pdf, spec_pdfs)
        ]
        fallback_tag = directory_spec_tag(pdf) or filename_spec_tag(pdf)
        if not fallback_tag:
            fallback_tag = explicit_tags[0] if explicit_tags else ""
        try:
            doc = fitz.open(pdf)
        except Exception:
            continue
        try:
            if direct_items:
                for spec_tag, item in direct_items:
                    try:
                        page_no = int(item.get("page", 0))
                    except (TypeError, ValueError):
                        continue
                    if not 1 <= page_no <= len(doc):
                        continue
                    text = page_text_with_ocr(pdf, doc[page_no - 1], page_no)
                    valve_key = normalize_spec_tag(spec_tag)
                    fields = spec_fields_from_text(text, spec_tag)
                    if item.get("model_no"):
                        fields["model_no"] = clean_spaces(str(item["model_no"]))
                    spec = SpecPage(
                        valve=valve_key,
                        tag=spec_tag,
                        source=source,
                        page=page_no,
                        source_sha256=source_hash,
                        roles=("specification",),
                        match_basis=("configured_mapping",),
                        fields=fields,
                    )
                    spec.model_no = fields.get("model_no", "")
                    spec.valve_spec = clean_spaces(str(item.get("valve_spec", ""))) or format_valve_spec(fields)
                    specs[valve_key] = spec
                    specs["3" + valve_key] = spec
                continue
            if explicit_tags:
                page_no = 2 if len(doc) >= 2 else 1
                page = doc[page_no - 1]
                text = page_text_with_ocr(pdf, page, page_no)
                for spec_tag in explicit_tags:
                    valve_key = normalize_spec_tag(spec_tag)
                    spec = SpecPage(
                        valve=valve_key,
                        tag=spec_tag,
                        source=source,
                        page=page_no,
                        source_sha256=source_hash,
                        roles=("specification",),
                        match_basis=("configured_file",),
                        fields=spec_fields_from_text(text, spec_tag),
                    )
                    spec.model_no = spec.fields.get("model_no", "")
                    spec.valve_spec = format_valve_spec(spec.fields)
                    specs[valve_key] = spec
                    specs["3" + valve_key] = spec
                    if valve_key.startswith("L") and len(valve_key) > 1 and valve_key[1] == "V":
                        specs[valve_key[1:]] = spec
                        specs["3" + valve_key[1:]] = spec
                continue
            for page_index, page in enumerate(doc):
                page_no = page_index + 1
                text = page_text_with_ocr(pdf, page, page_no)
                if not is_specification_sheet(text):
                    continue
                quality_plan = detect_spec_format(text) == "assembly_quality_plan"
                if quality_plan:
                    page_tags = {normalize_spec_tag(tag) for tag in extract_spec_tags(text)}
                    if not page_tags or (fallback_tag and normalize_spec_tag(fallback_tag) not in page_tags):
                        continue
                if fallback_tag and not page_supports_spec_tag(text, fallback_tag):
                    continue
                spec_tag = fallback_tag or extract_spec_tag(text)
                if not spec_tag:
                    continue
                valve_key = normalize_spec_tag(spec_tag)
                if quality_plan and valve_key in specs:
                    continue  # A quality record must not replace an existing specification sheet.
                spec = SpecPage(
                    valve=valve_key,
                    tag=spec_tag,
                    source=source,
                    page=page_no,
                    source_sha256=source_hash,
                    roles=("specification",),
                    match_basis=("page_label",),
                    fields=spec_fields_from_text(text, spec_tag),
                )
                spec.model_no = spec.fields.get("model_no", "")
                spec.valve_spec = format_valve_spec(spec.fields)
                specs[valve_key] = spec
                specs["3" + valve_key] = spec
                if valve_key.startswith("L") and len(valve_key) > 1 and valve_key[1] == "V":
                    specs[valve_key[1:]] = spec
                    specs["3" + valve_key[1:]] = spec
                if fallback_tag and not quality_plan:
                    break
        finally:
            doc.close()
    return specs


def load_accessory_pages() -> dict[str, SpecPage]:
    accessories: dict[str, SpecPage] = {}
    spec_pdfs = iter_spec_pdfs(SPECS_DIR)
    for pdf in spec_pdfs:
        source = spec_source_path(pdf)
        source_hash = file_sha256(pdf)
        fallback_tag = directory_spec_tag(pdf) or filename_spec_tag(pdf)
        direct_items = [
            (tag, item)
            for tag, item in DIRECT_ACCESSORY_PAGE_BY_TAG.items()
            if isinstance(item, dict)
            and configured_spec_source_matches(str(item.get("source", "")), pdf, spec_pdfs)
        ]
        if direct_items:
            try:
                doc = fitz.open(pdf)
            except Exception:
                continue
            try:
                for accessory_tag, item in direct_items:
                    try:
                        page_no = int(item.get("page", 0))
                    except (TypeError, ValueError):
                        continue
                    if not 1 <= page_no <= len(doc):
                        continue
                    text = page_text_with_ocr(pdf, doc[page_no - 1], page_no)
                    valve_key = normalize_spec_tag(accessory_tag)
                    fields = parse_accessory_fields(text)
                    if item.get("positioner_model"):
                        fields["positioner_model"] = clean_spaces(
                            str(item["positioner_model"])
                        )
                    accessory = SpecPage(
                        valve=valve_key,
                        tag=accessory_tag,
                        source=source,
                        page=page_no,
                        source_sha256=source_hash,
                        roles=("accessory",),
                        match_basis=("configured_mapping",),
                        fields=fields,
                    )
                    accessories[valve_key] = accessory
                    accessories["3" + valve_key] = accessory
            finally:
                doc.close()
            continue
        try:
            doc = fitz.open(pdf)
        except Exception:
            continue
        try:
            found_spec = False
            for page_index, page in enumerate(doc):
                page_no = page_index + 1
                text = page_text_with_ocr(pdf, page, page_no)
                if is_specification_sheet(text):
                    found_spec = True
                    continue
                if not found_spec or not is_accessory_sheet(text):
                    continue
                if fallback_tag and not page_supports_spec_tag(text, fallback_tag):
                    continue
                spec_tag = fallback_tag or extract_spec_tag(text)
                if not spec_tag:
                    continue
                valve_key = normalize_spec_tag(spec_tag)
                accessory = SpecPage(
                    valve=valve_key,
                    tag=spec_tag,
                    source=source,
                    page=page_no,
                    source_sha256=source_hash,
                    roles=("accessory",),
                    match_basis=("page_label",),
                    fields=parse_accessory_fields(text),
                )
                accessories[valve_key] = accessory
                accessories["3" + valve_key] = accessory
                break
        finally:
            doc.close()
    return accessories


def load_air_piping_pages() -> dict[str, SpecPage]:
    pages: dict[str, SpecPage] = {}
    spec_pdfs = iter_spec_pdfs(SPECS_DIR)
    for pdf in spec_pdfs:
        source = spec_source_path(pdf)
        source_hash = file_sha256(pdf)
        fallback_tag = directory_spec_tag(pdf) or filename_spec_tag(pdf)
        direct_items = [
            (tag, item)
            for tag, item in DIRECT_AIR_PIPING_PAGE_BY_TAG.items()
            if isinstance(item, dict)
            and configured_spec_source_matches(str(item.get("source", "")), pdf, spec_pdfs)
        ]
        try:
            doc = fitz.open(pdf)
        except Exception:
            continue
        try:
            candidates: list[tuple[str, int, str]] = []
            if direct_items:
                for tag, item in direct_items:
                    try:
                        page_no = int(item.get("page", 0))
                    except (TypeError, ValueError):
                        continue
                    if 1 <= page_no <= len(doc):
                        candidates.append((tag, page_no, "configured_mapping"))
            else:
                for page_index, page in enumerate(doc):
                    page_no = page_index + 1
                    text = page_text_with_ocr(pdf, page, page_no)
                    if not is_air_piping_page(text):
                        continue
                    if fallback_tag and not page_supports_spec_tag(text, fallback_tag):
                        continue
                    tag = fallback_tag or extract_spec_tag(text)
                    if tag:
                        candidates.append((tag, page_no, "page_label"))
            for tag, page_no, basis in candidates:
                valve_key = normalize_spec_tag(tag)
                ref = SpecPage(
                    valve=valve_key,
                    tag=tag,
                    source=source,
                    page=page_no,
                    source_sha256=source_hash,
                    roles=("air_piping",),
                    match_basis=(basis,),
                )
                pages[valve_key] = ref
                pages["3" + valve_key] = ref
        finally:
            doc.close()
    return pages


def render_report_page(page_ref: SpecPage) -> str:
    """Render one physical attachment page regardless of its semantic roles."""
    out_dir = SPEC_IMAGE_DIR / page_ref.valve
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf = SPECS_DIR / page_ref.source
    with fitz.open(pdf) as doc:
        page = doc[page_ref.page - 1]
        clip = effective_spec_content_rect(page)
        matrix = fitz.Matrix(1.7, 1.7)
        identity = {
            "source": str(pdf.resolve()),
            "source_sha256": file_sha256(pdf),
            "page": page_ref.page,
            "clip": list(clip),
            "matrix": list(matrix),
            "alpha": False,
            "renderer": fitz.VersionBind,
        }
        digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        out = out_dir / f"{digest}_p{page_ref.page:02d}_report_page.png"
        if not out.exists():
            page.get_pixmap(matrix=matrix, clip=clip, alpha=False).save(out)
    page_ref.image_path = str(out)
    return str(out)


def spec_for_valve(valve_tag: str) -> SpecPage | None:
    for key in spec_match_keys(valve_tag):
        if key in SPEC_PAGES_BY_VALVE:
            return SPEC_PAGES_BY_VALVE[key]
    return None


def accessory_for_valve(valve_tag: str) -> SpecPage | None:
    for key in spec_match_keys(valve_tag):
        if key in ACCESSORY_PAGES_BY_VALVE:
            return ACCESSORY_PAGES_BY_VALVE[key]
    return None


def air_piping_for_valve(valve_tag: str) -> SpecPage | None:
    for key in spec_match_keys(valve_tag):
        if key in AIR_PIPING_PAGES_BY_VALVE:
            return AIR_PIPING_PAGES_BY_VALVE[key]
    return None


def source_file_is_excluded(path: Path) -> bool:
    try:
        relative_path = path.resolve().relative_to(BASE_DIR.resolve()).as_posix()
    except ValueError:
        return False
    return relative_path in EXCLUDED_SOURCE_FILES


def filter_excluded_source_images(
    valve_dir: Path,
    images: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        image
        for image in images
        if not image.get("source")
        or not source_file_is_excluded(valve_dir / str(image["source"]))
    ]


def collect_valves(
    *,
    render_assets: bool = True,
    curve_review_required_folders: set[str] | None = None,
) -> list[ValveReport]:
    valves: list[ValveReport] = []
    folders = sorted(p for p in RAW_DIR.iterdir() if p.is_dir())
    for folder in folders:
        valve = ValveReport(folder=folder.name)
        valve.structured_evidence = STRUCTURED_EVIDENCE_BY_TAG.get(folder.name)
        dev_infos: dict[str, dict[str, str]] = {}
        sequence_infos: list[dict[str, Any]] = []
        active_infos: list[dict[str, Any]] = []
        positioner_infos: list[dict[str, Any]] = []

        for path in sorted(folder.iterdir()):
            if not path.is_file():
                continue
            if source_file_is_excluded(path):
                continue
            if path.suffix.lower() == ".pdf":
                try:
                    doc = fitz.open(path)
                    pages = len(doc)
                    doc.close()
                    valve.files.append(SourceFile(name=path.name, kind="PDF", pages=pages))
                    pdf_text = "\f".join(run_pdftotext_pages(path))
                    if path.name.lower().startswith("seq_"):
                        if is_diagnostic_pdf(path, pdf_text):
                            valve.curve_inventory.extend(
                                parse_curve_inventory(pdf_text, path.name)
                            )
                        recs, info = parse_sequence_pdf(path)
                        sequence_infos.append(info)
                        valve.test_execution_issues.extend(
                            info.get("test_execution_issues") or []
                        )
                        valve.all_tests.extend(recs)
                        record_source_report(valve, "sequencer", info=info)
                    else:
                        info = parse_active_report_pdf(path)
                        if info.get("is_dtm_report"):
                            active_infos.append(info)
                            record_source_report(valve, "dtm_active", info=info)
                        else:
                            if info.get("report_type") == "valvxpert_execution":
                                positioner_infos.append(info)
                                report_type = "valvxpert"
                            else:
                                report_type = "unknown_pdf"
                            sequence_info: dict[str, str] = {}
                            recs: list[TestRecord] = []
                            if is_diagnostic_pdf(path, pdf_text):
                                valve.curve_inventory.extend(
                                    parse_curve_inventory(pdf_text, path.name)
                                )
                                recs, sequence_info = parse_sequence_pdf(path)
                                sequence_infos.append(sequence_info)
                                valve.test_execution_issues.extend(
                                    sequence_info.get("test_execution_issues") or []
                                )
                                valve.all_tests.extend(recs)
                                if report_type == "unknown_pdf":
                                    report_type = "sequencer"
                            record_source_report(
                                valve,
                                report_type,
                                info=(
                                    info
                                    if report_type == "valvxpert"
                                    else sequence_info
                                ),
                            )
                except Exception as exc:
                    valve.files.append(SourceFile(name=path.name, kind="PDF", readable=False, note=str(exc)))
                    record_source_report(valve, "unreadable_pdf")
                    valve.unreadable.append(f"{path.name}: {exc}")
            elif path.suffix.lower() == ".devdata":
                try:
                    recs, info = parse_devdata(path)
                    valve.files.append(SourceFile(name=path.name, kind="devdata"))
                    record_source_report(valve, "devdata", info=info)
                    if info.get("DeviceId"):
                        dev_infos[info["DeviceId"]] = info
                    valve.all_tests.extend(recs)
                except Exception as exc:
                    valve.files.append(SourceFile(name=path.name, kind="devdata", readable=False, note=str(exc)))
                    record_source_report(valve, "unreadable_devdata")
                    valve.unreadable.append(f"{path.name}: {exc}")
            else:
                valve.files.append(SourceFile(name=path.name, kind=path.suffix.lower().lstrip(".") or "unknown"))

        chosen = choose_device_id(folder.name, valve.all_tests, sequence_infos, active_infos + positioner_infos)
        valve.device_id = chosen
        valve.positioner_report_infos = [
            dict(info)
            for info in [
                *positioner_infos,
                *(sequence_positioner_report_info(info) for info in sequence_infos),
            ]
            if not chosen
            or not info.get("device_id")
            or info.get("device_id") == chosen
        ]
        valve.tests = [t for t in valve.all_tests if not chosen or not t.device_id or t.device_id == chosen]
        excluded = [t.source for t in valve.all_tests if chosen and t.device_id and t.device_id != chosen]
        if excluded:
            valve.data_note = f"发现设备ID不一致的诊断记录，评级仅采用与主设备ID一致的数据；另有 {len(set(excluded))} 条记录未纳入评级。"

        for info in sequence_infos:
            if not chosen or info.get("device_id") == chosen:
                valve.device_tag = info.get("device_tag") or valve.device_tag
                valve.serial_number = info.get("serial_number") or valve.serial_number
                if info.get("start_time"):
                    valve.detection_date = info["start_time"].split()[0]
                if info.get("actuator_type"):
                    valve.actuator_type = info["actuator_type"] + " Acting"
                if info.get("spring_range"):
                    valve.spring_range = info["spring_range"]
                if info.get("active_faults"):
                    valve.active_faults.extend(info["active_faults"])
                positioner_model = normalize_positioner_model(info.get("device_type", ""))
                if positioner_model:
                    valve.positioner_type = positioner_model
        for info in active_infos:
            if chosen and info.get("device_id") and info.get("device_id") != chosen:
                continue
            valve.dtm_info = {
                k: clean_spaces("；".join(v) if isinstance(v, list) else str(v))
                for k, v in info.items()
                if clean_spaces("；".join(v) if isinstance(v, list) else str(v))
            }
            positioner_type = normalize_positioner_model(info.get("device_type", ""))
            if positioner_type:
                valve.positioner_type = positioner_type
            if info.get("faults") and info.get("dtm_report_type") != "svi_ii_ap":
                valve.active_faults.extend(info["faults"])
            if not valve.serial_number:
                valve.serial_number = info.get("serial_number") or valve.serial_number
            if not valve.firmware:
                valve.firmware = info.get("firmware") or valve.firmware
            if info.get("date_time") and valve.detection_date == "未提供":
                valve.detection_date = normalize_date_text(info["date_time"])
        selected_positioner_info = select_positioner_report(positioner_infos, chosen)
        if selected_positioner_info:
            apply_positioner_report(valve, selected_positioner_info)
            valve.test_execution_issues.extend(selected_positioner_info.get("test_execution_issues") or [])
            if selected_positioner_info.get("report_time"):
                valve.detection_date = selected_positioner_info["report_time"]
        historical_runtime = HISTORICAL_RUNTIME_BY_TAG.get(valve.folder)
        if isinstance(historical_runtime, dict):
            valve.positioner_report_info = dict(valve.positioner_report_info)
            valve.positioner_report_info["historical_runtime"] = historical_runtime
        apply_sequence_positioner_fallback(valve, sequence_infos, chosen)

        if chosen in dev_infos:
            info = dev_infos[chosen]
            valve.serial_number = valve.serial_number or info.get("DeviceSerialNumber", "")
            valve.device_tag = valve.device_tag or info.get("Tag", "")
            valve.firmware = valve.firmware or info.get("FirmwareInfomation", "")
        if not valve.device_tag or valve.device_tag.upper() == "NEW":
            valve.device_tag = valve.folder

        for t in valve.tests:
            at = t.attrs.get("actuator_type")
            if at:
                valve.actuator_type = at
                break

        spec = QUALITY_SPEC_BY_FOLDER.get(valve.folder)
        if spec:
            valve.valve_model = spec["valve_model"]
            valve.valve_spec = spec["valve_spec"]
        valve.spec_page = spec_for_valve(valve.folder)
        if valve.spec_page:
            valve.spec_fields = valve.spec_page.fields
            valve.valve_model = valve.spec_page.model_no or valve.valve_model
            valve.valve_spec = valve.spec_page.valve_spec or valve.valve_spec
            # A ValvXpert Execution Report often omits the valve/positioner
            # serial number while the matched specification sheet records it.
            # Use that explicit spec value only as a fallback; never replace a
            # serial number from a stronger raw DTM/Sequencer/devdata source.
            apply_specification_identity_fallback(valve)
            valve.actuator_type = format_actuator_info(valve.spec_fields, valve.actuator_type)
            spec_bench_range = valve.spec_fields.get("bench_range") or ""
            spec_spring_note = valve.spec_fields.get("spring_range_note") or ""
            spec_travel = valve.spec_fields.get("actuator_travel") or ""
            if spec_bench_range and spec_travel:
                spec_spring_range = f"{spec_bench_range} / 行程 {spec_travel}"
            else:
                spec_spring_range = spec_bench_range or spec_spring_note
            valve.spring_range = spec_spring_range or valve.spring_range
            if valve.spec_fields.get("positioner_model"):
                valve.positioner_model = clean_spaces(valve.spec_fields["positioner_model"])
        valve.accessory_page = accessory_for_valve(valve.folder)
        if valve.accessory_page:
            accessory_model = valve.accessory_page.fields.get("positioner_model", "")
            if accessory_model:
                valve.positioner_model = accessory_model
        valve.air_piping_page = air_piping_for_valve(valve.folder)
        valve.report_pages = merge_spec_page_references([
            page
            for page in (
                valve.spec_page if INCLUDE_SPECIFICATIONS else None,
                valve.air_piping_page if INCLUDE_AIR_PIPING_PAGES else None,
                valve.accessory_page if INCLUDE_ACCESSORY_PAGES else None,
            )
            if page is not None
        ])
        if render_assets:
            for page_ref in valve.report_pages:
                render_report_page(page_ref)
            if valve.spec_page:
                matching = next(
                    (page for page in valve.report_pages if "specification" in page.roles), None
                )
                valve.spec_image = matching.image_path if matching else ""
            if valve.air_piping_page:
                matching = next(
                    (page for page in valve.report_pages if "air_piping" in page.roles), None
                )
                valve.air_piping_image = matching.image_path if matching else ""
            if valve.accessory_page:
                matching = next(
                    (page for page in valve.report_pages if "accessory" in page.roles), None
                )
                valve.accessory_image = matching.image_path if matching else ""
        valve.valve_type = infer_valve_type(valve.folder)
        valve.active_faults = sorted(set(clean_fault(f) for f in valve.active_faults if clean_fault(f)))
        valve.test_execution_issues = [
            dict(item)
            for item in {
                (item["test_key"], item["test_name"], item["occurrence"], item["status"]): item
                for item in valve.test_execution_issues
            }.values()
        ]
        valve.metrics = compute_metrics(valve.tests)
        apply_friction_measurement_override(valve.folder, valve.metrics)
        if render_assets:
            valve.images = sort_test_images(
                filter_excluded_source_images(
                    folder,
                    render_images_for_valve(
                        folder,
                        include_observation=False,
                        trusted_device_id=chosen,
                    ),
                )
            )
            valve.curve_interpretations = tuple(
                interpretation
                for image in valve.images
                if (
                    interpretation := CURVE_INTERPRETATIONS_BY_IMAGE_KEY.get(
                        curve_review_image_key(valve.folder, image)
                    )
                )
            )
            for image in valve.images:
                interpretation = CURVE_INTERPRETATIONS_BY_IMAGE_KEY.get(
                    curve_review_image_key(valve.folder, image)
                )
                if interpretation:
                    image["curve_review"] = interpretation
            if (
                CURVE_INTERPRETATIONS_BY_IMAGE_KEY
                and (
                    curve_review_required_folders is None
                    or valve.folder in curve_review_required_folders
                )
                and len(valve.curve_interpretations) != len(valve.images)
            ):
                raise RuntimeError(
                    f"final curve review does not cover every rendered curve for {valve.folder}"
                )
        apply_valve_context(valve)
        apply_structured_parameter_projection(valve)
        valves.append(valve)
    return valves


def collect_structured_evidence() -> list[ValveReport]:
    """Parse current project evidence without rendering charts or rating valves."""
    global SPEC_PAGES_BY_VALVE, AIR_PIPING_PAGES_BY_VALVE, ACCESSORY_PAGES_BY_VALVE
    global SPEC_FIELD_OVERRIDES_BY_TAG, FRICTION_MEASUREMENT_OVERRIDES_BY_TAG
    global HISTORICAL_RUNTIME_BY_TAG

    previous_spec_overrides = SPEC_FIELD_OVERRIDES_BY_TAG
    previous_friction_overrides = FRICTION_MEASUREMENT_OVERRIDES_BY_TAG
    previous_historical_runtime = HISTORICAL_RUNTIME_BY_TAG
    SPEC_FIELD_OVERRIDES_BY_TAG = {}
    FRICTION_MEASUREMENT_OVERRIDES_BY_TAG = {}
    HISTORICAL_RUNTIME_BY_TAG = {}
    try:
        SPEC_PAGES_BY_VALVE = load_spec_pages() if INCLUDE_SPECIFICATIONS else {}
        AIR_PIPING_PAGES_BY_VALVE = (
            load_air_piping_pages() if INCLUDE_AIR_PIPING_PAGES else {}
        )
        # Accessory sheets are an evidence source for the full positioner
        # order model even when the customer report is configured not to
        # insert accessory pages.  INCLUDE_ACCESSORY_PAGES controls document
        # presentation only; it must not suppress parameter extraction.
        ACCESSORY_PAGES_BY_VALVE = load_accessory_pages()
        return collect_valves(render_assets=False)
    finally:
        SPEC_FIELD_OVERRIDES_BY_TAG = previous_spec_overrides
        FRICTION_MEASUREMENT_OVERRIDES_BY_TAG = previous_friction_overrides
        HISTORICAL_RUNTIME_BY_TAG = previous_historical_runtime


def collect_curve_review_valves(*, render_assets: bool = True) -> list[ValveReport]:
    """Parse raw valve inputs without rating or curve interpretation."""
    valves: list[ValveReport] = []
    folders = sorted(p for p in RAW_DIR.iterdir() if p.is_dir())
    for folder in folders:
        valve = ValveReport(folder=folder.name)
        dev_infos: dict[str, dict[str, str]] = {}
        sequence_infos: list[dict[str, Any]] = []
        active_infos: list[dict[str, Any]] = []
        positioner_infos: list[dict[str, Any]] = []
        for path in sorted(folder.iterdir()):
            if not path.is_file():
                continue
            if source_file_is_excluded(path):
                continue
            if path.suffix.lower() == ".pdf":
                try:
                    if path.name.lower().startswith("seq_"):
                        records, info = parse_sequence_pdf(path)
                        sequence_infos.append(info)
                        valve.all_tests.extend(records)
                    else:
                        info = parse_active_report_pdf(path)
                        if info.get("is_dtm_report"):
                            active_infos.append(info)
                        else:
                            if info.get("report_type") == "valvxpert_execution":
                                positioner_infos.append(info)
                            if is_diagnostic_pdf(path, run_pdftotext(path)):
                                records, sequence_info = parse_sequence_pdf(path)
                                sequence_infos.append(sequence_info)
                                valve.all_tests.extend(records)
                except Exception as exc:
                    valve.unreadable.append(f"{path.name}: {exc}")
            elif path.suffix.lower() == ".devdata":
                try:
                    records, info = parse_devdata(path)
                    if info.get("DeviceId"):
                        dev_infos[info["DeviceId"]] = info
                    valve.all_tests.extend(records)
                except Exception as exc:
                    valve.unreadable.append(f"{path.name}: {exc}")

        chosen = choose_device_id(folder.name, valve.all_tests, sequence_infos, active_infos + positioner_infos)
        valve.device_id = chosen
        valve.tests = [test for test in valve.all_tests if not chosen or not test.device_id or test.device_id == chosen]
        for info in sequence_infos:
            if not chosen or info.get("device_id") == chosen:
                valve.device_tag = info.get("device_tag") or valve.device_tag
        if chosen in dev_infos:
            valve.device_tag = valve.device_tag or dev_infos[chosen].get("Tag", "")
        if not valve.device_tag or valve.device_tag.upper() == "NEW":
            valve.device_tag = valve.folder
        valve.metrics = compute_metrics(valve.tests)
        apply_friction_measurement_override(valve.folder, valve.metrics)
        if render_assets:
            render_curve_review_assets(valve)
        apply_valve_context(valve)
        valves.append(valve)
    return valves


def render_curve_review_assets(valve: ValveReport) -> None:
    folder = RAW_DIR / valve.folder
    valve.images = sort_test_images(
        filter_excluded_source_images(
            folder,
            render_images_for_valve(
                folder,
                include_observation=False,
                trusted_device_id=valve.device_id,
            ),
        )
    )


def choose_device_id(
    folder_name: str,
    tests: list[TestRecord],
    seq_infos: list[dict[str, Any]],
    active_infos: list[dict[str, Any]],
) -> str:
    folder_matched_infos = [
        info for info in [*seq_infos, *active_infos]
        if info.get("device_id") and execution_report_matches_folder(folder_name, info)
    ]
    if folder_matched_infos:
        return max(folder_matched_infos, key=report_selection_key).get("device_id", "")
    seq_ids = [i.get("device_id", "") for i in seq_infos if i.get("device_id")]
    if seq_ids:
        return Counter(seq_ids).most_common(1)[0][0]
    counts = Counter(t.device_id for t in tests if t.device_id)
    if counts:
        return counts.most_common(1)[0][0]
    return ""


def infer_valve_type(tag: str) -> str:
    if "-LV-" in tag:
        return "液位控制阀（依据位号推断，待现场复核）"
    if "-PV-" in tag:
        return "压力控制阀（依据位号推断，待现场复核）"
    if "-LCV-" in tag:
        return "液位控制阀（依据位号推断，待现场复核）"
    if "-TCV-" in tag:
        return "温度控制阀（依据位号推断，待现场复核）"
    if "-PZV-" in tag:
        return "压力/联锁阀（依据位号推断，待现场复核）"
    if "-TZV-" in tag:
        return "温度/联锁阀（依据位号推断，待现场复核）"
    return "控制阀（待现场复核）"


def compute_metrics(tests: list[TestRecord]) -> dict[str, Any]:
    step_values = []
    hys = []
    avg_hys = []
    run_ramp_values: dict[str, dict[str, list[Any]]] = {
        metric: {statistic: [] for statistic in ("max", "average", "min")}
        for metric in ("hysteresis", "dead_band", "linearity")
    }
    friction_measurements: list[dict[str, Any]] = []
    supply = []
    sig_errors = []
    response_open = []
    response_close = []

    for rec in tests:
        supply.extend(rec.supply_pressures)
        if rec.test_type in {"SIG-POS", "RAMP"}:
            hys.extend([rec.attrs.get("MaxHysteresis"), rec.attrs.get("MaximumHysteresis")])
            avg_hys.append(rec.attrs.get("AverageHysteresis"))
            for metric, prefix in (
                ("hysteresis", "Hysteresis"),
                ("dead_band", "DeadBand"),
                ("linearity", "Linearity"),
            ):
                for statistic, suffix in (
                    ("max", "Max"), ("average", "Average"), ("min", "Min")
                ):
                    run_ramp_values[metric][statistic].append(
                        rec.attrs.get(prefix + suffix)
                    )
            sig_errors.extend(
                [
                    rec.attrs.get("MaxErrorUp"),
                    rec.attrs.get("MaxErrorDown"),
                    rec.attrs.get("AverageErrorUp"),
                    rec.attrs.get("AverageErrorDown"),
                ]
            )
            for s in rec.steps:
                sig_errors.append(s.get("AverageError"))
        if rec.test_type in {"SIG-EXT", "SIG-S"}:
            values = rec.attrs.get("friction_values")
            if not isinstance(values, dict):
                values = {
                    "max": {"%": rec.attrs.get("maxFric"), "psi": rec.attrs.get("maxFric_pres")},
                    "min": {"%": rec.attrs.get("minFric"), "psi": rec.attrs.get("minFric_pres")},
                    "mean": {"%": rec.attrs.get("aveFric"), "psi": rec.attrs.get("aveFric_pres")},
                }
            friction_measurements.append(
                {
                    "source": rec.source,
                    "occurrence": rec.occurrence,
                    "test_type": rec.test_type,
                    "values": values,
                }
            )
            response_open.append(rec.attrs.get("responseTimeOpen"))
            response_close.append(rec.attrs.get("responseTimeClose"))
        if rec.test_type == "STEP":
            for step in rec.steps:
                if EXCLUDE_FULL_TRAVEL_SINGLE_STEP and is_full_travel_step_row(step):
                    continue
                step_values.append(step)

    # Percent is the canonical report channel because it is dimensionless and
    # is the unit used by the friction rating thresholds.  Pressure values are
    # used only when no signature occurrence supplies a percentage; never pool
    # psi and percent in one max/min calculation.
    pressure_unit_counts: Counter[str] = Counter(
        unit
        for item in friction_measurements
        for unit, value in item["values"].get("max", {}).items()
        if unit != "%" and value is not None
    )
    if any(item["values"].get("max", {}).get("%") is not None for item in friction_measurements):
        friction_unit = "%"
    else:
        friction_unit = pressure_unit_counts.most_common(1)[0][0] if pressure_unit_counts else "psi"
    max_friction = [item["values"].get("max", {}).get(friction_unit) for item in friction_measurements]
    min_friction = [item["values"].get("min", {}).get(friction_unit) for item in friction_measurements]
    mean_friction = [item["values"].get("mean", {}).get(friction_unit) for item in friction_measurements]
    selected_friction = {
        "max": max_num(max_friction),
        "min": min_num(min_friction),
        "mean": max_num(mean_friction),
    }

    friction_display: dict[str, dict[str, Any]] = {}
    for statistic, selected in selected_friction.items():
        if selected is None:
            continue
        for item in friction_measurements:
            channels = item["values"].get(statistic, {})
            if fnum(channels.get(friction_unit)) != selected:
                continue
            absolute = next(
                (
                    (unit, fnum(value))
                    for unit, value in channels.items()
                    if unit != "%" and fnum(value) is not None
                ),
                None,
            )
            friction_display[statistic] = {
                "percent": fnum(channels.get("%")),
                "absolute": absolute[1] if absolute else None,
                "absolute_unit": absolute[0] if absolute else "",
            }
            break

    run_ramp = {
        metric: {
            "max": max_num(values["max"]),
            "average": max_num(values["average"]),
            "min": min_num(values["min"]),
        }
        for metric, values in run_ramp_values.items()
    }
    return {
        "Average Error": max_num([s.get("AverageError") for s in step_values] + sig_errors),
        "T86": max_num([s.get("T86") for s in step_values]),
        "T63": max_num([s.get("T63") for s in step_values]),
        "Td": max_num([s.get("Td") for s in step_values]),
        "Overshoot": max_num([s.get("MaxOverShoot") for s in step_values]),
        "Maximum Hysteresis + Dead Band": max_num(hys),
        "Average Hysteresis + Dead Band": max_num(avg_hys),
        "run_ramp": run_ramp,
        "Max Friction": selected_friction["max"],
        "Min Friction": selected_friction["min"],
        "Mean Friction": selected_friction["mean"],
        "Friction Unit": friction_unit if friction_measurements else "",
        "Friction Display": friction_display,
        "Friction Measurements": friction_measurements,
        "Supply Pressure": {
            "min": min_num(supply),
            "max": max_num(supply),
            "avg": avg_num(supply),
        },
        "Response Time Open": max_num(response_open),
        "Response Time Close": max_num(response_close),
        "step_count": len(step_values),
        "test_types": sorted(set(t.test_type for t in tests)),
    }


def apply_friction_measurement_override(valve_tag: str, metrics: dict[str, Any]) -> None:
    """Apply a project-reviewed Signature table without embedding customer facts.

    Overrides are reserved for source pages whose PDF text layer lost decimal
    points or unit glyphs.  They remain project data and retain the raw page
    provenance used during manual/Codex review.
    """
    override = FRICTION_MEASUREMENT_OVERRIDES_BY_TAG.get(valve_tag)
    if not isinstance(override, dict):
        return
    percent = override.get("percent")
    force = override.get("force")
    if not isinstance(percent, dict) or not isinstance(force, dict):
        raise ValueError(f"invalid friction measurement override for {valve_tag}")
    force_unit = str(force.get("unit") or "").strip()
    required = ("max", "min", "mean")
    if not force_unit or any(fnum(percent.get(key)) is None for key in required) or any(
        fnum(force.get(key)) is None for key in required
    ):
        raise ValueError(f"incomplete friction measurement override for {valve_tag}")
    percent_values = {key: float(percent[key]) for key in required}
    force_values = {key: float(force[key]) for key in required}
    metrics.update(
        {
            "Max Friction": percent_values["max"],
            "Min Friction": percent_values["min"],
            "Mean Friction": percent_values["mean"],
            "Friction Unit": "%",
            "Friction Display": {
                key: {
                    "percent": percent_values[key],
                    "absolute": force_values[key],
                    "absolute_unit": force_unit,
                }
                for key in required
            },
            "Friction Measurements": [
                {
                    "source": str(override.get("source") or ""),
                    "source_page": override.get("source_page"),
                    "report_page": str(override.get("report_page") or ""),
                    "occurrence": int(override.get("occurrence") or 1),
                    "test_type": "SIG-EXT",
                    "review_status": "project_reviewed_override",
                    "values": {
                        "max": {"%": percent_values["max"], force_unit: force_values["max"]},
                        "min": {"%": percent_values["min"], force_unit: force_values["min"]},
                        "mean": {"%": percent_values["mean"], force_unit: force_values["mean"]},
                    },
                }
            ],
        }
    )


def is_full_travel_step_row(step: dict[str, Any]) -> bool:
    """Return true only for a 0%-to-100% or 100%-to-0% step row."""
    start = fnum(step.get("StartPosition", step.get("start")))
    end = fnum(step.get("EndPosition", step.get("end")))
    return start is not None and end is not None and {start, end} == {0.0, 100.0}


@dataclass(frozen=True)
class DataRatingCalculation:
    rating: int | None
    severe: list[str]
    defect: list[str]
    minor: list[str]


@dataclass(frozen=True)
class VisualRatingCalculation:
    rating: int | None
    evidence: list[dict[str, Any]]


def calculate_data_rating(
    valve: ValveReport,
    *,
    has_test_data: bool | None = None,
) -> DataRatingCalculation:
    """Rate only parsed metrics, test availability, and active fault evidence."""
    m = valve.metrics
    max_hys = m.get("Maximum Hysteresis + Dead Band")
    avg_hys = m.get("Average Hysteresis + Dead Band")
    max_err = m.get("Average Error")
    max_t86 = m.get("T86")
    max_t63 = m.get("T63")
    max_td = m.get("Td")
    max_os = m.get("Overshoot")
    max_fric = m.get("Max Friction")
    severe: list[str] = []
    defect: list[str] = []
    minor: list[str] = []
    if max_hys is not None:
        if max_hys >= 10: severe.append(f"迟滞/死区最大值 {fmt(max_hys, '%')} 明显偏大")
        elif max_hys >= 3: defect.append(f"迟滞/死区最大值 {fmt(max_hys, '%')} 偏大")
        elif max_hys >= 2: minor.append(f"迟滞/死区最大值 {fmt(max_hys, '%')} 略高")
    if avg_hys is not None:
        if avg_hys >= 8: severe.append(f"平均迟滞/死区 {fmt(avg_hys, '%')} 明显偏大")
        elif avg_hys >= 3: defect.append(f"平均迟滞/死区 {fmt(avg_hys, '%')} 偏大")
    if max_fric is not None:
        if max_fric >= 50: severe.append(f"最大摩擦指标 {fmt(max_fric)} 偏高")
        elif max_fric >= 20: defect.append(f"摩擦指标 {fmt(max_fric)} 偏高")
    if max_err is not None:
        if max_err >= 10: severe.append(f"阶跃/曲线平均误差最大值 {fmt(max_err, '%')} 明显偏大")
        elif max_err >= 4: defect.append(f"阶跃/曲线平均误差最大值 {fmt(max_err, '%')} 偏大")
        elif max_err >= 1: minor.append(f"阶跃/曲线平均误差最大值 {fmt(max_err, '%')} 略高")
    if max_t86 is not None:
        if max_t86 >= 3: defect.append(f"T86 最大 {fmt(max_t86, 's')}，响应偏慢")
    if max_t63 is not None and max_t63 >= 8: defect.append(f"T63 最大 {fmt(max_t63, 's')}，响应存在迟滞")
    if max_td is not None:
        if max_td >= 2: defect.append(f"Td 最大 {fmt(max_td, 's')}，启动延迟偏大")
    if max_os is not None:
        if max_os >= 100: defect.append(f"Overshoot 最大 {fmt(max_os, '%')}，小信号曲线存在超调/震荡迹象")
        elif max_os >= 30: defect.append(f"Overshoot 最大 {fmt(max_os, '%')} 偏大")
        elif max_os >= 10: minor.append(f"Overshoot 最大 {fmt(max_os, '%')} 略高")
    if valve.active_faults:
        defect.append("定位器活动故障：" + "；".join(valve.active_faults[:4]))
    test_data_available = bool(valve.tests) if has_test_data is None else has_test_data
    rating = None if not test_data_available and not valve.active_faults else (4 if severe else 3 if defect else 2 if minor else 1)
    return DataRatingCalculation(rating=rating, severe=severe, defect=defect, minor=minor)


def calculate_visual_rating(interpretations: tuple[CurveInterpretation, ...]) -> VisualRatingCalculation:
    """Rate only validated per-image interpretations; numerical metrics are never read."""
    if not interpretations:
        return VisualRatingCalculation(rating=None, evidence=[])
    if any(not isinstance(item, CurveInterpretation) for item in interpretations):
        raise TypeError("visual rating requires validated CurveInterpretation entries")
    evidence = [
        {"image_key": item.image_key, "test_key": item.test_key, "rating": item.rating, "confidence": item.confidence}
        for item in interpretations
    ]
    highest = max(item.rating for item in interpretations)
    low_grade_four = [item for item in interpretations if item.rating == 4 and item.confidence == "low"]
    if highest == 4 and low_grade_four:
        image_support = any(
            other.image_key != low.image_key and other.rating >= 3 and other.confidence in {"high", "medium"}
            for low in low_grade_four for other in interpretations
        )
        if not image_support:
            highest = 3
    return VisualRatingCalculation(rating=highest, evidence=evidence)


def calculate_algorithm_audit(
    source_record: Mapping[str, Any],
    curve_interpretations: tuple[CurveInterpretation, ...],
) -> dict[str, Any]:
    """Calculate the third-stage algorithm audit without report text or overrides."""
    has_test_data, metrics, active_faults, basic_action_failed = (
        _algorithm_audit_inputs(source_record)
    )

    valve = ValveReport(
        folder=str(source_record.get("folder") or ""),
        metrics=dict(metrics),
        active_faults=list(active_faults),
        basic_action_failed=basic_action_failed,
    )
    data = calculate_data_rating(valve, has_test_data=has_test_data)
    visual = calculate_visual_rating(curve_interpretations)
    combined = combine_ratings(
        data.rating,
        visual.rating,
        basic_action_failed=basic_action_failed,
    )
    rating_evidence = {
        "data": {
            "rating": data.rating,
            "severe": data.severe,
            "defect": data.defect,
            "minor": data.minor,
        },
        "visual": {"rating": visual.rating, "images": visual.evidence},
        "basic_action_failed": basic_action_failed,
        "data_evidence_status": combined.data_evidence_status,
        "data_evidence_reason": combined.data_evidence_reason,
    }
    input_digest = algorithm_audit_input_digest(
        source_record,
        curve_interpretations,
    )
    return {
        "schema_version": 1,
        "status": "available",
        "data_rating": combined.data_rating,
        "visual_rating": combined.visual_rating,
        "algorithm_rating": combined.final_rating,
        "rating_inconsistency_reason": (
            "data_visual_rating_difference_at_least_two_levels"
            if combined.inconsistent
            else None
        ),
        "rating_evidence": rating_evidence,
        "input_digest": input_digest,
    }


def algorithm_audit_input_digest(
    source_record: Mapping[str, Any],
    curve_interpretations: tuple[CurveInterpretation, ...],
) -> str:
    """Hash only facts consumed by the deterministic algorithm audit."""
    has_test_data, metrics, active_faults, basic_action_failed = (
        _algorithm_audit_inputs(source_record)
    )
    digest_payload = {
        "has_test_data": has_test_data,
        "metrics": dict(metrics),
        "active_faults": active_faults,
        "basic_action_failed": basic_action_failed,
        "curve_interpretations": [
            {
                "image_key": item.image_key,
                "test_key": item.test_key,
                "rating": item.rating,
                "confidence": item.confidence,
            }
            for item in curve_interpretations
        ],
    }
    return hashlib.sha256(
        (json.dumps(digest_payload, ensure_ascii=False, sort_keys=True) + "\n").encode(
            "utf-8"
        )
    ).hexdigest()


def _algorithm_audit_inputs(
    source_record: Mapping[str, Any],
) -> tuple[bool, Mapping[str, Any], list[str], bool]:
    has_test_data = source_record.get("has_test_data")
    metrics = source_record.get("metrics")
    active_faults = source_record.get("active_faults")
    basic_action_failed = source_record.get("basic_action_failed")
    if not isinstance(has_test_data, bool):
        raise ValueError("algorithm audit source requires boolean has_test_data")
    if not isinstance(metrics, Mapping):
        raise ValueError("algorithm audit source requires metrics")
    if not isinstance(active_faults, list) or not all(
        isinstance(item, str) for item in active_faults
    ):
        raise ValueError("algorithm audit source requires active_faults text list")
    if not isinstance(basic_action_failed, bool):
        raise ValueError("algorithm audit source requires boolean basic_action_failed")
    return has_test_data, metrics, active_faults, basic_action_failed


def report_device_id(text: str) -> str:
    compact = clean_spaces(text)
    return first_regex_value(
        compact,
        [r"Device ID\s*:?\s*([0-9_]+)", r"设备识别码\s*[:：]?\s*([0-9_]+)"],
    ).strip("_")


def render_images_for_valve(
    folder: Path,
    *,
    include_observation: bool = False,
    trusted_device_id: str = "",
) -> list[dict[str, str]]:
    if include_observation:
        raise ValueError(
            "automatic curve interpretation is retired; use validated curve review"
        )
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    valve_img_dir = IMAGE_DIR / folder.name
    valve_img_dir.mkdir(parents=True, exist_ok=True)
    images: list[dict[str, str]] = []

    for pdf in sorted(path for path in folder.iterdir() if path.is_file() and path.suffix.lower() == ".pdf"):
        image_start_index = len(images)
        prepared_pages = run_pdftotext_pages(pdf)
        text = "\f".join(prepared_pages)
        if is_svi_ii_ap_report_text(text):
            continue
        source_device_id = report_device_id(text)
        if trusted_device_id and source_device_id != trusted_device_id:
            continue
        try:
            doc = fitz.open(pdf)
        except Exception:
            continue
        try:
            if not is_diagnostic_pdf(pdf, text):
                curve_pages = [
                    page_index + 1
                    for page_index, page in enumerate(doc)
                    if re.search(r"Position\s*\(%\)|Set\s*Point\s*\(%\)|\bSP\s*\(%\)", page.get_text() or "", re.I)
                    and find_page_chart_rects(page)
                ]
                if curve_pages:
                    raise RuntimeError(
                        f"unrecognized diagnostic PDF contains curve pages: {pdf.name} pages {curve_pages}"
                    )
                continue
            chart_index = 0
            current_test = ""
            page_texts: list[str] = []
            for page_index, page in enumerate(doc):
                native_text = page.get_text() or ""
                prepared_text = (
                    prepared_pages[page_index]
                    if page_index < len(prepared_pages)
                    else ""
                )
                if prepared_text.strip() and (
                    page_curve_test_keys(prepared_text) or not native_text.strip()
                ):
                    page_texts.append(prepared_text)
                else:
                    page_texts.append(native_text)
            inventory_by_page: dict[int, list[str]] = {}
            for item in parse_curve_inventory("\f".join(page_texts), pdf.name):
                inventory_by_page.setdefault(int(item["page"]), []).append(
                    str(item["test_key"])
                )
            pending_curve_keys: list[str] = []
            for page_index, page in enumerate(doc):
                page_text = clean_spaces(page_texts[page_index])
                current_title_y: float | None = None
                events = []
                for title in page_test_title_events(page, page_text):
                    events.append(("title", title["y"], title))
                inventory_keys = inventory_by_page.get(page_index + 1, [])
                physical_keys = [*pending_curve_keys, *inventory_keys]
                page_keys = list(page_curve_test_keys(page_text))
                has_curve_axis = bool(
                    re.search(
                        r"\bTime\s*\(\s*(?:s|sec)\s*\)|"
                        r"\bSet\s*Point\s*\(|"
                        r"\bPressure\s*\(|"
                        r"\bPosition\s*\(",
                        page_text,
                        re.I,
                    )
                )
                legacy_seq_page = (
                    pdf.name.lower().startswith("seq_")
                    and page_index + 1 in {3, 5}
                )
                raw_chart_rects = find_page_chart_rects(page)
                # A new section above the first plot ends an unfinished prior
                # section; only an untitled leading plot can continue it.
                if events and (not raw_chart_rects or min(event[1] for event in events) < min(rect.y0 for rect in raw_chart_rects)):
                    physical_keys = inventory_keys
                chart_rects = (
                    filter_embedded_result_table_outliers(
                        page,
                        raw_chart_rects,
                        expected_test_count=(
                            len(physical_keys) or len(page_keys) or None
                        ),
                    )
                    if (
                        inventory_keys
                        or page_keys
                        or legacy_seq_page
                        or (pending_curve_keys and has_curve_axis)
                    )
                    else []
                )
                if len(physical_keys) == len(raw_chart_rects):
                    assigned_keys = physical_keys
                elif len(physical_keys) == 1:
                    assigned_keys = physical_keys * len(raw_chart_rects)
                elif physical_keys:
                    assigned_keys = physical_keys[: len(raw_chart_rects)]
                    if len(assigned_keys) < len(raw_chart_rects):
                        assigned_keys.extend(
                            [physical_keys[-1]]
                            * (len(raw_chart_rects) - len(assigned_keys))
                        )
                elif len(page_keys) == len(raw_chart_rects):
                    assigned_keys = page_keys
                elif len(page_keys) == 1:
                    assigned_keys = page_keys * len(raw_chart_rects)
                else:
                    assigned_keys = [""] * len(raw_chart_rects)
                assigned_by_rect = {
                    tuple(rect): test_key
                    for rect, test_key in zip(raw_chart_rects, assigned_keys)
                }
                retained_counts = Counter(
                    assigned_by_rect.get(tuple(rect), "")
                    for rect in chart_rects
                )
                pending_curve_keys = []
                for test_key in physical_keys:
                    if retained_counts[test_key]:
                        retained_counts[test_key] -= 1
                    else:
                        pending_curve_keys.append(test_key)
                for rect in chart_rects:
                    events.append(("chart", rect.y0, rect))
                events.sort(key=lambda item: (item[1], 0 if item[0] == "title" else 1))
                inferred_page_test = infer_test_key_from_text(page_text)
                if inferred_page_test:
                    current_test = inferred_page_test

                for event_type, _, payload in events:
                    if event_type == "title":
                        current_test = payload["test_key"]
                        current_title_y = float(payload["y"])
                        continue
                    rect = payload
                    test_key = (
                        assigned_by_rect.get(tuple(rect))
                        or current_test
                        or inferred_page_test
                        or "other"
                    )
                    if test_key == "other" and pdf.name.lower().startswith("seq_"):
                        if page_index + 1 == 3:
                            test_key = "run_step"
                        elif page_index + 1 == 5:
                            test_key = "pattern_step"
                    chart_index += 1
                    label = TEST_LABELS.get(test_key, TEST_LABELS["other"])
                    out = valve_img_dir / f"{pdf.stem}_{test_key}_p{page_index+1:02d}_{chart_index:02d}.png"
                    render_chart_crop(page, rect, out, title_y=current_title_y)
                    image = {
                        "path": str(out),
                        "source": pdf.name,
                        "page": str(page_index + 1),
                        "label": label,
                        "test_key": test_key,
                        "bbox": ",".join(f"{v:.1f}" for v in (rect.x0, rect.y0, rect.x1, rect.y1)),
                    }
                    image = apply_curve_review_override(folder.name, image)
                    if image is None:
                        continue
                    test_key = str(image["test_key"])
                    if test_key == "run_step" and is_full_travel_single_step_text(page_text):
                        image["step_profile"] = "full_travel_single_step"
                    if not (
                        EXCLUDE_FULL_TRAVEL_SINGLE_STEP
                        and image.get("step_profile") == "full_travel_single_step"
                    ):
                        images.append(image)
        finally:
            doc.close()
        attach_curve_execution_times(
            images[image_start_index:],
            parse_task_execution(text),
        )
    return images


def apply_curve_review_override(
    valve_tag: str,
    image: dict[str, str],
) -> dict[str, str] | None:
    """Apply project-level exclusions and visually confirmed test identities."""
    image_name = Path(str(image.get("path") or "")).name
    key = f"{valve_tag}/{image_name}"
    if key in CURVE_IMAGE_EXCLUSIONS:
        return None
    test_key = CURVE_TEST_KEY_OVERRIDES.get(key)
    if not test_key:
        return image
    updated = dict(image)
    updated["test_key"] = test_key
    updated["label"] = TEST_LABELS[test_key]
    return updated


def is_full_travel_single_step_text(text: str) -> bool:
    """Recognize an explicitly declared single 0%-to-100% step test page."""
    compact = clean_spaces(text)
    if not re.search(r"Step Type\s+Single Step", compact, re.I):
        return False
    start = first_regex_float(
        compact,
        [r"Position:\s*Start\s+([-+]?\d+(?:\.\d+)?)\s*%"],
    )
    end = first_regex_float(
        compact,
        [r"\bEnd\s+([-+]?\d+(?:\.\d+)?)\s*%"],
    )
    return start is not None and end is not None and {start, end} == {0.0, 100.0}


def curve_review_image_key(valve_folder: str, image: dict[str, Any]) -> str:
    """Return the stable review key shared with the prepare-review manifest."""
    return curve_image_key(valve_folder, image)


def report_page_insertion_markers(valve_folder: str, page: SpecPage) -> dict[str, str]:
    return {role: spec_insertion_marker(valve_folder, role) for role in page.roles}


def diagnostic_image_rect(page_rect: fitz.Rect, chart_rect: fitz.Rect, title_y: float | None = None) -> fitz.Rect:
    top = title_y - 6 if title_y is not None else page_rect.y0 + 18
    return fitz.Rect(
        max(page_rect.x0, chart_rect.x0 - 8),
        max(page_rect.y0, top),
        min(page_rect.x1, chart_rect.x1 + 16),
        min(page_rect.y1, chart_rect.y1 + 8),
    )


def render_chart_crop(page: fitz.Page, rect: fitz.Rect, out: Path, *, title_y: float | None = None) -> None:
    clip = diagnostic_image_rect(page.rect, rect, title_y)
    pix = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0), clip=clip, alpha=False)
    pix.save(out)


def find_page_chart_rects(page: fitz.Page) -> list[fitz.Rect]:
    candidates: list[fitz.Rect] = []
    page_rect = page.rect

    for block in page.get_text("dict").get("blocks", []):
        if block.get("type") != 1:
            continue
        rect = fitz.Rect(block["bbox"])
        if rect.width >= 250 and rect.height >= 120 and rect.y0 > 80:
            candidates.append(rect)

    for drawing in page.get_drawings():
        rect = drawing.get("rect")
        if not rect:
            continue
        if rect.width >= 300 and rect.height >= 150 and rect.width < page_rect.width * 0.98 and rect.height < page_rect.height * 0.6:
            candidates.append(fitz.Rect(rect))

    return dedupe_chart_rects(candidates)


def _embedded_image_chroma_ratio(image_bytes: bytes) -> float | None:
    """Estimate saturated UI content without adding an OCR dependency."""
    try:
        pixmap = fitz.Pixmap(image_bytes)
        if pixmap.colorspace and pixmap.colorspace.n != 3:
            pixmap = fitz.Pixmap(fitz.csRGB, pixmap)
    except (RuntimeError, ValueError):
        return None
    channels = pixmap.n
    if channels < 3 or pixmap.width <= 0 or pixmap.height <= 0:
        return None
    pixels = pixmap.width * pixmap.height
    stride = max(1, pixels // 50_000)
    samples = pixmap.samples
    saturated = 0
    sampled = 0
    for offset in range(0, len(samples), channels * stride):
        red, green, blue = samples[offset : offset + 3]
        saturated += max(red, green, blue) - min(red, green, blue) > 25
        sampled += 1
    return saturated / sampled if sampled else None


def filter_embedded_result_table_outliers(
    page: fitz.Page,
    rects: list[fitz.Rect],
    *,
    expected_test_count: int | None = None,
) -> list[fitz.Rect]:
    """Drop only a clear saturated UI-table outlier beside a chart candidate.

    ValVxpert exports may embed a result-table screenshot and a plot screenshot
    as adjacent large images.  The rule is deliberately relative within one
    page: a lone or similarly coloured candidate is never rejected.
    """
    image_scores: list[tuple[fitz.Rect, float]] = []
    for block in page.get_text("dict").get("blocks", []):
        if block.get("type") != 1 or not block.get("image"):
            continue
        score = _embedded_image_chroma_ratio(block["image"])
        if score is not None:
            image_scores.append((fitz.Rect(block["bbox"]), score))
    matched_scores: dict[tuple[float, float, float, float], float] = {}
    for rect in rects:
        match = max(
            (
                (rect_iou(rect, image_rect), score)
                for image_rect, score in image_scores
            ),
            default=(0.0, 0.0),
        )
        if match[0] >= 0.85:
            matched_scores[tuple(rect)] = match[1]
    kept = rects
    if len(matched_scores) >= 2:
        baseline = min(matched_scores.values())
        kept = [
            rect
            for rect in rects
            if (
                tuple(rect) not in matched_scores
                or matched_scores[tuple(rect)] - baseline < 0.012
                or matched_scores[tuple(rect)] < baseline * 1.5
            )
        ]
    if (
        expected_test_count == 1
        and len(kept) > 1
        and all(tuple(rect) in matched_scores for rect in kept)
    ):
        return [kept[-1]]
    return kept


def dedupe_chart_rects(rects: list[fitz.Rect]) -> list[fitz.Rect]:
    kept: list[fitz.Rect] = []
    for rect in sorted(rects, key=lambda r: r.width * r.height, reverse=True):
        if any(rect_iou(rect, existing) > 0.85 for existing in kept):
            continue
        if any(rect_inside(rect, existing, tolerance=3) for existing in kept):
            continue
        kept.append(rect)
    return sorted(kept, key=lambda r: (r.y0, r.x0))


def rect_inside(inner: fitz.Rect, outer: fitz.Rect, tolerance: float = 0) -> bool:
    return (
        inner.x0 >= outer.x0 - tolerance
        and inner.y0 >= outer.y0 - tolerance
        and inner.x1 <= outer.x1 + tolerance
        and inner.y1 <= outer.y1 + tolerance
    )


def rect_iou(a: fitz.Rect, b: fitz.Rect) -> float:
    x0 = max(a.x0, b.x0)
    y0 = max(a.y0, b.y0)
    x1 = min(a.x1, b.x1)
    y1 = min(a.y1, b.y1)
    if x1 <= x0 or y1 <= y0:
        return 0.0
    inter = (x1 - x0) * (y1 - y0)
    union = a.width * a.height + b.width * b.height - inter
    return inter / union if union else 0.0


def page_test_title_events(page: fitz.Page, page_text: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for block in page.get_text("blocks"):
        text = clean_spaces(str(block[4]))
        key = title_test_key(text, page_text)
        if key:
            events.append({"y": block[1], "test_key": key})
    return events


def title_test_key(title_text: str, page_text: str) -> str:
    if re.search(r"\bTask\s+Execution\s+Summary\b", page_text, re.I):
        return ""
    lower = title_text.lower()
    page_keys = page_curve_test_keys(page_text)
    if "pattern step test" in lower:
        return "pattern_step"
    if "step test information" in lower or re.search(r"\brun step test\b", lower):
        step_keys = set(page_keys) & {"run_step", "pattern_step"}
        return next(iter(step_keys)) if len(step_keys) == 1 else ""
    if "ramp test information" in lower or re.search(r"\brun ramp test\b", lower):
        return "run_ramp"
    if re.search(r"\bextended act", lower):
        return "extended_signature"
    if "standard signature" in lower:
        return "standard_signature"
    if "run signature test" in lower:
        signature_keys = set(page_keys) & {
            "standard_signature",
            "extended_signature",
        }
        if len(signature_keys) == 1:
            return next(iter(signature_keys))
        return "standard_signature" if not signature_keys else ""
    return ""


def infer_test_key_from_text(text: str) -> str:
    keys = page_curve_test_keys(text)
    return keys[0] if keys else ""


def is_pattern_step(text: str) -> bool:
    compact = clean_spaces(text)
    if (
        _PATTERN_STEP_TITLE_RE.search(compact)
        or _PATTERN_STEP_TYPE_RE.search(compact)
        or re.search(r"\bSt(?:e|o)p\s+Type\s+Pattern\b", compact, re.I)
    ):
        return True
    step_size = first_regex_float(
        compact,
        [
            r"Step Size(?: in %)?\s*[:=]\s*([-+]?\d+(?:\.\d+)?)",
            r"\bSize\s+([-+]?\d+(?:\.\d+)?)\s*%",
        ],
    )
    start = first_regex_float(
        compact,
        [
            r"Start Position(?: in %)?\s*[:=]\s*([-+]?\d+(?:\.\d+)?)",
            r"Position:\s*Start\s+([-+]?\d+(?:\.\d+)?)\s*%",
        ],
    )
    end = first_regex_float(
        compact,
        [
            r"End Position(?: in %)?\s*[:=]\s*([-+]?\d+(?:\.\d+)?)",
            r"\bEnd\s+([-+]?\d+(?:\.\d+)?)\s*%",
        ],
    )
    if step_size is not None and step_size <= 5:
        return True
    if start is not None and end is not None and abs(end - start) <= 10:
        return True
    return False


def regex_float(text: str, pattern: str) -> float | None:
    value = regex_value(text, pattern)
    return fnum(value)


def first_regex_float(text: str, patterns: list[str]) -> float | None:
    for pattern in patterns:
        value = regex_float(text, pattern)
        if value is not None:
            return value
    return None


def is_diagnostic_pdf(pdf: Path, text: str) -> bool:
    name = pdf.name.lower()
    if name.startswith("seq_"):
        return True
    return bool(
        re.search(
            r"Step Test Information|Ramp Test Information|Run Step Test|Run Ramp Test|"
            r"Extended Actuator|Extended Act\. Signature|Hysteresis and Dead Band|Friction",
            text,
            re.I,
        )
    )


def write_summary(
    valves: list[ValveReport],
    included_folders: set[str] | None = None,
    *,
    structured_evidence_project_root: Path | None = None,
    report_field_manifest_builder: Any | None = None,
    include_diagnosis: bool = True,
    diagnosis_folders: set[str] | None = None,
    include_has_test_data_folders: set[str] | None = None,
) -> list[dict[str, Any]]:
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    if report_field_manifest_builder is None and any(
        isinstance(valve.structured_evidence, dict)
        and (included_folders is None or valve.folder in included_folders)
        for valve in valves
    ):
        raise ValueError("report field manifest builder is required")

    data = []
    for v in valves:
        included = included_folders is None or v.folder in included_folders
        has_diagnosis = include_diagnosis and (
            diagnosis_folders is None or v.folder in diagnosis_folders
        )
        report_pages = report_pages_for_valve(v)
        item = {
                "folder": v.folder,
                "included_in_report": included,
                "description": v.description,
                "unit": v.unit,
                "failure_consequence": v.failure_consequence,
                "bypass": v.bypass,
                "site_note": v.site_note,
                "device_id": v.device_id,
                "device_tag": v.device_tag,
                "serial_number": v.serial_number,
                "detection_time": v.detection_date,
                "positioner_model": v.positioner_model,
                "positioner_type": v.positioner_type,
                "firmware": v.firmware,
                "actuator_type": v.actuator_type,
                "spring_range": v.spring_range,
                "valve_model": v.valve_model,
                "valve_spec": v.valve_spec,
                "files": [f.__dict__ for f in v.files],
                "source_report_types": v.source_report_types,
                "dtm_data_report_count": v.dtm_data_report_count,
                "active_faults": v.active_faults,
                "data_note": v.data_note,
                "spec_fields": v.spec_fields,
                "dtm_info": v.dtm_info,
                "positioner_report_info": v.positioner_report_info,
                "metrics": v.metrics,
                **(
                    {"has_test_data": bool(v.tests)}
                    if include_has_test_data_folders is None
                    or v.folder in include_has_test_data_folders
                    else {}
                ),
                "rating": v.rating if has_diagnosis else None,
                "algorithm_rating": v.algorithm_rating if has_diagnosis else None,
                "algorithm_audit_status": (
                    v.algorithm_audit_status if has_diagnosis else ""
                ),
                "algorithm_audit_snapshot": (
                    v.algorithm_audit_snapshot if has_diagnosis else None
                ),
                "data_rating": v.data_rating if has_diagnosis else None,
                "visual_rating": v.visual_rating if has_diagnosis else None,
                "final_rating": v.final_rating if has_diagnosis else None,
                "rating_inconsistency_reason": (
                    v.rating_inconsistency_reason if has_diagnosis else None
                ),
                "rating_evidence": v.rating_evidence if has_diagnosis else {},
                "basic_action_failed": v.basic_action_failed,
                "test_execution_issues": v.test_execution_issues,
                "rating_override_note": v.rating_override_note if has_diagnosis else "",
                "main_issue": v.main_issue if has_diagnosis else "",
                "action": v.action if has_diagnosis else "",
                "issues": v.issues if has_diagnosis else [],
                "actions": v.actions if has_diagnosis else [],
                "basis": v.basis if has_diagnosis else [],
                "curve_summary": v.curve_summary if has_diagnosis else [],
                "overall_assessment": v.overall_assessment if has_diagnosis else "",
                **(
                    {
                        "structured_evidence": v.structured_evidence,
                        "report_field_manifest": report_field_manifest_builder(
                            v, v.structured_evidence
                        ),
                    }
                    if included
                    and isinstance(v.structured_evidence, dict)
                    and report_field_manifest_builder is not None
                    else {}
                ),
                "report_pages": [
                    {
                        "source": page.source,
                        "source_sha256": page.source_sha256,
                        "page": page.page,
                        "roles": list(page.roles),
                        "image_path": page.image_path,
                        "match_basis": list(page.match_basis),
                        "physical_identity": page.physical_identity,
                        "insertion_markers": (
                            report_page_insertion_markers(v.folder, page) if included else {}
                        ),
                    }
                    for page in report_pages
                ],
                "spec_page": v.spec_page.__dict__ if v.spec_page else None,
                "spec_image": v.spec_image,
                "spec_insertion_marker": (
                    spec_insertion_marker(v.folder, "spec")
                    if included and v.spec_page and v.spec_image else ""
                ),
                "accessory_page": v.accessory_page.__dict__ if v.accessory_page else None,
                "accessory_image": v.accessory_image,
                "accessory_insertion_marker": (
                    spec_insertion_marker(v.folder, "accessory")
                    if included and v.accessory_page and v.accessory_image else ""
                ),
                "air_piping_page": v.air_piping_page.__dict__ if v.air_piping_page else None,
                "air_piping_image": v.air_piping_image,
                "air_piping_insertion_marker": (
                    spec_insertion_marker(v.folder, "air_piping")
                    if included and v.air_piping_page and v.air_piping_image else ""
                ),
                "images": [
                    {
                        **{key: value for key, value in image.items() if key != "curve_review"},
                        "interpretation_validated": isinstance(image.get("curve_review"), CurveInterpretation),
                        "review_marker": (
                            curve_review_marker(image["curve_review"].image_key)
                            if isinstance(image.get("curve_review"), CurveInterpretation) else ""
                        ),
                    }
                    for image in v.images
                ],
                "unreadable": v.unreadable,
            }
        if structured_evidence_project_root is not None:
            from .evidence_extraction import build_project_structured_evidence

            v.structured_evidence = build_project_structured_evidence(
                item,
                project_root=structured_evidence_project_root,
            )
            item["structured_evidence"] = v.structured_evidence
            if included:
                if report_field_manifest_builder is None:
                    raise ValueError("report field manifest builder is required")
                item["report_field_manifest"] = report_field_manifest_builder(
                    v,
                    v.structured_evidence,
                )
        data.append(item)
    SUMMARY_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def select_report_valves(
    valves: list[ValveReport],
    *,
    include_data_insufficient: bool,
    only_valves: tuple[str, ...],
    append_valves: tuple[str, ...],
) -> list[ValveReport]:
    """Apply presentation-only valve filtering without altering source data."""
    selected = valves if include_data_insufficient else [
        valve for valve in valves if valve.final_rating != 5
    ]
    if only_valves:
        only_order = {tag: index for index, tag in enumerate(only_valves)}
        selected = sorted(
            (valve for valve in selected if valve.folder in only_order),
            key=lambda valve: only_order[valve.folder],
        )
    if not append_valves:
        return selected
    append_order = {tag: index for index, tag in enumerate(append_valves)}
    existing = [valve for valve in selected if valve.folder not in append_order]
    appended = sorted(
        (valve for valve in selected if valve.folder in append_order),
        key=lambda valve: append_order[valve.folder],
    )
    return existing + appended


def prepare_valve_data(
    *,
    curve_review_required_folders: set[str] | None = None,
) -> list[ValveReport]:
    """Prepare every valve in the configured report scope without selecting output."""
    global SPEC_PAGES_BY_VALVE, AIR_PIPING_PAGES_BY_VALVE, ACCESSORY_PAGES_BY_VALVE
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    if IMAGE_DIR.exists():
        shutil.rmtree(IMAGE_DIR)
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    if SPEC_IMAGE_DIR.exists():
        shutil.rmtree(SPEC_IMAGE_DIR)
    SPEC_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    SPEC_PAGES_BY_VALVE = load_spec_pages() if INCLUDE_SPECIFICATIONS else {}
    AIR_PIPING_PAGES_BY_VALVE = load_air_piping_pages() if INCLUDE_AIR_PIPING_PAGES else {}
    # Always load accessory metadata.  The report-page assembly below still
    # obeys INCLUDE_ACCESSORY_PAGES and therefore does not expose the page
    # merely because its fields are used.
    ACCESSORY_PAGES_BY_VALVE = load_accessory_pages()
    return collect_valves(
        curve_review_required_folders=curve_review_required_folders,
    )
