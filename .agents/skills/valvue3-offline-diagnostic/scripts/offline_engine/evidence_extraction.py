from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import fitz

from .config import ProjectConfig
from .extraction_overrides import load_extraction_overrides
from .extraction_review import load_codex_review_candidates
from .supplementary_materials import collect_supplementary_materials


SCHEMA_VERSION = 1
EXTRACTOR_VERSION = 7
MISSING_VALUES = {"", "未提供", "无数据", "待业主补充", "数据不足", "待复核"}
TEST_KEYS = {
    "STEP": "run_step",
    "PATTERN": "pattern_step",
    "RAMP": "run_ramp",
    "SIG-POS": "run_ramp",
    "SIG-S": "standard_signature",
    "SIG-EXT": "extended_signature",
}
METRIC_FIELDS = {
    "Average Error": "metrics.average_error",
    "T86": "metrics.t86",
    "T63": "metrics.t63",
    "Td": "metrics.td",
    "Overshoot": "metrics.overshoot",
    "Maximum Hysteresis + Dead Band": "metrics.maximum_hysteresis_dead_band",
    "Average Hysteresis + Dead Band": "metrics.average_hysteresis_dead_band",
    "Max Friction": "metrics.max_friction",
    "Min Friction": "metrics.min_friction",
    "Mean Friction": "metrics.mean_friction",
    "Response Time Open": "metrics.response_time_open",
    "Response Time Close": "metrics.response_time_close",
    "Supply Pressure": "metrics.supply_pressure",
}
RUN_RAMP_FAMILIES = ("hysteresis", "dead_band", "linearity")
RUN_RAMP_STATISTICS = ("max", "average", "min")
RUN_RAMP_FIELDS = tuple(
    f"metrics.run_ramp.{family}.{statistic}"
    for family in RUN_RAMP_FAMILIES
    for statistic in RUN_RAMP_STATISTICS
)
SOURCE_LABEL_PATTERNS = {
    "file.report_start_time": r"\bStart\s+Time\b\s*[:\-]?\s*\d",
    "file.report_end_time": r"\bEnd\s+Time\b\s*[:\-]?\s*\d",
    "positioner.option_config": r"\bOption\s+Config\b\s+\S",
    "positioner.device_mode": r"\bDevice\s+Mode\b\s+\S",
    "positioner.valve_position": r"\bValve\s+Position\b\s+[-+]?\d",
    "positioner.manual_setpoint": r"\bManual\s+Setpoint\b\s+[-+]?\d",
    "positioner.input_signal": r"\bInput\s+Signal\b\s+[-+]?\d",
    "positioner.board_temperature": r"\bBoard\s+Temp\.?\s+[-+]?\d",
    "positioner.min_temperature": r"\bMin\s+Temp\.?\s+[-+]?\d",
    "positioner.max_temperature": r"\bMax\s+Temp\.?\s+[-+]?\d",
    "runtime.strokes": r"\bStrokes\s*\(counts\)\s+\d",
    "runtime.cycles": r"\bCy(?:cl|el)es\s*\(counts\)\s+\d",
    "runtime.time_open": r"\bTime\s+Open\s*\(hours\)\s+\d",
    "runtime.time_closed": r"\bTime\s+Closed\s*\(hours\)\s+\d",
    "runtime.time_near_closed": r"\bTime\s+Near\s+Closed\s*\(hours\)\s+\d",
    "status.active_faults": (
        r"\bActive\s+Faults\b\s+\S[\s\S]*?\bHistorical\s+Faults\b"
    ),
    "status.historical_faults": (
        r"\bHistorical\s+Faults\b\s+\S[\s\S]*?\bRaw\s+Data\b"
    ),
    "status.task_execution": (
        r"\bTask\s+Execution\s+Summary\b[\s\S]*?"
        r"\b(?:Succeeded|Normal|Failed|Cancelled|Aborted|Incomplete|Not Completed)\b"
    ),
    **{
        f"metrics.run_ramp.{family}.{statistic}": (
            r"\bDead\s+Band\b[\s\S]{0,250}?[-+]?\d"
            if family == "dead_band"
            else rf"\b{family.title()}\b[\s\S]{{0,250}}?[-+]?\d"
        )
        for family in RUN_RAMP_FAMILIES
        for statistic in RUN_RAMP_STATISTICS
    },
}
SPECIFICATION_FIELDS = {
    "manufacturer": "specification.manufacturer",
    "valve_type": "specification.valve_type",
    "model_no": "specification.model_no",
    "serial_number": "specification.serial_number",
    "valve_size": "specification.valve_size",
    "pressure_class": "specification.pressure_class",
    "end_connection": "specification.end_connection",
    "design_pressure": "specification.design_pressure",
    "design_temperature": "specification.design_temperature",
    "body_material": "specification.body_material",
    "body_type": "specification.body_type",
    "rated_cv": "specification.rated_cv",
    "leakage_class": "specification.leakage_class",
    "flow_action": "specification.flow_action",
    "trim_form": "specification.trim_form",
    "trim_size": "specification.trim_size",
    "characteristic": "specification.characteristic",
    "actuator_type": "actuator.type",
    # Keep actuator.model as a compatibility alias while exposing the
    # explicit full/display split required by the Stage 2 schema below.
    "actuator_model": "actuator.model_full",
    "actuator_size": "actuator.size",
    "actuator_action": "actuator.action",
    "fail_position": "actuator.fail_position",
    "bench_range": "actuator.bench_range",
    "supply_pressure": "actuator.supply_pressure",
    "actuator_travel": "actuator.travel",
    "positioner_model": "accessory.positioner_model",
    "positioner_type": "accessory.positioner_type",
    "positioner_diagnostics_level": "accessory.diagnostics_level",
}
EXPECTED_FIELDS = (
    "identity.valve_tag",
    "identity.device_tag",
    "identity.device_id",
    "identity.serial_number",
    "identity.device_type",
    "identity.manufacturer",
    "valve.manufacturer",
    "valve.type",
    "identity.firmware_version",
    "identity.dtm_version",
    "identity.communication_protocol",
    "file.report_type",
    "file.report_start_time",
    "file.report_end_time",
    "file.report_time",
    "positioner.model",
    "positioner.type",
    "positioner.diagnostics_level",
    "positioner.air_action",
    "positioner.actuator_style",
    "positioner.supply_pressure",
    "positioner.option_config",
    "positioner.device_mode",
    "positioner.valve_position",
    "positioner.manual_setpoint",
    "positioner.input_signal",
    "positioner.board_temperature",
    "positioner.min_temperature",
    "positioner.max_temperature",
    "runtime.strokes",
    "runtime.cycles",
    "runtime.time_open",
    "runtime.time_closed",
    "runtime.time_near_closed",
    "runtime.historical_runtime",
    "status.active_faults",
    "status.historical_faults",
    "status.task_execution",
    "status.test_execution",
    "status.continuous_diagnostics",
    *METRIC_FIELDS.values(),
    *RUN_RAMP_FIELDS,
    *SPECIFICATION_FIELDS.values(),
    "actuator.manufacturer",
    "actuator.model",
    "actuator.model_display",
    "context.description",
    "context.unit",
    "context.failure_consequence",
    "context.bypass",
    "context.site_note",
)

EVIDENCE_ACCOUNTING_VERSION = 2
_FAULT_SENTINELS = {"", "reset", "none", "no error", "no errors", "重置", "无", "无故障"}
_NON_EVIDENCE_SOURCE_FIELDS = {
    "included_in_report", "rating", "algorithm_rating", "data_rating",
    "visual_rating", "final_rating", "rating_inconsistency_reason",
    "rating_evidence", "rating_override_note", "main_issue", "action",
    "issues", "actions", "basis", "curve_summary", "overall_assessment",
    "algorithm_audit_contract", "algorithm_audit_snapshot",
    "algorithm_audit_status",
    "spec_image", "accessory_image", "air_piping_image", "images",
    "report_pages", "report_field_manifest", "structured_evidence",
    "spec_page", "accessory_page", "air_piping_page",
    "spec_insertion_marker", "accessory_insertion_marker",
    "air_piping_insertion_marker",
}
_VOLATILE_EVIDENCE_FIELDS = {
    "created_at", "generated_at", "updated_at", "extracted_at",
    "prepared_at", "frozen_at", "image_path", "cache_path",
    "ocr_cache_path", "temporary_path", "temp_path", "output_path",
    "project_root", "raw_folder", "output_version", "report_version",
}


def evidence_nested_value(value: Any, *paths: tuple[str, ...]) -> Any:
    for path in paths:
        current = value
        for key in path:
            if not isinstance(current, dict) or key not in current:
                current = None
                break
            current = current[key]
        if current is not None:
            return current
    return None


def _normalize_evidence_faults(value: Any) -> list[str]:
    values = value if isinstance(value, list) else [value]
    normalized: list[str] = []
    for item in values:
        if isinstance(item, dict):
            item = item.get("name") or item.get("fault") or item.get("value")
        for part in str(item or "").replace("；", ";").split(";"):
            text = " ".join(part.split()).strip()
            if text.casefold() not in _FAULT_SENTINELS and text:
                normalized.append(text)
    return list(dict.fromkeys(normalized))


def _accounted_evidence_field(
    structured_evidence: dict[str, Any],
    field_name: str,
    fallback: Any,
    *,
    normalize_faults: bool = False,
) -> dict[str, Any]:
    fields = structured_evidence.get("material_extraction", {}).get("fields")
    field = fields.get(field_name) if isinstance(fields, dict) else None
    if isinstance(field, dict) and isinstance(field.get("selected"), dict):
        selected = field["selected"]
        value = selected.get("normalized_value")
        unit = selected.get("normalized_unit") or selected.get("raw_unit") or ""
        status = str(field.get("status") or "")
        conflict = bool(field.get("conflict"))
    else:
        if isinstance(fallback, dict) and "value" in fallback:
            value, unit = fallback.get("value"), fallback.get("unit") or ""
        else:
            value, unit = fallback, ""
        status = "present" if value not in (None, "", []) else "missing"
        conflict = False
    if normalize_faults:
        value = _normalize_evidence_faults(value)
    return {"value": value, "unit": unit, "status": status, "conflict": conflict}


def structured_evidence_accounting(
    structured_evidence: dict[str, Any],
) -> dict[str, Any]:
    """Project required review facts into the shared deterministic contract."""
    source_record = structured_evidence.get("source_record") or {}
    task_execution = evidence_nested_value(
        structured_evidence,
        ("source_record", "positioner_report_info", "status", "task_execution"),
        ("source_record", "positioner_report_info", "task_execution"),
        ("source_record", "status", "task_execution"),
    )
    active_faults = evidence_nested_value(
        structured_evidence,
        ("source_record", "positioner_report_info", "status", "active_faults"),
        ("source_record", "positioner_report_info", "active_faults"),
        ("source_record", "status", "active_faults"),
        ("source_record", "active_faults"),
    )
    historical_faults = evidence_nested_value(
        structured_evidence,
        ("source_record", "positioner_report_info", "status", "historical_faults"),
        ("source_record", "positioner_report_info", "historical_faults"),
        ("source_record", "status", "historical_faults"),
        ("source_record", "historical_faults"),
    )
    task = _accounted_evidence_field(
        structured_evidence, "status.task_execution",
        task_execution if isinstance(task_execution, list) else [],
    )
    explicit_count = evidence_nested_value(
        source_record,
        ("positioner_report_info", "task_execution_expected_count"),
        ("positioner_report_info", "status", "task_execution_expected_count"),
        ("task_execution_expected_count",),
    )
    task_rows = task["value"] if isinstance(task["value"], list) else []
    expected_count = len(task_rows) if explicit_count is None else explicit_count
    if isinstance(expected_count, bool) or not isinstance(expected_count, int) or expected_count < 0:
        raise ValueError("task_execution_expected_count must be a non-negative integer")
    if expected_count != len(task_rows):
        raise ValueError(
            "task_execution_expected_count does not match complete task_execution row count"
        )
    task["expected_count"] = expected_count
    active = _accounted_evidence_field(
        structured_evidence, "status.active_faults",
        active_faults if active_faults is not None else [], normalize_faults=True,
    )
    historical = _accounted_evidence_field(
        structured_evidence, "status.historical_faults",
        historical_faults if historical_faults is not None else [], normalize_faults=True,
    )
    metrics = source_record.get("metrics") or {}
    run_ramp = metrics.get("run_ramp") or metrics.get("Run Ramp") or {} if isinstance(metrics, dict) else {}
    family_aliases = {
        "hysteresis": ("hysteresis", "Hysteresis"),
        "dead_band": ("dead_band", "deadband", "Dead Band"),
        "linearity": ("linearity", "Linearity"),
    }
    statistic_aliases = {
        "max": ("max", "maximum", "Max", "Maximum"),
        "average": ("average", "avg", "mean", "Average", "Mean"),
        "min": ("min", "minimum", "Min", "Minimum"),
    }
    ramp: dict[str, dict[str, dict[str, Any]]] = {}
    for family, aliases in family_aliases.items():
        family_values = next((run_ramp.get(alias) for alias in aliases if alias in run_ramp), {})
        family_values = family_values if isinstance(family_values, dict) else {}
        ramp[family] = {}
        for statistic, aliases in statistic_aliases.items():
            fallback = next((family_values.get(alias) for alias in aliases if alias in family_values), None)
            ramp[family][statistic] = _accounted_evidence_field(
                structured_evidence,
                f"metrics.run_ramp.{family}.{statistic}",
                fallback,
            )
    return {
        "schema_version": EVIDENCE_ACCOUNTING_VERSION,
        "active_faults": active,
        "historical_faults": historical,
        "task_execution": task,
        "run_ramp": ramp,
    }


def stable_evidence_value(value: Any, project_root: Path) -> Any:
    if isinstance(value, dict):
        return {
            str(key): stable_evidence_value(item, project_root)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if str(key) not in _VOLATILE_EVIDENCE_FIELDS
        }
    if isinstance(value, (list, tuple)):
        return [stable_evidence_value(item, project_root) for item in value]
    if isinstance(value, str) and Path(value).is_absolute():
        path = Path(value)
        if project_root != Path("/"):
            try:
                return path.resolve().relative_to(project_root.resolve()).as_posix()
            except (OSError, ValueError):
                pass
        for anchor in ("raw", "specs", "inbound", "experience", "work"):
            if anchor in path.parts:
                return Path(*path.parts[path.parts.index(anchor):]).as_posix()
        return path.name
    return value


def project_source_files(project_root: Path, valve_tag: str) -> list[dict[str, Any]]:
    raw_dir = project_root / "raw" / valve_tag
    if not raw_dir.is_dir():
        return []
    files = []
    for path in sorted((item for item in raw_dir.rglob("*") if item.is_file()), key=lambda item: item.as_posix()):
        suffix = path.suffix.lower()
        files.append({
            "relative_path": path.relative_to(project_root).as_posix(),
            "kind": "PDF" if suffix == ".pdf" else "devdata" if suffix == ".devdata" else suffix.lstrip(".") or "file",
            "size_bytes": path.stat().st_size,
            "sha256": _sha256(path),
        })
    return files


def project_evidence_pages(
    source: dict[str, Any], *, project_root: Path | None = None,
) -> list[dict[str, Any]]:
    pages = []
    for raw_page in source.get("report_pages") or []:
        page = dict(raw_page) if isinstance(raw_page, dict) else dict(vars(raw_page))
        digest = str(page.get("source_sha256") or "").strip()
        number = page.get("page")
        if not digest or not isinstance(number, int):
            raise ValueError("formal report page requires source_sha256 and integer page")
        relative = str(page.get("source") or "").strip()
        if project_root is not None and relative and (project_root / relative).is_file():
            if _sha256(project_root / relative) != digest:
                raise ValueError("formal report page source_sha256 does not match source PDF")
        pages.append({
            "source": relative,
            "source_sha256": digest,
            "page": number,
            "physical_identity": f"{digest}:{number}",
            "roles": sorted({str(role).strip() for role in page.get("roles") or [] if str(role).strip()}),
            "match_basis": sorted({str(item).strip() for item in page.get("match_basis") or [] if str(item).strip()}),
            "image_path": str(page.get("image_path") or "").strip(),
        })
    deduplicated: dict[str, dict[str, Any]] = {}
    for page in pages:
        previous = deduplicated.get(page["physical_identity"])
        if previous is None:
            deduplicated[page["physical_identity"]] = page
            continue
        previous["roles"] = sorted(set(previous["roles"]) | set(page["roles"]))
        previous["match_basis"] = sorted(set(previous["match_basis"]) | set(page["match_basis"]))
        if not previous["image_path"]:
            previous["image_path"] = page["image_path"]
    return sorted(deduplicated.values(), key=lambda item: (item["source_sha256"], item["page"], item["roles"]))


def load_material_extraction_evidence(project_root: Path, valve_tag: str) -> dict[str, Any]:
    path = project_root / "work/evidence-extraction/valves" / f"{valve_tag}.json"
    manifest_path = path.parent.parent / "run-manifest.json"
    if not path.is_file():
        raise ValueError(f"material extraction evidence is missing for {valve_tag}")
    if not manifest_path.is_file():
        raise ValueError("material extraction run manifest is missing")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("material extraction evidence JSON is invalid") from exc
    if manifest.get("extractor_version") != EXTRACTOR_VERSION:
        raise ValueError(f"material extraction extractor_version {EXTRACTOR_VERSION} is required")
    inputs = manifest.get("inputs")
    if not isinstance(inputs, list):
        raise ValueError("material extraction run manifest inputs are invalid")
    for item in inputs:
        if not isinstance(item, dict):
            raise ValueError("material extraction run manifest input is invalid")
        relative = Path(str(item.get("path") or ""))
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise ValueError("material extraction input path is unsafe")
        source_path = project_root / relative
        if not source_path.is_file() or source_path.stat().st_size != item.get("size_bytes") or _sha256(source_path) != item.get("sha256"):
            raise ValueError(f"material extraction run input is stale: {relative.as_posix()}")
    if str(payload.get("valve_tag") or "").strip() != valve_tag:
        raise ValueError(f"material extraction valve tag mismatch: {path}")

    def validate_sources(value: Any) -> None:
        if isinstance(value, dict):
            source = value.get("source")
            if isinstance(source, dict):
                relative = str(source.get("path") or "").strip()
                digest = str(source.get("sha256") or "").strip()
                if relative and digest:
                    source_path = project_root / relative
                    requires_file = Path(relative).parts[:1] in {("raw",), ("specs",)}
                    if requires_file and not source_path.is_file():
                        raise ValueError(f"material extraction source is missing: {relative}")
                    if source_path.is_file() and _sha256(source_path) != digest:
                        raise ValueError(f"material extraction source hash is stale: {relative}")
            for item in value.values():
                validate_sources(item)
        elif isinstance(value, list):
            for item in value:
                validate_sources(item)
    validate_sources(payload)
    return payload


def _project_source_record(
    source: dict[str, Any], material: dict[str, Any],
) -> dict[str, Any]:
    source_record = json.loads(json.dumps({
        key: value
        for key, value in source.items()
        if key not in _NON_EVIDENCE_SOURCE_FIELDS
    }, ensure_ascii=False))
    fields = material.get("fields") if isinstance(material, dict) else None
    for field_name, field_key in (
        ("status.active_faults", "active_faults"),
        ("status.historical_faults", "historical_faults"),
    ):
        field = fields.get(field_name) if isinstance(fields, dict) else None
        selected = field.get("selected") if isinstance(field, dict) else None
        raw = (
            selected.get("normalized_value")
            if isinstance(selected, dict) and "normalized_value" in selected
            else evidence_nested_value(
                source_record,
                (field_key,),
                ("positioner_report_info", field_key),
                ("positioner_report_info", "status", field_key),
                ("status", field_key),
            )
        )
        normalized = _normalize_evidence_faults(raw)
        source_record[field_key] = normalized
        positioner = source_record.get("positioner_report_info")
        if isinstance(positioner, dict):
            if field_key in positioner:
                positioner[field_key] = normalized
            status = positioner.get("status")
            if isinstance(status, dict) and field_key in status:
                status[field_key] = normalized
        status = source_record.get("status")
        if isinstance(status, dict) and field_key in status:
            status[field_key] = normalized
    return source_record


def validate_project_structured_evidence(
    source: dict[str, Any], *, project_root: Path,
) -> dict[str, Any]:
    """Validate and return the formal evidence already bound into a summary."""
    structured = source.get("structured_evidence")
    if not isinstance(structured, dict) or not structured:
        raise ValueError(
            "analysis summary requires structured_evidence; run prepare-formal-evidence"
        )
    valve_tag = str(source.get("folder") or "").strip()
    if not valve_tag:
        raise ValueError("project target is missing canonical folder tag")
    try:
        current_material = load_material_extraction_evidence(project_root, valve_tag)
    except ValueError as exc:
        raise ValueError(
            f"analysis summary evidence is stale; run prepare-formal-evidence: {exc}"
        ) from exc
    try:
        current_source_record = stable_evidence_value(
            _project_source_record(source, current_material), project_root
        )
        current_source_files = stable_evidence_value(
            project_source_files(project_root, valve_tag), project_root
        )
        current_evidence_pages = stable_evidence_value(
            [
                {key: value for key, value in page.items() if key != "image_path"}
                for page in project_evidence_pages(source, project_root=project_root)
            ],
            project_root,
        )
        current_material = stable_evidence_value(current_material, project_root)
    except ValueError as exc:
        raise ValueError(
            f"analysis summary evidence is stale; run prepare-formal-evidence: {exc}"
        ) from exc
    if (
        structured.get("schema_version") != 1
        or structured.get("source_record") != current_source_record
        or structured.get("source_files") != current_source_files
        or structured.get("evidence_pages") != current_evidence_pages
        or structured.get("material_extraction") != current_material
    ):
        raise ValueError(
            "analysis summary structured_evidence is stale or inconsistent; "
            "run prepare-formal-evidence"
        )
    return structured


def build_project_structured_evidence(
    source: dict[str, Any], *, project_root: Path,
) -> dict[str, Any]:
    """Build the stable diagnosis evidence projection from current project inputs."""
    valve_tag = str(source.get("folder") or "").strip()
    if not valve_tag:
        raise ValueError("project target is missing canonical folder tag")
    pages = project_evidence_pages(source, project_root=project_root)
    material = load_material_extraction_evidence(project_root, valve_tag)
    source_record = _project_source_record(source, material)
    stable_pages = [{key: value for key, value in page.items() if key != "image_path"} for page in pages]
    return {
        "schema_version": 1,
        "source_record": stable_evidence_value(source_record, project_root),
        "source_files": stable_evidence_value(project_source_files(project_root, valve_tag), project_root),
        "evidence_pages": stable_evidence_value(stable_pages, project_root),
        "material_extraction": stable_evidence_value(material, project_root),
    }


@dataclass(frozen=True)
class EvidenceExtraction:
    status: str
    index_path: Path
    markdown_path: Path
    payload: dict[str, Any]


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _is_present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() not in MISSING_VALUES
    if isinstance(value, (list, tuple, dict, set)):
        return bool(value)
    return True


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    text = str(value or "").strip()
    if not re.fullmatch(r"[-+]?\d+(?:\.\d+)?", text):
        return None
    number = float(text)
    return int(number) if number.is_integer() else number


def _line_containing(text: str, value: Any) -> str:
    needle = str(value or "").strip()
    if not needle:
        return ""
    for line in text.splitlines():
        if needle.casefold() in line.casefold():
            return re.sub(r"\s+", " ", line).strip()[:500]
    return ""


def _ocr_document(config: ProjectConfig, path: Path) -> dict[str, Any]:
    from .document_ocr_cache import load_cached_pdf_document

    return load_cached_pdf_document(config.project_dir, path) or {}


def _source(
    config: ProjectConfig,
    path: Path | None,
    *,
    value: Any,
    page: int | None = None,
    record: str = "",
    method: str = "",
) -> tuple[dict[str, Any], float | None]:
    if path is None:
        return {
            "path": "",
            "sha256": "",
            "page": page,
            "record": record,
            "method": method,
            "raw_text": "",
            "degraded": False,
        }, 1.0 if method == "project_directory" else None
    relative = path.relative_to(config.project_dir).as_posix()
    source_hash = _sha256(path) if path.is_file() else ""
    confidence: float | None = 1.0 if method == "structured_data" else None
    preserve_review_provenance = method == "codex_review"
    raw_text = ""
    page_degraded = False
    if path.suffix.lower() == ".pdf":
        document = _ocr_document(config, path)
        pages = document.get("pages") if isinstance(document, dict) else []
        if isinstance(pages, list):
            selected_page: dict[str, Any] | None = None
            if page is not None:
                selected_page = next(
                    (
                        candidate
                        for candidate in pages
                        if isinstance(candidate, dict)
                        and candidate.get("page_no") == page
                    ),
                    None,
                )
            if selected_page is None and page is None:
                selected_page = next(
                    (
                        candidate
                        for candidate in pages
                        if isinstance(candidate, dict)
                        and _line_containing(
                            str(candidate.get("parser_text") or candidate.get("text") or ""),
                            value,
                        )
                    ),
                    None,
                )
            if selected_page is None and page is None and pages:
                selected_page = pages[0] if isinstance(pages[0], dict) else None
            if selected_page is not None:
                page = int(selected_page.get("page_no") or 0) or None
                if not preserve_review_provenance:
                    method = str(
                        selected_page.get("method") or method or "pdf_text"
                    )
                raw_text = _line_containing(
                    str(
                        selected_page.get("parser_text")
                        or selected_page.get("text")
                        or ""
                    ),
                    value,
                )
                raw_confidence = selected_page.get("confidence")
                if not preserve_review_provenance:
                    if isinstance(raw_confidence, (int, float)) and not isinstance(
                        raw_confidence, bool
                    ):
                        confidence = float(raw_confidence)
                    elif method == "native_text":
                        confidence = 1.0
                ocr_result = selected_page.get("ocr_result")
                page_degraded = bool(
                    selected_page.get("degraded")
                    or (
                        ocr_result.get("degraded")
                        if isinstance(ocr_result, dict)
                        else False
                    )
                )
            elif page is not None and not preserve_review_provenance:
                # An explicitly bound physical page must never inherit another
                # cached page's location, OCR method, confidence, or degradation.
                try:
                    with fitz.open(path) as native_document:
                        if 1 <= page <= len(native_document):
                            native_text = native_document[page - 1].get_text("text")
                            if native_text.strip():
                                raw_text = _line_containing(native_text, value)
                                method = "native_text"
                                confidence = 1.0
                except (OSError, RuntimeError, ValueError):
                    pass
    return {
        "path": relative,
        "sha256": source_hash,
        "page": page,
        "record": record,
        "method": method or "file_content",
        "raw_text": raw_text,
        "degraded": page_degraded,
    }, confidence


def _candidate(
    config: ProjectConfig,
    path: Path | None,
    raw_value: Any,
    *,
    normalized_value: Any | None = None,
    raw_unit: str = "",
    normalized_unit: str = "",
    page: int | None = None,
    record: str = "",
    method: str = "",
    source_role: str = "",
    priority: int = 0,
    candidate_status: str = "extracted",
    reason: str = "",
    confidence_override: float | None = None,
    degraded: bool = False,
    review_reason: str = "",
    normalization_rule: str = "",
) -> dict[str, Any]:
    if normalized_value is None:
        normalized_value = raw_value
    source, confidence = _source(
        config,
        path,
        value=raw_value,
        page=page,
        record=record,
        method=method,
    )
    if confidence_override is not None:
        confidence = confidence_override
    source["degraded"] = bool(source.get("degraded") or degraded)
    if candidate_status == "extracted" and source["method"] == "ocr":
        if source["degraded"]:
            candidate_status = "review_required"
            review_reason = "ocr_degraded"
        elif confidence is None:
            candidate_status = "review_required"
            review_reason = "ocr_confidence_unavailable"
        elif confidence < 0.85:
            candidate_status = "review_required"
            review_reason = "low_ocr_confidence"
    candidate = {
        "raw_value": raw_value,
        "normalized_value": normalized_value,
        "raw_unit": raw_unit,
        "normalized_unit": normalized_unit,
        "source": source,
        "confidence": confidence,
        "status": candidate_status,
        "source_role": source_role,
        "priority": priority,
        "reason": reason,
        "review_reason": review_reason,
    }
    if normalization_rule:
        candidate["normalization_rule"] = normalization_rule
    return candidate


def _field(candidate: dict[str, Any] | None) -> dict[str, Any]:
    if candidate is None:
        return {
            "status": "missing",
            "selected": None,
            "candidates": [],
            "conflict": False,
        }
    candidate_status = str(candidate.get("status") or "extracted")
    return {
        "status": candidate_status,
        "selected": candidate,
        "candidates": [candidate],
        "conflict": False,
    }


def _field_from_candidates(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    if not candidates:
        return _field(None)
    ordered = sorted(
        candidates,
        key=lambda item: (
            -int(item.get("priority") or 0),
            str(item.get("source", {}).get("path") or ""),
        ),
    )
    distinct_values = {
        json.dumps(item.get("normalized_value"), ensure_ascii=False, sort_keys=True)
        for item in ordered
    }
    conflict = len(distinct_values) > 1
    selected_status = str(ordered[0].get("status") or "extracted")
    if selected_status in {"manual_override", "codex_reviewed"}:
        return {
            "status": selected_status,
            "selected": ordered[0],
            "candidates": ordered,
            "conflict": False,
            "resolved_conflict": conflict,
        }
    if selected_status == "review_required":
        return {
            "status": "review_required",
            "selected": ordered[0],
            "candidates": ordered,
            "conflict": conflict,
        }
    if selected_status == "source_present_parser_missing" and not conflict:
        return {
            "status": selected_status,
            "selected": ordered[0],
            "candidates": ordered,
            "conflict": False,
        }
    return {
        "status": "conflict" if conflict else "extracted",
        "selected": ordered[0],
        "candidates": ordered,
        "conflict": conflict,
    }


def _raw_source(config: ProjectConfig, valve_tag: str, name: str) -> Path | None:
    path = config.raw_dir / valve_tag / name
    return path if name and path.is_file() else None


def _metric_source(config: ProjectConfig, valve: Any) -> Path | None:
    for test in valve.tests:
        source = _raw_source(config, valve.folder, str(test.source or ""))
        if source is not None:
            return source
    return None


def _info_source(config: ProjectConfig, valve: Any, info: dict[str, Any]) -> Path | None:
    return _raw_source(config, valve.folder, str(info.get("source") or ""))


def _identity_candidates(
    config: ProjectConfig,
    valve_tag: str,
) -> dict[str, list[dict[str, Any]]]:
    from . import generator

    fields: dict[str, list[dict[str, Any]]] = {
        "identity.device_tag": [],
        "identity.device_id": [],
        "identity.serial_number": [],
        "identity.firmware_version": [],
    }
    raw_dir = config.raw_dir / valve_tag
    if not raw_dir.is_dir():
        return fields

    def add(
        field_name: str,
        value: Any,
        path: Path,
        *,
        source_role: str,
        priority: int,
    ) -> None:
        if not _is_present(value):
            return
        fields[field_name].append(
            _candidate(
                config,
                path,
                value,
                normalized_value=str(value).strip(),
                method=(
                    "structured_data" if path.suffix.lower() == ".devdata" else ""
                ),
                source_role=source_role,
                priority=priority,
            )
        )

    for path in sorted(
        (item for item in raw_dir.iterdir() if item.is_file()),
        key=lambda item: item.name,
    ):
        try:
            if path.suffix.lower() == ".devdata":
                _tests, info = generator.parse_devdata(path)
                add(
                    "identity.device_tag",
                    info.get("Tag"),
                    path,
                    source_role="devdata",
                    priority=200,
                )
                add(
                    "identity.device_id",
                    info.get("DeviceId"),
                    path,
                    source_role="devdata",
                    priority=200,
                )
                add(
                    "identity.serial_number",
                    info.get("DeviceSerialNumber"),
                    path,
                    source_role="devdata",
                    priority=200,
                )
                add(
                    "identity.firmware_version",
                    info.get("FirmwareInfomation"),
                    path,
                    source_role="devdata",
                    priority=200,
                )
            elif path.suffix.lower() == ".pdf":
                info = generator.parse_active_report_pdf(path)
                report_type = str(info.get("report_type") or "")
                if info.get("is_dtm_report") or report_type == "valvxpert_execution":
                    source_role = (
                        "dtm_active" if info.get("is_dtm_report") else "valvxpert"
                    )
                    priority = 400
                elif generator.is_diagnostic_pdf(path, generator.run_pdftotext(path)):
                    _tests, info = generator.parse_sequence_pdf(path)
                    source_role = "sequencer"
                    priority = 300
                else:
                    continue
                add(
                    "identity.device_tag",
                    info.get("device_tag"),
                    path,
                    source_role=source_role,
                    priority=priority,
                )
                add(
                    "identity.device_id",
                    info.get("device_id"),
                    path,
                    source_role=source_role,
                    priority=priority,
                )
                add(
                    "identity.serial_number",
                    info.get("serial_number"),
                    path,
                    source_role=source_role,
                    priority=priority,
                )
                add(
                    "identity.firmware_version",
                    info.get("firmware"),
                    path,
                    source_role=source_role,
                    priority=priority,
                )
        except Exception:
            continue
    return fields


def _runtime_candidate(
    config: ProjectConfig,
    source: Path | None,
    value: Any,
    *,
    hours: bool = False,
) -> dict[str, Any] | None:
    number = _number(value)
    if number is None:
        return None
    if hours:
        return _candidate(
            config,
            source,
            value,
            normalized_value=float(number) * 3600,
            raw_unit="h",
            normalized_unit="s",
            normalization_rule="runtime_hours_to_seconds",
        )
    return _candidate(config, source, value, normalized_value=number)


def _pressure_candidate(
    config: ProjectConfig,
    source: Path | None,
    value: Any,
) -> dict[str, Any] | None:
    match = re.fullmatch(
        r"\s*([-+]?\d+(?:\.\d+)?)\s*(psi|kPa|bar|MPa)\s*g?\s*",
        str(value or ""),
        re.I,
    )
    if not match:
        return None
    number = float(match.group(1))
    raw_unit = {
        "psi": "psi",
        "kpa": "kPa",
        "bar": "bar",
        "mpa": "MPa",
    }[match.group(2).lower()]
    multiplier = {
        "psi": 6.894757293,
        "kPa": 1.0,
        "bar": 100.0,
        "MPa": 1000.0,
    }[raw_unit]
    return _candidate(
        config,
        source,
        value,
        normalized_value=round(number * multiplier, 3),
        raw_unit=raw_unit,
        normalized_unit="kPa",
        normalization_rule="pressure_to_kpa",
    )


def _measurement_candidate(
    config: ProjectConfig,
    source: Path | None,
    value: Any,
    *,
    unit: str,
) -> dict[str, Any] | None:
    match = re.fullmatch(
        rf"\s*([-+]?\d+(?:\.\d+)?)\s*{re.escape(unit)}\s*",
        str(value or ""),
        re.I,
    )
    if not match:
        return None
    number = float(match.group(1))
    return _candidate(
        config,
        source,
        value,
        normalized_value=number,
        raw_unit=unit,
        normalized_unit=unit,
    )


_LEAKAGE_ROMAN = {
    "1": "I",
    "2": "II",
    "3": "III",
    "4": "IV",
    "5": "V",
    "6": "VI",
}


def _normalize_leakage_value(value: Any) -> tuple[str, str]:
    raw = str(value or "").strip()
    if not raw:
        return "", ""
    # IEC leakage expressions are sometimes emitted as a percentage rather
    # than ``Class V``.  Do this before numeric-token matching: the ``1`` in
    # ``0.001%/Max.Diff.Pr.`` is not a leakage class.
    if re.search(r"0\s*\.\s*001\s*%\s*/\s*Max\.?\s*\.??\s*Diff\.?\s*Pr\.?", raw, re.I):
        return "V", "leakage_percent_to_class_v"
    match = re.search(
        r"(?:CLASS|等级)\s*([IVX]+|[1-6])\b|(?<![\d.])([IVX]+)(?![A-Z])|(?<![\d.])([1-6])(?![\d.])",
        raw,
        re.I,
    )
    if not match:
        return raw, ""
    token = next(group for group in match.groups() if group is not None).upper()
    return _LEAKAGE_ROMAN.get(token, token), "leakage_class_to_roman"


def _leakage_candidate(
    config: ProjectConfig,
    source: Path | None,
    value: Any,
    *,
    page: int | None = None,
) -> dict[str, Any] | None:
    """Preserve the source leakage expression and expose a Roman class."""
    raw = str(value or "").strip()
    if not raw:
        return None
    normalized, normalization_rule = _normalize_leakage_value(value)
    return _candidate(
        config,
        source,
        value,
        normalized_value=normalized,
        page=page,
        normalization_rule=normalization_rule,
    )


def _actuator_model_display(value: Any) -> str:
    """Return the report model prefix before a ``-21129`` suffix."""
    text = str(value or "").strip()
    if not text:
        return ""
    match = re.match(r"(.+?)\s*-\s*21129(?:\b|$)", text, re.I)
    return match.group(1).strip() if match else text


def _actuator_model_size_display(model: Any, size: Any) -> str:
    """Return the report-facing actuator model, including explicit spec size."""
    display_model = _actuator_model_display(model)
    display_size = str(size or "").strip()
    if not display_model or not display_size:
        return display_model
    if re.search(
        rf"(?:^|\s*/\s*){re.escape(display_size)}(?:$|\s*/\s*)",
        display_model,
        re.I,
    ):
        return display_model
    return f"{display_model} / {display_size}"


def _derive_actuator_display_from_spec(
    fields: dict[str, dict[str, Any]],
) -> None:
    """Apply the report display rule without inventing an actuator source field."""
    trusted_statuses = {"extracted", "manual_override", "codex_reviewed"}
    if fields["actuator.model_full"]["status"] != "missing":
        return
    if fields["actuator.model"]["status"] != "missing":
        return
    if fields["actuator.model_display"]["status"] in trusted_statuses:
        return
    specification = fields["specification.model_no"]
    if specification["status"] not in trusted_statuses:
        return
    selected = specification.get("selected")
    if not isinstance(selected, dict):
        return
    full_model = str(selected.get("normalized_value") or "").strip()
    if not re.search(r"\s*-\s*21129(?:\b|$)", full_model, re.I):
        return
    candidate = {
        **selected,
        "normalized_value": _actuator_model_display(full_model),
        "normalized_unit": "",
        "source": dict(selected.get("source") or {}),
        "status": "extracted",
        "source_role": "derived_specification_model",
        "reason": "report display rule derived from specification.model_no",
        "review_reason": "",
        "normalization_rule": "actuator_model_display_before_-21129",
    }
    fields["actuator.model_display"] = _field(candidate)


def _source_text(config: ProjectConfig, source: Path | None) -> str:
    if source is None or not source.is_file():
        return ""
    cached = _ocr_document(config, source)
    pages = cached.get("pages") if isinstance(cached, dict) else None
    if isinstance(pages, list):
        text = "\n".join(
            str(page.get("parser_text") or page.get("text") or "")
            for page in pages
            if isinstance(page, dict)
        )
        if text.strip():
            return text
    if source.suffix.lower() != ".pdf":
        return ""
    try:
        with fitz.open(source) as document:
            return "\n".join(page.get_text("text") for page in document)
    except (OSError, RuntimeError, ValueError):
        return ""


def _specification_source_values(
    config: ProjectConfig, source: Path | None, page_no: int | None,
) -> dict[str, str]:
    """Read only the matched physical spec page, including cached key/value data.

    Document-level key/value rows lack page provenance, so they are safe only
    for a single-page source.  Never borrow another valve's specification page.
    """
    from .specifications import specification_source_values

    if source is None or not source.is_file() or page_no is None:
        return {}
    cached = _ocr_document(config, source)
    pages = cached.get("pages") if isinstance(cached, dict) else None
    values: dict[str, str] = {}
    if isinstance(pages, list):
        for page in pages:
            if not isinstance(page, dict) or page.get("page_no") != page_no:
                continue
            values.update(specification_source_values(
                str(page.get("parser_text") or page.get("text") or "")
            ))
            containers = [page.get("structured_fields"), page]
            if len(pages) == 1 and cached.get("page_count", 1) == 1:
                containers.append(cached.get("structured_fields"))
            for container in containers:
                key_values = container.get("key_values") if isinstance(container, dict) else None
                if not isinstance(key_values, dict):
                    continue
                for label, value in key_values.items():
                    if value is None:
                        continue
                    for key, raw in specification_source_values(f"{label}: {value}").items():
                        values.setdefault(key, raw)
            if values:
                return values
    if source.suffix.lower() == ".pdf":
        try:
            with fitz.open(source) as document:
                if 1 <= page_no <= len(document):
                    values.update(specification_source_values(document[page_no - 1].get_text("text")))
        except (OSError, RuntimeError, ValueError):
            pass
    return values


def _source_label_page(
    config: ProjectConfig,
    source: Path | None,
    pattern: str,
) -> int | None:
    if source is None or not source.is_file():
        return None
    cached = _ocr_document(config, source)
    pages = cached.get("pages") if isinstance(cached, dict) else None
    if isinstance(pages, list):
        for page in pages:
            if not isinstance(page, dict):
                continue
            text = str(page.get("parser_text") or page.get("text") or "")
            if re.search(pattern, text, re.I):
                return int(page.get("page_no") or 0) or None
    if source.suffix.lower() != ".pdf":
        return None
    try:
        with fitz.open(source) as document:
            for page_number, page in enumerate(document, start=1):
                if re.search(pattern, page.get_text("text"), re.I):
                    return page_number
    except (OSError, RuntimeError, ValueError):
        return None
    return None


def _fault_section_is_explicitly_empty(
    text: str,
    start_label: str,
    end_label: str,
) -> bool:
    match = re.search(
        rf"\b{start_label}\b(.*?)\b{end_label}\b",
        re.sub(r"\s+", " ", text),
        re.I,
    )
    if not match:
        return False
    values = [
        value.strip().casefold()
        for value in re.split(r"[;,]", match.group(1))
        if value.strip()
    ]
    return bool(values) and all(
        value in {"reset", "none", "no error", "no errors"}
        for value in values
    )


def _run_ramp_value(
    metrics: dict[str, Any],
    info: dict[str, Any],
    family: str,
    statistic: str,
) -> Any:
    family_aliases = {
        "hysteresis": ("hysteresis", "Hysteresis"),
        "dead_band": ("dead_band", "deadband", "Dead Band"),
        "linearity": ("linearity", "Linearity"),
    }[family]
    statistic_aliases = {
        "max": ("max", "maximum", "Max", "Maximum"),
        "average": ("average", "avg", "mean", "Average", "Mean"),
        "min": ("min", "minimum", "Min", "Minimum"),
    }[statistic]
    containers = [
        metrics.get("run_ramp"),
        metrics.get("Run Ramp"),
        info.get("run_ramp"),
    ]
    for container in containers:
        if not isinstance(container, dict):
            continue
        family_values = next(
            (container.get(key) for key in family_aliases if key in container),
            None,
        )
        if not isinstance(family_values, dict):
            continue
        for key in statistic_aliases:
            if key in family_values:
                return family_values[key]
    for family_label in family_aliases:
        for statistic_label in statistic_aliases:
            for label in (
                f"{statistic_label} {family_label}",
                f"{family_label} {statistic_label}",
            ):
                if label in metrics:
                    return metrics[label]
    return None


def _metric_unit(metric_name: str, metrics: dict[str, Any]) -> str:
    if metric_name in {
        "Average Error",
        "Overshoot",
        "Maximum Hysteresis + Dead Band",
        "Average Hysteresis + Dead Band",
    }:
        return "%"
    if metric_name in {"T86", "T63", "Td", "Response Time Open", "Response Time Close"}:
        return "s"
    if metric_name in {"Max Friction", "Min Friction", "Mean Friction"}:
        return str(metrics.get("Friction Unit") or "")
    if metric_name == "Supply Pressure":
        return "psi"
    return ""


def _ordered_tests(valve: Any) -> list[tuple[Any, int, bool]]:
    totals: dict[str, int] = {}
    for test in valve.all_tests:
        key = str(test.test_type).upper()
        totals[key] = totals.get(key, 0) + 1
    seen: dict[str, int] = {}
    ordered: list[tuple[Any, int, bool]] = []
    for test in valve.all_tests:
        key = str(test.test_type).upper()
        occurrence = seen.get(key, 0) + 1
        seen[key] = occurrence
        ordered.append((test, occurrence, occurrence == totals[key]))
    return ordered


def _curve_records(config: ProjectConfig, valve: Any) -> list[dict[str, Any]]:
    curves: list[dict[str, Any]] = []
    inventory_sources: set[str] = set()
    for item in getattr(valve, "curve_inventory", ()):
        if not isinstance(item, dict):
            continue
        source_name = str(item.get("source") or "")
        source = _raw_source(config, valve.folder, source_name)
        if source is None:
            continue
        inventory_sources.add(source_name)
        occurrence = int(item.get("occurrence") or 1)
        test_key = str(item.get("test_key") or "other")
        provenance, _confidence = _source(
            config,
            source,
            value=str(item.get("title") or item.get("test_type") or test_key),
            page=int(item.get("page") or 0) or None,
            record=f"curve:{test_key}:{occurrence}",
            method="native_text",
        )
        curves.append(
            {
                "test_key": test_key,
                "test_type": str(item.get("test_type") or ""),
                "occurrence": occurrence,
                "is_current": bool(item.get("is_current")),
                "status": str(item.get("status") or "completed"),
                "source": provenance,
            }
        )
    for test, occurrence, is_current in _ordered_tests(valve):
        test_key = TEST_KEYS.get(str(test.test_type).upper(), "other")
        source = _raw_source(config, valve.folder, str(test.source or ""))
        if (
            source is not None
            and source.suffix.lower() == ".pdf"
            and source.name in inventory_sources
        ):
            continue
        provenance, _confidence = _source(
            config,
            source,
            value=test.test_type,
            record=f"test:{occurrence}",
            method="structured_data" if source and source.suffix.lower() == ".devdata" else "",
        )
        curves.append(
            {
                "test_key": test_key,
                "test_type": test.test_type,
                "occurrence": occurrence,
                "is_current": is_current,
                "status": "completed",
                "source": provenance,
            }
        )
    return curves


def _test_records(config: ProjectConfig, valve: Any) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for test, occurrence, is_current in _ordered_tests(valve):
        source = _raw_source(config, valve.folder, str(test.source or ""))
        provenance, _confidence = _source(
            config,
            source,
            value=test.test_type,
            record=f"test:{occurrence}",
            method="structured_data" if source and source.suffix.lower() == ".devdata" else "",
        )
        records.append(
            {
                "test_type": test.test_type,
                "occurrence": occurrence,
                "is_current": is_current,
                "started": test.started,
                "device_id": test.device_id,
                "device_tag": test.device_tag,
                "attributes": test.attrs,
                "steps": test.steps,
                "supply_pressures": test.supply_pressures,
                "source": provenance,
            }
        )
    return records


def _report_page_source(config: ProjectConfig, source: str) -> Path | None:
    for candidate in (
        config.specs_dir / source,
        config.project_dir / source,
    ):
        try:
            resolved = candidate.resolve()
            resolved.relative_to(config.project_dir)
        except ValueError:
            continue
        if resolved.is_file():
            return resolved
    return None


def _normalized_report_pages(
    config: ProjectConfig,
    valve: Any,
) -> list[dict[str, Any]]:
    from . import generator

    allowed_roles = ("specification", "air_piping", "accessory")
    role_order = {role: index for index, role in enumerate(allowed_roles)}
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for page in generator.report_pages_for_valve(valve):
        source = str(getattr(page, "source", "") or "")
        page_number = int(getattr(page, "page", 0) or 0)
        if not source or page_number <= 0:
            continue
        source_sha256 = str(getattr(page, "source_sha256", "") or "")
        source_path = _report_page_source(config, source)
        if not source_sha256 and source_path is not None:
            source_sha256 = _sha256(source_path)
        roles = [
            role
            for role in allowed_roles
            if role in set(getattr(page, "roles", ()) or ())
        ]
        if not roles:
            continue
        physical_identity = f"{source_sha256 or source}:{page_number}"
        if physical_identity not in merged:
            merged[physical_identity] = {
                "source": source,
                "source_sha256": source_sha256,
                "page": page_number,
                "roles": roles,
                "image_path": str(getattr(page, "image_path", "") or ""),
                "match_basis": list(
                    dict.fromkeys(getattr(page, "match_basis", ()) or ())
                ),
                "physical_identity": physical_identity,
            }
            order.append(physical_identity)
            continue
        current = merged[physical_identity]
        current["roles"] = sorted(
            set(current["roles"]) | set(roles),
            key=lambda role: role_order[role],
        )
        current["match_basis"] = list(
            dict.fromkeys(
                [
                    *current["match_basis"],
                    *(getattr(page, "match_basis", ()) or ()),
                ]
            )
        )
        current["image_path"] = current["image_path"] or str(
            getattr(page, "image_path", "") or ""
        )
    return [merged[identity] for identity in order]


def _report_page_summary(pages: list[dict[str, Any]]) -> dict[str, Any]:
    role_names = ("specification", "air_piping", "accessory")
    return {
        "physical_page_count": len(pages),
        "roles": {
            role: sum(role in page.get("roles", ()) for page in pages)
            for role in role_names
        },
        "pages": [
            {
                "physical_identity": str(page.get("physical_identity") or ""),
                "roles": list(page.get("roles") or ()),
            }
            for page in pages
        ],
    }


def _extract_valve(config: ProjectConfig, valve: Any) -> dict[str, Any]:
    fields: dict[str, dict[str, Any]] = {
        key: _field(None) for key in EXPECTED_FIELDS
    }
    raw_dir = config.raw_dir / valve.folder
    fields["identity.valve_tag"] = _field(
        _candidate(
            config,
            None,
            valve.folder,
            method="project_directory",
            record=raw_dir.relative_to(config.project_dir).as_posix(),
        )
    )

    for key, candidates in _identity_candidates(config, valve.folder).items():
        fields[key] = _field_from_candidates(candidates)

    # Keep the two report projections independent.  A positioner report may
    # contain current device facts while DTM/Active contains valve/actuator
    # facts; selecting one whole dictionary silently loses the other side.
    info_sources: list[tuple[str, dict[str, Any], int]] = []
    if isinstance(valve.positioner_report_info, dict) and valve.positioner_report_info:
        info_sources.append(("positioner_report", valve.positioner_report_info, 450))
    for report_info in getattr(valve, "positioner_report_infos", ()) or ():
        if not isinstance(report_info, dict) or not report_info:
            continue
        report_type = str(report_info.get("report_type") or "").casefold()
        source_role = (
            "sequencer"
            if "sequencer" in report_type
            else "valvxpert"
            if "valvxpert" in report_type
            else "positioner_report"
        )
        info_sources.append((source_role, report_info, 425))
    if isinstance(valve.dtm_info, dict) and valve.dtm_info:
        info_sources.append(("dtm_active", valve.dtm_info, 400))
    info = info_sources[0][1] if info_sources else {}
    info_source = _info_source(config, valve, info)

    def info_lookup(*keys: str) -> tuple[Any, Path | None]:
        """Find a fact per field, retaining the source projection that owns it."""
        for _role, source_info, _priority in info_sources:
            for key in keys:
                if key in source_info and _is_present(source_info.get(key)):
                    return source_info.get(key), _info_source(config, valve, source_info)
        return None, None
    info_values = {
        "identity.device_tag": valve.device_tag,
        "identity.device_id": valve.device_id,
        "identity.serial_number": valve.serial_number,
        "identity.device_type": info.get("device_type") or valve.positioner_type,
        "identity.manufacturer": info.get("manufacturer"),
        "valve.manufacturer": info.get("valve_manufacturer"),
        "valve.type": info.get("dtm_valve_type"),
        "identity.firmware_version": info.get("firmware") or valve.firmware,
        "identity.dtm_version": info.get("diagnostic_software_version")
        or info.get("dtm_revision"),
        "file.report_type": info.get("report_type")
        or info.get("dtm_report_type"),
        "file.report_start_time": info.get("start_time"),
        "file.report_end_time": info.get("end_time"),
        "file.report_time": info.get("report_time") or info.get("date_time"),
        # A DTM device family/revision is current-device identity, not the
        # complete positioner order model printed by a matched specification.
        # `positioner.model` is populated from specification/accessory or an
        # explicit nameplate/manual fact later in this extraction.
        "positioner.model": None,
        "positioner.type": valve.positioner_type or info.get("device_type"),
        "positioner.diagnostics_level": info.get("diagnostics_level"),
        "positioner.air_action": info.get("air_action"),
        "positioner.actuator_style": info.get("actuator_style")
        or info.get("actuator_type"),
        "positioner.supply_pressure": info.get("supply_pressure"),
        "positioner.option_config": info.get("option_config"),
        "positioner.device_mode": info.get("device_mode"),
        "positioner.valve_position": info.get("valve_position"),
        "positioner.manual_setpoint": info.get("manual_setpoint"),
        "positioner.input_signal": info.get("input_signal"),
        "positioner.board_temperature": info.get("board_temperature"),
        "positioner.min_temperature": info.get("min_temperature"),
        "positioner.max_temperature": info.get("max_temperature"),
        "status.test_execution": valve.test_execution_issues,
    }
    def add_info_candidate(
        key: str,
        value: Any,
        source: Path | None,
        *,
        source_role: str,
        priority: int,
    ) -> None:
        if not _is_present(value):
            return
        candidate = (
            _pressure_candidate(config, source, value)
            if key == "positioner.supply_pressure"
            else _measurement_candidate(config, source, value, unit="%")
            if key in {"positioner.valve_position", "positioner.manual_setpoint"}
            else _measurement_candidate(config, source, value, unit="mA")
            if key == "positioner.input_signal"
            else _measurement_candidate(
                config, source, value, unit="degC"
            )
            if key in {
                "positioner.board_temperature",
                "positioner.min_temperature",
                "positioner.max_temperature",
            }
            else _candidate(
                config,
                source,
                value,
                method=(
                    "structured_data"
                    if source is not None and source.suffix.lower() == ".devdata"
                    else ""
                ),
                source_role=source_role,
                priority=priority,
            )
        )
        if candidate is None:
            candidate = _candidate(
                config,
                source,
                value,
                source_role=source_role,
                priority=priority,
            )
        else:
            candidate["source_role"] = source_role
            candidate["priority"] = priority
        existing = list(fields[key].get("candidates") or [])
        fields[key] = _field_from_candidates([*existing, candidate])

    # Direct ValveReport attributes remain a compatibility fallback.
    for key, value in info_values.items():
        if _is_present(value) and fields[key]["status"] == "missing":
            add_info_candidate(
                key,
                value,
                info_source,
                source_role="valve_report",
                priority=350,
            )

    info_field_keys = {
        "identity.device_tag": "device_tag",
        "identity.device_id": "device_id",
        "identity.serial_number": "serial_number",
        "identity.device_type": "device_type",
        "identity.manufacturer": "manufacturer",
        "valve.manufacturer": "valve_manufacturer",
        "valve.type": "dtm_valve_type",
        "identity.firmware_version": "firmware",
        "identity.dtm_version": "diagnostic_software_version",
        "file.report_type": "report_type",
        "file.report_start_time": "start_time",
        "file.report_end_time": "end_time",
        "file.report_time": "report_time",
        "positioner.type": "device_type",
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
    }
    for source_role, source_info, priority in info_sources:
        source = _info_source(config, valve, source_info)
        for key, raw_key in info_field_keys.items():
            value = source_info.get(raw_key)
            if key == "positioner.type" and _is_present(valve.positioner_type):
                continue
            add_info_candidate(
                key,
                value,
                source,
                source_role=source_role,
                priority=priority,
            )

    info_source_text = _source_text(config, info_source)
    info_source_entries = [
        (
            source_role,
            source_info,
            priority,
            source_path,
            _source_text(config, source_path),
        )
        for source_role, source_info, priority in info_sources
        if (source_path := _info_source(config, valve, source_info)) is not None
    ]
    info_source_texts = [entry[4] for entry in info_source_entries]
    continuous_data_keys = {
        "strokes",
        "cycles",
        "time_open",
        "time_closed",
        "time_near_closed",
        "average_error",
        "t86",
        "t63",
        "td",
        "overshoot",
        "run_ramp",
    }
    continuous_candidates: list[dict[str, Any]] = []
    continuous_label_pattern = (
        r"(?:Strokes|Cy(?:cl|el)es|Time\s+(?:Open|Closed|Near\s+Closed)|"
        r"Average\s+Error|T86|T63|\bTd\b|Overshoot|Run\s+Ramp)"
    )
    for source_role, source_info, priority, source_path, source_text in info_source_entries:
        explicit_section = _is_present(
            source_info.get("continuous_diagnostics")
        ) or bool(re.search(r"\bContinuous\s+Diagnostics\b", source_text, re.I))
        if not explicit_section:
            continue
        data_present = any(
            key in source_info and _is_present(source_info.get(key))
            for key in continuous_data_keys
        ) or bool(
            re.search(
                continuous_label_pattern + r"[^\n]{0,100}[-+]?\d",
                source_text,
                re.I,
            )
        )
        labels_present = bool(
            re.search(continuous_label_pattern, source_text, re.I)
        )
        continuous_status = (
            "present"
            if data_present
            else "parse_failed"
            if labels_present
            else "no_matching_data"
        )
        candidate = _candidate(
            config,
            source_path,
            source_info.get("continuous_diagnostics")
            or "Continuous Diagnostics",
            normalized_value={"status": continuous_status},
            source_role=source_role,
            priority=priority,
            candidate_status=(
                "source_present_parser_missing"
                if continuous_status == "parse_failed"
                else "extracted"
            ),
            reason=f"continuous_diagnostics_{continuous_status}",
            review_reason=(
                "source_present_parser_missing"
                if continuous_status == "parse_failed"
                else ""
            ),
        )
        continuous_candidates.append(candidate)
    if continuous_candidates:
        fields["status.continuous_diagnostics"] = _field_from_candidates(
            continuous_candidates
        )
    else:
        fields["status.continuous_diagnostics"] = {
            "status": "missing",
            "selected": _candidate(
                config,
                info_source,
                None,
                normalized_value={"status": "not_provided"},
                candidate_status="missing",
                reason="continuous_diagnostics_not_provided",
            ),
            "candidates": [],
            "conflict": False,
        }
    combined_info_text = "\n".join(info_source_texts) or info_source_text
    active_faults_explicit_empty = _fault_section_is_explicitly_empty(
        combined_info_text,
        r"Active\s+Faults",
        r"Historical\s+Faults",
    )
    historical_faults_explicit_empty = _fault_section_is_explicitly_empty(
        combined_info_text,
        r"Historical\s+Faults",
        r"Raw\s+Data",
    )
    active_fault_candidates: list[dict[str, Any]] = []
    historical_fault_candidates: list[dict[str, Any]] = []
    task_candidates: list[dict[str, Any]] = []
    test_execution_candidates: list[dict[str, Any]] = []
    for source_role, source_info, priority in info_sources:
        source_path = _info_source(config, valve, source_info)
        if "active_faults" in source_info or "faults" in source_info:
            candidate = _candidate(
                config,
                source_path,
                source_info.get("active_faults", source_info.get("faults")) or [],
                source_role=source_role,
                priority=priority,
            )
            active_fault_candidates.append(candidate)
        if "historical_faults" in source_info:
            candidate = _candidate(
                config,
                source_path,
                source_info.get("historical_faults") or [],
                source_role=source_role,
                priority=priority,
            )
            historical_fault_candidates.append(candidate)
        task_value = source_info.get("task_execution")
        if task_value is None:
            task_value = source_info.get("task_execution_summary")
        if task_value is not None:
            task_candidates.append(
                _candidate(
                    config,
                    source_path,
                    task_value,
                    source_role=source_role,
                    priority=priority,
                )
            )
        # The task table is the complete execution fact.  Some parsers also
        # expose a failure-only ``test_execution`` projection; treating both
        # as competing values creates a false source conflict.
        test_value = task_value
        if test_value is None:
            test_value = source_info.get("test_execution")
        if test_value is None:
            test_value = source_info.get("test_execution_summary")
        if test_value is not None:
            if isinstance(test_value, list):
                test_value = [
                    (
                        {
                            **item,
                            "status": item.get("status") or item.get("result"),
                        }
                        if isinstance(item, dict)
                        else item
                    )
                    for item in test_value
                ]
            test_execution_candidates.append(
                _candidate(
                    config,
                    source_path,
                    test_value,
                    source_role=source_role,
                    priority=priority,
                )
            )

    if not active_fault_candidates and (
        active_faults_explicit_empty or _is_present(valve.active_faults)
    ):
        active_fault_candidates.append(
            _candidate(config, info_source, valve.active_faults or [])
        )
    if not historical_fault_candidates and historical_faults_explicit_empty:
        historical_fault_candidates.append(_candidate(config, info_source, []))
    if active_fault_candidates:
        fields["status.active_faults"] = _field_from_candidates(
            active_fault_candidates
        )
    if historical_fault_candidates:
        fields["status.historical_faults"] = _field_from_candidates(
            historical_fault_candidates
        )

    if not task_candidates:
        task_execution = getattr(valve, "task_execution", None)
        if task_execution is not None:
            task_candidates.append(_candidate(config, info_source, task_execution))
    if task_candidates:
        fields["status.task_execution"] = _field_from_candidates(task_candidates)
        task_execution = fields["status.task_execution"]["selected"].get(
            "normalized_value"
        )
    else:
        task_execution = None
    # ``test_execution`` is the complete execution fact, not just the list of
    # failed tests. A fully successful run must therefore remain extracted.
    if not test_execution_candidates and task_execution is not None:
        test_execution_candidates.append(
            _candidate(config, info_source, task_execution)
        )
    if not test_execution_candidates and valve.test_execution_issues:
        test_execution_candidates.append(
            _candidate(config, info_source, valve.test_execution_issues)
        )
    if test_execution_candidates:
        fields["status.test_execution"] = _field_from_candidates(
            test_execution_candidates
        )

    for key, raw_key, hours in (
        ("runtime.strokes", "strokes", False),
        ("runtime.cycles", "cycles", False),
        ("runtime.time_open", "time_open", True),
        ("runtime.time_closed", "time_closed", True),
        ("runtime.time_near_closed", "time_near_closed", True),
    ):
        runtime_candidates: list[dict[str, Any]] = []
        for source_role, source_info, priority in info_sources:
            candidate = _runtime_candidate(
                config,
                _info_source(config, valve, source_info),
                source_info.get(raw_key),
                hours=hours,
            )
            if candidate is not None:
                candidate["source_role"] = source_role
                candidate["priority"] = priority
                runtime_candidates.append(candidate)
        if runtime_candidates:
            fields[key] = _field_from_candidates(runtime_candidates)
    historical_runtime_candidates = [
        _candidate(
            config,
            _info_source(config, valve, source_info),
            source_info["historical_runtime"],
            source_role=source_role,
            priority=priority,
        )
        for source_role, source_info, priority in info_sources
        if _is_present(source_info.get("historical_runtime"))
    ]
    if historical_runtime_candidates:
        fields["runtime.historical_runtime"] = _field_from_candidates(
            historical_runtime_candidates
        )

    metric_source = _metric_source(config, valve)
    for metric_name, field_name in METRIC_FIELDS.items():
        value = valve.metrics.get(metric_name)
        if not _is_present(value):
            continue
        unit = _metric_unit(metric_name, valve.metrics)
        fields[field_name] = _field(
            _candidate(
                config,
                metric_source,
                value,
                raw_unit=unit,
                normalized_unit=unit,
                method=(
                    "structured_data"
                    if metric_source and metric_source.suffix.lower() == ".devdata"
                    else ""
                ),
            )
        )
    for family in RUN_RAMP_FAMILIES:
        for statistic in RUN_RAMP_STATISTICS:
            value = _run_ramp_value(valve.metrics, info, family, statistic)
            if not _is_present(value):
                continue
            number = _number(value)
            fields[f"metrics.run_ramp.{family}.{statistic}"] = _field(
                _candidate(
                    config,
                    metric_source or info_source,
                    value,
                    normalized_value=number if number is not None else value,
                    raw_unit="%",
                    normalized_unit="%",
                )
            )

    spec_source = None
    spec_page = None
    if valve.spec_page is not None:
        spec_source = config.specs_dir / valve.spec_page.source
        spec_page = valve.spec_page.page
    spec_source_values = _specification_source_values(config, spec_source, spec_page)
    for raw_key, field_name in SPECIFICATION_FIELDS.items():
        value = valve.spec_fields.get(raw_key)
        if _is_present(value):
            candidate = (
                _leakage_candidate(
                    config,
                    spec_source,
                    value,
                    page=spec_page,
                )
                if raw_key == "leakage_class"
                else _candidate(
                    config, spec_source, spec_source_values.get(raw_key, value),
                    normalized_value=value, page=spec_page,
                )
            )
            if candidate is not None and raw_key in {"valve_size", "supply_pressure"}:
                unit_pattern = (
                    r'(in(?:ch(?:es)?)?\.?|["″]|mm)$'
                    if raw_key == "valve_size"
                    else r'(psi(?:\s*[ga])?|bar(?:\s*[ga])?|[Mk]?Pa(?:\s*[ga])?)$'
                )
                raw_unit = re.search(unit_pattern, str(candidate["raw_value"]), re.I)
                normalized_unit = re.search(unit_pattern, str(value), re.I)
                candidate["raw_unit"] = raw_unit.group(1) if raw_unit else ""
                candidate["normalized_unit"] = normalized_unit.group(1) if normalized_unit else ""
                if candidate["raw_value"] != value:
                    candidate["normalization_rule"] = "specification_labelled_value_normalization"
            fields[field_name] = _field(
                candidate
            )

    # The full order model belongs to the matched Specification/Accessory
    # evidence, while DTM/ValvXpert expose only a device family/revision such
    # as SVI2AP (0xCA / 02).  Use the specification model for both approval
    # and customer-report projections when it exists; retain the current
    # device type separately under identity.device_type/positioner.type.
    accessory_model = ""
    accessory_source = None
    accessory_page_no = None
    if valve.accessory_page is not None:
        accessory_model = str(
            valve.accessory_page.fields.get("positioner_model") or ""
        )
        accessory_source = config.specs_dir / valve.accessory_page.source
        accessory_page_no = valve.accessory_page.page
    if not _is_present(accessory_model):
        accessory_model = str(valve.spec_fields.get("positioner_model") or "")
        accessory_source = spec_source
        accessory_page_no = spec_page
    if _is_present(accessory_model):
        order_model_candidate = _candidate(
            config,
            accessory_source,
            accessory_model,
            page=accessory_page_no,
            source_role="specification_accessory",
            priority=500,
        )
        fields["accessory.positioner_model"] = _field(order_model_candidate)
        fields["positioner.model"] = _field(order_model_candidate)

    # Expose valve/spec/actuator ownership separately.  The historical
    # `manufacturer` and `actuator_model` values remain available through
    # their original ValveReport/spec fields, but are no longer overloaded in
    # the Stage 2 evidence matrix.
    spec_manufacturer = valve.spec_fields.get("manufacturer")
    if _is_present(spec_manufacturer):
        spec_manufacturer_candidate = _candidate(
            config, spec_source, spec_manufacturer, page=spec_page,
            source_role="specification", priority=300,
        )
        fields["specification.manufacturer"] = _field(spec_manufacturer_candidate)
        fields["valve.manufacturer"] = _field_from_candidates(
            [*fields["valve.manufacturer"].get("candidates", []), spec_manufacturer_candidate]
        )
    spec_type = valve.spec_fields.get("body_type") or valve.spec_fields.get("valve_type")
    if _is_present(spec_type):
        spec_type_candidate = _candidate(
            config, spec_source, spec_type, page=spec_page,
            source_role="specification", priority=300,
        )
        fields["specification.valve_type"] = _field(spec_type_candidate)
        fields["valve.type"] = _field_from_candidates(
            [*fields["valve.type"].get("candidates", []), spec_type_candidate]
        )
    actuator_manufacturer = next(
        (
            source_info.get("actuator_manufacturer")
            for _role, source_info, _priority in info_sources
            if _is_present(source_info.get("actuator_manufacturer"))
        ),
        None,
    )
    if _is_present(actuator_manufacturer):
        actuator_source = _info_source(
            config,
            valve,
            next(
                source_info
                for _role, source_info, _priority in info_sources
                if _is_present(source_info.get("actuator_manufacturer"))
            ),
        )
        fields["actuator.manufacturer"] = _field(
            _candidate(
                config,
                actuator_source,
                actuator_manufacturer,
                source_role="actuator_report",
            )
        )
    actuator_model_full = valve.spec_fields.get("actuator_model")
    if not _is_present(actuator_model_full):
        actuator_model_full = next(
            (
                source_info.get("actuator_model")
                for _role, source_info, _priority in info_sources
                if _is_present(source_info.get("actuator_model"))
            ),
            None,
        )
    if _is_present(actuator_model_full):
        model_candidate = _candidate(
            config,
            spec_source or info_source,
            actuator_model_full,
            normalized_value=str(actuator_model_full).strip(),
        )
        fields["actuator.model_full"] = _field(model_candidate)
        fields["actuator.model"] = _field(
            _candidate(
                config,
                spec_source or info_source,
                actuator_model_full,
                normalized_value=str(actuator_model_full).strip(),
            )
        )
        display_model = _actuator_model_size_display(
            actuator_model_full,
            valve.spec_fields.get("actuator_size"),
        )
        fields["actuator.model_display"] = _field(
            _candidate(
                config,
                spec_source or info_source,
                actuator_model_full,
                normalized_value=display_model,
                normalization_rule=(
                    "actuator_model_with_explicit_size"
                    if _is_present(valve.spec_fields.get("actuator_size"))
                    else "actuator_model_display_before_-21129"
                ),
            )
        )

    for raw_key, raw_value in spec_source_values.items():
        field_names = (
            ("actuator.model_full", "actuator.model", "actuator.model_display", "actuator.size")
            if raw_key == "actuator_model_size"
            else ("actuator.model_full", "actuator.model", "actuator.model_display")
            if raw_key == "actuator_model"
            else (SPECIFICATION_FIELDS.get(raw_key),)
        )
        for field_name in field_names:
            if not field_name or fields[field_name]["status"] != "missing":
                continue
            candidate = _candidate(
                config,
                spec_source,
                raw_value,
                page=spec_page,
                record=field_name,
                method="native_text",
                source_role="specification",
                candidate_status="source_present_parser_missing",
                reason="specification label has a nonblank value but the parser could not normalize it",
                review_reason="source_present_parser_missing",
            )
            # Keep the source value for audit/review, never promote it to a fact.
            candidate["normalized_value"] = None
            if not candidate["source"]["raw_text"]:
                candidate["source"]["raw_text"] = raw_value
            fields[field_name] = _field(candidate)

    context_values = {
        "context.description": valve.description,
        "context.unit": valve.unit,
        "context.failure_consequence": valve.failure_consequence,
        "context.bypass": valve.bypass,
        "context.site_note": valve.site_note,
    }
    for key, value in context_values.items():
        if _is_present(value):
            fields[key] = _field(
                _candidate(
                    config,
                    config.valve_context_file,
                    value,
                    record=valve.folder,
                    method="structured_data",
                    source_role="project_context",
                    priority=500,
                )
            )

    source_texts = {
        source: (
            info_source_text
            if source == info_source
            else _source_text(config, source)
        )
        for source in (info_source, metric_source)
        if source is not None
    }
    for field_name, label_pattern in SOURCE_LABEL_PATTERNS.items():
        if fields[field_name]["status"] != "missing":
            continue
        source = next(
            (
                candidate_source
                for candidate_source, text in source_texts.items()
                if re.search(label_pattern, text, re.I)
            ),
            None,
        )
        if source is None:
            continue
        fields[field_name] = _field(
            _candidate(
                config,
                source,
                None,
                page=_source_label_page(config, source, label_pattern),
                record=field_name,
                method="native_text",
                candidate_status="source_present_parser_missing",
                reason="source label is present but the normalized parser value is missing",
                review_reason="source_present_parser_missing",
            )
        )

    _derive_actuator_display_from_spec(fields)

    conflicts: list[dict[str, Any]] = [
        {
            "type": "source_value_conflict",
            "field": key,
            "selected_value": field["selected"]["normalized_value"],
            "candidate_values": [
                candidate["normalized_value"]
                for candidate in field["candidates"]
            ],
            "sources": [
                candidate["source"]["path"]
                for candidate in field["candidates"]
            ],
        }
        for key, field in fields.items()
        if field["conflict"]
    ]
    device_tag = str(valve.device_tag or "").strip()
    if device_tag and device_tag.upper() != valve.folder.upper():
        conflicts.append(
            {
                "type": "valve_tag_mismatch",
                "field": "identity.device_tag",
                "project_value": valve.folder,
                "source_value": device_tag,
            }
        )

    curves = _curve_records(config, valve)
    report_pages = _normalized_report_pages(config, valve)
    missing_fields = [
        key for key, value in fields.items() if value["status"] == "missing"
    ]
    parser_missing_fields = [
        key
        for key, value in fields.items()
        if value["status"] == "source_present_parser_missing"
    ]
    # A source conflict is a semantic human-choice item, not a visual OCR
    # task. Keep it in ``conflicts``/the Stage 2 attention report; only
    # low-confidence or parser/relationship failures enter Codex's queue.
    review_items = [
        {
            "valve_tag": valve.folder,
            "reason": "source_present_parser_missing",
            "field": field_name,
        }
        for field_name in parser_missing_fields
    ]
    status = (
        "review_required"
        if review_items or conflicts
        else "extracted_with_gaps"
        if missing_fields
        else "extracted"
    )
    material_summary = {
        "folder": valve.folder,
        "files": [source.__dict__ for source in valve.files],
        "source_report_types": valve.source_report_types,
        "dtm_data_report_count": valve.dtm_data_report_count,
        "images": [
            {
                "test_key": curve["test_key"],
                "source": Path(curve["source"]["path"]).name,
            }
            for curve in curves
        ],
        "test_execution_issues": valve.test_execution_issues,
        "task_execution": task_execution or [],
        "spec_page": valve.spec_page.__dict__ if valve.spec_page else None,
        "air_piping_page": (
            valve.air_piping_page.__dict__ if valve.air_piping_page else None
        ),
        "accessory_page": (
            valve.accessory_page.__dict__ if valve.accessory_page else None
        ),
        "report_pages": report_pages,
        "description": valve.description,
        "unit": valve.unit,
        "failure_consequence": valve.failure_consequence,
        "bypass": valve.bypass,
        "site_note": valve.site_note,
        "data_note": valve.data_note,
        "unreadable": valve.unreadable,
        "metrics": valve.metrics,
    }
    continuous_selected = fields["status.continuous_diagnostics"].get("selected")
    continuous_normalized = (
        continuous_selected.get("normalized_value")
        if isinstance(continuous_selected, dict)
        else None
    )
    continuous_status = (
        str(continuous_normalized.get("status") or "not_provided")
        if isinstance(continuous_normalized, dict)
        else "not_provided"
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "valve_tag": valve.folder,
        "status": status,
        "fields": fields,
        "reports": {
            "counts": valve.source_report_types,
            "dtm_data_report_count": valve.dtm_data_report_count,
            "continuous_diagnostics": continuous_status,
        },
        # Preserve the complete nested metric object for later diagnosis;
        # field projections above are intentionally not the only copy.
        "metrics": valve.metrics,
        "supply_pressures": [
            test.supply_pressures
            for test in valve.all_tests
            if getattr(test, "supply_pressures", None)
        ],
        "tests": _test_records(config, valve),
        "curves": curves,
        "report_pages": report_pages,
        "spec_page": valve.spec_page.__dict__ if valve.spec_page else None,
        "air_piping_page": (
            valve.air_piping_page.__dict__ if valve.air_piping_page else None
        ),
        "accessory_page": (
            valve.accessory_page.__dict__ if valve.accessory_page else None
        ),
        "missing_fields": missing_fields,
        "parser_missing_fields": parser_missing_fields,
        "conflicts": conflicts,
        "review_items": review_items,
        "source_files": [source.__dict__ for source in valve.files],
        "material_summary": material_summary,
    }


def _selected_raw_value(valve: dict[str, Any], field_name: str) -> Any:
    selected = valve["fields"][field_name].get("selected")
    return selected.get("raw_value") if isinstance(selected, dict) else "未提供"


def _review_candidate_value(
    field_name: str,
    candidate: dict[str, Any] | None,
) -> Any:
    if not isinstance(candidate, dict):
        return None
    if candidate.get("status") == "source_present_parser_missing":
        return candidate.get("raw_value")
    if (
        field_name == "specification.leakage_class"
        and candidate.get("normalization_rule")
    ):
        # Visual review confirms the printed leakage expression.  The shared
        # normalizer converts that confirmed raw value to its Roman class.
        return candidate.get("raw_value")
    return candidate.get("normalized_value")


def _review_id(
    valve_tag: str,
    field_name: str,
    reason: str,
    candidate: dict[str, Any] | None,
) -> str:
    source = candidate.get("source", {}) if isinstance(candidate, dict) else {}
    identity = {
        "valve_tag": valve_tag,
        "field": field_name,
        "reason": reason,
        "source_path": source.get("path"),
        "source_sha256": source.get("sha256"),
        "page": source.get("page"),
        "candidate_value": _review_candidate_value(field_name, candidate),
    }
    return hashlib.sha256(
        json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:24]


def _review_item(
    valve_tag: str,
    field_name: str,
    reason: str,
    candidate: dict[str, Any] | None,
) -> dict[str, Any]:
    source = candidate.get("source", {}) if isinstance(candidate, dict) else {}
    source_path = str(source.get("path") or "")
    image_path = (
        source_path
        if Path(source_path).suffix.lower()
        in {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"}
        else ""
    )
    return {
        "review_id": _review_id(valve_tag, field_name, reason, candidate),
        "valve_tag": valve_tag,
        "field": field_name,
        "reason": reason,
        "source_path": source_path,
        "source_sha256": str(source.get("sha256") or ""),
        "page": source.get("page"),
        "image_path": image_path,
        "candidate_value": _review_candidate_value(field_name, candidate),
        "normalization_rule": (
            str(candidate.get("normalization_rule") or "")
            if isinstance(candidate, dict)
            else ""
        ),
        "confidence": (
            candidate.get("confidence") if isinstance(candidate, dict) else None
        ),
        "degraded": bool(source.get("degraded")),
    }


def _supplementary_review_items(
    config: ProjectConfig,
    requests: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for request in requests:
        # A reliable, high-confidence air-piping page does not need a visual
        # pass merely because it was newly discovered.  Keep this filter at
        # the queue boundary so any collector can provide such requests
        # without turning every new page into a Codex task.  Requests that do
        # not explicitly establish both facts remain queued conservatively.
        role = str(request.get("role") or request.get("page_role") or "").strip()
        reason = str(request.get("reason") or "visual_review_required").strip()
        confidence = request.get("confidence")
        high_confidence = (
            isinstance(confidence, (int, float))
            and not isinstance(confidence, bool)
            and float(confidence) >= 0.85
        )
        reliable_role = request.get("role_reliable") is True
        new_air_piping_reasons = {
            "new_air_piping_page",
            "new_air_piping",
            "air_piping_new_page",
            "new_page_role_review",
        }
        if (
            role == "air_piping"
            and reason in new_air_piping_reasons
            and reliable_role
            and high_confidence
        ):
            continue
        path = request.get("path")
        if not isinstance(path, Path) or not path.is_file():
            continue
        source = {
            "path": path.relative_to(config.project_dir).as_posix(),
            "sha256": _sha256(path),
            "page": None,
            "record": "",
            "method": "visual_review",
            "raw_text": "",
            "degraded": False,
        }
        candidate = {
            "normalized_value": None,
            "confidence": None,
            "source": source,
        }
        items.append(
            _review_item(
                str(request.get("valve_tag") or ""),
                str(request.get("field") or "supplementary.image_content"),
                str(request.get("reason") or "visual_review_required"),
                candidate,
            )
        )
    return items


def _refresh_valve_state(valve: dict[str, Any]) -> None:
    fields = valve["fields"]
    _derive_actuator_display_from_spec(fields)
    conflicts = [
        {
            "type": "source_value_conflict",
            "field": key,
            "selected_value": field["selected"]["normalized_value"],
            "candidate_values": [
                candidate["normalized_value"]
                for candidate in field["candidates"]
            ],
            "sources": [
                candidate["source"]["path"]
                for candidate in field["candidates"]
            ],
        }
        for key, field in fields.items()
        if field["conflict"]
    ]
    selected_device_tag = fields["identity.device_tag"].get("selected")
    device_tag = (
        str(selected_device_tag.get("normalized_value") or "").strip()
        if isinstance(selected_device_tag, dict)
        else ""
    )
    if device_tag and device_tag.upper() != valve["valve_tag"].upper():
        conflicts.append(
            {
                "type": "valve_tag_mismatch",
                "field": "identity.device_tag",
                "project_value": valve["valve_tag"],
                "source_value": device_tag,
            }
        )
    missing_fields = [
        key for key, value in fields.items() if value["status"] == "missing"
    ]
    parser_missing_fields = [
        key
        for key, value in fields.items()
        if value["status"] == "source_present_parser_missing"
    ]
    # Conflicts remain explicit semantic attention items. They must not be
    # rendered as Codex visual-review tasks because seeing one page cannot
    # resolve two genuinely different source values.
    review_items = []
    for field_name, field in fields.items():
        if field["status"] not in {
            "review_required",
            "source_present_parser_missing",
        }:
            continue
        selected = field.get("selected")
        reason = (
            str(
                selected.get("review_reason")
                or (
                    "source_present_parser_missing"
                    if field["status"] == "source_present_parser_missing"
                    else "ocr_review_required"
                )
            )
            if isinstance(selected, dict)
            else "ocr_review_required"
        )
        review_items.append(
            _review_item(
                valve["valve_tag"],
                field_name,
                reason,
                selected,
            )
        )
    valve["missing_fields"] = missing_fields
    valve["parser_missing_fields"] = parser_missing_fields
    valve["conflicts"] = conflicts
    valve["review_items"] = review_items
    valve["status"] = (
        "review_required"
        if review_items or conflicts
        else "extracted_with_gaps"
        if missing_fields
        else "extracted"
    )
    valve["material_summary"].update(
        {
            "description": _selected_raw_value(valve, "context.description"),
            "unit": _selected_raw_value(valve, "context.unit"),
            "failure_consequence": _selected_raw_value(
                valve, "context.failure_consequence"
            ),
            "bypass": _selected_raw_value(valve, "context.bypass"),
            "site_note": _selected_raw_value(valve, "context.site_note"),
        }
    )


def _apply_supplementary_candidates(
    config: ProjectConfig,
    valves: list[dict[str, Any]],
    candidates: dict[str, list[dict[str, Any]]],
) -> None:
    by_tag = {valve["valve_tag"]: valve for valve in valves}
    for valve_tag, descriptors in candidates.items():
        valve = by_tag.get(valve_tag)
        if valve is None:
            continue
        for descriptor in descriptors:
            field_name = str(descriptor["field"])
            if field_name not in valve["fields"]:
                continue
            normalized_value = descriptor["value"]
            normalization_rule = str(
                descriptor.get("normalization_rule") or ""
            )
            if field_name == "specification.leakage_class":
                normalized_value, inferred_rule = _normalize_leakage_value(
                    descriptor["value"]
                )
                normalization_rule = normalization_rule or inferred_rule
            candidate = _candidate(
                config,
                descriptor["path"],
                descriptor["value"],
                normalized_value=normalized_value,
                record=str(descriptor.get("record") or ""),
                method=str(descriptor.get("method") or ""),
                source_role=str(descriptor.get("source_role") or ""),
                priority=int(descriptor.get("priority") or 0),
                raw_unit=str(descriptor.get("raw_unit") or ""),
                normalized_unit=str(descriptor.get("raw_unit") or ""),
                candidate_status=str(
                    descriptor.get("candidate_status") or "extracted"
                ),
                reason=str(descriptor.get("reason") or ""),
                confidence_override=(
                    float(descriptor["confidence"])
                    if isinstance(descriptor.get("confidence"), (int, float))
                    and not isinstance(descriptor.get("confidence"), bool)
                    else None
                ),
                degraded=bool(descriptor.get("degraded")),
                review_reason=str(descriptor.get("review_reason") or ""),
                normalization_rule=normalization_rule,
            )
            existing = list(valve["fields"][field_name]["candidates"])
            valve["fields"][field_name] = _field_from_candidates(
                [*existing, candidate]
            )
    for valve in valves:
        _refresh_valve_state(valve)


def _mark_adopted_supplements(
    materials: list[dict[str, Any]],
    valves: list[dict[str, Any]],
) -> None:
    selected_by_source: dict[str, set[str]] = {}
    for valve in valves:
        for field_name, field in valve["fields"].items():
            selected = field.get("selected")
            if not isinstance(selected, dict):
                continue
            path = str(selected.get("source", {}).get("path") or "")
            if path and field["status"] != "review_required":
                selected_by_source.setdefault(path, set()).add(field_name)
    for material in materials:
        evidence_path = str(material.get("extracted_path") or material["path"])
        adopted_fields = sorted(selected_by_source.get(evidence_path, set()))
        material["adopted_fields"] = adopted_fields
        material["adopted"] = bool(adopted_fields)


def _render_supplementary_markdown(materials: list[dict[str, Any]]) -> str:
    lines = [
        "# 补充料单",
        "",
        "| 文件 | 识别类型 | 匹配范围 | 匹配阀门 | 已采用字段 | 状态 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for material in materials:
        lines.append(
            f"| `{material['path']}` | {material['detected_type']} | "
            f"{material['scope']} | {', '.join(material['matched_valves']) or '-'} | "
            f"{', '.join(material['adopted_fields']) or '-'} | {material['status']} |"
        )
    if not materials:
        lines.extend(["", "当前没有已识别的补充资料。"])
    return "\n".join(lines) + "\n"


def _write_codex_review_support(
    output_dir: Path,
    review_items: list[dict[str, Any]],
) -> tuple[Path, Path]:
    schema_path = output_dir / "codex-review-schema.json"
    prompt_path = output_dir / "codex-review-prompt.md"
    scalar_value_schemas = [
        {"type": "string"},
        {"type": "number"},
        {"type": "boolean"},
    ]
    visible_field_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["field", "value"],
        "properties": {
            "field": {
                "type": "string",
                "enum": sorted(EXPECTED_FIELDS),
            },
            "value": {"anyOf": scalar_value_schemas},
        },
    }
    review_value_schema = {
        "anyOf": [
            *scalar_value_schemas,
            {"type": "null"},
            {
                "type": "array",
                "items": {"anyOf": scalar_value_schemas},
            },
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["valve_tag", "fields"],
                "properties": {
                    "valve_tag": {"type": "string"},
                    "fields": {
                        "type": "array",
                        "items": visible_field_schema,
                    },
                },
            },
        ]
    }
    _atomic_json(
        schema_path,
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "additionalProperties": False,
            "required": ["schema_version", "reviews"],
            "properties": {
                "schema_version": {"type": "integer", "const": 1},
                "reviews": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["review_id", "status", "value", "reason"],
                        "properties": {
                            "review_id": {"type": "string"},
                            "status": {
                                "type": "string",
                                "enum": ["confirmed", "corrected", "unresolved"]
                            },
                            "value": review_value_schema,
                            "reason": {"type": "string"},
                        },
                    },
                },
            },
        },
    )
    lines = [
        "# 离线资料字段视觉复核任务",
        "",
        "逐项实际查看所附原图或页面，只确认直接可见的文字和表格归属。",
        "不得推断缺失值，不得生成评级、诊断结论或检修建议。",
        (
            "字段为 supplementary.image_content 时，value 必须是 "
            '{"valve_tag":"...","fields":'
            '[{"field":"字段名","value":"可见值"}]}；'
            "看不清时返回 unresolved。"
        ),
        "返回结果必须符合 codex-review-schema.json。",
        "",
    ]
    for item in review_items:
        lines.extend(
            [
                f"## {item['review_id']}",
                "",
                f"- 阀门：{item['valve_tag']}",
                f"- 字段：{item['field']}",
                f"- 原因：{item['reason']}",
                f"- 来源：{item['source_path']}",
                f"- 图片：{item['image_path'] or '需由源 PDF 页渲染'}",
                f"- 自动候选：{item['candidate_value']}",
                "",
            ]
        )
    prompt_path.write_text("\n".join(lines), encoding="utf-8")
    return prompt_path, schema_path


def _render_pdf_review_pages(
    config: ProjectConfig,
    review_items: list[dict[str, Any]],
) -> list[Path]:
    review_dir = config.project_dir / "work/evidence-extraction/review"
    rendered: list[Path] = []
    for item in review_items:
        source_path = str(item.get("source_path") or "")
        page_no = item.get("page")
        if Path(source_path).suffix.lower() != ".pdf" or not isinstance(page_no, int):
            continue
        source = (config.project_dir / source_path).resolve()
        try:
            source.relative_to(config.project_dir)
        except ValueError:
            continue
        if not source.is_file() or _sha256(source) != item.get("source_sha256"):
            continue
        output = review_dir / f"{item['source_sha256'][:16]}-p{page_no:04d}.png"
        if not output.is_file():
            try:
                document = fitz.open(source)
                try:
                    if not 1 <= page_no <= len(document):
                        continue
                    pixmap = document[page_no - 1].get_pixmap(
                        matrix=fitz.Matrix(2.0, 2.0),
                        alpha=False,
                    )
                    output.parent.mkdir(parents=True, exist_ok=True)
                    pixmap.save(output)
                finally:
                    document.close()
            except Exception:
                continue
        item["image_path"] = output.relative_to(config.project_dir).as_posix()
        rendered.append(output)
    return sorted(set(rendered))


def _input_files(config: ProjectConfig) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for root in (config.raw_dir, config.specs_dir, config.inbound_dir):
        if not root.is_dir():
            continue
        for path in sorted(
            (item for item in root.rglob("*") if item.is_file() and not item.is_symlink()),
            key=lambda item: item.relative_to(config.project_dir).as_posix(),
        ):
            records.append(
                {
                    "path": path.relative_to(config.project_dir).as_posix(),
                    "sha256": _sha256(path),
                    "size_bytes": path.stat().st_size,
                }
            )
    override_path = config.project_dir / "experience/extraction_overrides.json"
    if override_path.is_file():
        records.append(
            {
                "path": override_path.relative_to(config.project_dir).as_posix(),
                "sha256": _sha256(override_path),
                "size_bytes": override_path.stat().st_size,
            }
        )
    review_results_path = (
        config.project_dir / "work/evidence-extraction/codex-review-results.json"
    )
    if review_results_path.is_file():
        records.append(
            {
                "path": review_results_path.relative_to(
                    config.project_dir
                ).as_posix(),
                "sha256": _sha256(review_results_path),
                "size_bytes": review_results_path.stat().st_size,
            }
        )
    return records


def _render_markdown(payload: dict[str, Any], valves: list[dict[str, Any]]) -> str:
    lines = [
        "# 当前项目逐阀参数提取结果",
        "",
        f"- 项目：{payload['project']['name']}",
        f"- 客户：{payload['project']['customer']}",
        f"- 阀门数：{payload['summary']['valve_count']}",
        "- 说明：本结果只包含资料参数，不包含评级、诊断结论或检修建议。",
        "",
        "| 阀门 | 状态 | 已提取字段 | 缺失字段 | 冲突 | 待复核 |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for valve in valves:
        extracted = sum(
            field["status"] != "missing" for field in valve["fields"].values()
        )
        lines.append(
            f"| {valve['valve_tag']} | {valve['status']} | {extracted} | "
            f"{len(valve['missing_fields'])} | {len(valve['conflicts'])} | "
            f"{len(valve['review_items'])} |"
        )
    for valve in valves:
        lines.extend(["", f"## {valve['valve_tag']}", ""])
        for key, field in valve["fields"].items():
            selected = field.get("selected")
            if not isinstance(selected, dict):
                continue
            lines.append(f"- `{key}`：{selected['raw_value']}")
        if valve["missing_fields"]:
            lines.append("- 缺失：" + "、".join(valve["missing_fields"]))
        if valve.get("parser_missing_fields"):
            lines.append(
                "- 源有值但解析缺失："
                + "、".join(valve["parser_missing_fields"])
            )
        if valve["conflicts"]:
            lines.append(
                "- 冲突："
                + "、".join(item["field"] for item in valve["conflicts"])
            )
    return "\n".join(lines) + "\n"


def extract_project_evidence(
    config: ProjectConfig,
    valves: list[Any],
    *,
    ocr_stats: dict[str, int],
) -> EvidenceExtraction:
    output_dir = config.project_dir / "work/evidence-extraction"
    valve_dir = output_dir / "valves"
    valve_dir.mkdir(parents=True, exist_ok=True)
    valve_payloads = [_extract_valve(config, valve) for valve in valves]
    supplements = collect_supplementary_materials(
        config,
        [valve["valve_tag"] for valve in valve_payloads],
    )
    _apply_supplementary_candidates(config, valve_payloads, supplements.candidates)
    supplementary_review_items = _supplementary_review_items(
        config,
        supplements.review_items,
    )
    codex_reviews = load_codex_review_candidates(
        config,
        valve_payloads,
        extra_review_items=supplementary_review_items,
        field_names=set(EXPECTED_FIELDS),
    )
    _apply_supplementary_candidates(
        config,
        valve_payloads,
        codex_reviews.candidates,
    )
    overrides = load_extraction_overrides(
        config,
        valve_tags={valve["valve_tag"] for valve in valve_payloads},
        field_names=set(EXPECTED_FIELDS),
    )
    _apply_supplementary_candidates(config, valve_payloads, overrides)
    _mark_adopted_supplements(supplements.materials, valve_payloads)
    review_items = [
        item
        for valve in valve_payloads
        for item in valve["review_items"]
    ]
    review_items.extend(
        item
        for item in supplementary_review_items
        if item["review_id"] not in codex_reviews.applied_review_ids
    )
    review_images = _render_pdf_review_pages(config, review_items)
    index_valves: list[dict[str, Any]] = []
    for valve in valve_payloads:
        path = valve_dir / f"{valve['valve_tag']}.json"
        _atomic_json(path, valve)
        index_valves.append(
            {
                "valve_tag": valve["valve_tag"],
                "path": path.relative_to(config.project_dir).as_posix(),
                "status": valve["status"],
                "missing_count": len(valve["missing_fields"]),
                "parser_missing_count": len(valve["parser_missing_fields"]),
                "conflict_count": len(valve["conflicts"]),
                "review_count": len(valve["review_items"]),
                "report_pages": _report_page_summary(valve["report_pages"]),
            }
        )

    created_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    input_files = _input_files(config)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "extractor_version": EXTRACTOR_VERSION,
        "created_at": created_at,
        "status": "materials_extracted",
        "project": {
            "name": config.project_dir.name,
            "customer": config.customer,
        },
        "summary": {
            "valve_count": len(valve_payloads),
            "review_required_count": sum(
                valve["status"] == "review_required" for valve in valve_payloads
            ),
            "with_missing_fields_count": sum(
                bool(valve["missing_fields"]) for valve in valve_payloads
            ),
            "with_parser_missing_fields_count": sum(
                bool(valve["parser_missing_fields"])
                for valve in valve_payloads
            ),
        },
        "inputs": input_files,
        "valves": index_valves,
    }
    index_path = output_dir / "project-index.json"
    markdown_path = output_dir / "extraction.md"
    _atomic_json(index_path, payload)
    markdown_path.write_text(
        _render_markdown(payload, valve_payloads),
        encoding="utf-8",
    )
    supplementary = {
        "schema_version": SCHEMA_VERSION,
        "created_at": created_at,
        "materials": supplements.materials,
    }
    _atomic_json(output_dir / "supplementary-materials.json", supplementary)
    (output_dir / "supplementary-materials.md").write_text(
        _render_supplementary_markdown(supplements.materials),
        encoding="utf-8",
    )
    queue_payload = {
        "schema_version": SCHEMA_VERSION,
        "created_at": created_at,
        "items": review_items,
    }
    queue_payload["queue_sha256"] = hashlib.sha256(
        json.dumps(review_items, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    _atomic_json(output_dir / "review-queue.json", queue_payload)
    prompt_path, schema_path = _write_codex_review_support(
        output_dir,
        review_items,
    )
    outputs = [
        index_path,
        markdown_path,
        output_dir / "supplementary-materials.json",
        output_dir / "supplementary-materials.md",
        output_dir / "review-queue.json",
        prompt_path,
        schema_path,
        *review_images,
        *sorted(valve_dir.glob("*.json")),
    ]
    _atomic_json(
        output_dir / "run-manifest.json",
        {
            "schema_version": SCHEMA_VERSION,
            "created_at": created_at,
            "status": "materials_extracted",
            "extractor_version": EXTRACTOR_VERSION,
            "ocr": ocr_stats,
            "inputs": input_files,
            "outputs": [
                {
                    "path": path.relative_to(config.project_dir).as_posix(),
                    "sha256": _sha256(path),
                }
                for path in outputs
            ],
        },
    )
    return EvidenceExtraction(
        status="materials_extracted",
        index_path=index_path,
        markdown_path=markdown_path,
        payload=payload,
    )
