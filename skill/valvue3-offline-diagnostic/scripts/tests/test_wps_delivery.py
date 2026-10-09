from pathlib import Path

import fitz

from offline_engine import wps_delivery


def test_wps_layout_accepts_sequencer_positioner_diagnostics_heading(
    tmp_path: Path,
) -> None:
    pdf = tmp_path / "report.pdf"
    document = fitz.open()
    page = document.new_page(
        width=wps_delivery.A4_WIDTH_POINTS,
        height=wps_delivery.A4_HEIGHT_POINTS,
    )
    page.insert_text(
        (72, 72),
        "设备概览\n关键检测数据\n运行与定位器诊断信息",
        fontname="china-s",
    )
    document.set_metadata({"creator": "WPS Writer"})
    document.save(pdf)
    document.close()

    result = wps_delivery.inspect_wps_pdf_layout(
        pdf,
        contact_sheet=tmp_path / "contact-sheet.png",
    )

    assert result["incomplete_device_overview_pages"] == []
    assert result["ok"] is True


def test_representative_pages_include_air_piping_pages() -> None:
    texts = [f"ordinary page {index}" for index in range(12)]
    texts[4] = "Air Piping Diagram"
    texts[8] = "气路图"

    selected = wps_delivery._representative_pages(texts)

    assert 4 in selected
    assert 8 in selected
