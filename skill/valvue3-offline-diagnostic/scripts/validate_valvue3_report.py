#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import posixpath
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from docx import Document

from offline_engine.business_report import resolve_report_evidence_value
from offline_engine.config import ConfigError, ProjectConfig, load_project
from offline_engine.presentation import replace_customer_terms, sanitize_customer_text
from offline_engine.report_text import missing_pdf_body_text, normalize_report_text


DEFAULT_DOCX_NAME = "report.docx"
FIGURE_LABEL = "图形判读"
EXPERIENCE_LABEL = "经验反馈引用"
LEGACY_SINGLE_CURVE_OUTPUT_TEXT = (
    "本图评级依据：",
    "图形判读为",
    "建议行动：",
    "图形判读（客观观察）：",
    "客观观察：",
    "诊断：",
)
FORBIDDEN_CUSTOMER_TEXT = (
    "待复核",
    "confidence",
    "experience_rule_ids",
    "Codex",
    "中间评分",
    "/work/",
    "\\\\work\\\\",
    "/raw/",
    "\\\\raw\\\\",
    "devdata",
    "data_rating",
    "visual_rating",
    "final_rating",
)
FORBIDDEN_PATH_OR_SCORE_PATTERN = re.compile(
    r"(?:[A-Za-z]:[\\/]|/(?:home|tmp|workspace|users|var|opt|mnt|data)(?:[\\/]|\b)|(?:^|\s)(?:\.{1,2}[\\/]|(?:work|raw|output|inbound|specs)[\\/])\S+|\b[^\s]+\.(?:png|jpe?g|gif|bmp|webp)\b|内部[^。；\n]{0,12}评分|(?:算法|自动|初步|中间)[^。；\n]{0,12}评分)",
    re.IGNORECASE,
)
REPORT_PAGE_ROLES = frozenset({"specification", "air_piping", "accessory"})
REQUIRED_STRUCTURED_EVIDENCE_PATHS = (
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
    "file.report_start_time",
    "file.report_end_time",
    "file.report_time",
    "status.active_faults",
    "status.historical_faults",
    "status.task_execution",
    "metrics.run_ramp.hysteresis.max",
    "metrics.run_ramp.hysteresis.average",
    "metrics.run_ramp.hysteresis.min",
    "metrics.run_ramp.dead_band.max",
    "metrics.run_ramp.dead_band.average",
    "metrics.run_ramp.dead_band.min",
    "metrics.run_ramp.linearity.max",
    "metrics.run_ramp.linearity.average",
    "metrics.run_ramp.linearity.min",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate a generated ValVue3 diagnostic report.")
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--docx", type=Path)
    parser.add_argument("--pdf", type=Path)
    parser.add_argument("--summary", type=Path)
    parser.add_argument(
        "--report-period",
        help="Select the configured report period when resolving default paths and features.",
    )
    parser.add_argument("--min-figure-interpretations", type=int, default=1)
    parser.add_argument("--require-finalized-curve-review", action="store_true")
    parser.add_argument("--allow-no-spec-match", action="store_true")
    parser.add_argument(
        "--information-only",
        action="store_true",
        help=(
            "Run the compact customer-information acceptance check. "
            "Internal consistency checks remain available by omitting this flag."
        ),
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON only.")
    return parser.parse_args()


def docx_text(path: Path) -> tuple[bool, str, str]:
    if not path.exists():
        return False, "", f"DOCX not found: {path}"
    if not zipfile.is_zipfile(path):
        return False, "", f"DOCX is not a valid zip package: {path}"
    chunks: list[str] = []
    try:
        with zipfile.ZipFile(path) as package:
            for name in package.namelist():
                if not name.startswith("word/") or not name.endswith(".xml"):
                    continue
                root = ET.fromstring(package.read(name))
                for paragraph in (node for node in root.iter() if node.tag.endswith("}p")):
                    text = "".join(
                        node.text for node in paragraph.iter()
                        if node.tag.endswith("}t") and node.text
                    )
                    if text:
                        chunks.append(text)
    except Exception as exc:  # noqa: BLE001 - validation should report any package/XML failure.
        return False, "", f"DOCX text extraction failed: {exc}"
    return True, "\n".join(chunks), ""


def legacy_single_curve_output_text(text: str) -> list[str]:
    lines = ["".join(line.split()) for line in text.splitlines()]
    return [token for token in LEGACY_SINGLE_CURVE_OUTPUT_TEXT if any(line.startswith(token) for line in lines)]


def contains_section_heading(text: str, heading: str) -> bool:
    for raw_line in text.splitlines():
        line = "".join(raw_line.split())
        line = re.sub(r"^(?:第)?\d+(?:[.．、]|章节?)*", "", line)
        if line == heading:
            return True
    return False


def contains_attachment_heading(text: str, terms: tuple[str, ...]) -> bool:
    for raw_line in text.splitlines():
        line = "".join(raw_line.split()).lower()
        if any(line == term.lower() or line.endswith("|" + term.lower()) for term in terms):
            return True
    return False


def docx_curve_review_markers(path: Path) -> tuple[bool, set[str], str]:
    if not path.exists() or not zipfile.is_zipfile(path):
        return False, set(), "DOCX is unavailable for curve marker validation."
    try:
        with zipfile.ZipFile(path) as package:
            root = ET.fromstring(package.read("word/document.xml"))
    except Exception as exc:  # noqa: BLE001
        return False, set(), f"DOCX curve marker extraction failed: {exc}"
    return True, {
        value for node in root.iter() if node.tag.endswith("}bookmarkStart")
        if (value := next((item for key, item in node.attrib.items() if key.endswith("}name")), "")).startswith("curve-review-")
    }, ""


def docx_spec_insertion_markers(
    path: Path,
) -> tuple[bool, dict[str, tuple[int, str]], str]:
    if not path.exists() or not zipfile.is_zipfile(path):
        return False, {}, "DOCX is unavailable for specification insertion validation."
    try:
        with zipfile.ZipFile(path) as package:
            root = ET.fromstring(package.read("word/document.xml"))
            relationships = ET.fromstring(package.read("word/_rels/document.xml.rels"))
            targets = {
                node.attrib.get("Id", ""): (
                    node.attrib.get("Target", ""),
                    node.attrib.get("Type", ""),
                )
                for node in relationships
            }
            package_names = set(package.namelist())
    except Exception as exc:  # noqa: BLE001
        return False, {}, f"DOCX specification marker extraction failed: {exc}"
    prefixes = ("spec-insert-", "air-piping-insert-", "accessory-insert-")
    valid: dict[str, tuple[int, str]] = {}
    for paragraph_index, paragraph in enumerate(
        node for node in root.iter() if node.tag.endswith("}p")
    ):
        markers = {
            value for node in paragraph.iter() if node.tag.endswith("}bookmarkStart")
            if (value := next((item for key, item in node.attrib.items() if key.endswith("}name")), "")).startswith(prefixes)
        }
        if not markers:
            continue
        image_relationships = {
            value for node in paragraph.iter() if node.tag.endswith("}blip")
            for key, value in node.attrib.items() if key.endswith("}embed")
        }
        valid_media_targets: set[str] = set()
        for relationship_id in image_relationships:
            if relationship_id not in targets:
                continue
            target, relationship_type = targets[relationship_id]
            normalized_target = posixpath.normpath(posixpath.join("word", target.lstrip("/")))
            if (
                relationship_type.endswith("/image")
                and normalized_target.startswith("word/media/")
                and normalized_target in package_names
            ):
                valid_media_targets.add(normalized_target)
        if len(valid_media_targets) == 1:
            target = next(iter(valid_media_targets))
            valid.update({marker: (paragraph_index, target) for marker in markers})
    return True, valid, ""


def deterministic_spec_marker(folder: str, page_kind: str) -> str:
    prefix = {
        "accessory": "accessory-insert",
        "air_piping": "air-piping-insert",
    }.get(page_kind, "spec-insert")
    identity = f"{folder.strip()}\x1f{page_kind}"
    return f"{prefix}-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:24]}"


def _page_roles(raw: object) -> set[str]:
    if isinstance(raw, str):
        return {raw.strip()} if raw.strip() else set()
    if isinstance(raw, (list, tuple)):
        return {str(item).strip() for item in raw if str(item).strip()}
    return set()


def _physical_page_identity(page: dict[str, object]) -> str:
    source_sha256 = str(page.get("source_sha256") or "").strip().lower()
    try:
        page_number = int(page.get("page") or 0)
    except (TypeError, ValueError):
        page_number = 0
    return f"{source_sha256}:{page_number}"


def expected_spec_insertion_markers(
    path: Path,
    *,
    enabled_roles: set[str] | frozenset[str] | None = None,
) -> tuple[dict[str, str], dict[str, set[str]], list[str]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}, {}, []
    valves = data if isinstance(data, list) else data.get("valves", [])
    expected: dict[str, str] = {}
    physical_roles: dict[str, set[str]] = {}
    errors: list[str] = []
    for valve in valves:
        if not isinstance(valve, dict) or valve.get("included_in_report") is False:
            continue
        folder = str(valve.get("folder") or valve.get("tag") or "")
        report_pages = valve.get("report_pages")
        if isinstance(report_pages, list) and report_pages:
            valve_physical_roles: dict[str, set[str]] = {}
            for index, raw_page in enumerate(report_pages):
                if not isinstance(raw_page, dict):
                    errors.append(f"{folder}: report_pages[{index}] is not an object")
                    continue
                roles = _page_roles(raw_page.get("roles"))
                unknown_roles = roles - REPORT_PAGE_ROLES
                if not roles or unknown_roles:
                    errors.append(
                        f"{folder}: report_pages[{index}] has invalid roles: "
                        + ", ".join(sorted(unknown_roles or roles))
                    )
                    continue
                if enabled_roles is not None:
                    roles &= set(enabled_roles)
                    if not roles:
                        continue
                identity = _physical_page_identity(raw_page)
                source_sha256, _, page_number = identity.partition(":")
                if not re.fullmatch(r"[0-9a-f]{64}", source_sha256) or int(page_number or 0) < 1:
                    errors.append(
                        f"{folder}: report_pages[{index}] lacks a valid source_sha256 + one-based page identity"
                    )
                    continue
                valve_physical_roles.setdefault(identity, set()).update(roles)
                global_identity = f"{folder}\x1f{identity}"
                physical_roles.setdefault(global_identity, set()).update(roles)
            for identity, roles in valve_physical_roles.items():
                for role in sorted(roles):
                    marker = deterministic_spec_marker(folder, role)
                    if marker in expected and expected[marker] != identity:
                        errors.append(f"{folder}: duplicate {role} pages cannot share one role bookmark")
                    expected[marker] = f"{folder}\x1f{identity}"
            continue
        for page_kind, page_field, marker_field in (
            ("spec", "spec_page", "spec_insertion_marker"),
            ("air_piping", "air_piping_page", "air_piping_insertion_marker"),
            ("accessory", "accessory_page", "accessory_insertion_marker"),
        ):
            canonical_role = (
                "specification" if page_kind == "spec" else page_kind
            )
            if enabled_roles is not None and canonical_role not in enabled_roles:
                continue
            declared = str(valve.get(marker_field) or "")
            if isinstance(valve.get(page_field), dict):
                marker = deterministic_spec_marker(folder, page_kind)
                expected[marker] = f"legacy:{folder}:{page_kind}"
                if declared != marker:
                    errors.append(f"{folder}: {marker_field} is missing or invalid")
            elif declared:
                errors.append(f"{folder}: {marker_field} exists without {page_field}")
    return expected, physical_roles, errors


def _try_load_project_config(
    project: Path,
    *,
    report_period: str | None = None,
) -> tuple[ProjectConfig | None, str]:
    """Load the one project configuration contract used by report generation.

    Synthetic validator fixtures may omit ``project.toml`` entirely, in which
    case the documented feature defaults remain useful.  A present but partial
    legacy fixture is likewise treated as an unconfigured project here; real
    project entry points still reject that configuration through ``load_project``.
    """
    try:
        return load_project(project, report_period=report_period), ""
    except ConfigError as exc:
        return None, str(exc)


def project_report_page_features(
    project: Path,
    *,
    config: ProjectConfig | None = None,
    report_period: str | None = None,
) -> dict[str, bool]:
    defaults = {
        "specification": True,
        "air_piping": True,
        "accessory": False,
    }
    config = config or _try_load_project_config(
        project,
        report_period=report_period,
    )[0]
    if config is None:
        return defaults
    return {
        "specification": bool(config.features.get("include_specifications", True)),
        "air_piping": bool(config.features.get("include_air_piping_pages", True)),
        "accessory": config.features.get("include_accessory_pages") is True,
    }


def disabled_overview_sections(
    project: Path,
    *,
    config: ProjectConfig | None = None,
    report_period: str | None = None,
) -> tuple[str, ...]:
    config = config or _try_load_project_config(
        project,
        report_period=report_period,
    )[0]
    presentation = config.presentation if config is not None else {}
    switches = {
        "现场信息清单": "include_field_context_overview",
        "整体判断": "include_overview_judgement",
        "首要关注": "include_overview_priority",
        "后续复测、维护和检修建议": "include_retest_recommendations",
    }
    return tuple(
        heading for heading, switch in switches.items()
        if presentation.get(switch, False) is not True
    )


def project_uses_formal_diagnosis(
    project: Path,
    *,
    config: ProjectConfig | None = None,
    report_period: str | None = None,
    report_path: Path | None = None,
) -> bool:
    config = config or _try_load_project_config(
        project,
        report_period=report_period,
    )[0]
    if config is None:
        return False
    # A formal path can remain in a shared project config while a factual
    # data-only report is being produced.  That mode intentionally has no
    # frozen-diagnosis/field-binding requirement.
    if config.formal_diagnosis_run_dir is None:
        return False
    if config.presentation.get("data_only") is True:
        return False
    # ``generate_data_report`` uses a config override that is not persisted in
    # project.toml.  Its stable output location is therefore the second
    # compatibility signal when WPS delivery validation reloads the base
    # project config.
    if report_path is not None:
        try:
            relative = report_path.resolve().relative_to(config.project_dir)
        except ValueError:
            relative = None
        if relative is not None:
            parts = relative.parts
            if len(parts) >= 2 and parts[0] == "output" and parts[1] == "data":
                return False
    return True


def _nested_value(data: object, path: str) -> tuple[bool, object]:
    current = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return False, None
        current = current[part]
    return True, current


def _contract_evidence_view(evidence: dict[str, object]) -> dict[str, object]:
    """Adapt the frozen bundle wrapper to the section-7 field contract."""
    source_record = evidence.get("source_record")
    if not isinstance(source_record, dict):
        return evidence
    report_info = source_record.get("positioner_report_info")
    if not isinstance(report_info, dict):
        report_info = {}
    return {
        "positioner": report_info.get("positioner", {}),
        "runtime": report_info.get("runtime", {}),
        "file": report_info.get("file", {}),
        "status": report_info.get("status", {}),
        "metrics": source_record.get("metrics", {}),
    }


def structured_evidence_failures(summary: Path) -> tuple[int, list[str]]:
    try:
        raw = json.loads(summary.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return 0, []
    valves = raw if isinstance(raw, list) else raw.get("valves", [])
    checked = 0
    failures: list[str] = []
    for valve in valves:
        if not isinstance(valve, dict) or valve.get("included_in_report") is False:
            continue
        evidence = valve.get("structured_evidence")
        if not isinstance(evidence, dict):
            continue
        checked += 1
        tag = str(valve.get("folder") or valve.get("tag") or "UNKNOWN")
        for field in REQUIRED_STRUCTURED_EVIDENCE_PATHS:
            present, value = resolve_report_evidence_value(evidence, field)
            declared, _field_record = _declared_material_field(evidence, field)
            if (
                not declared
                and (
                    not present
                    or value is None
                    or (isinstance(value, str) and not value.strip())
                )
            ):
                failures.append(f"{tag}: structured_evidence missing required field {field}")
        _tasks_present, tasks = resolve_report_evidence_value(evidence, "status.task_execution")
        for index, task in enumerate(tasks if isinstance(tasks, list) else []):
            if not isinstance(task, dict) or any(
                not str(task.get(key) or "").strip()
                for key in ("task_name", "start_time", "end_time", "result")
            ):
                failures.append(f"{tag}: structured_evidence task_execution[{index}] is incomplete")
    return checked, failures


def _comparable_value(value: object) -> object:
    if isinstance(value, dict):
        for key in ("normalized_value", "value", "selected"):
            if key in value:
                return _comparable_value(value[key])
    return value


def _declared_material_field(
    structured: object,
    path: str,
) -> tuple[bool, dict[str, object] | None]:
    """Return a Stage 2 field record even when its selected value is missing.

    A complete extraction matrix distinguishes a declared source gap from an
    omitted parser field. Customer reports must show that declared gap as
    ``未提供``/``数据不足`` instead of failing the information gate merely
    because no numeric value exists.
    """
    if not isinstance(structured, dict):
        return False, None
    material = structured.get("material_extraction")
    fields = material.get("fields") if isinstance(material, dict) else None
    field = fields.get(path) if isinstance(fields, dict) else None
    return (isinstance(field, dict), field if isinstance(field, dict) else None)


def report_field_manifest_failures(summary: Path, docx_body: str) -> tuple[int, list[str]]:
    """Bind declared customer text to the same structured evidence snapshot.

    Producers may add ``report_field_manifest`` once a valve is backed by frozen
    structured evidence. Each entry names the evidence path, its snapshotted
    value, and the exact customer-visible text expected in the DOCX.
    """
    try:
        raw = json.loads(summary.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return 0, []
    valves = raw if isinstance(raw, list) else raw.get("valves", [])
    checked = 0
    failures: list[str] = []
    for valve in valves:
        if not isinstance(valve, dict) or valve.get("included_in_report") is False:
            continue
        entries = valve.get("report_field_manifest")
        if not isinstance(entries, list):
            continue
        checked += 1
        tag = str(valve.get("folder") or valve.get("tag") or "UNKNOWN")
        evidence = valve.get("structured_evidence")
        if not isinstance(evidence, dict):
            failures.append(f"{tag}: report_field_manifest exists without structured_evidence")
            continue
        seen: set[str] = set()
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                failures.append(f"{tag}: report_field_manifest[{index}] is not an object")
                continue
            path = str(entry.get("path") or "").strip()
            display_text = str(entry.get("display_text") or "").strip()
            present, evidence_value = resolve_report_evidence_value(evidence, path)
            declared, _field_record = _declared_material_field(evidence, path)
            if not path or (not present and not declared):
                failures.append(f"{tag}: report field path is absent from structured_evidence: {path or index}")
                continue
            seen.add(path)
            if (
                "evidence_value" in entry
                and _comparable_value(evidence_value) != entry["evidence_value"]
            ):
                failures.append(f"{tag}: report field {path} differs from frozen structured evidence")
            if not display_text or display_text not in docx_body:
                failures.append(f"{tag}: DOCX does not contain declared report field text for {path}")
        missing = set(REQUIRED_STRUCTURED_EVIDENCE_PATHS) - seen
        if missing:
            failures.append(
                f"{tag}: report_field_manifest does not cover required fields: "
                + ", ".join(sorted(missing))
            )
    return checked, failures


def pdf_text(path: Path | None) -> tuple[bool | None, str, str]:
    if path is None:
        return None, "", "PDF path not provided."
    if not path.exists():
        return False, "", f"PDF not found: {path}"
    tool = shutil.which("pdftotext")
    if not tool:
        return False, "", "pdftotext is not available."
    result = subprocess.run(
        [tool, "-layout", str(path), "-"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        return False, "", result.stderr.strip() or "pdftotext failed."
    return True, result.stdout, ""


def load_summary(path: Path) -> tuple[int | None, int, int, set[str], list[str], str]:
    if not path.exists():
        return None, 0, 0, set(), [], f"Summary JSON not found: {path}"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return None, 0, 0, set(), [], f"Summary JSON parse failed: {exc}"
    valves = data if isinstance(data, list) else data.get("valves", [])
    review_items: list[str] = []
    curve_count = 0
    validated_curve_count = 0
    validated_markers: set[str] = set()
    included_valve_count = 0
    for valve in valves:
        if not isinstance(valve, dict):
            continue
        if valve.get("included_in_report") is False:
            continue
        included_valve_count += 1
        tag = str(valve.get("folder") or valve.get("tag") or "UNKNOWN")
        if valve.get("rating") == 5:
            review_items.append(f"{tag}: 5级/数据不足")
        if valve.get("data_note"):
            review_items.append(f"{tag}: {valve['data_note']}")
        for item in valve.get("unreadable") or []:
            review_items.append(f"{tag}: unreadable {item}")
        images = valve.get("images") or []
        curve_count += len(images)
        validated_curve_count += sum(
            1 for image in images if isinstance(image, dict) and image.get("interpretation_validated") is True
        )
        validated_markers.update(
            str(image.get("review_marker")) for image in images
            if isinstance(image, dict) and image.get("interpretation_validated") is True and image.get("review_marker")
        )
    return included_valve_count, curve_count, validated_curve_count, validated_markers, review_items, ""


def resolve_summary_path(
    project: Path,
    explicit_summary: Path | None = None,
    *,
    config: ProjectConfig | None = None,
    report_period: str | None = None,
) -> Path:
    """Resolve the report summary in the same order as the formal CLI.

    An explicitly supplied path is authoritative, including when it is
    missing (the validator must report that error rather than silently use a
    different run).  Otherwise use the effective project ``work`` directory,
    then the pre-decoupling shared-engine location for compatibility.
    """
    if explicit_summary is not None:
        return explicit_summary.resolve()
    config = config or _try_load_project_config(
        project,
        report_period=report_period,
    )[0]
    current = (
        config.work_dir / "analysis_summary.json"
        if config is not None
        else project / "work" / "analysis_summary.json"
    )
    if current.is_file():
        return current.resolve()
    return (project / "work" / "shared_engine" / "analysis_summary.json").resolve()


def specification_coverage(project: Path, summary: Path) -> tuple[int, int]:
    spec_pdf_count = sum(1 for path in (project / "specs").rglob("*.pdf") if path.is_file())
    try:
        data = json.loads(summary.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return spec_pdf_count, 0
    valves = data if isinstance(data, list) else data.get("valves", [])
    matched = 0
    for valve in valves:
        if not isinstance(valve, dict) or valve.get("included_in_report") is False:
            continue
        report_pages = valve.get("report_pages")
        if isinstance(report_pages, list) and report_pages:
            if any(
                isinstance(page, dict)
                and "specification" in _page_roles(page.get("roles"))
                for page in report_pages
            ):
                matched += 1
        elif isinstance(valve.get("spec_page"), dict):
            matched += 1
    return spec_pdf_count, matched


def _included_summary_valves(path: Path) -> tuple[list[dict[str, object]], str]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - compact acceptance reports the file error.
        return [], f"报告摘要无法读取：{exc}"
    valves = raw if isinstance(raw, list) else raw.get("valves", [])
    if not isinstance(valves, list):
        return [], "报告摘要中的阀门清单无效"
    included = [
        valve
        for valve in valves
        if isinstance(valve, dict) and valve.get("included_in_report") is not False
    ]
    return included, ""


def _short_items(items: list[str], *, limit: int = 4) -> str:
    clean = list(dict.fromkeys(str(item) for item in items if str(item).strip()))
    if len(clean) <= limit:
        return "、".join(clean)
    return "、".join(clean[:limit]) + f" 等 {len(clean)} 项"


def _marker_role(marker: str) -> str:
    if marker.startswith("spec-insert-"):
        return "Specification"
    if marker.startswith("air-piping-insert-"):
        return "气路页"
    if marker.startswith("accessory-insert-"):
        return "普通附件页"
    return "附件页"


def _marker_valve(identity: str) -> str:
    if "\x1f" in identity:
        return identity.split("\x1f", 1)[0]
    if identity.startswith("legacy:"):
        parts = identity.split(":", 2)
        return parts[1] if len(parts) > 1 else "REPORT"
    return "REPORT"


def _summarize_information_issues(
    details: list[dict[str, object]],
) -> list[str]:
    labels = {
        "valve": "阀门章节未完整进入报告",
        "required_fields": "所需字段未完整进入报告",
        "diagnosis": "诊断或建议未完整进入报告",
        "curves": "曲线或图形判读未完整进入报告",
        "technical_pages": "Specification/气路页未完整进入报告",
        "excluded_content": "报告包含不需要的内容",
        "internal_text": "报告包含内部处理文字",
        "overview": "报告缺少总体结论",
    }
    grouped: dict[str, list[dict[str, object]]] = {}
    passthrough: list[str] = []
    for detail in details:
        category = str(detail.get("category") or "")
        if category not in labels:
            message = str(detail.get("message") or "").strip()
            if message and message not in passthrough:
                passthrough.append(message)
            continue
        grouped.setdefault(category, []).append(detail)
    summarized: list[str] = []
    for category, entries in grouped.items():
        valves = {
            str(entry.get("valve") or "")
            for entry in entries
            if str(entry.get("valve") or "") not in {"", "REPORT"}
        }
        prefix = f"{len(valves)}台阀门" if valves else "报告"
        item_count = sum(
            len(entry.get("items") or [])
            for entry in entries
        )
        suffix = (
            f"，共{item_count}项"
            if item_count
            else f"（{len(entries)}项问题）"
        )
        summarized.append(f"{prefix}：{labels[category]}{suffix}")
    return passthrough + summarized


def check_required_information(
    project: Path,
    docx: Path,
    summary: Path,
    *,
    pdf: Path | None = None,
    config: ProjectConfig | None = None,
    report_period: str | None = None,
) -> dict[str, object]:
    """Check only whether the customer-required information reached the report.

    Detailed hash, inheritance, and implementation diagnostics remain in the
    deep validator.  This compact contract is the default report-generation
    gate: report files must open, every declared valve/curve/required field and
    enabled technical page must be present, and excluded/internal content must
    stay out of the customer document.
    """
    project = project.resolve()
    docx = docx.resolve()
    summary = summary.resolve()
    pdf = pdf.resolve() if pdf is not None else None
    config = config or _try_load_project_config(
        project,
        report_period=report_period,
    )[0]
    formal = project_uses_formal_diagnosis(
        project,
        config=config,
        report_period=report_period,
        report_path=docx,
    )
    data_only = bool(config and config.presentation.get("data_only"))
    term_replacements = config.presentation.get("term_replacements") if config else None

    def customer_text(value: object) -> str:
        return replace_customer_terms(sanitize_customer_text(str(value)), term_replacements)
    issues: list[str] = []
    details: list[dict[str, object]] = []

    def add_issue(
        valve: str,
        category: str,
        message: str,
        items: list[str] | None = None,
    ) -> None:
        prefix = f"{valve}：" if valve and valve != "REPORT" else ""
        issues.append(prefix + message)
        details.append(
            {
                "valve": valve or "REPORT",
                "category": category,
                "message": message,
                "items": list(items or []),
            }
        )

    valves, summary_error = _included_summary_valves(summary)
    if summary_error:
        add_issue("REPORT", "summary", summary_error)

    docx_ok, docx_body, docx_error = docx_text(docx)
    if not docx_ok:
        add_issue("REPORT", "document", f"DOCX 无法打开：{docx_error}")
        docx_body = ""

    pdf_ok: bool | None = None
    pdf_body = ""
    if pdf is not None:
        pdf_ok, pdf_body, pdf_error = pdf_text(pdf)
        if not pdf_ok:
            add_issue("REPORT", "document", f"PDF 无法读取：{pdf_error}")
        elif docx_ok:
            try:
                _, missing_pdf_text = missing_pdf_body_text(Document(docx), pdf_body)
            except Exception as exc:  # noqa: BLE001 - report malformed DOCX bodies as failures.
                add_issue("REPORT", "document", f"无法核对 DOCX 与 PDF 正文：{exc}")
            else:
                if missing_pdf_text:
                    add_issue(
                        "REPORT", "pdf",
                        f"PDF 未保留对应 DOCX 正文，共 {len(missing_pdf_text)} 项",
                        missing_pdf_text,
                    )

    counts: dict[str, dict[str, int]] = {
        "valves": {"expected": len(valves), "confirmed": 0},
        "fields": {"expected": 0, "confirmed": 0},
        "curves": {"expected": 0, "confirmed": 0},
        "technical_pages": {"expected": 0, "confirmed": 0},
    }

    marker_ok, docx_curve_markers, _marker_error = docx_curve_review_markers(docx)
    missing_curve_markers: dict[str, list[str]] = {}
    fallback_curve_count = 0
    for valve in valves:
        tag = str(valve.get("folder") or valve.get("tag") or "UNKNOWN").strip()
        if docx_ok and tag and tag in docx_body:
            counts["valves"]["confirmed"] += 1
        else:
            add_issue(tag, "valve", "阀门章节未出现在报告中")

        if pdf is not None and pdf_ok and tag and normalize_report_text(tag) not in normalize_report_text(pdf_body):
            add_issue(tag, "pdf", "PDF 中未找到该阀门")

        images = [item for item in valve.get("images") or [] if isinstance(item, dict)]
        counts["curves"]["expected"] += len(images)
        expected_markers = [
            str(item.get("review_marker"))
            for item in images
            if str(item.get("review_marker") or "").strip()
        ]
        if expected_markers:
            confirmed = [marker for marker in expected_markers if marker in docx_curve_markers]
            counts["curves"]["confirmed"] += len(confirmed)
            missing = [marker for marker in expected_markers if marker not in docx_curve_markers]
            if missing:
                missing_curve_markers[tag] = missing
        else:
            fallback_curve_count += len(images)

        if formal:
            counts["fields"]["expected"] += len(REQUIRED_STRUCTURED_EVIDENCE_PATHS)
            structured = valve.get("structured_evidence")
            entries = valve.get("report_field_manifest")
            entry_by_path = {
                str(entry.get("path") or ""): entry
                for entry in entries or []
                if isinstance(entry, dict) and str(entry.get("path") or "").strip()
            }
            missing_fields: list[str] = []
            mismatched_fields: list[str] = []
            undisplayed_fields: list[str] = []
            for path in REQUIRED_STRUCTURED_EVIDENCE_PATHS:
                present, source_value = resolve_report_evidence_value(
                    structured if isinstance(structured, dict) else None,
                    path,
                )
                entry = entry_by_path.get(path)
                declared, _field_record = _declared_material_field(
                    structured,
                    path,
                )
                if (not present and not declared) or entry is None:
                    missing_fields.append(path)
                    continue
                if (
                    "evidence_value" not in entry
                    or _comparable_value(source_value) != entry.get("evidence_value")
                ):
                    mismatched_fields.append(path)
                    continue
                display_text = customer_text(entry.get("display_text") or "")
                if not display_text or (docx_ok and display_text not in docx_body):
                    undisplayed_fields.append(path)
                    continue
                counts["fields"]["confirmed"] += 1
            if missing_fields:
                add_issue(
                    tag,
                    "required_fields",
                    "缺少所需字段：" + _short_items(missing_fields),
                    missing_fields,
                )
            if mismatched_fields:
                add_issue(
                    tag,
                    "required_fields",
                    "字段与来源不一致：" + _short_items(mismatched_fields),
                    mismatched_fields,
                )
            if undisplayed_fields:
                add_issue(
                    tag,
                    "required_fields",
                    "报告未显示字段：" + _short_items(undisplayed_fields),
                    undisplayed_fields,
                )

        if not data_only and docx_ok:
            missing_diagnosis_text = [
                customer_text(value)
                for field in ("issues", "actions")
                for value in valve.get(field) or []
                if str(value).strip()
                and customer_text(value) not in docx_body
            ]
            if missing_diagnosis_text:
                add_issue(
                    tag,
                    "diagnosis",
                    "诊断或建议未完整进入报告，共 "
                    f"{len(missing_diagnosis_text)} 项",
                    missing_diagnosis_text,
                )

    if fallback_curve_count:
        visible = docx_body.count(FIGURE_LABEL) if docx_ok else 0
        confirmed = min(fallback_curve_count, visible)
        counts["curves"]["confirmed"] += confirmed
        if confirmed < fallback_curve_count:
            add_issue(
                "REPORT",
                "curves",
                f"曲线或图形判读缺失 {fallback_curve_count - confirmed} 张",
            )
    if missing_curve_markers:
        for tag, markers in missing_curve_markers.items():
            add_issue(tag, "curves", f"曲线或图形判读缺失 {len(markers)} 张")
    if not marker_ok and counts["curves"]["expected"]:
        add_issue("REPORT", "curves", "无法确认报告中的曲线")

    report_page_features = project_report_page_features(
        project,
        config=config,
        report_period=report_period,
    )
    enabled_report_roles = {
        role for role, enabled in report_page_features.items() if enabled
    }
    spec_marker_ok, docx_page_markers, spec_marker_error = docx_spec_insertion_markers(docx)
    expected_page_markers, physical_page_roles, declaration_errors = (
        expected_spec_insertion_markers(
            summary,
            enabled_roles=enabled_report_roles,
        )
    )
    counts["technical_pages"]["expected"] = len(physical_page_roles)
    insertion_by_identity: dict[str, set[tuple[int, str]]] = {}
    missing_pages: dict[tuple[str, str], int] = {}
    for marker, identity in expected_page_markers.items():
        insertion = docx_page_markers.get(marker)
        if insertion is None:
            key = (_marker_valve(identity), _marker_role(marker))
            missing_pages[key] = missing_pages.get(key, 0) + 1
        else:
            insertion_by_identity.setdefault(identity, set()).add(insertion)
    for identity, roles in physical_page_roles.items():
        insertions = insertion_by_identity.get(identity, set())
        if len(insertions) == 1 and all(
            marker in docx_page_markers
            for marker, marker_identity in expected_page_markers.items()
            if marker_identity == identity
        ):
            counts["technical_pages"]["confirmed"] += 1
        elif len(insertions) > 1:
            add_issue(
                _marker_valve(identity),
                "technical_pages",
                "同一物理页被重复插入",
                sorted(roles),
            )
    for (tag, role), amount in missing_pages.items():
        add_issue(tag, "technical_pages", f"{role}未确认插入，共 {amount} 页")
    if not spec_marker_ok and expected_page_markers:
        add_issue(
            "REPORT",
            "technical_pages",
            "无法确认技术资料页：" + (spec_marker_error or "DOCX 结构无效"),
        )
    for error in declaration_errors:
        add_issue("REPORT", "technical_pages", "技术资料页声明无效", [error])

    unexpected_page_markers = set(docx_page_markers) - set(expected_page_markers)
    if unexpected_page_markers:
        add_issue(
            "REPORT",
            "technical_pages",
            f"报告包含 {len(unexpected_page_markers)} 个未声明的技术资料页",
        )

    accessory_present = any(
        marker.startswith("accessory-insert-") for marker in docx_page_markers
    )
    if not report_page_features["accessory"] and accessory_present:
        add_issue("REPORT", "excluded_content", "报告包含不需要的普通附件页")
    if not report_page_features["accessory"] and contains_attachment_heading(
        docx_body,
        ("Accessory", "Accessory Sheet", "附件规格", "附件清单"),
    ):
        add_issue("REPORT", "excluded_content", "报告包含不需要的附件章节")
    if contains_attachment_heading(
        docx_body,
        ("Restrictor", "Restrictor Specification", "Restrictor Sheet"),
    ):
        add_issue("REPORT", "excluded_content", "报告包含不需要的 Restrictor 页面")

    disabled_sections = [
        heading
        for heading in disabled_overview_sections(
            project,
            config=config,
            report_period=report_period,
        )
        if contains_section_heading(docx_body, heading)
    ]
    if disabled_sections:
        add_issue(
            "REPORT",
            "excluded_content",
            "报告包含已关闭板块：" + "、".join(disabled_sections),
            disabled_sections,
        )
    if formal and not data_only and docx_ok and not contains_section_heading(
        docx_body,
        "总体结论与检修建议",
    ):
        add_issue("REPORT", "overview", "缺少“总体结论与检修建议”")

    forbidden = [
        token
        for token in FORBIDDEN_CUSTOMER_TEXT
        if token.lower() in docx_body.lower()
    ]
    if forbidden:
        add_issue(
            "REPORT",
            "internal_text",
            "报告包含内部处理文字：" + _short_items(forbidden),
            forbidden,
        )
    if FORBIDDEN_PATH_OR_SCORE_PATTERN.search(docx_body):
        add_issue("REPORT", "internal_text", "报告包含内部路径、图片名或评分过程文字")

    ok = not issues
    summary_issues = _summarize_information_issues(details)
    return {
        "schema_version": 1,
        "mode": "required_information",
        "status": "information_complete" if ok else "information_missing",
        "ok": ok,
        "project": str(project),
        "docx": str(docx),
        "pdf": str(pdf) if pdf is not None else None,
        "summary": str(summary),
        "counts": counts,
        "summary_issues": summary_issues,
        "issues": issues,
        "issue_details": details,
    }


def main() -> int:
    args = parse_args()
    project = args.project.resolve()
    docx = (args.docx or project / DEFAULT_DOCX_NAME).resolve()
    project_config, config_error = _try_load_project_config(
        project,
        report_period=args.report_period,
    )
    summary = resolve_summary_path(
        project,
        args.summary,
        config=project_config,
        report_period=args.report_period,
    )
    pdf = args.pdf.resolve() if args.pdf else None

    if args.information_only:
        result = check_required_information(
            project,
            docx,
            summary,
            pdf=pdf,
            config=project_config,
            report_period=args.report_period,
        )
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            counts = result["counts"]
            print(
                ("PASS" if result["ok"] else "FAIL")
                + " "
                + " ".join(
                    f"{name}={value['confirmed']}/{value['expected']}"
                    for name, value in counts.items()
                )
            )
            for issue in result["summary_issues"]:
                print(f"- {issue}")
        return 0 if result["ok"] else 1

    docx_ok, docx_body, docx_error = docx_text(docx)
    pdf_ok, pdf_body, pdf_error = pdf_text(pdf)
    valve_count, curve_count, validated_curve_count, validated_markers, review_items, summary_error = load_summary(summary)
    spec_pdf_count, matched_spec_count = specification_coverage(project, summary)
    marker_ok, docx_markers, marker_error = docx_curve_review_markers(docx)
    spec_marker_ok, docx_spec_marker_media, spec_marker_error = docx_spec_insertion_markers(docx)
    report_page_features = project_report_page_features(
        project,
        config=project_config,
        report_period=args.report_period,
    )
    enabled_report_roles = {
        role for role, enabled in report_page_features.items() if enabled
    }
    expected_spec_marker_identities, physical_page_roles, spec_declaration_errors = (
        expected_spec_insertion_markers(
            summary,
            enabled_roles=enabled_report_roles,
        )
    )
    expected_spec_markers = set(expected_spec_marker_identities)
    docx_spec_markers = set(docx_spec_marker_media)
    structured_evidence_count, structured_evidence_errors = structured_evidence_failures(summary)
    report_field_manifest_count, report_field_errors = report_field_manifest_failures(
        summary, docx_body
    )
    finalized_markers_match = bool(
        args.require_finalized_curve_review
        and curve_count
        and validated_curve_count == curve_count
        and marker_ok
        and docx_markers == validated_markers
    )

    formal_diagnosis_configured = project_uses_formal_diagnosis(
        project,
        config=project_config,
        report_period=args.report_period,
        report_path=docx,
    )
    result = {
        "project": str(project),
        "docx": str(docx),
        "pdf": str(pdf) if pdf else None,
        "summary": str(summary),
        "summary_selection": (
            "explicit"
            if args.summary is not None
            else "project_work"
            if project_config is not None and summary == (project_config.work_dir / "analysis_summary.json").resolve()
            else "legacy_shared_engine"
        ),
        "report_period": args.report_period,
        "formal_diagnosis_configured": formal_diagnosis_configured,
        "config_error": config_error or None,
        "docx_zip_ok": docx_ok,
        "docx_error": docx_error,
        "pdf_text_ok": pdf_ok,
        "pdf_error": pdf_error,
        "summary_error": summary_error,
        "valve_count": valve_count,
        "summary_curve_count": curve_count,
        "summary_validated_curve_count": validated_curve_count,
        "spec_pdf_count": spec_pdf_count,
        "matched_spec_count": matched_spec_count,
        "docx_curve_review_markers": sorted(docx_markers),
        "expected_spec_insertion_markers": sorted(expected_spec_markers),
        "docx_spec_insertion_markers": sorted(docx_spec_markers),
        "report_page_physical_count": len(physical_page_roles),
        "report_page_role_counts": {
            role: sum(role in roles for roles in physical_page_roles.values())
            for role in sorted(REPORT_PAGE_ROLES)
        },
        "structured_evidence_valve_count": structured_evidence_count,
        "report_field_manifest_valve_count": report_field_manifest_count,
        "docx_figure_interpretations": docx_body.count(FIGURE_LABEL),
        "docx_experience_references": docx_body.count(EXPERIENCE_LABEL),
        "pdf_figure_interpretations": pdf_body.count(FIGURE_LABEL) if pdf_body else None,
        "pdf_experience_references": pdf_body.count(EXPERIENCE_LABEL) if pdf_body else None,
        "review_items": review_items,
    }

    failures: list[str] = []
    if not docx_ok:
        failures.append(docx_error)
    if (
        result["docx_figure_interpretations"] < args.min_figure_interpretations
        and not finalized_markers_match
    ):
        failures.append(
            f"DOCX {FIGURE_LABEL} count {result['docx_figure_interpretations']} "
            f"is below minimum {args.min_figure_interpretations}."
        )
    if pdf is not None and not pdf_ok:
        failures.append(pdf_error)
    if valve_count is None:
        failures.append(summary_error)
    if (
        spec_pdf_count
        and valve_count
        and matched_spec_count == 0
        and not args.allow_no_spec_match
    ):
        failures.append(
            f"Project contains {spec_pdf_count} specification PDFs but the report summary matched none."
        )
    if not spec_marker_ok or docx_spec_markers != expected_spec_markers:
        failures.append(
            spec_marker_error
            or "DOCX specification insertion markers do not match the report summary."
        )
    failures.extend(spec_declaration_errors)
    failures.extend(structured_evidence_errors)
    failures.extend(report_field_errors)
    if (
        formal_diagnosis_configured
        and valve_count is not None
        and structured_evidence_count != valve_count
    ):
        failures.append(
            "Frozen formal diagnosis is configured, but analysis_summary does not bind "
            "structured_evidence for every report valve."
        )
    if (
        formal_diagnosis_configured
        and valve_count is not None
        and report_field_manifest_count != valve_count
    ):
        failures.append(
            "Frozen formal diagnosis is configured, but analysis_summary does not bind "
            "report_field_manifest for every report valve."
        )
    if physical_page_roles:
        insertion_by_identity: dict[str, set[tuple[int, str]]] = {}
        for marker, identity in expected_spec_marker_identities.items():
            insertion = docx_spec_marker_media.get(marker)
            if insertion:
                insertion_by_identity.setdefault(identity, set()).add(insertion)
        split_pages = [
            identity for identity, insertions in insertion_by_identity.items()
            if len(insertions) != 1
        ]
        if split_pages:
            failures.append(
                "DOCX report page roles do not map to exactly one media item per physical page: "
                + ", ".join(sorted(split_pages))
            )
        identity_by_paragraph: dict[int, set[str]] = {}
        for identity, insertions in insertion_by_identity.items():
            for paragraph_index, _media in insertions:
                identity_by_paragraph.setdefault(paragraph_index, set()).add(identity)
        merged_pages = [
            ",".join(sorted(identities))
            for identities in identity_by_paragraph.values()
            if len(identities) > 1
        ]
        if merged_pages:
            failures.append(
                "DOCX reuses one insertion paragraph for different physical report pages: "
                + "; ".join(sorted(merged_pages))
            )
    accessory_markers = {
        marker for marker in docx_spec_markers
        if marker.startswith("accessory-insert-")
    }
    accessory_roles = sum(
        "accessory" in roles for roles in physical_page_roles.values()
    )
    if not report_page_features["accessory"] and (accessory_markers or accessory_roles):
        failures.append(
            "DOCX/report manifest contains ordinary Accessory pages while include_accessory_pages is disabled."
        )
    if not report_page_features["accessory"] and contains_attachment_heading(
        docx_body, ("Accessory", "Accessory Sheet", "附件规格", "附件清单")
    ):
        failures.append(
            "DOCX contains an ordinary Accessory section while include_accessory_pages is disabled."
        )
    if contains_attachment_heading(
        docx_body, ("Restrictor", "Restrictor Specification", "Restrictor Sheet")
    ):
        failures.append("DOCX contains a forbidden Restrictor attachment section.")
    for role, marker_prefix, setting in (
        ("specification", "spec-insert-", "include_specifications"),
        ("air_piping", "air-piping-insert-", "include_air_piping_pages"),
    ):
        if report_page_features[role]:
            continue
        if any(marker.startswith(marker_prefix) for marker in docx_spec_markers) or any(
            role in roles for roles in physical_page_roles.values()
        ):
            failures.append(
                f"DOCX/report manifest contains {role} pages while {setting} is disabled."
            )
    forbidden_sections = [
        heading
        for heading in disabled_overview_sections(
            project,
            config=project_config,
            report_period=args.report_period,
        )
        if contains_section_heading(docx_body, heading)
    ]
    if forbidden_sections:
        failures.append(
            "DOCX contains disabled overview sections: "
            + ", ".join(forbidden_sections)
        )
    if args.require_finalized_curve_review and curve_count and validated_curve_count != curve_count:
        failures.append(
            f"Summary has {curve_count} inserted curves but only {validated_curve_count} validated interpretations."
        )
    if args.require_finalized_curve_review and (not marker_ok or docx_markers != validated_markers):
        failures.append(marker_error or "DOCX curve markers do not match validated summary interpretations.")
    if args.require_finalized_curve_review:
        legacy_docx_text = legacy_single_curve_output_text(docx_body)
        if legacy_docx_text:
            failures.append("DOCX contains legacy single-curve output text: " + ", ".join(legacy_docx_text))
    forbidden = [
        token for token in FORBIDDEN_CUSTOMER_TEXT
        if not (token == "待复核" and finalized_markers_match)
        and token.lower() in docx_body.lower()
    ]
    if forbidden:
        failures.append("DOCX contains customer-forbidden internal review text: " + ", ".join(forbidden))
    if FORBIDDEN_PATH_OR_SCORE_PATTERN.search(docx_body):
        failures.append("DOCX contains a customer-forbidden path, image filename, or internal score wording.")
    if args.require_finalized_curve_review and pdf is not None and pdf_ok and not pdf_body.strip():
        failures.append("PDF text is empty; finalized client-text validation cannot be completed.")
    if pdf_body:
        if args.require_finalized_curve_review:
            legacy_pdf_text = legacy_single_curve_output_text(pdf_body)
            if legacy_pdf_text:
                failures.append("PDF contains legacy single-curve output text: " + ", ".join(legacy_pdf_text))
        forbidden_pdf = [
            token for token in FORBIDDEN_CUSTOMER_TEXT
            if not (token == "待复核" and finalized_markers_match)
            and token.lower() in pdf_body.lower()
        ]
        if forbidden_pdf:
            failures.append("PDF contains customer-forbidden internal review text: " + ", ".join(forbidden_pdf))
        if FORBIDDEN_PATH_OR_SCORE_PATTERN.search(pdf_body):
            failures.append("PDF contains a customer-forbidden path, image filename, or internal score wording.")

    result["ok"] = not failures
    result["failures"] = failures

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for key, value in result.items():
            if key in {"review_items", "failures"}:
                print(f"{key}:")
                for item in value:
                    print(f"  - {item}")
            else:
                print(f"{key}: {value}")

    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
