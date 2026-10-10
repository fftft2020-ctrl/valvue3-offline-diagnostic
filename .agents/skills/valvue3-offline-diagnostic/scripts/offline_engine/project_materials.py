from __future__ import annotations

import hashlib
import html
import json
import re
import subprocess
import tomllib
from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import ProjectConfig


SOURCE_SUFFIXES = {".pdf", ".devdata"}
CURVE_TYPES = (
    ("run_step", "Run Step"),
    ("pattern_step", "Pattern Step"),
    ("run_ramp", "Run Ramp"),
    ("standard_signature", "Standard Signature"),
    ("extended_signature", "Extended Signature"),
)
CONTEXT_FIELDS = (
    ("description", "阀门描述"),
    ("unit", "装置"),
    ("failure_consequence", "失效后果"),
    ("bypass", "有无副线"),
    ("site_note", "现场备注"),
)
MISSING_VALUES = {"", "未提供", "无数据", "待业主补充", "数据不足", "待复核"}
REPORT_PAGE_ROLES = ("specification", "air_piping", "accessory")
MATERIAL_STAGE_DIR = Path("work/stage-gates")
MATERIAL_STAGE_REPORT_JSON = "material-archive-report.json"
MATERIAL_STAGE_REPORT_MARKDOWN = "material-archive-report.md"
MATERIAL_STAGE_REPORT_HTML = "material-archive-report.html"
MATERIAL_STAGE_APPROVAL = "materials-approval.json"
UNCHANGED_REUSE_POLICIES = {"reuse_unchanged", "reanalyze_all"}
PARAMETER_STAGE_REPORT_JSON = "parameter-extraction-report.json"
PARAMETER_STAGE_REPORT_MARKDOWN = "parameter-extraction-report.md"
PARAMETER_STAGE_REPORT_HTML = "parameter-extraction-report.html"
PARAMETER_STAGE_APPROVAL = "parameters-approval.json"
PARAMETER_MAIN_FIELDS = (
    ("identity.valve_tag", "阀门位号"),
    ("context.description", "阀门描述"),
    ("valve.manufacturer", "阀门制造商"),
    ("specification.model_no", "阀门型号"),
    ("valve.type", "阀门类型"),
    ("specification.body_type", "阀体形式"),
    ("specification.valve_size", "阀门口径"),
    ("specification.pressure_class", "压力等级"),
    ("specification.end_connection", "端连接"),
    ("specification.rated_cv", "额定 Cv"),
    ("specification.flow_action", "流向"),
    ("specification.leakage_class", "泄漏等级"),
    ("actuator.fail_position", "失效位置"),
    ("actuator.manufacturer", "执行机构制造商"),
    ("actuator.model_full", "执行机构完整型号"),
    ("actuator.model_display", "执行机构显示型号"),
    ("actuator.type", "执行机构类型"),
    ("actuator.size", "执行机构尺寸"),
    ("actuator.action", "执行机构作用方式"),
    ("actuator.bench_range", "执行机构弹簧范围"),
    ("actuator.supply_pressure", "执行机构供气压力"),
    ("actuator.travel", "执行机构行程"),
    ("identity.device_tag", "设备位号"),
    ("identity.device_id", "设备 ID"),
    ("identity.serial_number", "定位器序列号"),
    ("identity.device_type", "定位器设备类型"),
    ("identity.manufacturer", "定位器制造商"),
    ("identity.firmware_version", "固件版本"),
    ("identity.dtm_version", "软件/DTM 版本"),
    ("identity.communication_protocol", "通信协议"),
    ("positioner.model", "定位器型号"),
    ("positioner.type", "定位器类型"),
    ("positioner.diagnostics_level", "诊断等级"),
    ("positioner.option_config", "选件配置"),
    ("positioner.device_mode", "设备模式"),
    ("positioner.valve_position", "当前阀位"),
    ("positioner.manual_setpoint", "手动设定值"),
    ("positioner.input_signal", "输入信号"),
    ("positioner.board_temperature", "板卡温度"),
    ("positioner.min_temperature", "最低温度"),
    ("positioner.max_temperature", "最高温度"),
    ("positioner.air_action", "气路动作"),
    ("positioner.actuator_style", "执行机构形式"),
    ("positioner.supply_pressure", "定位器供气压力"),
    ("runtime.strokes", "全行程次数"),
    ("runtime.cycles", "循环次数"),
    ("runtime.time_open", "全开累计时间"),
    ("runtime.time_closed", "全关累计时间"),
    ("runtime.time_near_closed", "近关累计时间"),
    ("runtime.historical_runtime", "历史运行数据"),
    ("file.report_type", "报告类型"),
    ("file.report_start_time", "检测开始时间"),
    ("file.report_end_time", "检测结束时间"),
    ("status.continuous_diagnostics", "Continuous Diagnostics"),
    ("status.active_faults", "当前故障"),
    ("status.historical_faults", "历史故障"),
    ("status.task_execution", "任务执行汇总"),
    ("status.test_execution", "测试执行状态"),
    ("metrics.average_error", "平均误差"),
    ("metrics.t86", "T86"),
    ("metrics.t63", "T63"),
    ("metrics.td", "Td"),
    ("metrics.overshoot", "超调量"),
    ("metrics.max_friction", "最大摩擦力"),
    ("metrics.min_friction", "最小摩擦力"),
    ("metrics.mean_friction", "平均摩擦力"),
    ("metrics.response_time_open", "开启响应时间"),
    ("metrics.response_time_close", "关闭响应时间"),
    ("metrics.supply_pressure", "供气压力统计"),
    ("metrics.run_ramp.hysteresis.max", "回差最大值"),
    ("metrics.run_ramp.hysteresis.average", "回差平均值"),
    ("metrics.run_ramp.hysteresis.min", "回差最小值"),
    ("metrics.run_ramp.dead_band.max", "死区最大值"),
    ("metrics.run_ramp.dead_band.average", "死区平均值"),
    ("metrics.run_ramp.dead_band.min", "死区最小值"),
    ("metrics.run_ramp.linearity.max", "线性度最大值"),
    ("metrics.run_ramp.linearity.average", "线性度平均值"),
    ("metrics.run_ramp.linearity.min", "线性度最小值"),
    ("context.unit", "装置"),
    ("context.failure_consequence", "失效后果"),
    ("context.bypass", "有无副线"),
    ("context.site_note", "现场备注"),
)
PARAMETER_MAIN_LABELS = dict(PARAMETER_MAIN_FIELDS)
@dataclass(frozen=True)
class MaterialReadiness:
    status: str
    json_path: Path
    markdown_path: Path
    payload: dict[str, Any]


@dataclass(frozen=True)
class ParameterExtractionReport:
    status: str
    json_path: Path
    markdown_path: Path
    payload: dict[str, Any]


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _record(project_dir: Path, path: Path) -> dict[str, Any]:
    return {
        "path": path.relative_to(project_dir).as_posix(),
        "extension": path.suffix.lower(),
        "size_bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _files(project_dir: Path, root: Path) -> list[dict[str, Any]]:
    if not root.is_dir():
        return []
    paths = sorted(
        (
            path
            for path in root.rglob("*")
            if path.is_file() and not path.is_symlink()
        ),
        key=lambda path: path.relative_to(project_dir).as_posix(),
    )
    return [_record(project_dir, path) for path in paths]


def _payload_sha256(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _material_stage_paths(config: ProjectConfig) -> tuple[Path, Path, Path]:
    stage_dir = config.project_dir / MATERIAL_STAGE_DIR
    return (
        stage_dir / MATERIAL_STAGE_REPORT_JSON,
        stage_dir / MATERIAL_STAGE_REPORT_MARKDOWN,
        stage_dir / MATERIAL_STAGE_APPROVAL,
    )


def parameter_extraction_report_paths(
    config: ProjectConfig,
) -> tuple[Path, Path, Path]:
    """Return the frozen Stage 2 report and approval paths."""
    stage_dir = config.project_dir / MATERIAL_STAGE_DIR
    return (
        stage_dir / PARAMETER_STAGE_REPORT_JSON,
        stage_dir / PARAMETER_STAGE_REPORT_MARKDOWN,
        stage_dir / PARAMETER_STAGE_APPROVAL,
    )


def parameter_extraction_web_report_path(config: ProjectConfig) -> Path:
    """Return the self-contained Stage 2 approval page path."""
    return config.project_dir / MATERIAL_STAGE_DIR / PARAMETER_STAGE_REPORT_HTML


def material_archive_report_paths(config: ProjectConfig) -> tuple[Path, Path]:
    report_json, report_markdown, _approval = _material_stage_paths(config)
    return report_json, report_markdown


def material_archive_web_report_path(config: ProjectConfig) -> Path:
    """Return the self-contained Stage 1 browser review page."""
    return config.project_dir / MATERIAL_STAGE_DIR / MATERIAL_STAGE_REPORT_HTML


def _material_input_records(config: ProjectConfig) -> list[dict[str, Any]]:
    paths: list[Path] = []
    for root in (config.inbound_dir, config.raw_dir, config.specs_dir):
        if root.is_dir():
            paths.extend(
                path
                for path in root.rglob("*")
                if path.is_file() and not path.is_symlink()
            )
    for path in (config.catalog_file, config.valve_context_file):
        if path is not None and path.is_file() and not path.is_symlink():
            paths.append(path)
    unique = sorted(
        {path.resolve() for path in paths},
        key=lambda path: path.relative_to(config.project_dir).as_posix(),
    )
    records = [_record(config.project_dir, path) for path in unique]
    material_config = {
        "raw": config.raw_dir.relative_to(config.project_dir).as_posix(),
        "specs": config.specs_dir.relative_to(config.project_dir).as_posix(),
        "inbound": config.inbound_dir.relative_to(config.project_dir).as_posix(),
        "catalog": (
            config.catalog_file.relative_to(config.project_dir).as_posix()
            if config.catalog_file is not None
            else ""
        ),
        "valve_context": (
            config.valve_context_file.relative_to(config.project_dir).as_posix()
            if config.valve_context_file is not None
            else ""
        ),
        "include_accessory_pages": bool(
            config.features.get("include_accessory_pages", False)
        ),
    }
    records.append(
        {
            "path": "project.toml#material-stage-config",
            "extension": ".toml",
            "size_bytes": 0,
            "sha256": _payload_sha256(material_config),
        }
    )
    return sorted(records, key=lambda record: record["path"])


def material_input_fingerprint(config: ProjectConfig) -> str:
    return _payload_sha256(_material_input_records(config))


def _material_report_fingerprint(payload: dict[str, Any]) -> str:
    stable = {
        key: value
        for key, value in payload.items()
        if key not in {"generated_at", "stage"}
    }
    return _payload_sha256(stable)


def approve_material_archive(
    config: ProjectConfig,
    approval_text: str,
    unchanged_reuse_policy: str | None = None,
) -> Path:
    """Bind explicit user approval to the frozen Stage 1 archive report."""
    note = str(approval_text or "").strip()
    if not note:
        raise RuntimeError("material archive approval text cannot be empty")
    report_json, report_markdown, approval_path = _material_stage_paths(config)
    report_html = material_archive_web_report_path(config)
    if (
        not report_json.is_file()
        or not report_markdown.is_file()
        or not report_html.is_file()
    ):
        raise RuntimeError(
            "Stage 1 material archive report is missing; run --prepare-materials "
            "and present the report to the user first"
        )
    try:
        report = json.loads(report_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("Stage 1 material archive report is invalid") from exc
    stage = report.get("stage") if isinstance(report, dict) else None
    if not isinstance(stage, dict):
        raise RuntimeError("Stage 1 material archive report has no gate metadata")
    stored_report_fingerprint = str(stage.get("report_fingerprint") or "")
    if (
        not stored_report_fingerprint
        or stored_report_fingerprint != _material_report_fingerprint(report)
    ):
        raise RuntimeError("Stage 1 material archive report content has changed")
    current_input_fingerprint = material_input_fingerprint(config)
    if str(stage.get("input_fingerprint") or "") != current_input_fingerprint:
        raise RuntimeError(
            "project materials changed after the Stage 1 report; rerun "
            "--prepare-materials and obtain user approval again"
        )
    reuse_review = report.get("reuse_review")
    reuse_review = reuse_review if isinstance(reuse_review, dict) else {}
    decision_required = bool(reuse_review.get("decision_required"))
    policy = str(unchanged_reuse_policy or "").strip()
    if decision_required and policy not in UNCHANGED_REUSE_POLICIES:
        raise RuntimeError(
            "Stage 1 found unchanged material from a frozen baseline; choose "
            "--unchanged-reuse-policy reuse_unchanged or reanalyze_all"
        )
    if not decision_required:
        policy = "not_applicable"
    approval = {
        "schema_version": 1,
        "stage": "material_archive",
        "status": "approved",
        "approved_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "approval_text": note,
        "input_fingerprint": current_input_fingerprint,
        "report_fingerprint": stored_report_fingerprint,
        "report_json": report_json.relative_to(config.project_dir).as_posix(),
        "report_json_sha256": _sha256(report_json),
        "report_markdown": report_markdown.relative_to(config.project_dir).as_posix(),
        "report_markdown_sha256": _sha256(report_markdown),
        "report_html": report_html.relative_to(config.project_dir).as_posix(),
        "report_html_sha256": _sha256(report_html),
        "unchanged_reuse_policy": policy,
        "reuse_candidate_fingerprint": str(
            reuse_review.get("candidate_fingerprint") or ""
        ),
        "unchanged_valves": list(reuse_review.get("unchanged_valves") or []),
    }
    approval_path.parent.mkdir(parents=True, exist_ok=True)
    approval_path.write_text(
        json.dumps(approval, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return approval_path


def require_material_archive_approval(config: ProjectConfig) -> dict[str, Any]:
    """Refuse Stage 2+ work unless current material inputs were approved."""
    report_json, report_markdown, approval_path = _material_stage_paths(config)
    report_html = material_archive_web_report_path(config)
    if not approval_path.is_file():
        raise RuntimeError(
            "Stage 1 material archive approval is required: run "
            "--prepare-materials, present material-archive-report.md to the "
            "user, wait for explicit approval, then run --approve-materials "
            "\"<user approval>\""
        )
    try:
        approval = json.loads(approval_path.read_text(encoding="utf-8"))
        report = json.loads(report_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("Stage 1 material archive approval is invalid") from exc
    if approval.get("status") != "approved":
        raise RuntimeError("Stage 1 material archive has not been approved")
    if not report_markdown.is_file() or not report_html.is_file():
        raise RuntimeError("approved Stage 1 material archive report is missing")
    stage = report.get("stage") if isinstance(report, dict) else None
    if not isinstance(stage, dict):
        raise RuntimeError("approved Stage 1 material archive report is invalid")
    reuse_review = report.get("reuse_review")
    reuse_review = reuse_review if isinstance(reuse_review, dict) else {}
    report_fingerprint = _material_report_fingerprint(report)
    current_input_fingerprint = material_input_fingerprint(config)
    if (
        str(stage.get("report_fingerprint") or "") != report_fingerprint
        or str(approval.get("report_fingerprint") or "") != report_fingerprint
        or str(approval.get("input_fingerprint") or "")
        != current_input_fingerprint
        or str(stage.get("input_fingerprint") or "")
        != current_input_fingerprint
        or str(approval.get("report_markdown_sha256") or "")
        != _sha256(report_markdown)
        or str(approval.get("report_html_sha256") or "")
        != _sha256(report_html)
        or str(approval.get("reuse_candidate_fingerprint") or "")
        != str(reuse_review.get("candidate_fingerprint") or "")
    ):
        raise RuntimeError(
            "Stage 1 material archive approval is stale; project materials or "
            "the approved report changed. Rerun --prepare-materials and obtain "
            "user approval again"
        )
    if reuse_review.get("decision_required") and approval.get(
        "unchanged_reuse_policy"
    ) not in UNCHANGED_REUSE_POLICIES:
        raise RuntimeError(
            "Stage 1 unchanged-material reuse decision is missing; rerun "
            "--approve-materials with --unchanged-reuse-policy"
        )
    return approval


def _parameter_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Stage 2 {label} is invalid: {path}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"Stage 2 {label} must be a JSON object: {path}")
    return value


def _parameter_stage_sources(config: ProjectConfig) -> dict[str, Any]:
    """Read the existing extraction artifacts without rerunning extraction."""
    output = config.project_dir / "work/evidence-extraction"
    index_path = output / "project-index.json"
    if not index_path.is_file():
        raise RuntimeError(
            "Stage 2 evidence extraction output is missing; run --extract-materials first"
        )
    index = _parameter_json(index_path, "evidence index")
    from .evidence_extraction import EXTRACTOR_VERSION

    if index.get("extractor_version") != EXTRACTOR_VERSION:
        raise RuntimeError(
            "Stage 2 evidence extraction is stale; rerun --extract-materials "
            f"with extractor_version {EXTRACTOR_VERSION}"
        )
    raw_valves = index.get("valves")
    if not isinstance(raw_valves, list):
        raise RuntimeError("Stage 2 evidence index valves must be a list")
    valves: list[dict[str, Any]] = []
    for entry in raw_valves:
        if not isinstance(entry, dict):
            raise RuntimeError("Stage 2 evidence index valve entry must be an object")
        relative = str(entry.get("path") or "")
        path = (config.project_dir / relative).resolve()
        try:
            path.relative_to(config.project_dir.resolve())
        except ValueError as exc:
            raise RuntimeError("Stage 2 evidence valve path escapes project") from exc
        if not path.is_file():
            raise RuntimeError(f"Stage 2 evidence valve file is missing: {relative}")
        valve = _parameter_json(path, f"evidence valve {entry.get('valve_tag', '')}")
        if not str(valve.get("valve_tag") or ""):
            raise RuntimeError(f"Stage 2 evidence valve has no valve_tag: {relative}")
        valves.append(valve)
    supplementary_path = output / "supplementary-materials.json"
    supplementary = (
        _parameter_json(supplementary_path, "supplementary materials")
        if supplementary_path.is_file()
        else {"schema_version": 1, "materials": []}
    )
    review_path = output / "review-queue.json"
    review_queue = (
        _parameter_json(review_path, "review queue")
        if review_path.is_file()
        else {"schema_version": 1, "items": []}
    )
    review_results_path = output / "codex-review-results.json"
    review_results = (
        _parameter_json(review_results_path, "Codex review results")
        if review_results_path.is_file()
        else {"schema_version": 1, "reviews": []}
    )
    override_path = config.project_dir / "experience/extraction_overrides.json"
    overrides = (
        _parameter_json(override_path, "extraction overrides")
        if override_path.is_file()
        else {}
    )
    replacement_path = (
        config.project_dir / "experience/positioner_replacement_status.json"
    )
    positioner_replacements = (
        _parameter_json(replacement_path, "positioner replacement status")
        if replacement_path.is_file()
        else {"schema_version": 1, "valves": {}}
    )
    if not isinstance(positioner_replacements.get("valves"), dict):
        raise RuntimeError("positioner replacement status valves must be an object")
    cache_paths: list[Path] = []
    ocr_dir = config.project_dir / "work/ocr"
    if ocr_dir.is_dir():
        cache_paths.extend(
            path
            for path in ocr_dir.rglob("*.json")
            if path.is_file() and not path.is_symlink()
        )
    ocr_cache: list[dict[str, Any]] = []
    for path in sorted(
        cache_paths,
        key=lambda item: item.relative_to(config.project_dir).as_posix(),
    ):
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            semantic_sha256 = _sha256(path)
        else:
            semantic_sha256 = _payload_sha256(_strip_dynamic_keys(parsed))
        ocr_cache.append(
            {
                "path": path.relative_to(config.project_dir).as_posix(),
                "semantic_sha256": semantic_sha256,
            }
        )
    return {
        "index": index,
        "valves": sorted(valves, key=lambda item: str(item.get("valve_tag") or "")),
        "supplementary": supplementary,
        "review_queue": review_queue,
        "review_results": review_results,
        "overrides": overrides,
        "positioner_replacements": positioner_replacements,
        "ocr_cache": ocr_cache,
    }


def _strip_dynamic_keys(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _strip_dynamic_keys(item)
            for key, item in value.items()
            if key not in {"created_at", "generated_at"}
        }
    if isinstance(value, list):
        return [_strip_dynamic_keys(item) for item in value]
    return value


def _parameter_input_fingerprint_from(
    config: ProjectConfig,
    stage_one: dict[str, Any],
    sources: dict[str, Any],
) -> str:
    from .document_ocr_cache import SCHEMA_VERSION as OCR_CACHE_SCHEMA_VERSION

    payload = {
        "schema_version": 1,
        "ocr_cache_schema_version": OCR_CACHE_SCHEMA_VERSION,
        "stage_one": {
            "input_fingerprint": stage_one.get("input_fingerprint"),
            "report_fingerprint": stage_one.get("report_fingerprint"),
        },
        "extraction_config": {
            "report_period_key": config.report_period_key,
            "features": dict(sorted(config.features.items())),
            "metadata": _strip_dynamic_keys(config.metadata),
        },
        "extraction": _strip_dynamic_keys(sources),
    }
    return _payload_sha256(payload)


def parameter_extraction_input_fingerprint(config: ProjectConfig) -> str:
    """Fingerprint Stage 2 semantic inputs without volatile timestamps."""
    stage_one = require_material_archive_approval(config)
    sources = _parameter_stage_sources(config)
    return _parameter_input_fingerprint_from(config, stage_one, sources)


def _parameter_value(value: Any) -> str:
    if value is None or value == "":
        return "无数据"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return str(value)


def _parameter_field_snapshot(field_name: str, field: Any) -> dict[str, Any]:
    if not isinstance(field, dict):
        field = {"status": "missing", "selected": None, "candidates": []}
    status = str(field.get("status") or "missing")
    selected = field.get("selected")
    selected = selected if isinstance(selected, dict) else {}
    normalized = selected.get("normalized_value")
    raw = selected.get("raw_value")
    if status == "missing":
        display_status = "no_data"
    elif status in {"conflict", "review_required", "source_present_parser_missing"}:
        display_status = "to_confirm" if status != "conflict" else "conflict"
    else:
        display_status = "extracted"
    source = selected.get("source")
    source = source if isinstance(source, dict) else {}
    candidate_values = [
        candidate.get("normalized_value")
        for candidate in field.get("candidates", [])
        if isinstance(candidate, dict)
    ]
    display_value = _parameter_value(normalized if normalized is not None else raw)
    if status == "missing":
        display_value = "无数据"
    if field_name == "status.continuous_diagnostics":
        continuous_status = (
            str(normalized.get("status") or "")
            if isinstance(normalized, dict)
            else str(normalized or "")
        ).strip().casefold()
        if continuous_status not in {"present", "available", "有数据"}:
            display_value = "无数据"
    if status == "conflict":
        unique_candidates = list(
            dict.fromkeys(_parameter_value(value) for value in candidate_values)
        )
        display_value = "候选：" + " / ".join(unique_candidates)
    return {
        "field": field_name,
        "display_label": PARAMETER_MAIN_LABELS.get(field_name, field_name),
        "status": status,
        "display_status": display_status,
        "display_value": display_value,
        "raw_value": raw,
        "normalized_value": normalized,
        "raw_unit": str(selected.get("raw_unit") or ""),
        "normalized_unit": str(selected.get("normalized_unit") or ""),
        "confidence": selected.get("confidence"),
        "normalization_rule": str(selected.get("normalization_rule") or ""),
        "source": {
            "path": str(source.get("path") or ""),
            "sha256": str(source.get("sha256") or ""),
            "page": source.get("page"),
            "record": str(source.get("record") or ""),
            "method": str(source.get("method") or ""),
        },
        "candidate_values": candidate_values,
        "candidate_sources": [
            str((candidate.get("source") or {}).get("path") or "")
            if isinstance(candidate.get("source") or {}, dict)
            else ""
            for candidate in field.get("candidates", [])
            if isinstance(candidate, dict)
        ],
        "conflict": bool(field.get("conflict")),
    }


def _parameter_field_names(valve: dict[str, Any]) -> list[str]:
    try:
        from .evidence_extraction import EXPECTED_FIELDS

        names = set(EXPECTED_FIELDS)
    except ImportError:
        names = set()
    names.update(
        str(key)
        for key in (valve.get("fields") or {})
        if str(key).strip()
    )
    return sorted(names)


def _parameter_report_valve(valve: dict[str, Any]) -> dict[str, Any]:
    fields = valve.get("fields") if isinstance(valve.get("fields"), dict) else {}
    all_snapshots = {
        name: _parameter_field_snapshot(name, fields.get(name))
        for name in _parameter_field_names(valve)
    }
    snapshots = {
        name: all_snapshots.get(name, _parameter_field_snapshot(name, None))
        for name, _label in PARAMETER_MAIN_FIELDS
    }
    extra_snapshots = {
        name: item
        for name, item in all_snapshots.items()
        if name not in snapshots
    }
    missing = [name for name, item in snapshots.items() if item["status"] == "missing"]
    conflicts = list(valve.get("conflicts") or []) if isinstance(valve.get("conflicts"), list) else []
    conflicts.extend(
        {"type": "source_value_conflict", "field": name}
        for name, item in snapshots.items()
        if item["conflict"] and not any(item2.get("field") == name for item2 in conflicts if isinstance(item2, dict))
    )
    review_items = valve.get("review_items") if isinstance(valve.get("review_items"), list) else []
    extracted_count = sum(
        item["status"] not in {"missing", "conflict", "review_required", "source_present_parser_missing"}
        for item in snapshots.values()
    )
    reports = valve.get("reports") if isinstance(valve.get("reports"), dict) else {}
    continuous_field = snapshots["status.continuous_diagnostics"]
    continuous = continuous_field.get("normalized_value")
    continuous_status = (
        str(continuous.get("status") or "")
        if isinstance(continuous, dict)
        else str(continuous or "")
    ).strip().casefold()
    if continuous_field.get("status") == "conflict":
        continuous_display = "冲突"
    elif continuous_status not in {"present", "available", "有数据"}:
        continuous_display = "无数据"
    else:
        continuous_display = "有数据"
    return {
        "valve_tag": str(valve.get("valve_tag") or ""),
        "status": str(valve.get("status") or "extracted"),
        "fields": snapshots,
        "extra_fields": extra_snapshots,
        "missing_fields": missing,
        "parser_missing_fields": list(valve.get("parser_missing_fields") or []),
        "conflicts": conflicts,
        "review_items": review_items,
        "reports": reports,
        "source_availability": {"Continuous Diagnostics": continuous_display},
        "tests": valve.get("tests") or [],
        "curves": valve.get("curves") or [],
        "report_pages": valve.get("report_pages") or [],
        "full_evidence": valve,
        "extracted_field_count": extracted_count,
    }


def _parameter_reuse_projection(valve: dict[str, Any]) -> dict[str, Any]:
    report = _parameter_report_valve(valve)
    fields = {**report["fields"], **report["extra_fields"]}
    return {
        "fields": {
            name: {
                "status": item.get("status"),
                "normalized_value": item.get("normalized_value"),
                "raw_value": item.get("raw_value"),
                "normalized_unit": item.get("normalized_unit"),
                "conflict": item.get("conflict"),
                "candidate_values": item.get("candidate_values") or [],
            }
            for name, item in sorted(fields.items())
        },
        "report_pages": [
            {
                "physical_identity": item.get("physical_identity"),
                "roles": sorted(item.get("roles") or []),
            }
            for item in report.get("report_pages") or []
            if isinstance(item, dict)
        ],
    }


def _parameter_reuse_review(
    config: ProjectConfig,
    stage_one: dict[str, Any],
    current_valves: list[dict[str, Any]],
) -> dict[str, Any]:
    policy = str(stage_one.get("unchanged_reuse_policy") or "not_applicable")
    candidate_tags = [
        str(item.get("valve_tag") or "")
        for item in stage_one.get("unchanged_valves") or []
        if isinstance(item, dict) and str(item.get("valve_tag") or "")
    ]
    run_root = config.formal_diagnosis_run_dir
    if not candidate_tags or run_root is None:
        return {
            "policy": policy,
            "status": "not_applicable",
            "parameter_unchanged_valves": [],
            "parameter_changed_valves": [],
        }
    current_by_tag = {
        str(item.get("valve_tag") or ""): item for item in current_valves
    }
    unchanged: list[str] = []
    changed: list[dict[str, str]] = []
    for tag in candidate_tags:
        current = current_by_tag.get(tag)
        bundle = _json_object(run_root / tag / "01_evidence_bundle.json")
        structured = (
            (bundle.get("evidence") or {}).get("structured_evidence")
            if isinstance(bundle, dict)
            else None
        )
        baseline = (
            structured.get("material_extraction")
            if isinstance(structured, dict)
            else None
        )
        if not isinstance(current, dict) or not isinstance(baseline, dict):
            changed.append({"valve_tag": tag, "reason": "缺少当前或基线参数事实"})
            continue
        if _parameter_reuse_projection(current) == _parameter_reuse_projection(
            baseline
        ):
            unchanged.append(tag)
        else:
            changed.append({"valve_tag": tag, "reason": "参数值、状态、单位、冲突或技术页绑定发生变化"})
    return {
        "policy": policy,
        "status": "confirmed",
        "parameter_unchanged_valves": unchanged,
        "parameter_changed_valves": changed,
        "reuse_scope": (
            "允许复用参数事实；客观观察和整阀结论仍受各自严格哈希门禁约束。"
            if policy == "reuse_unchanged"
            else "已选择全部重新处理，不继承旧观察或整阀结论。"
        ),
    }


def parameter_extraction_report_fingerprint(payload: dict[str, Any]) -> str:
    """Return a stable fingerprint for a Stage 2 report snapshot."""
    stable = _strip_dynamic_keys(payload)
    if isinstance(stable, dict):
        stage = stable.get("stage")
        if isinstance(stage, dict):
            stage.pop("report_fingerprint", None)
    return _payload_sha256(stable)


def _parameter_display_text(field: dict[str, Any]) -> str:
    value = str(field.get("display_value") or "无数据")
    unit = str(field.get("normalized_unit") or "").strip()
    if unit and unit.casefold() not in value.casefold():
        return f"{value} {unit}"
    return value


def _parameter_markdown_text(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _parameter_compact_value(valve: dict[str, Any], field_name: str) -> str:
    field = (valve.get("fields") or {}).get(field_name)
    if not isinstance(field, dict) or field.get("display_status") == "no_data":
        return "-"
    value = _parameter_display_text(field)
    if value == "无数据":
        return "-"
    if field.get("display_status") == "conflict":
        return "冲突"
    if field.get("display_status") == "to_confirm":
        value += "（待确认）"
    return _parameter_markdown_text(value)


def _parameter_compact_group(
    valve: dict[str, Any],
    fields: tuple[tuple[str, str], ...],
) -> str:
    values = [
        f"{label}：{_parameter_compact_value(valve, field_name)}"
        for field_name, label in fields
    ]
    return "<br>".join(values)


def _parameter_non_success_tests(valve: dict[str, Any]) -> str:
    field = (valve.get("fields") or {}).get("status.task_execution") or {}
    tasks = field.get("normalized_value")
    if not isinstance(tasks, list):
        return "测试状态：未提供任务汇总"
    problem_statuses = {
        "failed",
        "cancelled",
        "aborted",
        "incomplete",
        "not completed",
    }
    problems: list[str] = []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        status = str(task.get("status") or task.get("result") or "").strip()
        if status.casefold() not in problem_statuses:
            continue
        name = str(
            task.get("task_name")
            or task.get("test_name")
            or task.get("test_key")
            or "未命名测试"
        ).strip()
        item = f"{name} {status}"
        if item not in problems:
            problems.append(item)
    return "测试异常：" + ("；".join(problems) if problems else "无")


def _parameter_fault_text(valve: dict[str, Any]) -> str:
    fields = valve.get("fields") or {}

    def _faults(field_name: str) -> str:
        field = fields.get(field_name) or {}
        value = field.get("normalized_value")
        if isinstance(value, list):
            return "、".join(str(item) for item in value) if value else "无"
        if value in (None, ""):
            return "-"
        return _parameter_markdown_text(value)

    return (
        f"当前故障：{_faults('status.active_faults')}<br>"
        f"历史故障：{_faults('status.historical_faults')}"
    )


def _parameter_review_groups(payload: dict[str, Any]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for valve in payload.get("valves") or []:
        for item in valve.get("review_items") or []:
            if not isinstance(item, dict):
                continue
            field_name = str(item.get("field") or "待确认字段")
            if field_name.startswith("actuator.model"):
                topic = "执行机构型号"
            else:
                topic = PARAMETER_MAIN_LABELS.get(field_name, field_name)
            group = grouped.setdefault(
                topic,
                {"topic": topic, "valve_tags": set(), "candidates": set()},
            )
            group["valve_tags"].add(str(valve.get("valve_tag") or "-"))
            candidate = item.get("candidate_value")
            if candidate not in (None, ""):
                group["candidates"].add(_parameter_value(candidate))
    return [
        {
            "topic": item["topic"],
            "valve_tags": sorted(item["valve_tags"]),
            "candidates": sorted(item["candidates"]),
        }
        for item in sorted(grouped.values(), key=lambda value: value["topic"])
    ]


def _parameter_conflict_groups(payload: dict[str, Any]) -> list[dict[str, Any]]:
    grouped: dict[str, set[str]] = {}
    for valve in payload.get("valves") or []:
        for conflict in valve.get("conflicts") or []:
            if not isinstance(conflict, dict):
                continue
            field_name = str(conflict.get("field") or "未命名字段")
            label = PARAMETER_MAIN_LABELS.get(field_name, field_name)
            grouped.setdefault(label, set()).add(str(valve.get("valve_tag") or "-"))
    return [
        {"topic": topic, "valve_tags": sorted(tags)}
        for topic, tags in sorted(grouped.items())
    ]


def _parameter_context_gap_groups(payload: dict[str, Any]) -> list[dict[str, Any]]:
    context_fields = (
        ("context.description", "阀门描述"),
        ("context.unit", "装置"),
        ("context.failure_consequence", "失效后果"),
        ("context.bypass", "有无副线"),
        ("context.site_note", "现场备注"),
    )
    grouped: dict[tuple[str, ...], list[str]] = {}
    for valve in payload.get("valves") or []:
        missing = set(valve.get("missing_fields") or [])
        labels = tuple(label for name, label in context_fields if name in missing)
        if labels:
            grouped.setdefault(labels, []).append(str(valve.get("valve_tag") or "-"))
    return [
        {"labels": list(labels), "valve_tags": sorted(tags)}
        for labels, tags in sorted(grouped.items())
    ]


def _parameter_report_markdown(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    has_uncertainty = bool(
        summary["conflict_count"] or summary["review_required_count"]
    )
    approval_meaning = (
        "接受本报告列出的资料缺口和未解决项，并将其带入后续证据。"
        if has_uncertainty
        else "接受本报告列出的明确资料缺口；当前没有待确认项或冲突。"
    )
    approval_request = (
        "如接受以上缺口和未解决项，请回复“批准第二阶段，进入第三阶段”。"
        if has_uncertainty
        else "如接受以上明确资料缺口，请回复“批准第二阶段，进入第三阶段”。"
    )
    lines = [
        "# 第二阶段：资料参数审核报告",
        "",
        f"- 项目：{payload['project']['name']}",
        f"- 客户：{payload['project']['customer']}",
        f"- 阀门数：{summary['valve_count']}",
        "- 范围：只审核资料参数；不包含曲线判读、评级、诊断或检修建议。",
        "",
        "## 审批摘要",
        "",
        "- 流程结论：资料已完成提取，可以知情审批；批准后进入第三阶段。",
        f"- 参数概况：已取得 {summary['extracted_count']} 项；明确无数据 {summary['no_data_count']} 项；"
        f"冲突 {summary['conflict_count']} 项；待确认 {summary['review_required_count']} 项。",
        f"- 批准含义：{approval_meaning}",
        "",
        "## 与上一冻结版本的参数比较",
        "",
    ]
    reuse_review = payload.get("reuse_review")
    reuse_review = reuse_review if isinstance(reuse_review, dict) else {}
    unchanged = list(reuse_review.get("parameter_unchanged_valves") or [])
    changed = [
        str(item.get("valve_tag") or "")
        for item in reuse_review.get("parameter_changed_valves") or []
        if isinstance(item, dict)
    ]
    if reuse_review.get("status") == "confirmed":
        lines.extend(
            [
                f"- 第一阶段选择：`{reuse_review.get('policy')}`。",
                f"- 参数完全未变化：{len(unchanged)} 台（{'、'.join(unchanged) if unchanged else '无'}）。",
                f"- 参数或绑定发生变化：{len(changed)} 台（{'、'.join(changed) if changed else '无'}）。",
                f"- 复用边界：{reuse_review.get('reuse_scope')}",
            ]
        )
    else:
        lines.append("- 没有上一冻结版本的未变化候选，本阶段无需执行参数复用比较。")
    lines.extend(
        [
            "",
        "## 逐阀关键参数",
        "",
        "| 阀门 | 阀门规格 | 定位器 | 执行机构 | 关键测试数据 |",
        "| --- | --- | --- | --- | --- |",
        ]
    )
    for valve in payload["valves"]:
        specification = _parameter_compact_group(
            valve,
            (
                ("specification.model_no", "型号"),
                ("specification.valve_size", "口径"),
                ("specification.pressure_class", "压力等级"),
                ("specification.rated_cv", "Cv"),
                ("specification.leakage_class", "泄漏等级"),
            ),
        )
        positioner = _parameter_compact_group(
            valve,
            (
                ("positioner.model", "规格书型号"),
                ("identity.serial_number", "序列号"),
                ("positioner.diagnostics_level", "诊断等级"),
            ),
        )
        replacement = valve.get("positioner_replacement") or {}
        if replacement.get("status") == "replaced":
            positioner += "<br>更换状态：" + _parameter_markdown_text(
                replacement.get("display") or "已更换"
            )
        actuator = _parameter_compact_group(
            valve,
            (
                ("actuator.model_display", "型号"),
                ("actuator.type", "类型"),
                ("actuator.size", "尺寸"),
                ("actuator.supply_pressure", "供气"),
            ),
        )
        metrics = _parameter_compact_group(
            valve,
            (
                ("metrics.average_error", "平均误差"),
                ("metrics.mean_friction", "平均摩擦力"),
                ("metrics.run_ramp.hysteresis.max", "最大回差"),
                ("metrics.run_ramp.dead_band.max", "最大死区"),
                ("metrics.run_ramp.linearity.max", "最大线性度"),
            ),
        )
        lines.extend(
            [
                f"| {valve['valve_tag']} | {specification} | {positioner} | "
                f"{actuator} | {metrics} |"
            ]
        )

    lines.extend(
        [
            "",
            "## 逐阀审核状态",
            "",
            "| 阀门 | 连续诊断数据 | 测试与故障 | 无数据 | 冲突 | 待确认 |",
            "| --- | --- | --- | ---: | ---: | ---: |",
        ]
    )
    for valve in payload["valves"]:
        test_and_fault = (
            _parameter_non_success_tests(valve)
            + "<br>"
            + _parameter_fault_text(valve)
        )
        lines.append(
            f"| {valve['valve_tag']} | "
            f"{valve.get('source_availability', {}).get('Continuous Diagnostics', '无数据')} | "
            f"{test_and_fault} | {len(valve['missing_fields'])} | "
            f"{len(valve['conflicts'])} | {len(valve['review_items'])} |"
        )

    lines.extend(["", "## 需要你决定", ""])
    review_groups = _parameter_review_groups(payload)
    conflict_groups = _parameter_conflict_groups(payload)
    if review_groups:
        lines.append(f"待确认 {summary['review_required_count']} 项：")
        lines.append("")
        for group in review_groups:
            candidates = "、".join(group["candidates"]) or "无明确候选值"
            tags = "、".join(group["valve_tags"])
            lines.append(
                f"- **{group['topic']}**（{tags}）：候选值 {candidates}；确认前按不确定项处理。"
            )
    if conflict_groups:
        if review_groups:
            lines.append("")
        lines.append(f"冲突 {summary['conflict_count']} 项：")
        lines.append("")
        for group in conflict_groups:
            tags = "、".join(group["valve_tags"])
            lines.append(
                f"- **{group['topic']}**（{tags}）：存在多个候选表示；后续不得静默选值。"
            )
    if not review_groups and not conflict_groups:
        lines.append("没有需要决定的待确认项或冲突。")

    lines.extend(["", "## 已知缺口（不阻断）", ""])
    context_groups = _parameter_context_gap_groups(payload)
    for group in context_groups:
        lines.append(
            f"- **现场信息**（{'、'.join(group['valve_tags'])}）："
            f"缺少{'、'.join(group['labels'])}。"
        )
    context_gap_count = sum(
        len(group["labels"]) * len(group["valve_tags"])
        for group in context_groups
    )
    other_gap_count = max(0, summary["no_data_count"] - context_gap_count)
    if other_gap_count:
        lines.append(
            f"- 其他无数据共 {other_gap_count} 项；逐阀数量见审核状态，完整字段留在 JSON。"
        )
    if not context_groups and not other_gap_count:
        lines.append("没有无数据项。")

    lines.extend(["", "## 内部明细", ""])
    lines.extend(
        [
            "- 完整主参数矩阵、原始值、规范值、单位、来源、置信度和候选值："
            "`work/stage-gates/parameter-extraction-report.json`。",
            "- 完整逐阀事实：`work/evidence-extraction/valves/*.json`。",
            "- 补充资料及采用原因：`work/evidence-extraction/supplementary-materials.md`。",
        ]
    )
    lines.extend(
        [
            "",
            "## 下一步",
            "",
            approval_request,
            "",
        ]
    )
    return "\n".join(lines)


def _parameter_html_value(valve: dict[str, Any], field_name: str) -> str:
    field = (valve.get("fields") or {}).get(field_name)
    if not isinstance(field, dict) or field.get("display_status") == "no_data":
        return "—"
    if field.get("display_status") == "conflict":
        return '<span class="flag conflict">冲突</span>'
    value = html.escape(_parameter_display_text(field))
    if field.get("display_status") == "to_confirm":
        return f'{value} <span class="flag review">待确认</span>'
    return value


def _parameter_html_group(
    valve: dict[str, Any],
    fields: tuple[tuple[str, str], ...],
) -> str:
    return "".join(
        f"<span><small>{html.escape(label)}</small>{_parameter_html_value(valve, name)}</span>"
        for name, label in fields
    )


def _render_parameter_stage_gate_html(payload: dict[str, Any]) -> str:
    """Render a concise Stage 2 table containing report-facing parameters only."""
    project = payload.get("project") or {}
    valves = [item for item in payload.get("valves") or [] if isinstance(item, dict)]
    specification_fields = (
        ("specification.model_no", "型号"),
        ("specification.valve_size", "口径"),
        ("specification.pressure_class", "等级"),
        ("actuator.fail_position", "失效位"),
        ("specification.rated_cv", "Cv"),
    )
    positioner_fields = (
        ("positioner.model", "规格书型号"),
        ("identity.serial_number", "序列号"),
        ("positioner.diagnostics_level", "诊断等级"),
    )
    actuator_fields = (
        ("actuator.model_display", "型号"),
        ("actuator.type", "类型"),
        ("actuator.supply_pressure", "供气"),
        ("actuator.bench_range", "弹簧范围"),
    )
    metric_fields = (
        ("metrics.average_error", "平均误差"),
        ("metrics.mean_friction", "平均摩擦力"),
        ("metrics.run_ramp.hysteresis.max", "最大回差"),
        ("metrics.run_ramp.dead_band.max", "最大死区"),
        ("metrics.run_ramp.linearity.max", "最大线性度"),
    )
    visible_fields = tuple(
        name
        for group in (
            specification_fields,
            positioner_fields,
            actuator_fields,
            metric_fields,
        )
        for name, _label in group
    )

    rows: list[str] = []
    anomaly_count = 0
    incomplete_count = 0
    report_missing_count = 0
    report_conflict_count = 0
    for valve in valves:
        tag = str(valve.get("valve_tag") or "-")
        fields = valve.get("fields") or {}
        missing_count = sum(
            not isinstance(fields.get(name), dict)
            or fields[name].get("display_status") == "no_data"
            for name in visible_fields
        )
        conflict_count = sum(
            isinstance(fields.get(name), dict)
            and fields[name].get("display_status") == "conflict"
            for name in visible_fields
        )
        report_missing_count += missing_count
        report_conflict_count += conflict_count
        has_gap = bool(missing_count or conflict_count)
        incomplete_count += int(has_gap)
        test_text = _parameter_non_success_tests(valve)
        has_anomaly = test_text.startswith("测试异常：") and not test_text.endswith(
            "无"
        )
        anomaly_count += int(has_anomaly)
        continuous = html.escape(
            str(
                (valve.get("source_availability") or {}).get(
                    "Continuous Diagnostics", "无数据"
                )
            )
        )
        active_faults = (fields.get("status.active_faults") or {}).get(
            "normalized_value"
        )
        if isinstance(active_faults, list):
            fault_text = "、".join(str(item) for item in active_faults) or "未发现"
        elif active_faults in (None, ""):
            fault_text = "未发现"
        else:
            fault_text = str(active_faults)
        test_display = (
            test_text.removeprefix("测试异常：")
            if has_anomaly
            else "无异常"
            if test_text == "测试异常：无"
            else test_text.removeprefix("测试状态：")
        )
        status_cell = (
            f'<span><small>连续诊断数据</small>{continuous}</span>'
            f'<span><small>测试结果</small><b class="{"alert" if has_anomaly else ""}">'
            f'{html.escape(test_display)}</b></span>'
            f'<span><small>当前活动故障</small>{html.escape(fault_text)}</span>'
        )
        replacement = valve.get("positioner_replacement") or {}
        positioner_cell = _parameter_html_group(valve, positioner_fields)
        if replacement.get("status") == "replaced":
            replacement_text = html.escape(
                str(replacement.get("display") or "已更换")
            )
            positioner_cell += (
                '<span><small>更换状态</small>'
                f'<b class="replacement">{replacement_text}</b></span>'
            )
        rows.append(
            f'<tr data-tag="{html.escape(tag.casefold())}" '
            f'data-anomaly="{int(has_anomaly)}" data-gap="{int(has_gap)}">'
            f'<td data-label="阀门"><strong>{html.escape(tag)}</strong>'
            f'<i>{"参数待核对" if has_gap else "参数已取得"}</i></td>'
            f'<td data-label="阀门规格"><div class="stack">'
            f'{_parameter_html_group(valve, specification_fields)}</div></td>'
            f'<td data-label="定位器"><div class="stack">'
            f'{positioner_cell}</div></td>'
            f'<td data-label="执行机构"><div class="stack">'
            f'{_parameter_html_group(valve, actuator_fields)}</div></td>'
            f'<td data-label="关键检测指标"><div class="stack">'
            f'{_parameter_html_group(valve, metric_fields)}</div></td>'
            f'<td data-label="数据与状态"><div class="stack">{status_cell}</div></td>'
            f'<td data-label="报告字段缺口"><span class="issue">'
            f'缺 {missing_count} / 冲突 {conflict_count}</span></td></tr>'
        )

    project_name = html.escape(str(project.get("name") or "离线诊断项目"))
    customer = html.escape(str(project.get("customer") or ""))
    generated = html.escape(str(payload.get("generated_at") or ""))
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{project_name}｜第二阶段参数审核报告</title>
<style>
:root{{--ink:#111612;--paper:#f1efe7;--acid:#c8ff36;--teal:#0a6b5e;--orange:#ff6b2c;--line:rgba(17,22,18,.17);--muted:#687068}}*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;background:var(--paper);color:var(--ink);font-family:"Noto Sans SC","Microsoft YaHei",sans-serif}}button,input{{font:inherit}}header{{position:sticky;top:0;z-index:9;height:62px;padding:0 clamp(18px,5vw,72px);display:flex;align-items:center;justify-content:space-between;background:rgba(17,22,18,.95);color:#fff;backdrop-filter:blur(14px)}}.brand{{display:flex;align-items:center;gap:14px;font-weight:800}}.mark{{display:grid;place-items:center;width:44px;height:29px;background:var(--acid);color:var(--ink);font:900 12px monospace;transform:skew(-8deg)}}
.hero{{padding:70px clamp(18px,5vw,72px) 58px;background:var(--ink);color:#fff}}.kicker,.eyebrow{{font:700 11px monospace;letter-spacing:.16em;color:var(--acid)}}h1{{margin:34px 0 26px;max-width:980px;font:900 clamp(43px,6vw,80px)/1.06 "Noto Serif SC","Songti SC",serif;letter-spacing:-.045em}}h1 em{{color:var(--acid);font-style:normal}}.hero p{{max-width:820px;color:#ffffffaa;line-height:1.8}}.metrics{{display:grid;grid-template-columns:repeat(4,1fr);padding:0 clamp(18px,5vw,72px);border-bottom:1px solid var(--line)}}.metric{{padding:30px 22px;border-right:1px solid var(--line)}}.metric:first-child{{border-left:1px solid var(--line)}}.metric small{{color:var(--teal);font:700 10px monospace;letter-spacing:.12em}}.metric strong{{display:block;margin:12px 0 5px;font:900 clamp(38px,5vw,62px)/.9 sans-serif}}.metric p{{margin:0;color:var(--muted);font-size:12px}}
section{{padding:76px clamp(18px,5vw,72px)}}.heading{{display:grid;grid-template-columns:1.1fr .9fr;gap:50px;align-items:end;margin-bottom:34px}}h2{{margin:12px 0 0;font:900 clamp(33px,4.5vw,58px)/1.12 "Noto Serif SC","Songti SC",serif;letter-spacing:-.04em}}.heading p{{color:var(--muted);line-height:1.8}}.controls{{display:flex;gap:12px;justify-content:space-between;margin-bottom:18px;flex-wrap:wrap}}.controls input{{min-width:min(360px,100%);padding:12px;border:0;border-bottom:2px solid var(--ink);background:transparent;outline:none}}.filters{{display:flex;gap:8px;flex-wrap:wrap}}.filters button{{padding:10px 14px;border:1px solid var(--line);background:transparent;cursor:pointer}}.filters button.on{{background:var(--ink);color:#fff}}
.table{{overflow:auto;border-top:3px solid var(--ink);background:#ffffff66}}table{{width:100%;min-width:1500px;border-collapse:collapse}}th,td{{padding:14px 12px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}th{{color:var(--muted);font:700 10px monospace;letter-spacing:.04em}}td{{font-size:12px}}td:first-child strong{{display:block;font:900 13px monospace;white-space:nowrap}}td:first-child i{{display:block;margin-top:8px;color:var(--muted);font-style:normal;font-size:10px}}.stack{{min-width:180px}}.stack>span{{display:grid;grid-template-columns:72px 1fr;gap:9px;padding:4px 0;border-bottom:1px dotted var(--line);line-height:1.45}}.stack>span:last-child{{border-bottom:0}}.stack small{{color:var(--muted);font-size:10px}}.stack b{{font-weight:700}}.alert{{color:#a72e00}}.replacement{{width:max-content;padding:3px 7px;color:#8f2b09;background:#ff6b2c20;font:800 10px monospace}}.flag{{display:inline-block!important;width:max-content;padding:3px 6px!important;border:0!important;font:800 10px monospace!important}}.conflict{{color:#a72e00;background:#ff6b2c22}}.review{{color:#6e5a00;background:#d6b40022}}.issue{{display:inline-block;padding:6px 8px;background:#1116120b;font:700 10px monospace;white-space:nowrap}}
.note{{margin-top:16px;padding:14px 16px;border-left:4px solid var(--teal);background:#fff;color:var(--muted);font-size:12px;line-height:1.7}}.approval{{text-align:center;background:#dedbd0}}.approval p{{max-width:760px;margin:20px auto;color:var(--muted);line-height:1.8}}.copy{{padding:16px 22px;border:0;background:var(--ink);color:#fff;font-weight:800;cursor:pointer}}footer{{padding:32px clamp(18px,5vw,72px);background:var(--ink);color:#ffffff77;font:11px monospace}}
@media(max-width:900px){{.heading{{display:block}}.metrics{{grid-template-columns:repeat(2,1fr)}}}}@media(max-width:700px){{header>span:last-child{{display:none}}.hero{{padding:54px 18px 42px}}h1{{font-size:clamp(39px,12vw,55px)}}.metrics{{padding:0 18px}}.metric{{padding:23px 13px}}.metric strong{{font-size:38px}}section{{padding:58px 18px}}.controls input{{width:100%;min-width:0}}.table{{overflow:visible;border-top:0;background:transparent}}table{{min-width:0}}thead{{display:none}}tbody{{display:grid;gap:14px}}tr{{display:grid;padding:0 12px 12px;background:#fff;border:1px solid var(--line);border-top:4px solid var(--teal)}}td{{display:grid;grid-template-columns:92px 1fr;gap:10px;padding:12px 6px}}td:before{{content:attr(data-label);color:var(--muted);font:700 9px monospace}}td:first-child{{margin:0 -12px 2px;padding:14px 16px;background:var(--ink);color:#fff}}td:first-child:before{{color:#ffffff88}}td:first-child i{{color:#ffffff88}}.stack{{min-width:0}}.stack>span{{grid-template-columns:68px 1fr}}}}
</style></head><body><header><div class="brand"><span class="mark">VV3</span><span>{customer} · 参数审核</span></div><span>STAGE 02</span></header><main>
<div class="hero"><span class="kicker">REPORT PARAMETER CHECK / APPROVAL GATE</span><h1>{customer}<br><em>{len(valves)} 台阀门</em><br>报告参数审核</h1><p>只展示后续诊断报告会使用的规格、定位器、执行机构、关键检测指标和测试状态。其他完整提取字段仍保留在内部证据中，不在审批页铺开。本阶段不包含曲线判读、评级、诊断或建议。</p></div>
<div class="metrics"><div class="metric"><small>VALVES</small><strong>{len(valves)}</strong><p>本次报告阀门</p></div><div class="metric"><small>PARAMETER GAPS</small><strong>{report_missing_count}</strong><p>报告字段明确缺失</p></div><div class="metric"><small>FIELD CONFLICTS</small><strong>{report_conflict_count}</strong><p>报告字段有多候选值</p></div><div class="metric"><small>TEST FLAGS</small><strong>{anomaly_count}</strong><p>源报告测试异常</p></div></div>
<section id="ledger"><div class="heading"><div><span class="eyebrow">01 / REPORT FIELD LEDGER</span><h2>逐阀报告参数表</h2></div><p>表格只保留后续报告和诊断会直接使用的字段。可搜索位号，或只查看参数缺口与测试异常。</p></div><div class="controls"><input id="search" placeholder="搜索阀门位号"><div class="filters"><button class="on" data-filter="all">全部 {len(valves)}</button><button data-filter="gap">参数待核对 {incomplete_count}</button><button data-filter="anomaly">测试异常 {anomaly_count}</button></div></div><div class="table"><table><thead><tr><th>阀门</th><th>阀门规格</th><th>定位器</th><th>执行机构</th><th>关键检测指标</th><th>数据与状态</th><th>报告字段缺口</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div><div class="note">“连续诊断数据：有数据”表示原始报告包含该数据模块；“测试结果：无异常”表示未发现失败、取消或未完成的测试；“当前活动故障：未发现”表示检测时没有活动故障。页面未展示的运行计数、温度、任务明细和原始候选值仍保留在完整 JSON 证据中。</div></section>
<section class="approval"><span class="eyebrow">02 / APPROVAL</span><h2>第二阶段等待你审批</h2><p>批准表示你已查看报告会使用的关键参数与测试状态，然后才会进入第三阶段曲线观察和诊断准备。</p><button class="copy" onclick="navigator.clipboard.writeText('批准第二阶段，进入第三阶段');this.textContent='批准语句已复制'">复制批准语句</button></section></main><footer>生成时间 {generated} · 完整证据未删减 · 仅限本地或 Tailscale 内网访问</footer>
<script>const rows=[...document.querySelectorAll('tbody tr')],q=document.querySelector('#search');let mode='all';function apply(){{const s=q.value.trim().toLowerCase();rows.forEach(r=>{{const okText=!s||r.dataset.tag.includes(s);const okMode=mode==='all'||mode==='gap'&&r.dataset.gap==='1'||mode==='anomaly'&&r.dataset.anomaly==='1';r.hidden=!(okText&&okMode)}})}}q.addEventListener('input',apply);document.querySelectorAll('[data-filter]').forEach(b=>b.addEventListener('click',()=>{{mode=b.dataset.filter;document.querySelectorAll('[data-filter]').forEach(x=>x.classList.toggle('on',x===b));apply()}}));</script></body></html>"""


def write_parameter_extraction_report(
    config: ProjectConfig,
) -> ParameterExtractionReport:
    """Freeze a Stage 2 review snapshot from existing extraction artifacts."""
    stage_one = require_material_archive_approval(config)
    sources = _parameter_stage_sources(config)
    input_fingerprint = _parameter_input_fingerprint_from(
        config,
        stage_one,
        sources,
    )
    valves = [_parameter_report_valve(valve) for valve in sources["valves"]]
    replacement_by_tag = (
        (sources.get("positioner_replacements") or {}).get("valves") or {}
    )
    for valve in valves:
        replacement = replacement_by_tag.get(valve["valve_tag"])
        valve["positioner_replacement"] = (
            replacement if isinstance(replacement, dict) else {}
        )
    reuse_review = _parameter_reuse_review(
        config,
        stage_one,
        sources["valves"],
    )
    all_fields = [field for valve in valves for field in valve["fields"].values()]
    review_items = [item for valve in valves for item in valve["review_items"]]
    conflicts = [item for valve in valves for item in valve["conflicts"]]
    attention: list[dict[str, Any]] = []
    for valve in valves:
        for field_name in valve["missing_fields"]:
            attention.append({
                "valve_tag": valve["valve_tag"], "field": field_name,
                "message": "无数据", "impact": "后续对应分析依据不足",
            })
        for conflict in valve["conflicts"]:
            field_name = str(conflict.get("field") or "")
            candidate_values = (
                valve["fields"].get(field_name, {}).get("candidate_values") or []
            )
            attention.append({
                "valve_tag": valve["valve_tag"], "field": field_name,
                "message": "存在多个候选值，需确认："
                + " / ".join(_parameter_value(value) for value in candidate_values),
                "impact": "后续诊断不能静默选值",
            })
        for review_item in valve["review_items"]:
            if not isinstance(review_item, dict):
                continue
            field_name = str(review_item.get("field") or "")
            reason = str(review_item.get("reason") or "待复核")
            candidate = review_item.get("candidate_value")
            message = f"低置信度或字段关系待复核（{reason}）"
            if candidate not in (None, ""):
                message += f"，候选值：{_parameter_value(candidate)}"
            attention.append(
                {
                    "valve_tag": valve["valve_tag"],
                    "field": field_name,
                    "message": message,
                    "impact": "确认前按不确定项处理",
                }
            )
    report_json, report_markdown, _approval = parameter_extraction_report_paths(config)
    payload: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "parameter_extraction_reported",
        "project": {"name": config.project_dir.name, "customer": config.customer},
        "source": {
            "evidence_index": "work/evidence-extraction/project-index.json",
            "input_fingerprint": input_fingerprint,
            "stage_one_approval_fingerprint": stage_one.get("report_fingerprint", ""),
        },
        "summary": {
            "valve_count": len(valves),
            "total_field_count": len(all_fields),
            "extracted_count": sum(item["status"] not in {"missing", "conflict", "review_required", "source_present_parser_missing"} for item in all_fields),
            "no_data_count": sum(item["status"] == "missing" for item in all_fields),
            "missing_count": sum(item["status"] == "missing" for item in all_fields),
            "conflict_count": len(conflicts),
            "review_required_count": len(review_items),
            "low_confidence_unresolved_count": sum(
                str(item.get("reason") or "").startswith(("low_", "ocr_", "source_present"))
                for item in review_items if isinstance(item, dict)
            ),
        },
        "reuse_review": reuse_review,
        "attention": attention,
        "valves": valves,
        "supplementary_materials": sources["supplementary"].get("materials") or [],
        "stage": {
            "name": "parameter_extraction",
            "sequence": 2,
            "status": "awaiting_user_approval",
            "approval_required": True,
            "input_fingerprint": "",
            "report_fingerprint": "",
            "excluded_work": ["objective curve observation", "diagnosis", "rating", "recommendation"],
        },
    }
    payload["stage"]["input_fingerprint"] = payload["source"]["input_fingerprint"]
    payload["stage"]["report_fingerprint"] = parameter_extraction_report_fingerprint(payload)
    report_html = parameter_extraction_web_report_path(config)
    report_json.parent.mkdir(parents=True, exist_ok=True)
    report_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_markdown.write_text(_parameter_report_markdown(payload), encoding="utf-8")
    report_html.write_text(_render_parameter_stage_gate_html(payload), encoding="utf-8")
    return ParameterExtractionReport("parameter_extraction_reported", report_json, report_markdown, payload)


def approve_parameter_extraction(
    config: ProjectConfig,
    *,
    approval_text: str,
) -> Path:
    note = str(approval_text or "").strip()
    if not note:
        raise RuntimeError("parameter extraction approval text cannot be empty")
    report_json, report_markdown, approval_path = parameter_extraction_report_paths(config)
    report_html = parameter_extraction_web_report_path(config)
    if not report_json.is_file() or not report_markdown.is_file() or not report_html.is_file():
        raise RuntimeError("Stage 2 parameter extraction report is missing; run --extract-materials first")
    report = _parameter_json(report_json, "parameter extraction report")
    stage = report.get("stage") if isinstance(report.get("stage"), dict) else None
    if not stage or stage.get("report_fingerprint") != parameter_extraction_report_fingerprint(report):
        raise RuntimeError("Stage 2 parameter extraction report content has changed")
    current_input = parameter_extraction_input_fingerprint(config)
    if stage.get("input_fingerprint") != current_input:
        raise RuntimeError("project evidence changed after the Stage 2 report; rerun --extract-materials")
    approval = {
        "schema_version": 1,
        "stage": "parameter_extraction",
        "status": "approved",
        "approved_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "approval_text": note,
        "input_fingerprint": current_input,
        "report_fingerprint": stage["report_fingerprint"],
        "report_json": report_json.relative_to(config.project_dir).as_posix(),
        "report_json_sha256": _sha256(report_json),
        "report_markdown": report_markdown.relative_to(config.project_dir).as_posix(),
        "report_markdown_sha256": _sha256(report_markdown),
        "report_html": report_html.relative_to(config.project_dir).as_posix(),
        "report_html_sha256": _sha256(report_html),
    }
    approval_path.parent.mkdir(parents=True, exist_ok=True)
    approval_path.write_text(json.dumps(approval, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return approval_path


def require_parameter_extraction_approval(config: ProjectConfig) -> dict[str, Any]:
    report_json, report_markdown, approval_path = parameter_extraction_report_paths(config)
    report_html = parameter_extraction_web_report_path(config)
    if not approval_path.is_file():
        raise RuntimeError("Stage 2 parameter extraction approval is required: present parameter-extraction-report.md and obtain explicit approval")
    approval = _parameter_json(approval_path, "parameter extraction approval")
    report = _parameter_json(report_json, "parameter extraction report")
    if approval.get("status") != "approved":
        raise RuntimeError("Stage 2 parameter extraction has not been approved")
    stage = report.get("stage") if isinstance(report.get("stage"), dict) else None
    stage_one = require_material_archive_approval(config)
    sources = _parameter_stage_sources(config)
    current_input = _parameter_input_fingerprint_from(config, stage_one, sources)
    approved_input = approval.get("input_fingerprint")
    report_input = stage.get("input_fingerprint") if isinstance(stage, dict) else None
    input_matches = approved_input == report_input == current_input
    if not input_matches and approved_input == report_input:
        # Stage 2 reports cover the whole extracted project, while report_period_key
        # can merely select a presentation subset. Reuse the original approval only
        # when every other current semantic input reproduces its exact fingerprint.
        reported_tags = {
            item.get("valve_tag") for item in report.get("valves", [])
            if isinstance(item, dict)
        }
        source_tags = {
            item.get("valve_tag") for item in sources["valves"]
        }
        if reported_tags == source_tags:
            periods = tomllib.loads(config.config_path.read_text(encoding="utf-8")).get("report_periods", {})
            input_matches = any(
                _parameter_input_fingerprint_from(
                    replace(config, report_period_key=key.removesuffix("_label")),
                    stage_one,
                    sources,
                ) == approved_input
                for key in periods
                if key.endswith("_label")
            )
    if (
        not isinstance(stage, dict)
        or stage.get("report_fingerprint") != parameter_extraction_report_fingerprint(report)
        or approval.get("report_fingerprint") != stage.get("report_fingerprint")
        or not input_matches
        or not report_markdown.is_file()
        or approval.get("report_markdown_sha256") != _sha256(report_markdown)
        or not report_html.is_file()
        or approval.get("report_html_sha256") != _sha256(report_html)
    ):
        raise RuntimeError("parameter extraction approval is stale; rerun --extract-materials and obtain approval again")
    return approval


def _raw_inventory(config: ProjectConfig) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    if not config.raw_dir.is_dir():
        return [], []
    loose_files = [
        _record(config.project_dir, path)
        for path in sorted(config.raw_dir.iterdir(), key=lambda item: item.name)
        if path.is_file() and not path.is_symlink()
    ]
    valves: list[dict[str, Any]] = []
    for valve_dir in sorted(
        (
            path
            for path in config.raw_dir.iterdir()
            if path.is_dir() and not path.is_symlink()
        ),
        key=lambda path: path.name,
    ):
        direct_paths = sorted(
            (
                path
                for path in valve_dir.iterdir()
                if path.is_file() and not path.is_symlink()
            ),
            key=lambda path: path.name,
        )
        files = [_record(config.project_dir, path) for path in direct_paths]
        sources = [
            record
            for record in files
            if record["extension"] in SOURCE_SUFFIXES
            and record["size_bytes"] > 0
        ]
        nested = [
            record
            for record in _files(config.project_dir, valve_dir)
            if Path(record["path"]).parent
            != Path(valve_dir.relative_to(config.project_dir).as_posix())
        ]
        ignored = [record for record in files if record not in sources] + nested
        valves.append(
            {
                "valve_tag": valve_dir.name,
                "files": files,
                "source_files": sources,
                "ignored_files": ignored,
            }
        )
    return valves, loose_files


def _json_object(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _record_identity(records: list[dict[str, Any]]) -> list[tuple[str, int]]:
    return sorted(
        (
            str(record.get("sha256") or ""),
            int(record.get("size_bytes") or 0),
        )
        for record in records
        if str(record.get("sha256") or "")
    )


def _frozen_baseline_review(
    config: ProjectConfig,
    raw_valves: list[dict[str, Any]],
    specification_files: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compare Stage 1 source identities with the configured frozen run.

    This is deliberately a source-level check.  Parameter equality is confirmed
    separately in the Stage 2 report; source equality never authorizes whole-
    valve diagnosis inheritance by itself.
    """
    run_root = config.formal_diagnosis_run_dir
    manifest = (
        _json_object(run_root / "00_project_run_manifest.json")
        if run_root is not None
        else None
    )
    if (
        run_root is None
        or not isinstance(manifest, dict)
        or manifest.get("stage") != "frozen"
    ):
        return {
            "status": "no_frozen_baseline",
            "decision_required": False,
            "candidate_fingerprint": "",
            "unchanged_valves": [],
            "changed_or_new_valves": [],
            "removed_baseline_valves": [],
        }

    targets = {
        str(item.get("valve_tag") or ""): item
        for item in manifest.get("targets") or []
        if isinstance(item, dict)
        and item.get("stage") == "frozen"
        and str(item.get("valve_tag") or "").strip()
    }
    all_spec_hashes = {
        str(record.get("sha256") or "") for record in specification_files
    }
    current_tags = {str(item.get("valve_tag") or "") for item in raw_valves}
    unchanged: list[dict[str, Any]] = []
    changed: list[dict[str, Any]] = []
    for valve in raw_valves:
        tag = str(valve.get("valve_tag") or "")
        if tag not in targets:
            changed.append({"valve_tag": tag, "status": "new", "reasons": ["上一冻结版本无此阀门"]})
            continue
        bundle = _json_object(run_root / tag / "01_evidence_bundle.json")
        if bundle is None:
            changed.append({"valve_tag": tag, "status": "changed", "reasons": ["上一冻结证据包缺失或无效"]})
            continue
        evidence = bundle.get("evidence")
        evidence = evidence if isinstance(evidence, dict) else {}
        baseline_sources = [
            item for item in evidence.get("source_files") or [] if isinstance(item, dict)
        ]
        raw_unchanged = _record_identity(valve.get("source_files") or []) == _record_identity(baseline_sources)
        baseline_spec_hashes = sorted(
            {
                str(item.get("source_sha256") or "")
                for item in evidence.get("specification_images") or []
                if isinstance(item, dict) and str(item.get("source_sha256") or "")
            }
        )
        tag_spec_dir = config.specs_dir / tag
        tag_spec_records = [
            item
            for item in _files(config.project_dir, tag_spec_dir)
            if item.get("extension") == ".pdf" and item.get("size_bytes", 0) > 0
        ]
        if tag_spec_records:
            spec_unchanged = sorted(
                {str(item.get("sha256") or "") for item in tag_spec_records}
            ) == baseline_spec_hashes
            spec_check = "exact_tag_directory"
        else:
            spec_unchanged = set(baseline_spec_hashes) <= all_spec_hashes
            spec_check = "baseline_source_present"
        reasons: list[str] = []
        if not raw_unchanged:
            reasons.append("原始诊断文件哈希集合发生变化")
        if not spec_unchanged:
            reasons.append("已绑定规格/气路源文件发生变化或缺失")
        if reasons:
            changed.append({"valve_tag": tag, "status": "changed", "reasons": reasons})
            continue
        unchanged.append(
            {
                "valve_tag": tag,
                "raw_source_count": len(baseline_sources),
                "specification_source_count": len(baseline_spec_hashes),
                "specification_check": spec_check,
                "status": "source_unchanged",
                "parameter_confirmation": "Stage 2 required",
            }
        )

    removed = sorted(set(targets) - current_tags)
    candidate_payload = {
        "baseline_manifest_sha256": _sha256(run_root / "00_project_run_manifest.json"),
        "unchanged_valves": unchanged,
        "changed_or_new_valves": changed,
        "removed_baseline_valves": removed,
    }
    return {
        "status": "decision_required" if unchanged else "no_unchanged_candidates",
        "decision_required": bool(unchanged),
        "candidate_fingerprint": _payload_sha256(candidate_payload),
        "baseline": {
            "run_dir": run_root.relative_to(config.project_dir).as_posix(),
            "project_id": str(manifest.get("project_id") or ""),
            "diagnosis_version": str(manifest.get("diagnosis_version") or ""),
            "manifest_sha256": candidate_payload["baseline_manifest_sha256"],
        },
        "unchanged_valves": unchanged,
        "changed_or_new_valves": changed,
        "removed_baseline_valves": removed,
        "allowed_policies": {
            "reuse_unchanged": "复用严格哈希一致的缓存、参数事实和已验证客观观察；整阀结论仍须通过完整输入一致性门禁。",
            "reanalyze_all": "不继承旧观察或整阀结论，全部重新提取、重新读图和重新诊断。",
        },
        "decision_question": "上一冻结版本中存在来源未变化的阀门：复用未变化内容，还是全部重新处理？",
    }


def _analysis_snapshot(
    config: ProjectConfig,
    raw_valves: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    evidence_index = config.project_dir / "work/evidence-extraction/project-index.json"
    if evidence_index.is_file():
        try:
            index = json.loads(evidence_index.read_text(encoding="utf-8"))
            entries = index.get("valves") if isinstance(index, dict) else None
            if not isinstance(entries, list):
                raise ValueError("valves must be a list")
            evidence_summaries: dict[str, dict[str, Any]] = {}
            for entry in entries:
                if not isinstance(entry, dict):
                    raise ValueError("valve index entry must be an object")
                valve_tag = str(entry.get("valve_tag") or "")
                valve_path = (config.project_dir / str(entry.get("path") or "")).resolve()
                valve_path.relative_to(config.project_dir)
                valve_payload = json.loads(valve_path.read_text(encoding="utf-8"))
                material_summary = valve_payload.get("material_summary")
                if not valve_tag or not isinstance(material_summary, dict):
                    raise ValueError("valve evidence is missing its material summary")
                evidence_summaries[valve_tag] = material_summary
        except (OSError, ValueError, json.JSONDecodeError):
            return {
                "status": "invalid",
                "path": evidence_index.relative_to(config.project_dir).as_posix(),
            }, {}

        raw_names = {
            valve["valve_tag"]: {
                Path(record["path"]).name for record in valve["files"]
            }
            for valve in raw_valves
        }
        evidence_names = {
            tag: {
                str(record.get("name") or "")
                for record in summary.get("files") or []
                if isinstance(record, dict) and str(record.get("name") or "")
            }
            for tag, summary in evidence_summaries.items()
        }
        relative = evidence_index.relative_to(config.project_dir).as_posix()
        if evidence_names == raw_names:
            return {"status": "available", "path": relative}, evidence_summaries
        return {"status": "stale", "path": relative}, {}

    candidates = [
        config.work_dir / "analysis_summary.json",
        config.project_dir / "work/analysis_summary.json",
    ]
    raw_names = {
        valve["valve_tag"]: {
            Path(record["path"]).name for record in valve["files"]
        }
        for valve in raw_valves
    }
    for path in dict.fromkeys(candidates):
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"status": "invalid", "path": str(path)}, {}
        if not isinstance(data, list):
            return {"status": "invalid", "path": str(path)}, {}
        summary = {
            str(item.get("folder") or ""): item
            for item in data
            if isinstance(item, dict) and str(item.get("folder") or "")
        }
        summary_names = {
            tag: {
                str(record.get("name") or "")
                for record in item.get("files") or []
                if isinstance(record, dict) and str(record.get("name") or "")
            }
            for tag, item in summary.items()
        }
        relative = path.relative_to(config.project_dir).as_posix()
        if summary_names == raw_names:
            return {"status": "available", "path": relative}, summary
        return {"status": "stale", "path": relative}, {}
    return {"status": "not_available", "path": ""}, {}


def _native_pdf_text(path: Path) -> str:
    """Read native PDF text for archive classification without OCR."""
    try:
        result = subprocess.run(
            ["pdftotext", "-layout", str(path), "-"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except FileNotFoundError:
        return ""
    return result.stdout or ""


def _archive_analysis_snapshot(
    config: ProjectConfig,
    raw_valves: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Classify archived reports and curve inventory without extracting parameters."""
    from .generator import (
        parse_curve_inventory,
        parse_task_execution,
        task_execution_issues,
    )

    summaries: dict[str, dict[str, Any]] = {}
    for raw_valve in raw_valves:
        source_counts: Counter[str] = Counter()
        images: list[dict[str, Any]] = []
        issues: list[dict[str, Any]] = []
        files = [
            {
                "name": Path(record["path"]).name,
                "kind": (
                    "PDF" if record["extension"] == ".pdf" else "devdata"
                ),
            }
            for record in raw_valve["source_files"]
        ]
        for record in raw_valve["source_files"]:
            if record["extension"] != ".pdf":
                continue
            path = config.project_dir / record["path"]
            text = _native_pdf_text(path)
            if not text.strip():
                source_counts["unknown_pdf"] += 1
                continue
            curve_records = parse_curve_inventory(text, path.name)
            tasks = parse_task_execution(text)
            images.extend(curve_records)
            issues.extend(task_execution_issues(tasks))
            compact = re.sub(r"\s+", " ", text)
            if re.search(r"\bValvXpert\b", compact, re.IGNORECASE):
                source_counts["valvxpert"] += 1
            elif re.search(
                r"\b(?:DTM|Active)\s+(?:Report|Configuration)\b",
                compact,
                re.IGNORECASE,
            ):
                source_counts["dtm_active"] += 1
            elif curve_records or tasks or re.search(
                r"\bTask Execution Summary\b|\bValVue3 Sequencer\b",
                compact,
                re.IGNORECASE,
            ):
                source_counts["sequencer"] += 1
            else:
                source_counts["unknown_pdf"] += 1
        summaries[raw_valve["valve_tag"]] = {
            "archive_only": True,
            "files": files,
            "source_report_types": dict(source_counts),
            "dtm_data_report_count": 0,
            "images": images,
            "test_execution_issues": issues,
            "report_pages": [],
            "data_note": "",
            "unreadable": [],
        }
    return {
        "status": "archive_scanned",
        "path": "work/stage-gates/material-archive-report.json",
    }, summaries


def _load_context(config: ProjectConfig) -> tuple[dict[str, Any], str]:
    path = config.valve_context_file
    if path is None or not path.is_file():
        return {}, ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {}, f"阀门现场资料无法读取：{exc}"
    if not isinstance(data, dict):
        return {}, "阀门现场资料必须是按位号组织的对象。"
    return data, ""


def _cell(
    status: str,
    *,
    count: int = 0,
    detail: str = "",
) -> dict[str, Any]:
    return {"status": status, "count": count, "detail": detail}


def _pending_or_missing(has_source: bool, detail: str = "待解析后识别") -> dict[str, Any]:
    return _cell("pending", detail=detail) if has_source else _cell("missing")


def _report_cells(
    raw_valve: dict[str, Any],
    summary: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    pdf_count = sum(
        record["extension"] == ".pdf"
        for record in raw_valve["source_files"]
    )
    devdata_count = sum(
        record["extension"] == ".devdata"
        for record in raw_valve["source_files"]
    )
    devdata = (
        _cell("present", count=devdata_count)
        if devdata_count
        else _cell("missing")
    )
    if summary is None:
        return {
            "devdata": devdata,
            "dtm_active": _pending_or_missing(bool(pdf_count)),
            "sequencer": _pending_or_missing(bool(pdf_count)),
            "valvxpert": _pending_or_missing(bool(pdf_count)),
            "test_report": _pending_or_missing(bool(pdf_count)),
            "dtm_data": _pending_or_missing(bool(pdf_count)),
        }
    raw_counts = summary.get("source_report_types")
    if isinstance(raw_counts, dict):
        counts = Counter(
            {
                str(key): int(value)
                for key, value in raw_counts.items()
                if isinstance(value, int)
                and not isinstance(value, bool)
                and value >= 0
            }
        )
        dtm_data_count = int(summary.get("dtm_data_report_count") or 0)
    else:
        counts = Counter()
        known_sources: set[str] = set()
        dtm_info = summary.get("dtm_info") or {}
        positioner_info = summary.get("positioner_report_info") or {}
        if isinstance(dtm_info, dict) and dtm_info:
            counts["dtm_active"] = 1
            known_sources.add(str(dtm_info.get("source") or ""))
        if (
            isinstance(positioner_info, dict)
            and positioner_info.get("report_type") == "valvxpert_execution"
        ):
            counts["valvxpert"] = 1
            known_sources.add(str(positioner_info.get("source") or ""))
        image_sources = {
            str(image.get("source") or "")
            for image in summary.get("images") or []
            if isinstance(image, dict) and str(image.get("source") or "")
        }
        sequencer_sources = image_sources - known_sources
        if sequencer_sources:
            counts["sequencer"] = len(sequencer_sources)
        dtm_data_count = sum(
            isinstance(info, dict)
            and info.get("dtm_source_status") == "present"
            and info.get("dtm_parser_status") == "parsed"
            for info in (dtm_info, positioner_info)
        )
        recognized = counts["dtm_active"] + counts["sequencer"] + counts["valvxpert"]
        counts["unknown_pdf"] = max(0, pdf_count - recognized)
    unknown_count = counts["unknown_pdf"]
    unreadable_count = counts["unreadable_pdf"]

    def report_cell(report_type: str) -> dict[str, Any]:
        count = counts[report_type]
        if count:
            return _cell("present", count=count)
        if unknown_count:
            return _cell("pending", detail="存在未识别 PDF")
        if unreadable_count:
            return _cell("failed", detail="PDF 无法读取")
        return _cell("missing")

    dtm_active = report_cell("dtm_active")
    sequencer = report_cell("sequencer")
    valvxpert = report_cell("valvxpert")
    test_count = counts["sequencer"] + counts["valvxpert"]
    if test_count:
        test_report = _cell("present", count=test_count)
    elif unknown_count or unreadable_count:
        test_report = _cell("pending", detail="测试报告类型待确认")
    else:
        test_report = _cell("missing")

    dtm_candidate_count = counts["dtm_active"] + counts["valvxpert"]
    if summary.get("archive_only") and dtm_candidate_count:
        dtm_data = _cell("pending", detail="参数待第二阶段提取")
    elif dtm_data_count:
        dtm_data = _cell("present", count=dtm_data_count)
    elif dtm_candidate_count:
        dtm_data = _cell("failed", detail="报告存在，但 DTM 数据未确认")
    elif unknown_count or unreadable_count:
        dtm_data = _cell("pending", detail="DTM 数据来源待确认")
    else:
        dtm_data = _cell("missing")
    return {
        "devdata": devdata,
        "dtm_active": dtm_active,
        "sequencer": sequencer,
        "valvxpert": valvxpert,
        "test_report": test_report,
        "dtm_data": dtm_data,
    }


def _curve_cells(
    has_source: bool,
    summary: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    if summary is None:
        return {
            key: _pending_or_missing(has_source)
            for key, _label in CURVE_TYPES
        }
    image_counts = Counter(
        str(image.get("test_key") or "other")
        for image in summary.get("images") or []
        if isinstance(image, dict)
    )
    archive_unknown_pdf = (
        int((summary.get("source_report_types") or {}).get("unknown_pdf") or 0)
        if summary.get("archive_only")
        else 0
    )
    failures: dict[str, list[str]] = {}
    for issue in summary.get("test_execution_issues") or []:
        if not isinstance(issue, dict):
            continue
        key = str(issue.get("test_key") or "other")
        status = str(issue.get("status") or "Failed")
        failures.setdefault(key, []).append(status)
    result: dict[str, dict[str, Any]] = {}
    for key, _label in CURVE_TYPES:
        count = image_counts[key]
        if failures.get(key):
            result[key] = _cell(
                "failed",
                count=count,
                detail="/".join(sorted(set(failures[key]))),
            )
        elif count:
            result[key] = _cell("present", count=count)
        elif archive_unknown_pdf:
            result[key] = _cell(
                "pending",
                detail="原生文本不可读，待后续阶段识别",
            )
        else:
            result[key] = _cell("missing")
    return result


def _material_cells(
    valve_tag: str,
    summary: dict[str, Any] | None,
    context: dict[str, Any],
    specification_files: list[dict[str, Any]],
    *,
    include_accessory_pages: bool,
) -> dict[str, Any]:
    if summary is None or summary.get("archive_only"):
        page_cells = {
            role: (
                _cell(
                    "pending",
                    detail="气路页待匹配" if role == "air_piping" else "待匹配",
                )
                if specification_files
                else _cell(
                    "missing",
                    detail="气路页未提供" if role == "air_piping" else "",
                )
            )
            for role in REPORT_PAGE_ROLES
        }
    else:
        pages_by_role = _report_pages_by_role(summary)
        page_cells = {
            role: (
                _cell("present", count=len(pages_by_role[role]))
                if pages_by_role[role]
                else _cell(
                    "unmatched" if specification_files else "missing",
                    detail=(
                        "气路页待匹配"
                        if role == "air_piping" and specification_files
                        else "气路页未提供"
                        if role == "air_piping"
                        else ""
                    ),
                )
            )
            for role in REPORT_PAGE_ROLES
        }
    if not include_accessory_pages:
        page_cells["accessory"] = _cell(
            "disabled",
            detail="未启用/不纳入报告",
        )

    direct = context.get(valve_tag) if isinstance(context.get(valve_tag), dict) else {}
    values = {
        "description": direct.get("description"),
        "unit": direct.get("unit"),
        "failure_consequence": direct.get("failure_consequence"),
        "bypass": direct.get("bypass"),
        "site_note": direct.get("note"),
    }
    if summary:
        for key, _label in CONTEXT_FIELDS:
            if str(summary.get(key) or "").strip() not in MISSING_VALUES:
                values[key] = summary.get(key)
    context_cells = {
        key: _cell("present")
        if str(values.get(key) or "").strip() not in MISSING_VALUES
        else _cell("missing")
        for key, _label in CONTEXT_FIELDS
    }
    return {
        **page_cells,
        "context": context_cells,
    }


def _report_page_identity(page: dict[str, Any], fallback: str) -> str:
    page_number = page.get("page")
    if isinstance(page_number, int) and not isinstance(page_number, bool):
        source_sha256 = str(page.get("source_sha256") or "").strip()
        if source_sha256:
            return f"sha256:{source_sha256}:{page_number}"
        source = str(page.get("source") or "").strip()
        if source:
            return f"source:{source}:{page_number}"
    physical_identity = str(page.get("physical_identity") or "").strip()
    return physical_identity or fallback


def _report_pages_by_role(summary: dict[str, Any]) -> dict[str, set[str]]:
    """Collect semantic page roles while counting each physical page once per role."""
    result = {role: set() for role in REPORT_PAGE_ROLES}
    report_pages = summary.get("report_pages")
    if isinstance(report_pages, list):
        for index, raw_page in enumerate(report_pages):
            if not isinstance(raw_page, dict):
                continue
            raw_roles = raw_page.get("roles")
            if isinstance(raw_roles, str):
                roles = [raw_roles]
            elif isinstance(raw_roles, (list, tuple, set)):
                roles = raw_roles
            else:
                roles = []
            identity = _report_page_identity(raw_page, f"report_page:{index}")
            for raw_role in roles:
                role = str(raw_role).strip()
                if role in result:
                    result[role].add(identity)

    # Old summaries exposed one field per role. They remain conservative fallbacks:
    # in particular, an accessory page never satisfies the air-piping role.
    legacy_fields = {
        "specification": "spec_page",
        "air_piping": "air_piping_page",
        "accessory": "accessory_page",
    }
    for role, field in legacy_fields.items():
        if result[role]:
            continue
        raw_page = summary.get(field)
        if isinstance(raw_page, dict) and raw_page:
            result[role].add(_report_page_identity(raw_page, f"legacy:{field}"))
    return result


def _duplicate_assignments(raw_valves: list[dict[str, Any]]) -> dict[str, list[str]]:
    by_hash: dict[str, set[str]] = {}
    for valve in raw_valves:
        for record in valve["source_files"]:
            by_hash.setdefault(record["sha256"], set()).add(valve["valve_tag"])
    duplicates: dict[str, list[str]] = {}
    for sha256, tags in by_hash.items():
        if len(tags) > 1:
            for tag in tags:
                duplicates.setdefault(tag, []).append(sha256)
    return duplicates


def _consistency(
    valve_tag: str,
    summary: dict[str, Any] | None,
    duplicates: dict[str, list[str]],
) -> dict[str, Any]:
    issues: list[str] = []
    if duplicates.get(valve_tag):
        issues.append(f"有 {len(duplicates[valve_tag])} 个源文件与其他位号内容相同")
    if summary:
        data_note = str(summary.get("data_note") or "").strip()
        if data_note:
            issues.append(data_note)
        if summary.get("unreadable"):
            issues.append(f"有 {len(summary['unreadable'])} 个文件解析失败")
    return {
        "status": "warning" if issues else "ok",
        "issues": issues,
    }


def _overall(
    reports: dict[str, dict[str, Any]],
    materials: dict[str, Any],
    consistency: dict[str, Any],
) -> dict[str, Any]:
    required = {
        "devdata": reports["devdata"],
        "测试报告（Sequencer 或 ValvXpert）": reports["test_report"],
        "DTM 数据（DTM/Active 或 ValvXpert）": reports["dtm_data"],
        "匹配规格书": materials["specification"],
        "匹配气路页": materials["air_piping"],
    }
    for key, label in CONTEXT_FIELDS:
        required[label] = materials["context"][key]
    missing = [
        label
        for label, value in required.items()
        if value["status"] in {"missing", "failed", "unmatched"}
    ]
    pending = [
        label for label, value in required.items() if value["status"] == "pending"
    ]
    if missing:
        status = "incomplete"
    elif pending:
        status = "pending"
    elif consistency["status"] == "warning":
        status = "warning"
    else:
        status = "complete"
    return {"status": status, "missing": missing, "pending": pending}


def _format_cell(value: dict[str, Any], unit: str = "") -> str:
    status = value["status"]
    count = int(value.get("count") or 0)
    detail = str(value.get("detail") or "")
    if status == "present":
        return f"✅ {count}{unit}" if count else "✅"
    if status == "missing":
        return "❌ " + (detail or "缺失")
    if status == "pending":
        return "⏳ " + (detail or "待识别")
    if status == "unmatched":
        return "⚠ " + (detail or "待匹配")
    if status == "failed":
        prefix = f"{count}{unit} / " if count else ""
        return "⚠ " + prefix + (detail or "失败")
    if status == "disabled":
        return "— " + (detail or "未启用/不纳入报告")
    return "⚠ " + (detail or "需复核")


def _overall_text(overall: dict[str, Any]) -> str:
    return {
        "complete": "✅ 必需资料齐全",
        "warning": "⚠ 必需资料齐全，有一致性提醒",
        "pending": "⏳ 待识别/匹配",
        "incomplete": f"❌ 缺 {len(overall['missing'])} 项",
    }[overall["status"]]


def _render_markdown(payload: dict[str, Any]) -> str:
    analysis_labels = {
        "available": "已读取现有解析结果",
        "archive_scanned": "仅用原生文本识别资料类型、曲线数量和执行状态；未提取参数",
        "not_available": "尚无解析结果，报告与曲线类型显示待识别",
        "stale": "现有解析结果与 raw/ 不一致，类型显示待识别",
        "invalid": "现有解析结果无效，类型显示待识别",
    }
    stage = payload.get("stage") if isinstance(payload.get("stage"), dict) else {}
    lines = [
        "# 第一阶段：资料归档报告",
        "",
        f"- 项目：{payload['project']['name']}",
        f"- 客户：{payload['project']['customer']}",
        f"- 阀门数：{payload['summary']['valve_count']}",
        f"- 解析状态：{analysis_labels[payload['analysis']['status']]}",
        "- 阶段范围：只归档文件、确认归属、识别资料类型，并统计可确认的曲线数量与执行状态。",
        "- 阶段边界：不执行 OCR，不提取设备/规格/运行参数，不做逐图观察、评级、诊断或报告生成。",
        f"- 批准状态：{'等待用户明确批准' if stage else '内部刷新，不改变既有批准'}。",
        "- 门禁：用户批准并登记前，不得进入第二阶段参数提取或任何后续阶段。",
        "",
        "## 上一版本变化与复用选择",
        "",
    ]
    reuse_review = payload.get("reuse_review")
    reuse_review = reuse_review if isinstance(reuse_review, dict) else {}
    unchanged = [
        str(item.get("valve_tag") or "")
        for item in reuse_review.get("unchanged_valves") or []
        if isinstance(item, dict) and str(item.get("valve_tag") or "")
    ]
    changed = [
        str(item.get("valve_tag") or "")
        for item in reuse_review.get("changed_or_new_valves") or []
        if isinstance(item, dict) and str(item.get("valve_tag") or "")
    ]
    if unchanged:
        baseline = reuse_review.get("baseline") or {}
        lines.extend(
            [
                f"- 对照基线：{baseline.get('diagnosis_version') or '上一冻结版本'}。",
                f"- 来源未变化：{len(unchanged)} 台（{'、'.join(unchanged)}）。",
                f"- 新增或来源变化：{len(changed)} 台（{'、'.join(changed) if changed else '无'}）。",
                "- 当前结论只表示原始诊断文件与已绑定规格/气路源文件哈希未变；参数值是否完全一致将在第二阶段再次确认。",
                "- 必须选择其一：`reuse_unchanged`（复用未变化内容）或 `reanalyze_all`（全部重新处理）。未登记选择不得批准第一阶段。",
                "- 即使选择复用，整阀结论也只有在原始文件、完整结构化证据、观察和当次加载的经验正文全部一致时才允许继承。",
            ]
        )
    elif reuse_review.get("status") == "no_frozen_baseline":
        lines.append("- 没有可用的上一冻结版本，本次按全新项目处理，无需选择复用策略。")
    else:
        lines.append("- 未发现来源完全未变化的阀门，本次无需选择复用策略。")
    lines.extend(
        [
            "",
        "## 报告与原始数据",
        "",
        "| 阀门 | devdata | DTM/Active | Sequencer | ValvXpert | 测试报告 | DTM 数据 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for valve in payload["valves"]:
        report = valve["reports"]
        lines.append(
            f"| {valve['valve_tag']} | {_format_cell(report['devdata'], '份')} | "
            f"{_format_cell(report['dtm_active'], '份')} | "
            f"{_format_cell(report['sequencer'], '份')} | "
            f"{_format_cell(report['valvxpert'], '份')} | "
            f"{_format_cell(report['test_report'], '份')} | "
            f"{_format_cell(report['dtm_data'], '份')} |"
        )

    lines.extend(
        [
            "",
            "## 曲线数量与执行状态",
            "",
            (
                "| 阀门 | Run Step | Pattern Step | Run Ramp | "
                "Standard Signature | Extended Signature |"
            ),
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for valve in payload["valves"]:
        curves = valve["curves"]
        lines.append(
            f"| {valve['valve_tag']} | {_format_cell(curves['run_step'], '张')} | "
            f"{_format_cell(curves['pattern_step'], '张')} | "
            f"{_format_cell(curves['run_ramp'], '张')} | "
            f"{_format_cell(curves['standard_signature'], '张')} | "
            f"{_format_cell(curves['extended_signature'], '张')} |"
        )

    lines.extend(
        [
            "",
            "## 规格与项目资料",
            "",
            (
                "| 阀门 | Specification | 气路页 | 普通附件 | 阀门描述 | 装置 | "
                "失效后果 | 副线 | 现场备注 | 一致性 | 总体 |"
            ),
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for valve in payload["valves"]:
        materials = valve["materials"]
        context = materials["context"]
        consistency = (
            "✅"
            if valve["consistency"]["status"] == "ok"
            else f"⚠ {len(valve['consistency']['issues'])}项"
        )
        lines.append(
            f"| {valve['valve_tag']} | {_format_cell(materials['specification'], '份')} | "
            f"{_format_cell(materials['air_piping'], '份')} | "
            f"{_format_cell(materials['accessory'], '份')} | "
            f"{_format_cell(context['description'])} | {_format_cell(context['unit'])} | "
            f"{_format_cell(context['failure_consequence'])} | "
            f"{_format_cell(context['bypass'])} | {_format_cell(context['site_note'])} | "
            f"{consistency} | {_overall_text(valve['overall'])} |"
        )

    lines.extend(["", "## 缺失与一致性明细", ""])
    details = []
    for valve in payload["valves"]:
        messages = []
        if valve["overall"]["missing"]:
            messages.append("缺失：" + "、".join(valve["overall"]["missing"]))
        if valve["overall"]["pending"]:
            messages.append("待识别：" + "、".join(valve["overall"]["pending"]))
        messages.extend(valve["consistency"]["issues"])
        if messages:
            details.append(f"- **{valve['valve_tag']}**：" + "；".join(messages))
    lines.extend(details or ["无。"])

    lines.extend(["", "## 待归档文件", ""])
    pending_files = payload["inbound_files"] + payload["loose_raw_files"]
    if pending_files:
        lines.extend(f"- `{record['path']}`" for record in pending_files)
    else:
        lines.append("无。")
    lines.extend(
        [
            "",
            "## 状态说明",
            "",
            "- `✅`：已识别或已匹配；数字表示份数或曲线张数。",
            "- `❌ 缺失`：当前资料中没有该项，但不会阻断后续流程。",
            "- `⏳`：首检未解析内容，或已有文件尚未完成类型识别/规格匹配。",
            "- `⚠`：测试失败、匹配未完成或存在资料一致性问题。",
            "- `— 未启用/不纳入报告`：项目功能开关关闭，不需补充或匹配该项。",
            "",
            "## 下一步",
            "",
        ]
    )
    lines.extend(
        f"{index}. {action}"
        for index, action in enumerate(payload["next_actions"], start=1)
    )
    return "\n".join(lines) + "\n"


def _html_cell(cell: dict[str, Any], unit: str = "") -> str:
    status = str(cell.get("status") or "missing")
    count = int(cell.get("count") or 0)
    detail = str(cell.get("detail") or "")
    labels = {
        "present": f"✓ {count}{unit}" if count else "✓ 已识别",
        "warning": f"! {count}{unit}" if count else "! 异常",
        "pending": "… 待识别",
        "unmatched": "… 待匹配",
        "disabled": "— 未启用",
        "missing": "— 缺失",
    }
    label = labels.get(status, html.escape(status))
    if status == "warning" and detail:
        label += " / " + html.escape(detail)
    return f'<span class="pill {html.escape(status)}">{label}</span>'


def _render_stage_gate_html(payload: dict[str, Any]) -> str:
    """Render a self-contained, mobile-friendly Stage 1 approval page."""
    project = payload.get("project") or {}
    summary = payload.get("summary") or {}
    valves = [item for item in payload.get("valves") or [] if isinstance(item, dict)]
    pending_file_count = len(payload.get("inbound_files") or []) + len(
        payload.get("loose_raw_files") or []
    )
    failure_tags: list[str] = []
    no_spec_tags: list[str] = []
    rows: list[str] = []
    for valve in valves:
        tag = str(valve.get("valve_tag") or "")
        reports = valve.get("reports") or {}
        curves = valve.get("curves") or {}
        materials = valve.get("materials") or {}
        curve_cells = [curves.get(key) or {} for key, _label in CURVE_TYPES]
        has_failure = any(
            cell.get("status") == "warning" or "failed" in str(cell.get("detail") or "").casefold()
            for cell in curve_cells
        )
        has_spec = (materials.get("specification") or {}).get("status") == "present"
        if has_failure:
            failure_tags.append(tag)
        if not has_spec:
            no_spec_tags.append(tag)
        missing = len((valve.get("overall") or {}).get("missing") or [])
        pending = len((valve.get("overall") or {}).get("pending") or [])
        rows.append(
            '<tr data-failed="{}" data-spec-missing="{}">'.format(
                "1" if has_failure else "0",
                "0" if has_spec else "1",
            )
            + f'<td data-label="阀门"><strong>{html.escape(tag)}</strong></td>'
            + f'<td data-label="测试报告">{_html_cell(reports.get("test_report") or {}, "份")}</td>'
            + f'<td data-label="DTM 数据">{_html_cell(reports.get("dtm_data") or {}, "份")}</td>'
            + "".join(
                f'<td data-label="{html.escape(label)}">{_html_cell(cell, "张")}</td>'
                for cell, (_key, label) in zip(curve_cells, CURVE_TYPES)
            )
            + f'<td data-label="Specification">{_html_cell(materials.get("specification") or {}, "份")}</td>'
            + f'<td data-label="资料缺口"><span class="issue-count">缺 {missing} / 待 {pending}</span></td>'
            + "</tr>"
        )

    reuse_review = payload.get("reuse_review") or {}
    reuse_required = bool(reuse_review.get("decision_required"))
    reuse_note = (
        "检测到上一冻结版本的来源未变化候选，批准时必须选择复用未变化内容或全部重新处理。"
        if reuse_required
        else "没有需要选择复用策略的上一冻结版本候选。"
    )
    failure_text = "、".join(failure_tags) if failure_tags else "无"
    no_spec_text = "、".join(no_spec_tags) if no_spec_tags else "无"
    project_name = html.escape(str(project.get("name") or "离线诊断项目"))
    customer = html.escape(str(project.get("customer") or ""))
    generated = html.escape(str(payload.get("generated_at") or ""))
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{project_name}｜第一阶段资料归档报告</title>
<style>
:root{{--ink:#111612;--paper:#f1efe7;--acid:#c8ff36;--teal:#0a6b5e;--orange:#ff6b2c;--line:rgba(17,22,18,.18);--muted:#687068}}
*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;background:var(--paper);color:var(--ink);font-family:"Noto Sans SC","Microsoft YaHei",sans-serif}}button,input{{font:inherit}}
header{{position:sticky;top:0;z-index:9;height:62px;padding:0 clamp(18px,5vw,72px);display:flex;align-items:center;justify-content:space-between;background:rgba(17,22,18,.94);color:#fff;backdrop-filter:blur(15px)}}
.brand{{display:flex;align-items:center;gap:14px;font-weight:800}}.mark{{display:grid;place-items:center;width:44px;height:29px;background:var(--acid);color:var(--ink);font:900 12px monospace;transform:skew(-8deg)}}
.hero{{position:relative;overflow:hidden;padding:76px clamp(18px,5vw,72px) 68px;background:var(--ink);color:#fff}}.hero:after{{content:"{len(valves)}";position:absolute;right:1vw;bottom:-72px;color:transparent;-webkit-text-stroke:2px rgba(200,255,54,.14);font:900 clamp(230px,32vw,450px)/1 sans-serif}}
.kicker,.eyebrow{{font:700 11px monospace;letter-spacing:.16em;color:var(--acid)}}h1{{position:relative;z-index:1;margin:38px 0 44px;max-width:900px;font:900 clamp(46px,6vw,86px)/1.06 "Noto Serif SC","Songti SC",serif;letter-spacing:-.045em}}h1 em{{color:var(--acid);font-style:normal}}.nowrap{{white-space:nowrap}}
.hero-foot{{position:relative;z-index:1;display:flex;gap:35px;justify-content:space-between;align-items:end}}.hero-foot p{{max-width:650px;color:#ffffffaa;line-height:1.8}}.gate{{border-top:5px solid var(--acid);padding:14px 18px;background:#ffffff0b;min-width:170px}}.gate strong{{display:block;color:var(--acid);font-size:23px}}
.metrics{{display:grid;grid-template-columns:repeat(4,1fr);padding:0 clamp(18px,5vw,72px);border-bottom:1px solid var(--line)}}.metric{{padding:34px 24px;border-right:1px solid var(--line)}}.metric:first-child{{border-left:1px solid var(--line)}}.metric small{{color:var(--teal);font:700 10px monospace;letter-spacing:.16em}}.metric strong{{display:block;margin:13px 0 6px;font:900 clamp(42px,5vw,68px)/.9 sans-serif}}.metric p{{margin:0;color:var(--muted);font-size:12px}}
section{{padding:86px clamp(18px,5vw,72px)}}.heading{{display:grid;grid-template-columns:1.1fr .9fr;gap:60px;align-items:end;margin-bottom:42px}}h2{{margin:14px 0 0;font:900 clamp(34px,4.8vw,64px)/1.12 "Noto Serif SC","Songti SC",serif;letter-spacing:-.04em}}.heading p{{color:var(--muted);line-height:1.8}}
.controls{{display:flex;gap:12px;justify-content:space-between;margin-bottom:18px;flex-wrap:wrap}}.controls input{{min-width:min(360px,100%);padding:12px;border:0;border-bottom:2px solid var(--ink);background:transparent;outline:none}}.filters{{display:flex;gap:8px;flex-wrap:wrap}}.filters button{{padding:10px 14px;border:1px solid var(--line);background:transparent;cursor:pointer}}.filters button.on{{background:var(--ink);color:#fff}}
.table{{overflow:auto;border-top:3px solid var(--ink);background:#ffffff55}}table{{width:100%;min-width:1180px;border-collapse:collapse}}th,td{{padding:13px 11px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}}th{{color:var(--muted);font:700 10px monospace}}td{{font-size:12px}}.pill,.issue-count{{display:inline-block;padding:5px 7px;border-radius:2px;font:700 10px monospace}}.present{{color:#07564c;background:#0a6b5e16}}.warning{{color:#a72e00;background:#ff6b2c22}}.pending,.unmatched{{color:#6e5a00;background:#d6b40022}}.missing,.disabled{{color:#666;background:#11161212}}.issue-count{{background:#1116120b}}
.gaps{{background:var(--ink);color:#fff}}.cards{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}}.card{{padding:26px;min-height:250px;border:1px solid #ffffff22;background:#ffffff08}}.card.acid{{background:var(--acid);color:var(--ink)}}.card.alert{{border-top:5px solid var(--orange)}}.card strong{{display:block;margin:28px 0 22px;color:var(--acid);font:900 48px/.9 sans-serif}}.card.acid strong{{color:var(--ink)}}.card.alert strong{{color:var(--orange)}}.card p{{color:#ffffff99;font-size:12px;line-height:1.7;word-break:break-word}}.card.acid p{{color:#111612aa}}
.approval{{text-align:center;background:#dedbd0}}.approval p{{max-width:720px;margin:22px auto;color:var(--muted);line-height:1.8}}.copy{{padding:16px 22px;border:0;background:var(--ink);color:#fff;font-weight:800;cursor:pointer}}footer{{padding:34px clamp(18px,5vw,72px);background:var(--ink);color:#ffffff77;font:11px monospace}}
@media(max-width:900px){{.hero-foot,.heading{{display:block}}.metrics{{grid-template-columns:repeat(2,1fr)}}.cards{{grid-template-columns:repeat(2,1fr)}}}}
@media(max-width:700px){{header>span:last-child{{display:none}}.hero{{padding:56px 18px 44px}}.hero:after{{display:none}}h1{{margin:30px 0 34px;font-size:clamp(40px,12vw,56px);line-height:1.08}}.hero-foot p{{font-size:14px}}.gate{{margin-top:22px;width:100%}}.metrics{{padding:0 18px}}.metric{{padding:25px 15px}}.metric strong{{font-size:40px}}section{{padding:62px 18px}}.heading{{margin-bottom:30px}}.controls input{{width:100%;min-width:0}}.table{{overflow:visible;border-top:0;background:transparent}}table{{min-width:0}}thead{{display:none}}tbody{{display:grid;gap:14px}}tr{{display:grid;grid-template-columns:1fr 1fr;padding:0 12px 12px;background:#fff;border:1px solid var(--line);border-top:4px solid var(--teal)}}td{{display:flex;justify-content:space-between;align-items:center;gap:8px;padding:10px 6px;white-space:normal}}td:before{{content:attr(data-label);color:var(--muted);font:700 9px monospace}}td:first-child{{grid-column:1/-1;margin:0 -12px 2px;padding:13px 16px;background:var(--ink);color:#fff}}td:first-child:before{{color:#ffffff88}}.cards{{display:block}}.card{{margin-bottom:10px;min-height:auto}}}}
</style></head><body>
<header><div class="brand"><span class="mark">VV3</span><span>{customer} · 资料归档台账</span></div><span>STAGE 01</span></header>
<main><div class="hero"><span class="kicker">MATERIAL ARCHIVE / APPROVAL GATE</span><h1>{customer}<br><em>{len(valves)} 台阀门</em><br><span class="nowrap">资料归档审核</span></h1><div class="hero-foot"><p>第一阶段资料归档报告。仅展示归属、资料类型、曲线数量和执行状态；不包含参数提取、健康评级、故障诊断或检修建议。</p><div class="gate"><small>当前门禁</small><strong>等待批准</strong></div></div></div>
<div class="metrics"><div class="metric"><small>VALVES</small><strong>{len(valves)}</strong><p>唯一阀门位号</p></div><div class="metric"><small>FILES PENDING</small><strong>{pending_file_count}</strong><p>待归档或散落文件</p></div><div class="metric"><small>TEST FLAGS</small><strong>{len(failure_tags)}</strong><p>源报告测试异常</p></div><div class="metric"><small>INCOMPLETE</small><strong>{int(summary.get('incomplete_count') or 0)}</strong><p>当前存在资料缺口</p></div></div>
<section><div class="heading"><div><span class="eyebrow">01 / MACHINE LEDGER</span><h2>逐阀资料与曲线清单</h2></div><p>可搜索位号，或只查看源报告测试异常、Specification 未匹配的阀门。</p></div>
<div class="controls"><input id="search" placeholder="搜索阀门位号"><div class="filters"><button class="on" data-filter="all">全部 {len(valves)}</button><button data-filter="failed">测试异常 {len(failure_tags)}</button><button data-filter="spec">规格待匹配 {len(no_spec_tags)}</button></div></div>
<div class="table"><table><thead><tr><th>阀门</th><th>测试报告</th><th>DTM 数据</th><th>Run Step</th><th>Pattern Step</th><th>Run Ramp</th><th>Standard Signature</th><th>Extended Signature</th><th>Specification</th><th>缺口</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div></section>
<section class="gaps"><div class="heading"><div><span class="eyebrow">02 / KNOWN GAPS</span><h2>批准前需要知情接受</h2></div><p>缺失项不会被相似阀门数据补齐；后续仍须作为不确定项保留。</p></div><div class="cards"><article class="card acid"><small>资料状态</small><strong>{int(summary.get('incomplete_count') or 0)} 台</strong><h3>当前资料不完整</h3><p>逐阀缺项与待识别项已在上表列出。</p></article><article class="card alert"><small>测试状态</small><strong>{len(failure_tags)} 台</strong><h3>源报告出现异常</h3><p>{html.escape(failure_text)}</p></article><article class="card"><small>技术资料</small><strong>{len(no_spec_tags)} 台</strong><h3>Specification 待匹配</h3><p>{html.escape(no_spec_text)}</p></article><article class="card"><small>复用门禁</small><strong>{'需选择' if reuse_required else '无需'}</strong><h3>上一版本复用策略</h3><p>{html.escape(reuse_note)}</p></article></div></section>
<section class="approval"><span class="eyebrow">03 / APPROVAL</span><h2>网页是审核界面，不是诊断报告。</h2><p>批准后才能进入第二阶段参数提取。复制批准语句并回到当前对话发送即可。</p><button class="copy" onclick="navigator.clipboard.writeText('批准第一阶段，接受当前缺失项并继续');this.textContent='批准语句已复制'">复制批准语句</button></section></main>
<footer>生成时间 {generated} · 本页为共享入口生成的自包含 HTML · 仅限本地或 Tailscale 内网访问</footer>
<script>const rows=[...document.querySelectorAll('tbody tr')],q=document.querySelector('#search');let mode='all';function apply(){{const s=q.value.trim().toLowerCase();rows.forEach(r=>{{const okText=!s||r.cells[0].textContent.toLowerCase().includes(s);const okMode=mode==='all'||mode==='failed'&&r.dataset.failed==='1'||mode==='spec'&&r.dataset.specMissing==='1';r.hidden=!(okText&&okMode)}})}}q.addEventListener('input',apply);document.querySelectorAll('[data-filter]').forEach(b=>b.addEventListener('click',()=>{{mode=b.dataset.filter;document.querySelectorAll('[data-filter]').forEach(x=>x.classList.toggle('on',x===b));apply()}}));</script>
</body></html>"""


def write_material_readiness(
    config: ProjectConfig,
    *,
    freeze_stage_report: bool = False,
) -> MaterialReadiness:
    raw_valves, loose_raw_files = _raw_inventory(config)
    inbound_files = _files(config.project_dir, config.inbound_dir)
    specification_files = [
        record
        for record in _files(config.project_dir, config.specs_dir)
        if record["extension"] == ".pdf" and record["size_bytes"] > 0
    ]
    reuse_review = _frozen_baseline_review(
        config,
        raw_valves,
        specification_files,
    )
    if freeze_stage_report:
        analysis, summaries = _archive_analysis_snapshot(config, raw_valves)
    else:
        analysis, summaries = _analysis_snapshot(config, raw_valves)
    context, context_error = _load_context(config)
    duplicates = _duplicate_assignments(raw_valves)

    valves: list[dict[str, Any]] = []
    for raw_valve in raw_valves:
        tag = raw_valve["valve_tag"]
        summary = summaries.get(tag)
        reports = _report_cells(raw_valve, summary)
        curves = _curve_cells(bool(raw_valve["source_files"]), summary)
        materials = _material_cells(
            tag,
            summary,
            context,
            specification_files,
            include_accessory_pages=config.features.get(
                "include_accessory_pages", False
            ),
        )
        consistency = _consistency(tag, summary, duplicates)
        overall = _overall(reports, materials, consistency)
        valves.append(
            {
                "valve_tag": tag,
                "raw_files": raw_valve["source_files"],
                "ignored_raw_files": raw_valve["ignored_files"],
                "reports": reports,
                "curves": curves,
                "materials": materials,
                "consistency": consistency,
                "overall": overall,
            }
        )

    overall_counts = Counter(valve["overall"]["status"] for valve in valves)
    next_actions = []
    if inbound_files or loose_raw_files:
        next_actions.append("确认待归档文件的阀门归属并整理到标准目录。")
    if context_error:
        next_actions.append(context_error)
    if freeze_stage_report:
        if reuse_review["decision_required"]:
            next_actions.append(
                "对来源未变化的阀门明确选择：复用未变化内容，或全部重新处理。"
            )
        next_actions.extend(
            [
                "将本报告提交用户审阅，明确列出缺失、待识别和未归属资料。",
                "收到用户明确批准后，运行 --approve-materials \"<用户批准原文>\" 登记门禁。",
                "批准有效后，第二阶段才能运行 --extract-materials 提取全部所需参数。",
            ]
        )
    elif analysis["status"] != "available":
        next_actions.append("第二阶段参数尚未完成提取。")
    else:
        next_actions.append("当前矩阵已按第二阶段提取结果刷新。")

    payload: dict[str, Any] = {
        "schema_version": 3,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "materials_reported",
        "project": {
            "name": config.project_dir.name,
            "customer": config.customer,
        },
        "analysis": analysis,
        "summary": {
            "valve_count": len(valves),
            "complete_count": overall_counts["complete"],
            "warning_count": overall_counts["warning"],
            "pending_count": overall_counts["pending"],
            "incomplete_count": overall_counts["incomplete"],
        },
        "valves": valves,
        "inbound_files": inbound_files,
        "loose_raw_files": loose_raw_files,
        "specification_files": specification_files,
        "reuse_review": reuse_review,
        "next_actions": next_actions,
    }
    if freeze_stage_report:
        payload["stage"] = {
            "name": "material_archive",
            "sequence": 1,
            "status": "awaiting_user_approval",
            "approval_required": True,
            "input_fingerprint": material_input_fingerprint(config),
            "report_fingerprint": "",
            "included_work": [
                "file archiving and reliable valve ownership",
                "material type identification",
                "curve type/count and execution status where readable without OCR",
            ],
            "excluded_work": [
                "OCR and parameter extraction",
                "objective curve observation",
                "diagnosis, rating, recommendation, and report generation",
            ],
        }
        payload["stage"]["report_fingerprint"] = _material_report_fingerprint(
            payload
        )

    output_dir = config.project_dir / "work/material-readiness"
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "readiness.json"
    markdown_path = output_dir / "readiness.md"
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(_render_markdown(payload), encoding="utf-8")
    if freeze_stage_report:
        stage_json, stage_markdown, _approval = _material_stage_paths(config)
        stage_html = material_archive_web_report_path(config)
        stage_json.parent.mkdir(parents=True, exist_ok=True)
        stage_json.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        stage_markdown.write_text(_render_markdown(payload), encoding="utf-8")
        stage_html.write_text(_render_stage_gate_html(payload), encoding="utf-8")
    return MaterialReadiness(
        status="materials_reported",
        json_path=json_path,
        markdown_path=markdown_path,
        payload=payload,
    )
