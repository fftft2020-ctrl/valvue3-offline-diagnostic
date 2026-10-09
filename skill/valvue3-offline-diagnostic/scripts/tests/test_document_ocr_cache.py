import json
import hashlib
import base64
import re
from pathlib import Path

import fitz
import pytest

from offline_engine import document_ocr_cache
from offline_engine.document_ocr_cache import (
    load_cached_pdf_pages,
    load_cached_pdf_text,
    prepare_project_pdf_cache,
)


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def test_document_reader_rejects_old_payload_schema(tmp_path: Path) -> None:
    source = tmp_path / "raw/VALVE/report.pdf"
    _write_pdf(source, ["Native diagnostic evidence"])
    prepare_project_pdf_cache(tmp_path)
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    path = tmp_path / f"work/ocr/documents/diagnostic/{source_hash}.json"
    payload = json.loads(path.read_text())
    payload["schema_version"] = 1
    path.write_text(json.dumps(payload))

    assert document_ocr_cache.load_cached_pdf_document(tmp_path, source) is None
    assert load_cached_pdf_pages(tmp_path, source) is None
    prepare_project_pdf_cache(tmp_path)
    assert document_ocr_cache.load_cached_pdf_document(tmp_path, source)["schema_version"] == document_ocr_cache.SCHEMA_VERSION


def test_specifications_read_unified_pages_without_a_mirror(tmp_path: Path) -> None:
    source = tmp_path / "specs/nested/spec.pdf"
    _write_pdf(source, ["Native specification page", None])
    prepare_project_pdf_cache(tmp_path, ocr_page=lambda *_: "Scanned second page")

    pages = document_ocr_cache.load_specification_pages(tmp_path, tmp_path / "specs")

    assert pages == {
        (str(source.resolve()), 1): "Native specification page\n",
        (str(source.resolve()), 2): "Scanned second page",
    }
    assert not list((tmp_path / "work").rglob("spec_ocr_cache.json"))
    assert not list((tmp_path / "work").rglob("specification_audit.json"))

    _write_pdf(source, ["Changed specification page"])
    with pytest.raises(RuntimeError, match="extract-materials"):
        document_ocr_cache.load_specification_pages(tmp_path, tmp_path / "specs")


@pytest.mark.parametrize("damage", ["missing_page", "duplicate_page", "stale_strategy", "wrong_hash", "wrong_role"])
def test_specification_reader_rejects_incomplete_or_stale_evidence(tmp_path: Path, damage: str) -> None:
    source = tmp_path / "specs/spec.pdf"
    _write_pdf(source, ["First physical page", "Second physical page"])
    prepare_project_pdf_cache(tmp_path)
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    path = tmp_path / f"work/ocr/documents/specification/{source_hash}.json"
    payload = json.loads(path.read_text())
    if damage == "missing_page":
        payload["pages"].pop(0)
        payload["page_count"] = 1
    elif damage == "duplicate_page":
        payload["pages"][1]["page_no"] = 1
    elif damage == "stale_strategy":
        payload["ocr_strategy_version"] = 0
    elif damage == "wrong_hash":
        payload["source_sha256"] = "0" * 64
    else:
        payload["source_kind"] = "diagnostic"
    path.write_text(json.dumps(payload))

    with pytest.raises(RuntimeError, match="extract-materials"):
        document_ocr_cache.load_specification_pages(tmp_path, tmp_path / "specs")
    prepare_project_pdf_cache(tmp_path, ocr_page=lambda *_: "Rebuilt specification")
    assert len(document_ocr_cache.load_specification_pages(tmp_path, tmp_path / "specs")) == 2


def test_specification_reader_keeps_failed_page_text_without_retry(tmp_path: Path) -> None:
    source = tmp_path / "specs/spec.pdf"
    _write_pdf(source, ["CONTROL VALVE SPECIFICATIONS Model No. 88-21124"])
    calls = []
    def unavailable(*_):
        calls.append(1)
        raise RuntimeError("OCR unavailable")
    prepare_project_pdf_cache(tmp_path, ocr_page=unavailable)
    document_path = tmp_path / f"work/ocr/documents/specification/{hashlib.sha256(source.read_bytes()).hexdigest()}.json"
    before = document_path.read_bytes()

    pages = document_ocr_cache.load_specification_pages(tmp_path, tmp_path / "specs")
    prepare_project_pdf_cache(tmp_path, ocr_page=unavailable)

    assert "88-21124" in pages[(str(source.resolve()), 1)]
    assert calls == [1]
    assert document_path.read_bytes() == before
    assert json.loads(before)["pages"][0]["status"] == "failed"


def test_specification_paths_are_distinct_and_new_source_requires_preparation(tmp_path: Path) -> None:
    first, second = (tmp_path / f"specs/{folder}/spec.pdf" for folder in ("a", "b"))
    _write_pdf(first, ["First specification"])
    prepare_project_pdf_cache(tmp_path)
    _write_pdf(second, ["Second specification"])
    with pytest.raises(RuntimeError, match="PDF set changed"):
        document_ocr_cache.load_specification_pages(tmp_path, tmp_path / "specs")

    prepare_project_pdf_cache(tmp_path)
    assert document_ocr_cache.load_specification_pages(tmp_path, tmp_path / "specs") == {
        (str(first.resolve()), 1): "First specification\n",
        (str(second.resolve()), 1): "Second specification\n",
    }


def test_legacy_specification_mirror_is_not_imported_or_modified(tmp_path: Path) -> None:
    source = tmp_path / "specs/spec.pdf"
    _write_pdf(source, [None])
    legacy = tmp_path / "work/shared_engine/spec_ocr_cache.json"
    legacy.parent.mkdir(parents=True)
    old = json.dumps({"files": {"spec.pdf": {
        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "page_count": 1, "pages": {"1": "Obsolete text"},
    }}}).encode()
    legacy.write_bytes(old)
    calls = []
    def fresh(*_):
        calls.append(1)
        return "Fresh OCR text"
    prepare_project_pdf_cache(tmp_path, ocr_page=fresh)

    assert calls == [1]
    assert legacy.read_bytes() == old
    assert document_ocr_cache.load_specification_pages(tmp_path, tmp_path / "specs") == {
        (str(source.resolve()), 1): "Fresh OCR text",
    }


def test_identical_raw_and_spec_pdfs_do_not_replace_diagnostic_layout(tmp_path: Path, monkeypatch) -> None:
    from offline_engine import generator
    raw = tmp_path / "raw/VALVE-001/shared.pdf"
    _write_pdf(raw, ["Native diagnostic table"])
    spec = tmp_path / "specs/shared.pdf"
    spec.parent.mkdir()
    spec.write_bytes(raw.read_bytes())
    prepare_project_pdf_cache(tmp_path, ocr_page=lambda *_: "Specification OCR text")

    assert document_ocr_cache.load_specification_pages(tmp_path, tmp_path / "specs") == {
        (str(spec.resolve()), 1): "Native diagnostic table\n",
    }
    assert load_cached_pdf_pages(tmp_path, raw)[0].strip() == "Native diagnostic table"
    assert prepare_project_pdf_cache(tmp_path, ocr_page=lambda *_: pytest.fail("must reuse both roles"))["cache_hits"] == 2
    monkeypatch.setattr(generator, "BASE_DIR", tmp_path)
    assert generator.run_pdftotext_pages(raw)[0].strip() == "Native diagnostic table"


def test_identical_scans_keep_both_role_results_and_reuse_them(tmp_path: Path) -> None:
    raw = tmp_path / "raw/VALVE-001/shared.pdf"
    _write_pdf(raw, [None])
    spec = tmp_path / "specs/shared.pdf"
    spec.parent.mkdir()
    spec.write_bytes(raw.read_bytes())
    prepare_project_pdf_cache(tmp_path, ocr_page=lambda _page, _number, role: {
        "diagnostic": "Diagnostic layout text", "specification": "Specification table text",
    }[role])
    assert load_cached_pdf_pages(tmp_path, raw) == ["Diagnostic layout text"]
    assert document_ocr_cache.load_specification_pages(tmp_path, tmp_path / "specs") == {
        (str(spec.resolve()), 1): "Specification table text",
    }
    assert prepare_project_pdf_cache(tmp_path, ocr_page=lambda *_: pytest.fail("must reuse both roles"))["cache_hits"] == 2


def test_default_specification_ocr_uses_general_text_endpoint(monkeypatch) -> None:
    commands: list[list[str]] = []

    class Pixmap:
        def save(self, path: str) -> None:
            Path(path).write_bytes(PNG_1X1)

    class Page:
        def get_pixmap(self, **kwargs):
            return Pixmap()

    def fake_run(command, **kwargs):
        commands.append(command)
        return document_ocr_cache.subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps({"text": "Bench Range 10-15 psi g Fail Position Open"}),
            stderr="",
        )

    monkeypatch.setattr(document_ocr_cache.subprocess, "run", fake_run)

    result = document_ocr_cache._default_ocr(Page(), 1, "specification")

    assert result["text"] == "Bench Range 10-15 psi g Fail Position Open"
    assert any("/v1/ocr" in argument for argument in commands[0])
    assert "mode=general" in commands[0]


def test_specification_ocr_falls_back_to_general_when_table_ocr_fails(
    monkeypatch,
) -> None:
    calls: list[tuple[str, dict]] = []

    def fake_ocr(_page, _page_no, source_kind, **kwargs):
        calls.append((source_kind, kwargs))
        if source_kind == "specification_table":
            raise RuntimeError("local table OCR returned 503")
        return {
            "text": "visible fallback text",
            "blocks": [],
            "tables": [],
            "degraded": False,
        }

    monkeypatch.setattr(document_ocr_cache, "_default_ocr", fake_ocr)

    result = document_ocr_cache._default_specification_ocr(object(), 6)

    assert calls == [
        ("specification_table", {"retries": 0}),
        ("specification", {"scale": 1.1, "retries": 1}),
    ]
    assert result["text"] == "visible fallback text"
    assert result["degraded"] is True
    assert result["fallback_reason"] == "table_ocr_failed"
    assert "503" in result["structured_ocr_error"]


def _write_pdf(path: Path, texts: list[str | None]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    document = fitz.open()
    for text in texts:
        page = document.new_page()
        if text is None:
            page.insert_image(fitz.Rect(72, 72, 100, 100), stream=PNG_1X1)
        elif text:
            page.insert_text((72, 72), text)
    document.save(path)
    document.close()


def _write_layout_table_pdf(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "Run Step Test")
    for x, text in (
        (72, "0.00 -> 5.00"),
        (220, "1.00"),
        (280, "2.07"),
        (340, "0.26"),
        (400, "0.10"),
        (460, "0.04"),
    ):
        page.insert_text((x, 100), text)
    document.save(path)
    document.close()


def test_project_pdf_cache_uses_text_layer_then_ocr_and_reuses_by_hash(tmp_path: Path) -> None:
    source = tmp_path / "raw/FV-101/report.pdf"
    _write_pdf(source, ["Device Tag: FV-101 with readable embedded diagnostic text", None])
    calls: list[int] = []

    def fake_ocr(page, page_no, source_kind):
        calls.append(page_no)
        return {"text": "OCR fallback page", "blocks": [{"text": "OCR fallback page"}], "tables": []}

    first = prepare_project_pdf_cache(tmp_path, ocr_page=fake_ocr)
    second = prepare_project_pdf_cache(tmp_path, ocr_page=fake_ocr)

    assert first == {"documents": 1, "cache_hits": 0, "native_pages": 1, "ocr_pages": 1}
    assert second == {"documents": 1, "cache_hits": 1, "native_pages": 0, "ocr_pages": 0}
    assert calls == [2]
    manifest = json.loads((tmp_path / "work/ocr/manifest.json").read_text(encoding="utf-8"))
    source_hash = manifest["documents"]["raw/FV-101/report.pdf"]["sha256"]
    payload = json.loads(
        (tmp_path / f"work/ocr/documents/diagnostic/{source_hash}.json").read_text(encoding="utf-8")
    )
    assert [page["method"] for page in payload["pages"]] == ["native_text", "ocr"]
    assert payload["pages"][1]["text"] == "OCR fallback page"
    assert payload["pages"][1]["ocr_result"]["text"] == "OCR fallback page"
    assert payload["source_kind"] == "diagnostic"
    assert load_cached_pdf_pages(tmp_path, source) == [
        "Device Tag: FV-101 with readable embedded diagnostic text",
        "OCR fallback page",
    ]
    assert load_cached_pdf_text(tmp_path, source) == (
        "Device Tag: FV-101 with readable embedded diagnostic text\nOCR fallback page"
    )


def test_project_pdf_cache_records_ocr_failure_and_continues(tmp_path: Path) -> None:
    source = tmp_path / "specs/VALVE-001/spec.pdf"
    _write_pdf(source, [None, "Readable native specification page"])

    def failing_ocr(_page, page_no, _source_kind):
        raise RuntimeError(f"local OCR failed for page {page_no}: 503")

    stats = prepare_project_pdf_cache(tmp_path, ocr_page=failing_ocr)

    assert stats["ocr_failed_pages"] == 1
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    payload = json.loads(
        (tmp_path / f"work/ocr/documents/specification/{source_hash}.json").read_text(
            encoding="utf-8"
        )
    )
    assert payload["pages"][0]["status"] == "failed"
    assert payload["pages"][0]["method"] == "ocr_failed"
    assert "503" in payload["pages"][0]["ocr_error"]
    assert payload["pages"][1]["status"] == "complete"
    completed_page = payload["pages"][1]
    pages = document_ocr_cache.load_specification_pages(tmp_path, tmp_path / "specs")
    assert pages[(str(source.resolve()), 1)] == ""
    assert "Readable native specification page" in pages[(str(source.resolve()), 2)]
    second = prepare_project_pdf_cache(
        tmp_path,
        ocr_page=lambda *_args: (_ for _ in ()).throw(
            AssertionError("recorded OCR failure should be reused")
        ),
    )
    assert second["cache_hits"] == 1

    retried_pages: list[int] = []

    def recovered_ocr(_page, page_no, _source_kind):
        retried_pages.append(page_no)
        return {"text": f"Recovered specification page {page_no}"}

    retried = prepare_project_pdf_cache(
        tmp_path,
        ocr_page=recovered_ocr,
        retry_failed_ocr=True,
    )
    assert retried_pages == [1]
    assert retried["ocr_pages"] == 1
    recovered = json.loads(
        (tmp_path / f"work/ocr/documents/specification/{source_hash}.json").read_text(
            encoding="utf-8"
        )
    )
    assert all(page["status"] == "complete" for page in recovered["pages"])
    assert recovered["pages"][1] == completed_page


def test_project_pdf_cache_stops_calling_ocr_after_first_runtime_failure(
    tmp_path: Path,
) -> None:
    source = tmp_path / "specs/VALVE-001/spec.pdf"
    _write_pdf(source, [None, None])
    calls: list[int] = []

    def failing_ocr(_page, page_no, _source_kind):
        calls.append(page_no)
        raise RuntimeError("local OCR inference returned 503")

    stats = prepare_project_pdf_cache(tmp_path, ocr_page=failing_ocr)

    assert calls == [1]
    assert stats["ocr_failed_pages"] == 2
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    payload = json.loads(
        (tmp_path / f"work/ocr/documents/specification/{source_hash}.json").read_text(
            encoding="utf-8"
        )
    )
    assert [page["method"] for page in payload["pages"]] == [
        "ocr_failed",
        "ocr_skipped_after_failure",
    ]


def test_diagnostic_cache_preserves_layout_text_for_table_parsers(tmp_path: Path) -> None:
    source = tmp_path / "raw/FV-101/report.pdf"
    _write_layout_table_pdf(source)

    prepare_project_pdf_cache(tmp_path)

    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    payload = json.loads(
        (tmp_path / f"work/ocr/documents/diagnostic/{source_hash}.json").read_text(encoding="utf-8")
    )
    parser_text = payload["pages"][0]["parser_text"]
    assert re.search(
        r"0\.00 -> 5\.00\s+1\.00\s+2\.07\s+0\.26\s+0\.10\s+0\.04",
        parser_text,
    )
    assert load_cached_pdf_text(tmp_path, source) == parser_text.strip()


def test_project_pdf_cache_only_processes_new_document(tmp_path: Path) -> None:
    first = tmp_path / "specs/first.pdf"
    _write_pdf(first, [None])
    calls: list[str] = []

    def fake_ocr(page, page_no, source_kind):
        calls.append(source_kind)
        return {"text": "Model No. 88-21124", "blocks": [], "tables": []}

    prepare_project_pdf_cache(tmp_path, ocr_page=fake_ocr)
    second = tmp_path / "specs/second.pdf"
    _write_pdf(second, [None])
    stats = prepare_project_pdf_cache(tmp_path, ocr_page=fake_ocr)

    assert calls == ["specification", "specification"]
    assert stats == {"documents": 2, "cache_hits": 1, "native_pages": 0, "ocr_pages": 1}


def test_existing_garbled_structured_spec_cache_is_not_a_cache_hit(tmp_path: Path) -> None:
    spec = tmp_path / "specs/spec.pdf"
    _write_pdf(spec, ["Masoneilan Valves 52-78463"])
    source_hash = hashlib.sha256(spec.read_bytes()).hexdigest()
    document_path = tmp_path / f"work/ocr/documents/specification/{source_hash}.json"
    document_path.parent.mkdir(parents=True)
    document_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source_sha256": source_hash,
                "source_kind": "specification",
                "page_count": 1,
                "pages": [{
                    "page_no": 1,
                    "method": "ocr",
                    "text": "52-78463 " + "\ue000\ue001\ue002" * 20,
                    "status": "complete",
                }],
            }
        ),
        encoding="utf-8",
    )
    calls: list[int] = []

    stats = prepare_project_pdf_cache(
        tmp_path,
        ocr_page=lambda page, page_no, source_kind: (
            calls.append(page_no)
            or {
                "text": (
                    "Masoneilan Valves 52-78463 Size in 4 Rating ASME CL 2500 "
                    "Bench Range 10-15 psi g Fail Position Open"
                ),
                "blocks": [],
                "tables": [],
            }
        ),
    )

    assert calls == [1]
    assert stats["cache_hits"] == 0
    assert stats["ocr_pages"] == 1


def test_specification_native_text_with_private_use_glyphs_forces_ocr(
    monkeypatch, tmp_path: Path
) -> None:
    source = tmp_path / "specs/spec.pdf"
    _write_pdf(source, ["Masoneilan Valves 52-78463"])
    original_get_text = fitz.Page.get_text

    def garbled_get_text(page, *args, **kwargs):
        if args and args[0] == "blocks":
            return original_get_text(page, *args, **kwargs)
        return "Masoneilan Valves 52-78463 " + "\ue000\ue001\ue002" * 20

    monkeypatch.setattr(fitz.Page, "get_text", garbled_get_text)
    calls: list[int] = []

    stats = prepare_project_pdf_cache(
        tmp_path,
        ocr_page=lambda page, page_no, source_kind: (
            calls.append(page_no)
            or {
                "text": (
                    "Masoneilan Valves 52-78463 Size in 4 Rating ASME CL 2500 "
                    "Bench Range 10-15 psi g Fail Position Open"
                ),
                "blocks": [],
                "tables": [],
            }
        ),
    )

    assert calls == [1]
    assert stats["ocr_pages"] == 1


def test_intentionally_blank_page_does_not_trigger_ocr(tmp_path: Path) -> None:
    source = tmp_path / "raw/FV-101/blank.pdf"
    _write_pdf(source, [""])

    stats = prepare_project_pdf_cache(
        tmp_path,
        ocr_page=lambda *args: (_ for _ in ()).throw(AssertionError("blank page is not a scan")),
    )

    assert stats == {"documents": 1, "cache_hits": 0, "native_pages": 1, "ocr_pages": 0}


def test_specification_with_incomplete_text_layer_uses_ocr_fallback(tmp_path: Path) -> None:
    source = tmp_path / "specs/spec.pdf"
    _write_pdf(source, ["CONTROL VALVE SPECIFICATIONS without model fields"])
    calls: list[int] = []

    stats = prepare_project_pdf_cache(
        tmp_path,
        ocr_page=lambda page, page_no, source_kind: (
            calls.append(page_no)
            or {"text": "Model No. 88-21124", "blocks": [], "tables": []}
        ),
    )

    assert calls == [1]
    assert stats["ocr_pages"] == 1
