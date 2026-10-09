"""Compare visible DOCX body wording with extracted PDF text."""

import unicodedata


def normalize_report_text(value: str) -> str:
    # Keep punctuation and signs: removing them can change engineering values.
    return "".join(unicodedata.normalize("NFKC", value).split())


def missing_pdf_body_text(document, pdf_text: str) -> tuple[int, list[str]]:
    """Include short paragraphs and table cells, without headers or field codes."""
    rendered = normalize_report_text(pdf_text)
    paragraphs = []
    for paragraph in document.element.body.xpath(".//w:p"):
        visible = []
        for node in paragraph.xpath(".//w:t"):
            run = node.getparent()
            hidden = run.xpath("./w:rPr/w:vanish")
            if hidden and hidden[0].get(
                "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}val"
            ) not in {"0", "false", "off"}:
                continue
            visible.append(node.text or "")
        text = normalize_report_text("".join(visible))
        if text:
            paragraphs.append(text)
    return len(paragraphs), [text for text in paragraphs if text not in rendered]
