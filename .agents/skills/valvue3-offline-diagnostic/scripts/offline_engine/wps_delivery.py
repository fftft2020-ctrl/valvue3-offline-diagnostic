from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any

import fitz
from PIL import Image, ImageDraw


A4_WIDTH_POINTS = 595.28
A4_HEIGHT_POINTS = 841.89
A4_TOLERANCE_POINTS = 6.0
MAX_CONTACT_SHEET_PAGES = 9
MAX_ORPHAN_TEXT_CHARACTERS = 240


def _substantive_page_text(text: str) -> str:
    kept: list[str] = []
    for raw_line in text.splitlines():
        line = " ".join(raw_line.split()).strip()
        if not line:
            continue
        if line.upper().startswith("BAKER HUGHES | MASONEILAN"):
            continue
        if line.lower().startswith("baker hughes confidential"):
            continue
        if re.fullmatch(r"(?:第\s*)?\d+\s*(?:页)?", line):
            continue
        kept.append(line)
    return "\n".join(kept)


def _representative_pages(page_texts: list[str]) -> list[int]:
    page_count = len(page_texts)
    if page_count <= MAX_CONTACT_SHEET_PAGES:
        return list(range(page_count))

    selected = {0, page_count - 1}
    for marker in (
        "诊断总览",
        "Specification",
        "Air Piping",
        "气路",
        "Accessory",
    ):
        matches = [
            index
            for index, text in enumerate(page_texts)
            if marker.lower() in text.lower()
        ]
        if matches:
            selected.add(matches[0])
            selected.add(matches[-1])
    for marker in ("设备概览", "图形判读"):
        matches = [
            index
            for index, text in enumerate(page_texts)
            if marker in text
        ]
        if matches:
            selected.add(matches[0])
            selected.add(matches[len(matches) // 2])
            selected.add(matches[-1])

    ordered = sorted(selected)
    if len(ordered) <= MAX_CONTACT_SHEET_PAGES:
        return ordered
    positions = {
        round(index * (len(ordered) - 1) / (MAX_CONTACT_SHEET_PAGES - 1))
        for index in range(MAX_CONTACT_SHEET_PAGES)
    }
    return [ordered[index] for index in sorted(positions)]


def _write_contact_sheet(
    document: fitz.Document,
    page_numbers: list[int],
    output: Path,
) -> None:
    columns = 3
    thumb_width = 420
    label_height = 30
    margin = 18
    thumbnails: list[Image.Image] = []
    for page_number in page_numbers:
        page = document[page_number]
        scale = thumb_width / page.rect.width
        pixmap = page.get_pixmap(
            matrix=fitz.Matrix(scale, scale),
            alpha=False,
        )
        thumbnail = Image.frombytes(
            "RGB",
            (pixmap.width, pixmap.height),
            pixmap.samples,
        )
        cell = Image.new(
            "RGB",
            (thumb_width, label_height + thumbnail.height),
            "white",
        )
        ImageDraw.Draw(cell).text(
            (8, 7),
            f"Page {page_number + 1}",
            fill="black",
        )
        cell.paste(thumbnail, (0, label_height))
        thumbnails.append(cell)

    rows = (len(thumbnails) + columns - 1) // columns
    cell_height = max(image.height for image in thumbnails)
    sheet = Image.new(
        "RGB",
        (
            columns * thumb_width + (columns + 1) * margin,
            rows * cell_height + (rows + 1) * margin,
        ),
        "white",
    )
    for index, image in enumerate(thumbnails):
        row, column = divmod(index, columns)
        x = margin + column * (thumb_width + margin)
        y = margin + row * (cell_height + margin)
        sheet.paste(image, (x, y))
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output)


def inspect_wps_pdf_layout(
    pdf_path: Path,
    *,
    contact_sheet: Path,
) -> dict[str, Any]:
    """Run automatic WPS-origin and page-layout checks and render a visual sheet."""
    pdf_path = pdf_path.resolve()
    if not pdf_path.is_file():
        raise RuntimeError(f"WPS PDF is missing: {pdf_path}")
    try:
        document = fitz.open(pdf_path)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"WPS PDF cannot be opened: {pdf_path}") from exc
    try:
        creator = str(document.metadata.get("creator") or "").strip()
        creator_is_wps = "wps" in creator.lower()
        page_texts: list[str] = []
        page_image_counts: list[int] = []
        blank_pages: list[int] = []
        non_a4_pages: list[int] = []
        rotated_pages: list[int] = []
        incomplete_overviews: list[int] = []
        device_overview_pages: list[int] = []
        for index, page in enumerate(document):
            text = page.get_text("text")
            page_texts.append(text)
            page_image_counts.append(len(page.get_images(full=True)))
            compact_text = "".join(
                unicodedata.normalize("NFKC", text).split()
            )
            substantive = _substantive_page_text(text)
            if not substantive and not page.get_images(full=True):
                blank_pages.append(index + 1)
            if (
                abs(page.rect.width - A4_WIDTH_POINTS)
                > A4_TOLERANCE_POINTS
                or abs(page.rect.height - A4_HEIGHT_POINTS)
                > A4_TOLERANCE_POINTS
            ):
                non_a4_pages.append(index + 1)
            if page.rotation != 0:
                rotated_pages.append(index + 1)
            has_data_section = any(
                heading in compact_text
                for heading in (
                    "关键测试数据",
                    "关键诊断数据",
                    "关键检测数据",
                )
            )
            if "设备概览" in compact_text:
                device_overview_pages.append(index)
                if (
                    not has_data_section
                    or not any(
                        heading in compact_text
                        for heading in (
                            "运行与DTM信息",
                            "运行与定位器诊断信息",
                        )
                    )
                ):
                    incomplete_overviews.append(index + 1)

        representative = _representative_pages(page_texts)
        _write_contact_sheet(document, representative, contact_sheet)
        device_overview_contact_sheets: list[Path] = []
        for batch_index, start in enumerate(
            range(0, len(device_overview_pages), MAX_CONTACT_SHEET_PAGES),
            start=1,
        ):
            batch_output = contact_sheet.with_name(
                f"{contact_sheet.stem}-device-overviews-"
                f"{batch_index:02d}{contact_sheet.suffix}"
            )
            _write_contact_sheet(
                document,
                device_overview_pages[
                    start : start + MAX_CONTACT_SHEET_PAGES
                ],
                batch_output,
            )
            device_overview_contact_sheets.append(batch_output.resolve())
    finally:
        document.close()

    device_overview_continuations: list[dict[str, int]] = []
    page_start_markers = (
        "曲线证据与图形判读",
        "测试曲线与原始数据",
        "图形判读",
        "技术资料附件",
        "设备概览",
        "诊断总览",
        "整体判断",
        "总体结论与检修建议",
        "评级标准",
    )
    compact_page_texts = [
        "".join(unicodedata.normalize("NFKC", text).split())
        for text in page_texts
    ]
    for index, text in enumerate(compact_page_texts[:-1]):
        if "设备概览" not in text:
            continue
        next_text = compact_page_texts[index + 1]
        if not any(marker in next_text for marker in page_start_markers):
            device_overview_continuations.append(
                {
                    "overview_page": index + 1,
                    "continuation_page": index + 2,
                }
            )
    continuation_page_numbers = {
        item["continuation_page"]
        for item in device_overview_continuations
    }
    orphan_text_pages: list[int] = []
    for index, text in enumerate(compact_page_texts):
        page_number = index + 1
        if page_number == 1 or page_number in continuation_page_numbers:
            continue
        if page_image_counts[index]:
            continue
        if any(marker in text for marker in page_start_markers):
            continue
        substantive = "".join(
            _substantive_page_text(page_texts[index]).split()
        )
        if len(substantive) < MAX_ORPHAN_TEXT_CHARACTERS:
            orphan_text_pages.append(page_number)

    failures: list[str] = []
    if not creator_is_wps:
        failures.append(
            f"PDF creator is not WPS 文字: {creator or 'missing'}"
        )
    if not page_texts:
        failures.append("WPS PDF has no pages")
    if non_a4_pages:
        failures.append(
            "non-A4 portrait pages: "
            + ", ".join(map(str, non_a4_pages))
        )
    if rotated_pages:
        failures.append(
            "rotated pages: " + ", ".join(map(str, rotated_pages))
        )
    if blank_pages:
        failures.append(
            "blank pages: " + ", ".join(map(str, blank_pages))
        )
    if incomplete_overviews:
        failures.append(
            "device overview spills or is incomplete on pages: "
            + ", ".join(map(str, incomplete_overviews))
        )
    if device_overview_continuations:
        failures.append(
            "device overview continuation pages: "
            + ", ".join(
                f"{item['overview_page']}->{item['continuation_page']}"
                for item in device_overview_continuations
            )
        )
    if orphan_text_pages:
        failures.append(
            "orphan low-content text pages: "
            + ", ".join(map(str, orphan_text_pages))
        )
    return {
        "schema_version": 1,
        "pdf": str(pdf_path),
        "creator": creator,
        "creator_is_wps": creator_is_wps,
        "page_count": len(page_texts),
        "a4_portrait_pages": len(page_texts) - len(non_a4_pages),
        "non_a4_pages": non_a4_pages,
        "rotated_pages": rotated_pages,
        "blank_pages": blank_pages,
        "incomplete_device_overview_pages": incomplete_overviews,
        "device_overview_continuation_pages": (
            device_overview_continuations
        ),
        "orphan_text_pages": orphan_text_pages,
        "representative_pages": [
            page_number + 1 for page_number in representative
        ],
        "contact_sheet": str(contact_sheet.resolve()),
        "device_overview_contact_sheets": [
            str(path) for path in device_overview_contact_sheets
        ],
        "visual_review_required": True,
        "ok": not failures,
        "failures": failures,
    }
