from pathlib import Path

import fitz

from offline_engine import document_ocr_cache, generator, specifications


def _write_pdf(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with fitz.open() as document:
        page = document.new_page(width=612, height=792)
        page.insert_text((72, 72), text)
        page.insert_text((90, 180), text)
        page.draw_rect(fitz.Rect(72, 140, 500, 420))
        document.save(path)


def test_report_page_identity_binds_source_content_and_crop(tmp_path, monkeypatch):
    specs = tmp_path / "specs"
    monkeypatch.setattr(generator, "SPECS_DIR", specs)
    monkeypatch.setattr(generator, "SPEC_IMAGE_DIR", tmp_path / "images")
    refs = []
    for source, text in (("first/sheet.pdf", "First attachment"), ("second/sheet.pdf", "Second attachment")):
        _write_pdf(specs / source, text)
        refs.append(generator.SpecPage(valve="VALVE", tag="VALVE", source=source, page=1))
    first, second = (Path(generator.render_report_page(ref)) for ref in refs)
    assert first != second
    assert first.read_bytes() != second.read_bytes()
    assert generator.render_report_page(refs[1]) == str(second)

    # A stale or absent supplied hash must not alias changed source bytes.
    refs[1].source_sha256 = generator.file_sha256(specs / refs[1].source)
    _write_pdf(specs / refs[1].source, "Revised attachment")
    revised = Path(generator.render_report_page(refs[1]))
    assert revised != second
    assert revised.read_bytes() != second.read_bytes()
    monkeypatch.setattr(generator, "effective_spec_content_rect", lambda page: page.rect)
    assert generator.render_report_page(refs[1]) != str(revised)
    assert first.exists() and second.exists() and revised.exists()


def test_pdf_discovery_and_ocr_accept_mixed_case_extensions(tmp_path):
    relative_paths = ["raw/VALVE/report.PDF", "specs/a/sheet.PDF", "specs/b/sheet.pDf", "specs/c/sheet.pdf"]
    for name in relative_paths:
        _write_pdf(tmp_path / name, "Native PDF evidence")
    (tmp_path / "specs/not-a-file.PDF").mkdir()
    (tmp_path / "specs/ignored.txt").write_text("Not a PDF")
    assert specifications.iter_spec_pdfs(tmp_path / "specs") == [tmp_path / name for name in relative_paths[1:]]
    assert document_ocr_cache._sources(tmp_path) == [
        ("diagnostic" if name.startswith("raw/") else "specification", tmp_path / name)
        for name in relative_paths
    ]
    document_ocr_cache.prepare_project_pdf_cache(tmp_path)
    pages = document_ocr_cache.load_specification_pages(tmp_path, tmp_path / "specs")
    assert len(pages) == 3
    assert document_ocr_cache.load_cached_pdf_pages(tmp_path, tmp_path / relative_paths[0])


def test_curve_rendering_accepts_mixed_case_pdf_extensions(tmp_path, monkeypatch):
    valve_dir = tmp_path / "VALVE"
    names = ["Seq_first.PDF", "Seq_second.pDf", "Seq_third.pdf"]
    for name in names:
        _write_pdf(valve_dir / name, "Run Step Test\nPosition (%) Set Point (%)")
    (valve_dir / "directory.PDF").mkdir()
    monkeypatch.setattr(generator, "IMAGE_DIR", tmp_path / "images")
    monkeypatch.setattr(generator, "run_pdftotext_pages", lambda path: ["Run Step Test\nPosition (%) Set Point (%)"])
    images = generator.render_images_for_valve(valve_dir)
    assert [image["source"] for image in images] == names
    assert all(image["test_key"] == "run_step" for image in images)
