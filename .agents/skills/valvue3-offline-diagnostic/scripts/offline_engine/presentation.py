"""Customer-facing formatting for shared offline diagnostic reports."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any


TEST_LABELS = {
    "run_step": "Run Step Test / 阶跃响应测试图",
    "pattern_step": "Pattern Step / 小信号阶跃测试图",
    "run_ramp": "Run Ramp Test / 位置签名曲线图",
    "standard_signature": "Standard Signature / 标准签名图",
    "extended_signature": "执行机构性能测试",
    "other": "其他 ValVue3 诊断图形",
}

CUSTOMER_METRIC_LABELS = {
    "Average Error": "平均误差",
    "T86": "T86 响应时间",
    "T63": "T63 响应时间",
    "Td": "延迟时间（Td）",
    "Overshoot": "超调量",
    "Supply Pressure": "供气压力",
    "Max Friction": "最大摩擦力",
    "Min Friction": "最小摩擦力",
    "Mean Friction": "平均摩擦力",
}

TEST_EXECUTION_STATUS_ZH = {
    "failed": "失败",
    "cancelled": "已取消",
    "aborted": "已中止",
    "incomplete": "未完成",
    "not completed": "未完成",
}

_INTERNAL_IMAGE_TOKEN_RE = re.compile(
    r"(?<![^\s：:；,，。(（])"
    r"(?:(?:[A-Za-z]:)?[\\/])?(?:[^\\/\s：:；,，。()（）]+[\\/])*"
    r"[^\\/\s：:；,，。()（）]+\.(?:png|jpe?g|gif|bmp|tiff?|webp)"
    r"(?![A-Za-z0-9_.-])",
    re.IGNORECASE,
)


MISSING = "未提供"
SPECIFICATION_MISSING = "-"
_SPECIFICATION_EMPTY_VALUES = {
    "",
    "-",
    "—",
    "–",
    "一",
    "未提供",
    "规格书未提供",
    "规格书栏位为空",
}
_PARENTHETICAL_RE = re.compile(r"\s*[\(（][^\)）]*[\)）]\s*")
_REVIEW_QUALIFIER_RE = re.compile(
    r"(?:待[^\(\)（）]*(?:复核|确认|核实|审核)|需[^\(\)（）]*(?:复核|确认|核实)|待定|不确定|未知|"
    r"to\s+be\s+(?:confirmed|reviewed|verified)|pending\s+(?:review|confirmation|verification)|"
    r"review(?:\s+required)?|confirm(?:ation)?\s+required|verify)",
    re.IGNORECASE,
)
_BRACKETED_INCH_SIZE_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s*\[\s*(\d+(?:\.\d+)?)\s*\]"
    r"(?:\s*[x×]\s*\d+(?:\.\d+)?\s*\[\s*(\d+(?:\.\d+)?)\s*\])?",
)
_PRESSURE_CLASS_RE = re.compile(r"\b(?:ASME\s+)?(?:CLASS|CL)\s*(\d{2,4})\b", re.IGNORECASE)
_ACTION_ITEM_SPLIT_RE = re.compile(r"(?<=[。！？；;])\s*|\n+")
_ACTION_MARKER_RE = re.compile(r"^\s*(?:[•·▪‣]|[-*]|\d+[.)、])\s*")
_ACTION_PREFIX_RE = re.compile(r"^(?:建议(?:先|优先)?|请(?:先|优先)?|需要(?:先|优先)?)\s*")
_ACTION_CONNECTOR_RE = re.compile(r"并且\s*")
_ACTION_GROUP_KEYWORDS = {
    "air": ("气源", "供气", "过滤减压阀", "气路", "气管", "泄漏", "堵塞", "放大器"),
    "mechanical": ("填料", "阀杆", "阀芯", "卡涩", "摩擦", "导向", "密封", "弹簧", "膜片"),
    "positioner": ("定位器", "反馈", "零点", "量程", "标定", "校准", "整定", "增益", "阻尼", "死区"),
    "retest": ("复测", "重测", "重新执行", "补充测试", "测试"),
    "field": ("现场", "工况", "DCS", "趋势", "运行", "控制影响"),
}
_ACTION_OPERATION_RE = re.compile(
    r"^(?:优先|尽快|先|检查|核查|复核|确认|处理|调整|校准|整定|复测|重新执行|补充|维持|持续|安排|保留)"
)


def _action_compare_key(value: str) -> str:
    """Build a conservative comparison key without changing customer text."""
    return re.sub(
        r"[\s，,。；;：:、（）()\[\]【】]",
        "",
        value.casefold(),
    )


def _action_clause(value: Any) -> list[str]:
    """Split one action field into customer-readable clauses."""
    text = str(value or "").strip()
    if not text:
        return []
    clauses: list[str] = []
    for part in _ACTION_ITEM_SPLIT_RE.split(text):
        part = _ACTION_MARKER_RE.sub("", part).strip()
        if not part:
            continue
        # Keep the action's evidence and constraints, while removing only
        # presentation fillers that make every bullet start the same way.
        part = _ACTION_PREFIX_RE.sub("", part)
        part = _ACTION_CONNECTOR_RE.sub("并", part)
        part = re.sub(r"\s+", " ", part).strip()
        part = part.rstrip("；;").strip()
        if part and part[-1] not in "。！？.!?":
            part += "。"
        if part:
            clauses.append(part)
    return clauses


def _action_groups(value: str) -> set[str]:
    return {
        group
        for group, keywords in _ACTION_GROUP_KEYWORDS.items()
        if any(keyword.casefold() in value.casefold() for keyword in keywords)
    }


def _action_operation(value: str) -> str:
    match = _ACTION_OPERATION_RE.match(value)
    return match.group(0) if match else ""


def _merge_related_action_items(items: list[str]) -> list[str]:
    """Merge repeated action families while retaining each detail clause."""
    merged: list[str] = []
    for item in items:
        groups = _action_groups(item)
        operation = _action_operation(item)
        related_index = next(
            (
                index
                for index, existing in enumerate(merged)
                if groups
                and groups & _action_groups(existing)
                and (
                    operation == _action_operation(existing)
                    or "retest" in groups
                )
            ),
            None,
        )
        if related_index is None:
            merged.append(item)
            continue
        existing = merged[related_index].rstrip("。！？.!?；; ")
        addition = item
        if (
            operation
            and operation == _action_operation(existing)
            and addition.startswith(operation)
        ):
            addition = addition[len(operation):].lstrip(" ，,、")
        merged[related_index] = f"{existing}；{addition}"
    return merged


def simplify_action_items(
    values: Any,
    *,
    max_items: int | None = None,
) -> list[str]:
    """Return concise, de-duplicated action bullets for overview tables.

    This is deliberately a presentation-only transform.  It splits long
    action paragraphs at sentence/semicolon boundaries and removes exact or
    containment duplicates, but retains the original technical wording and
    constraints such as priority, field verification, retesting, and a
    maintenance window.  ``max_items`` is opt-in; the overall action list
    leaves it unset so no safety-critical action is silently truncated.
    """
    raw_values = values if isinstance(values, (list, tuple)) else [values]
    clauses: list[str] = []
    for value in raw_values:
        clauses.extend(_action_clause(value))

    unique: list[str] = []
    keys: list[str] = []
    for clause in clauses:
        key = _action_compare_key(clause)
        if not key:
            continue
        duplicate_index = next(
            (
                index
                for index, existing_key in enumerate(keys)
                if existing_key == key
                or existing_key in key
                or key in existing_key
            ),
            None,
        )
        if duplicate_index is None:
            unique.append(clause)
            keys.append(key)
        elif len(clause) > len(unique[duplicate_index]):
            # Prefer the more specific version when one clause contains the
            # other; this prevents deduplication from dropping a constraint.
            unique[duplicate_index] = clause
            keys[duplicate_index] = key

    if max_items is not None and len(unique) > max_items:
        # Never discard an action.  Fold only the tail into the last bullet;
        # this keeps the table compact while retaining every requirement.
        unique = unique[: max_items - 1] + ["；".join(unique[max_items - 1 :])]
    return _merge_related_action_items(unique)


def _value(value: Any) -> str:
    text = str(value or "").strip()
    return text if text else MISSING


def display_specification_value(value: Any) -> str:
    """Render an empty Specification cell as a concise dash."""
    text = str(value or "").strip()
    return SPECIFICATION_MISSING if text in _SPECIFICATION_EMPTY_VALUES else text


def display_manufacturer(value: Any) -> str:
    """Return the customer display name for Masoneilan equipment."""
    text = _value(value)
    if text != MISSING and "masoneilan" in text.casefold():
        return "Masoneilan"
    return text


def display_positioner_model(value: Any) -> str:
    """Remove parenthetical configuration details from a positioner model."""
    text = _PARENTHETICAL_RE.sub(" ", _value(value))
    return re.sub(r"\s+", " ", text).strip() or MISSING


def display_actuator_style(value: Any) -> str:
    """Display an unambiguous actuator action style in Chinese."""
    text = _value(value)
    normalized = re.sub(r"\s+", " ", text).strip().casefold()
    if normalized in {"single acting", "单作用"}:
        return "单作用"
    if normalized in {"double acting", "双作用"}:
        return "双作用"
    return text


def display_air_action(value: Any) -> str:
    """Display an unambiguous air action in Chinese."""
    text = _value(value)
    normalized = re.sub(r"\s+", " ", text).strip().casefold()
    if normalized in {"air to open", "气动打开", "气开"}:
        return "气开"
    if normalized in {"air to close", "气动关闭", "气关"}:
        return "气关"
    return text


def display_valve_type(value: Any) -> str:
    """Omit parenthetical review qualifiers while preserving valve type evidence."""
    text = _value(value)
    if text == MISSING:
        return text

    stack: list[tuple[str, int]] = []
    review_spans: list[tuple[int, int]] = []
    closing = {")": "(", "）": "（"}
    for index, character in enumerate(text):
        if character in "(（":
            stack.append((character, index))
        elif character in closing and stack and stack[-1][0] == closing[character]:
            _, start = stack.pop()
            if _REVIEW_QUALIFIER_RE.search(text[start + 1 : index]):
                review_spans.append((start, index + 1))

    outermost_review_spans: list[tuple[int, int]] = []
    for start, end in sorted(review_spans, key=lambda span: (span[0], -span[1])):
        if not any(
            outer_start <= start and end <= outer_end
            for outer_start, outer_end in outermost_review_spans
        ):
            outermost_review_spans.append((start, end))

    for start, end in reversed(outermost_review_spans):
        text = text[:start] + " " + text[end:]
    return re.sub(r"\s+", " ", text).strip() or MISSING


def display_size_pressure(valve_size: Any, pressure_class: Any) -> str:
    """Display valve size and pressure class, retaining each missing-value marker."""
    return (
        f"{display_specification_value(valve_size)} / "
        f"{display_specification_value(pressure_class)}"
    )


def display_fail_position(value: Any) -> str:
    """Translate only unambiguous OPEN/CLOSE fail-position values."""
    text = str(value or "").strip().upper()
    if text == "OPEN":
        return "Fail Open"
    if text in {"CLOSE", "CLOSED"}:
        return "Fail Close"
    return SPECIFICATION_MISSING


def _size_pressure_from_spec_source(value: Any) -> tuple[str, str]:
    """Extract only unambiguous inch size and pressure class from confirmed spec text."""
    text = str(value or "")
    size_match = _BRACKETED_INCH_SIZE_RE.search(text)
    if size_match:
        size = f"{size_match.group(1)} in"
        if size_match.group(2):
            size += f" x {size_match.group(2)} in"
    else:
        size = ""
    pressure_match = _PRESSURE_CLASS_RE.search(text)
    pressure = f"CL{pressure_match.group(1)}" if pressure_match else ""
    return size, pressure


def basic_information_rows(
    *,
    manufacturer: Any,
    positioner_model: Any,
    valve_model: Any,
    valve_type: Any,
    spec_fields: Mapping[str, Any],
    valve_spec: Any = "",
    spring_range: Any = "",
) -> list[tuple[str, str]]:
    """Build the concise customer-facing basic-information table rows."""
    fallback_size, fallback_pressure = _size_pressure_from_spec_source(valve_spec)
    return [
        ("厂家", display_manufacturer(manufacturer)),
        ("定位器型号", display_positioner_model(positioner_model)),
        (
            "阀门型号 / 类型",
            f"{_value(valve_model)} / {display_valve_type(valve_type)}",
        ),
        (
            "阀门尺寸 / 压力等级",
            display_size_pressure(
                spec_fields.get("valve_size") or fallback_size,
                spec_fields.get("pressure_class") or fallback_pressure,
            ),
        ),
        ("失效位置", display_fail_position(spec_fields.get("fail_position"))),
        ("弹簧范围", display_specification_value(spring_range)),
    ]


def display_model_spec(
    valve_model: Any,
    spec_fields: Mapping[str, Any],
    valve_spec: Any = "",
) -> str:
    """Display model plus size/pressure only; never expose Cv."""
    fallback_size, fallback_pressure = _size_pressure_from_spec_source(valve_spec)
    values = [
        _value(valve_model),
        _value(spec_fields.get("valve_size") or fallback_size),
        _value(spec_fields.get("pressure_class") or fallback_pressure),
    ]
    visible = [value for value in values if value != MISSING]
    return " / ".join(visible) or MISSING


def format_number(value: Any, unit: str = "") -> str:
    if isinstance(value, str) and unit.strip():
        value = value.strip().removesuffix(unit.strip()).strip()
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return MISSING
    if abs(number) >= 100:
        text = f"{number:.1f}"
    elif abs(number) >= 10:
        text = f"{number:.2f}"
    else:
        text = f"{number:.3f}"
    return f"{text.rstrip('0').rstrip('.')}{unit}"


def format_friction_metric(metrics: Mapping[str, Any], statistic: str) -> str:
    """Format one Signature statistic as percentage plus its paired force."""
    display = metrics.get("Friction Display") or {}
    item = display.get(statistic) if isinstance(display, dict) else None
    if isinstance(item, dict):
        parts = []
        if format_number(item.get("percent")) != MISSING:
            parts.append(format_number(item["percent"], "%"))
        if format_number(item.get("absolute")) != MISSING:
            unit = str(item.get("absolute_unit") or "").strip()
            unit = {"kpa": "kPa", "mpa": "MPa"}.get(unit.lower(), unit)
            parts.append(format_number(item["absolute"], f" {unit}" if unit else ""))
        if parts:
            return " / ".join(parts)
    metric_key = {
        "max": "Max Friction",
        "min": "Min Friction",
        "mean": "Mean Friction",
    }[statistic]
    return format_number(metrics.get(metric_key), str(metrics.get("Friction Unit") or ""))


def clean_fault(text: str) -> str:
    text = re.sub(r"\s+", " ", str(text)).strip()
    text = re.sub(r"^(I/P Current|Supply Pressure).*$", "", text)
    return text.strip(" ;")


def curve_execution_time_label(image: Mapping[str, Any]) -> str:
    """Return a compact 24-hour test window suitable for a figure caption."""
    from datetime import datetime

    start = str(image.get("test_start_time") or "").strip()
    end = str(image.get("test_end_time") or "").strip()
    if not start:
        return ""

    def parse(value: str) -> datetime | None:
        try:
            return datetime.strptime(value, "%m/%d/%Y %I:%M:%S %p")
        except ValueError:
            return None

    start_dt = parse(start)
    end_dt = parse(end) if end else None
    if start_dt is None:
        return start if not end or end == start else f"{start}–{end}"
    if end_dt is not None and end_dt.date() == start_dt.date():
        return f"{start_dt:%Y-%m-%d %H:%M:%S}–{end_dt:%H:%M:%S}"
    if end_dt is not None:
        return f"{start_dt:%Y-%m-%d %H:%M:%S}–{end_dt:%Y-%m-%d %H:%M:%S}"
    return f"{start_dt:%Y-%m-%d %H:%M:%S}"


def normalize_customer_terms(text: str) -> str:
    """Apply report terminology without changing frozen evidence."""
    normalized = str(text)
    for source in (
        "Extended Actuator Signature",
        "Extended Act. Signature",
        "扩展执行机构签名曲线",
        "扩展执行机构签名",
        "执行机构签名图",
        "扩展签名",
    ):
        normalized = normalized.replace(source, "执行机构性能测试")
    return normalized


def sanitize_customer_text(text: str) -> str:
    """Remove internal image paths and names before customer text is emitted."""
    sanitized = _INTERNAL_IMAGE_TOKEN_RE.sub("", str(text))
    sanitized = normalize_customer_terms(sanitized)
    return re.sub(r"([：:])\s+", r"\1", sanitized).strip()


def replace_customer_terms(text: str, replacements: Mapping[str, str] | None) -> str:
    """Use the same final terminology in report rendering and acceptance."""
    for source, target in sorted(
        ((str(k), str(v)) for k, v in (replacements or {}).items() if str(k)),
        key=lambda pair: len(pair[0]),
        reverse=True,
    ):
        text = text.replace(source, target)
    return normalize_customer_terms(text)


def test_execution_issue_text(item: Mapping[str, Any]) -> str:
    occurrence = int(item.get("occurrence") or 1)
    suffix = f"（第{occurrence}次）" if occurrence > 1 else ""
    status = str(item.get("status") or "").strip()
    status_zh = TEST_EXECUTION_STATUS_ZH.get(status.lower(), status or "未完成")
    test_name = (
        "执行机构性能测试"
        if item.get("test_key") == "extended_signature"
        else item.get("test_name") or "诊断测试"
    )
    return f"{test_name}{suffix}：{status_zh}（原始报告状态 {status}）"
