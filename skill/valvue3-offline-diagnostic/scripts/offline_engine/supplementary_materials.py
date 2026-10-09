from __future__ import annotations

import csv
import hashlib
import json
import re
import shutil
import stat
import subprocess
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import fitz

from .config import ProjectConfig


HEADER_ALIASES = {
    "valve_tag": {
        "tag",
        "valvetag",
        "valve_tag",
        "位号",
        "阀门位号",
        "调节阀位号",
    },
    "context.description": {"description", "阀门描述", "描述", "阀门名称"},
    "context.unit": {"unit", "装置", "装置名称", "单元"},
    "context.failure_consequence": {
        "failureconsequence",
        "failure_consequence",
        "失效后果",
        "故障后果",
    },
    "context.bypass": {"bypass", "副线", "有无副线", "是否有副线"},
    "context.site_note": {"sitenote", "site_note", "现场备注", "备注"},
}
MAX_ARCHIVE_MEMBERS = 5000
MAX_ARCHIVE_MEMBER_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"}
OCR_READY_URL = "http://127.0.0.1:8765/ready"
OCR_IMAGE_URL = "http://127.0.0.1:8765/v1/ocr"


@dataclass(frozen=True)
class SupplementaryCollection:
    materials: list[dict[str, Any]]
    candidates: dict[str, list[dict[str, Any]]]
    review_items: list[dict[str, Any]]


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _normalized_header(value: Any) -> str:
    return re.sub(r"[\s\-]+", "", str(value or "").strip().casefold())


def _canonical_header(value: Any) -> str:
    normalized = _normalized_header(value)
    for canonical, aliases in HEADER_ALIASES.items():
        if normalized in {_normalized_header(alias) for alias in aliases}:
            return canonical
    return ""


def _read_text(path: Path) -> str:
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _csv_rows(path: Path) -> list[tuple[int, dict[str, str]]]:
    text = _read_text(path)
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(text.splitlines(), dialect)
    try:
        raw_headers = next(reader)
    except StopIteration:
        return []
    headers = [_canonical_header(header) for header in raw_headers]
    if "valve_tag" not in headers:
        return []
    rows: list[tuple[int, dict[str, str]]] = []
    for row_no, values in enumerate(reader, start=2):
        row = {
            header: str(values[index]).strip()
            for index, header in enumerate(headers)
            if header and index < len(values) and str(values[index]).strip()
        }
        if row.get("valve_tag"):
            rows.append((row_no, row))
    return rows


def _xlsx_cell_value(cell: ET.Element, shared_strings: list[str]) -> str:
    cell_type = str(cell.get("t") or "")
    if cell_type == "inlineStr":
        return "".join(
            str(node.text or "") for node in cell.findall(".//{*}t")
        ).strip()
    value_node = cell.find("{*}v")
    value = str(value_node.text or "").strip() if value_node is not None else ""
    if cell_type == "s" and value.isdigit():
        index = int(value)
        return shared_strings[index] if index < len(shared_strings) else ""
    return value


def _xlsx_column_index(reference: str) -> int:
    match = re.match(r"([A-Z]+)", reference.upper())
    if not match:
        return 0
    index = 0
    for character in match.group(1):
        index = index * 26 + ord(character) - ord("A") + 1
    return index - 1


def _xlsx_rows(path: Path) -> list[tuple[str, dict[str, str]]]:
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            shared_strings: list[str] = []
            if "xl/sharedStrings.xml" in names:
                root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
                shared_strings = [
                    "".join(str(node.text or "") for node in item.findall(".//{*}t"))
                    for item in root.findall(".//{*}si")
                ]
            worksheet_names = sorted(
                name
                for name in names
                if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name)
            )
            output: list[tuple[str, dict[str, str]]] = []
            for worksheet_name in worksheet_names:
                root = ET.fromstring(archive.read(worksheet_name))
                raw_rows: list[tuple[int, list[str]]] = []
                for row in root.findall(".//{*}sheetData/{*}row"):
                    values: dict[int, str] = {}
                    for cell in row.findall("{*}c"):
                        index = _xlsx_column_index(str(cell.get("r") or "A1"))
                        values[index] = _xlsx_cell_value(cell, shared_strings)
                    width = max(values, default=-1) + 1
                    raw_rows.append(
                        (
                            int(row.get("r") or len(raw_rows) + 1),
                            [values.get(index, "") for index in range(width)],
                        )
                    )
                if not raw_rows:
                    continue
                headers = [_canonical_header(value) for value in raw_rows[0][1]]
                if "valve_tag" not in headers:
                    continue
                sheet_label = Path(worksheet_name).stem
                for row_no, values in raw_rows[1:]:
                    record = {
                        header: str(values[index]).strip()
                        for index, header in enumerate(headers)
                        if header
                        and index < len(values)
                        and str(values[index]).strip()
                    }
                    if record.get("valve_tag"):
                        output.append((f"{sheet_label}!row:{row_no}", record))
            return output
    except (OSError, KeyError, zipfile.BadZipFile, ET.ParseError):
        return []


def _base_material(config: ProjectConfig, path: Path) -> dict[str, Any]:
    return {
        "path": path.relative_to(config.project_dir).as_posix(),
        "sha256": _sha256(path),
        "size_bytes": path.stat().st_size,
        "detected_type": "unclassified",
        "scope": "unmatched",
        "matched_valves": [],
        "summary": "",
        "parameter_groups": [],
        "adopted": False,
        "adopted_fields": [],
        "status": "recognized",
        "reason": "",
    }


def _collect_valve_rows(
    config: ProjectConfig,
    path: Path,
    valve_tags: dict[str, str],
    rows: list[tuple[Any, dict[str, str]]],
    *,
    format_label: str,
) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    material = _base_material(config, path)
    if not rows:
        material["detected_type"] = f"{format_label.lower()}_table"
        material["summary"] = (
            f"{format_label} 未识别到阀门位号和可用项目资料列。"
        )
        material["reason"] = "no_supported_valve_rows"
        return material, {}

    material["detected_type"] = "valve_list"
    candidates: dict[str, list[dict[str, Any]]] = {}
    matched: set[str] = set()
    parameter_groups: set[str] = set()
    for row_reference, row in rows:
        normalized_tag = str(row["valve_tag"]).strip().upper()
        valve_tag = valve_tags.get(normalized_tag)
        if not valve_tag:
            continue
        matched.add(valve_tag)
        for field_name, value in row.items():
            if not field_name.startswith("context.") or not value:
                continue
            parameter_groups.add("project_context")
            candidates.setdefault(valve_tag, []).append(
                {
                    "field": field_name,
                    "value": value,
                    "path": path,
                    "record": (
                        str(row_reference)
                        if isinstance(row_reference, str)
                        else f"row:{row_reference}"
                    ),
                    "method": "structured_data",
                    "source_role": "valve_list",
                    "priority": 200,
                }
            )
    material["matched_valves"] = sorted(matched)
    material["scope"] = (
        "valve" if len(matched) == 1 else "project" if matched else "unmatched"
    )
    material["parameter_groups"] = sorted(parameter_groups)
    material["summary"] = (
        f"识别到 {len(rows)} 行阀门清单，匹配 {len(matched)} 台项目阀门。"
    )
    if not matched:
        material["reason"] = "no_project_valve_match"
    return material, candidates


def _legacy_xls_rows(path: Path) -> list[tuple[str, dict[str, str]]]:
    executable = shutil.which("libreoffice") or shutil.which("soffice")
    if not executable:
        return []
    with tempfile.TemporaryDirectory() as temporary_dir:
        result = subprocess.run(
            [
                executable,
                "--headless",
                "--convert-to",
                "xlsx",
                "--outdir",
                temporary_dir,
                str(path),
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        converted = Path(temporary_dir) / f"{path.stem}.xlsx"
        if result.returncode != 0 or not converted.is_file():
            return []
        return _xlsx_rows(converted)


def _pdf_text(path: Path) -> str:
    try:
        document = fitz.open(path)
    except Exception:
        return ""
    try:
        return "\n".join(str(page.get_text() or "") for page in document)
    finally:
        document.close()


def _docx_text(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
            root = ET.fromstring(archive.read("word/document.xml"))
    except (OSError, KeyError, zipfile.BadZipFile, ET.ParseError):
        return ""
    return "\n".join(
        str(node.text or "") for node in root.findall(".//{*}t")
    )


def _legacy_office_text(path: Path) -> str:
    executable = shutil.which("libreoffice") or shutil.which("soffice")
    if not executable:
        return ""
    with tempfile.TemporaryDirectory() as temporary_dir:
        result = subprocess.run(
            [
                executable,
                "--headless",
                "--convert-to",
                "txt:Text",
                "--outdir",
                temporary_dir,
                str(path),
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        converted = Path(temporary_dir) / f"{path.stem}.txt"
        if result.returncode != 0 or not converted.is_file():
            return ""
        return _read_text(converted)


def _content_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".txt", ".md"}:
        return _read_text(path)
    if suffix == ".json":
        try:
            payload = json.loads(_read_text(path))
        except json.JSONDecodeError:
            return ""
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)
    if suffix == ".docx":
        return _docx_text(path)
    if suffix == ".doc":
        return _legacy_office_text(path)
    if suffix == ".pdf":
        return _pdf_text(path)
    return ""


def _matched_valves(text: str, valve_tags: dict[str, str]) -> list[str]:
    matches = []
    for normalized_tag, valve_tag in valve_tags.items():
        if re.search(
            rf"(?<![A-Z0-9]){re.escape(normalized_tag)}(?![A-Z0-9])",
            text.upper(),
        ):
            matches.append(valve_tag)
    return sorted(matches)


def _text_document_type(path: Path, text: str) -> tuple[str, list[str], str]:
    normalized = re.sub(r"\s+", " ", text).strip()
    lower = normalized.casefold()
    if path.suffix.lower() == ".json" and (
        "manifest" in path.stem.casefold()
        or all(token in lower for token in ('"source"', '"file_count"'))
    ):
        return "intake_manifest", ["intake_provenance"], ""
    if re.search(
        r"(?:离线诊断.{0,12}报告|offline diagnostic report|health rating|健康等级)",
        normalized,
        re.I,
    ):
        return (
            "historical_report",
            ["historical_reference"],
            "historical_reference_not_enabled",
        )
    if re.search(r"(?:accessory sheet|附件表|附件清单)", normalized, re.I):
        return "accessory_sheet", ["accessories"], ""
    if re.search(
        r"(?:specification sheet|spec data sheet|规格书|技术规格)",
        normalized,
        re.I,
    ):
        return "specification", ["equipment_specification"], ""
    if re.search(r"(?:校验|校准|检定|calibration)", normalized, re.I):
        return "calibration_sheet", ["device_identity", "test_context"], ""
    if path.suffix.lower() in {".txt", ".md"}:
        return "project_note", ["project_note"], ""
    return "text_document", [], ""


def _collect_text_document(
    config: ProjectConfig,
    path: Path,
    valve_tags: dict[str, str],
) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    material = _base_material(config, path)
    text = _content_text(path)
    if not text.strip():
        material["status"] = "review_required"
        material["reason"] = "content_unavailable"
        material["summary"] = "文件存在，但未能读取可用文字。"
        return material, {}
    detected_type, parameter_groups, reason = _text_document_type(path, text)
    matched = _matched_valves(text, valve_tags)
    material.update(
        {
            "detected_type": detected_type,
            "scope": (
                "valve"
                if len(matched) == 1
                else "project"
                if len(matched) > 1
                else "unmatched"
            ),
            "matched_valves": matched,
            "summary": re.sub(r"\s+", " ", text).strip()[:240],
            "parameter_groups": parameter_groups,
            "reason": reason,
        }
    )
    return material, {}


def _ocr_confidence(payload: dict[str, Any]) -> float | None:
    direct = payload.get("confidence")
    if isinstance(direct, (int, float)) and not isinstance(direct, bool):
        return float(direct)
    values: list[float] = []
    blocks = payload.get("blocks")
    if isinstance(blocks, list):
        for block in blocks:
            if not isinstance(block, dict):
                continue
            for key in ("confidence", "score", "rec_score"):
                value = block.get(key)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    values.append(float(value))
                    break
    return sum(values) / len(values) if values else None


def _image_ocr(path: Path) -> tuple[str, float | None, bool, str]:
    ready = subprocess.run(
        ["curl", "--fail", "--silent", "--max-time", "3", OCR_READY_URL],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if ready.returncode != 0:
        return "", None, False, ready.stderr.strip() or "OCR service unavailable"
    result = subprocess.run(
        [
            "curl",
            "--fail",
            "--silent",
            "--show-error",
            "--max-time",
            "120",
            "-F",
            f"file=@{path}",
            "-F",
            "mode=general",
            OCR_IMAGE_URL,
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        return "", None, False, result.stderr.strip() or "local OCR failed"
    try:
        response = json.loads(result.stdout)
    except json.JSONDecodeError:
        return "", None, False, "local OCR returned invalid JSON"
    page_result = response
    pages = response.get("pages") if isinstance(response, dict) else None
    if isinstance(pages, list) and pages and isinstance(pages[0], dict):
        page_result = pages[0]
    if not isinstance(page_result, dict):
        return "", None, False, "local OCR returned invalid data"
    text = str(page_result.get("text") or page_result.get("markdown") or "")
    confidence = _ocr_confidence(page_result)
    degraded = bool(response.get("degraded") or page_result.get("degraded"))
    return text, confidence, degraded, ""


def _image_candidates(
    path: Path,
    text: str,
    matched: list[str],
    *,
    confidence: float | None,
    degraded: bool,
) -> dict[str, list[dict[str, Any]]]:
    if len(matched) != 1:
        return {}
    valve_tag = matched[0]
    low_confidence = confidence is None or confidence < 0.85 or degraded
    common = {
        "path": path,
        "method": "ocr",
        "source_role": "supplementary_image",
        "priority": 100,
        "candidate_status": "review_required" if low_confidence else "extracted",
        "confidence": confidence,
        "degraded": degraded,
        "review_reason": (
            "ocr_degraded"
            if degraded
            else "low_ocr_confidence"
            if confidence is not None and confidence < 0.85
            else "ocr_confidence_unavailable"
            if confidence is None
            else ""
        ),
    }
    candidates: list[dict[str, Any]] = []
    serial_match = re.search(
        r"(?:Device\s*S/N|Serial\s*(?:No\.?|Number)|序列号)\s*[:：]?\s*([A-Z0-9_-]{4,})",
        text,
        re.I,
    )
    if serial_match:
        candidates.append(
            {
                **common,
                "field": "identity.serial_number",
                "value": serial_match.group(1),
            }
        )
    model_match = re.search(
        r"(?:Positioner\s*Model|定位器型号)\s*[:：]?\s*([A-Z0-9._/-]{3,})",
        text,
        re.I,
    )
    if model_match:
        candidates.append(
            {
                **common,
                "field": "positioner.model",
                "value": model_match.group(1),
            }
        )
    return {valve_tag: candidates} if candidates else {}


def _collect_image(
    config: ProjectConfig,
    path: Path,
    valve_tags: dict[str, str],
) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    material = _base_material(config, path)
    material["detected_type"] = "image"
    text, confidence, degraded, error = _image_ocr(path)
    if error:
        assigned_valve = ""
        try:
            relative_to_raw = path.resolve().relative_to(config.raw_dir.resolve())
            if relative_to_raw.parts:
                assigned_valve = valve_tags.get(relative_to_raw.parts[0].upper(), "")
        except ValueError:
            pass
        material["status"] = "review_required"
        material["reason"] = "ocr_unavailable"
        material["summary"] = error
        material["matched_valves"] = [assigned_valve] if assigned_valve else []
        material["scope"] = "valve" if assigned_valve else "unmatched"
        material["_review_request"] = {
            "valve_tag": assigned_valve,
            "field": "supplementary.image_content",
            "reason": "ocr_unavailable",
            "path": path,
        }
        return material, {}
    matched = _matched_valves(text, valve_tags)
    candidates = _image_candidates(
        path,
        text,
        matched,
        confidence=confidence,
        degraded=degraded,
    )
    material.update(
        {
            "detected_type": (
                "nameplate_image"
                if re.search(
                    r"(?:Device\s*S/N|Serial\s*(?:No\.?|Number)|Positioner\s*Model|序列号|定位器型号)",
                    text,
                    re.I,
                )
                else "image"
            ),
            "scope": (
                "valve"
                if len(matched) == 1
                else "project"
                if len(matched) > 1
                else "unmatched"
            ),
            "matched_valves": matched,
            "summary": re.sub(r"\s+", " ", text).strip()[:240],
            "parameter_groups": ["potential_device_identity"],
            "ocr_confidence": confidence,
            "ocr_degraded": degraded,
            "status": (
                "review_required"
                if confidence is None or confidence < 0.85 or degraded
                else "recognized"
            ),
            "reason": (
                "ocr_degraded"
                if degraded
                else "low_ocr_confidence"
                if confidence is not None and confidence < 0.85
                else "ocr_confidence_unavailable"
                if confidence is None
                else ""
            ),
        }
    )
    return material, candidates


def _safe_member_path(name: str) -> Path:
    normalized = name.replace("\\", "/")
    if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
        raise ValueError("absolute archive member path")
    parts = tuple(part for part in normalized.split("/") if part not in {"", "."})
    if not parts or ".." in parts:
        raise ValueError("archive member escapes destination")
    return Path(*parts)


def _extract_zip(
    config: ProjectConfig,
    path: Path,
) -> tuple[dict[str, Any], list[tuple[str, Path]]]:
    material = _base_material(config, path)
    material["detected_type"] = "archive"
    destination = config.project_dir / "work/intake-unpacked" / material["sha256"]
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            if len(members) > MAX_ARCHIVE_MEMBERS:
                raise ValueError("archive contains too many members")
            total_bytes = sum(member.file_size for member in members)
            if total_bytes > MAX_ARCHIVE_TOTAL_BYTES:
                raise ValueError("archive uncompressed size exceeds limit")
            safe_members: list[tuple[zipfile.ZipInfo, Path]] = []
            for member in members:
                relative = _safe_member_path(member.filename)
                mode = member.external_attr >> 16
                if mode and stat.S_ISLNK(mode):
                    raise ValueError("archive contains a symbolic link")
                if member.file_size > MAX_ARCHIVE_MEMBER_BYTES:
                    raise ValueError("archive member exceeds size limit")
                if (
                    member.compress_size
                    and member.file_size > 10 * 1024 * 1024
                    and member.file_size / member.compress_size > 1000
                ):
                    raise ValueError("archive member compression ratio is unsafe")
                safe_members.append((member, relative))

            extracted: list[tuple[str, Path]] = []
            destination.mkdir(parents=True, exist_ok=True)
            for member, relative in safe_members:
                target = destination / relative
                target.resolve().relative_to(destination.resolve())
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.is_file():
                    if target.stat().st_size != member.file_size:
                        raise ValueError("existing extracted member does not match archive")
                else:
                    with archive.open(member) as source, target.open("xb") as output:
                        shutil.copyfileobj(source, output)
                extracted.append((relative.as_posix(), target))
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        material["status"] = "rejected"
        material["reason"] = str(exc)
        material["summary"] = "压缩包未解包，已记录安全检查结果。"
        return material, []

    material["status"] = "extracted"
    material["scope"] = "project"
    material["summary"] = f"安全解包 {len(extracted)} 个文件。"
    material["member_count"] = len(extracted)
    material["destination"] = destination.relative_to(config.project_dir).as_posix()
    return material, extracted


def _collect_file(
    config: ProjectConfig,
    path: Path,
    tags: dict[str, str],
) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    if path.suffix.lower() == ".csv":
        return _collect_valve_rows(
            config, path, tags, _csv_rows(path), format_label="CSV"
        )
    if path.suffix.lower() == ".xlsx":
        return _collect_valve_rows(
            config, path, tags, _xlsx_rows(path), format_label="XLSX"
        )
    if path.suffix.lower() == ".xls":
        return _collect_valve_rows(
            config, path, tags, _legacy_xls_rows(path), format_label="XLS"
        )
    if path.suffix.lower() in {".json", ".txt", ".md", ".doc", ".docx", ".pdf"}:
        return _collect_text_document(config, path, tags)
    if path.suffix.lower() in IMAGE_SUFFIXES:
        return _collect_image(config, path, tags)
    material = _base_material(config, path)
    material["status"] = "unsupported"
    material["reason"] = "format_not_implemented"
    return material, {}


def collect_supplementary_materials(
    config: ProjectConfig,
    valve_tags: list[str],
) -> SupplementaryCollection:
    tags = {tag.upper(): tag for tag in valve_tags}
    materials: list[dict[str, Any]] = []
    candidates: dict[str, list[dict[str, Any]]] = {}
    review_items: list[dict[str, Any]] = []
    selected_paths: set[Path] = set()
    if config.inbound_dir.is_dir():
        selected_paths.update(
            path
            for path in config.inbound_dir.rglob("*")
            if path.is_file() and not path.is_symlink()
        )
    if config.raw_dir.is_dir():
        selected_paths.update(
            path
            for path in config.raw_dir.rglob("*")
            if path.is_file()
            and not path.is_symlink()
            and path.suffix.lower() not in {".pdf", ".devdata"}
        )
    if config.specs_dir.is_dir():
        selected_paths.update(
            path
            for path in config.specs_dir.rglob("*")
            if path.is_file()
            and not path.is_symlink()
            and path.suffix.lower() != ".pdf"
        )
    paths = sorted(
        selected_paths,
        key=lambda path: path.relative_to(config.project_dir).as_posix(),
    )
    for path in paths:
        if path.suffix.lower() == ".zip":
            material, members = _extract_zip(config, path)
            materials.append(material)
            for member_name, member_path in members:
                member_material, member_candidates = _collect_file(
                    config,
                    member_path,
                    tags,
                )
                member_material["extracted_path"] = member_material["path"]
                member_material["path"] = (
                    path.relative_to(config.project_dir).as_posix()
                    + "!"
                    + member_name
                )
                review_request = member_material.pop("_review_request", None)
                if isinstance(review_request, dict):
                    review_items.append(review_request)
                materials.append(member_material)
                for valve_tag, values in member_candidates.items():
                    candidates.setdefault(valve_tag, []).extend(values)
            continue
        else:
            material, file_candidates = _collect_file(config, path, tags)
        review_request = material.pop("_review_request", None)
        if isinstance(review_request, dict):
            review_items.append(review_request)
        materials.append(material)
        for valve_tag, values in file_candidates.items():
            candidates.setdefault(valve_tag, []).extend(values)
    return SupplementaryCollection(
        materials=materials,
        candidates=candidates,
        review_items=review_items,
    )
