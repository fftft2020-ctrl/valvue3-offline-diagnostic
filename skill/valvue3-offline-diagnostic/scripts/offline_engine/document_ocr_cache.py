from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import fitz


SCHEMA_VERSION = 2
SPECIFICATION_OCR_STRATEGY_VERSION = 6
OcrPage = Callable[[fitz.Page, int, str], dict[str, Any] | str]


def _sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _default_ocr(
    page: fitz.Page,
    page_no: int,
    source_kind: str,
    *,
    scale: float = 1.6,
    retries: int = 3,
) -> dict[str, Any]:
    pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    if source_kind == "specification":
        endpoint = "http://127.0.0.1:8765/v1/ocr"
        extra_form_fields = ["-F", "mode=general"]
    elif source_kind == "specification_table":
        endpoint = "http://127.0.0.1:8765/v1/document"
        extra_form_fields = []
    else:
        endpoint = "http://127.0.0.1:8765/v1/document"
        extra_form_fields = []
    with tempfile.NamedTemporaryFile(suffix=".png") as temporary:
        pixmap.save(temporary.name)
        def call_ocr(url: str, fields: list[str]) -> dict[str, Any]:
            result = subprocess.run(
                [
                    "curl",
                    "--fail",
                    "--silent",
                    "--show-error",
                    "--retry",
                    str(retries),
                    "--retry-delay",
                    "1",
                    "-F",
                    f"file=@{temporary.name}",
                    *fields,
                    url,
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    result.stderr.strip() or f"local OCR failed for page {page_no}"
                )
            try:
                return json.loads(result.stdout)
            except (json.JSONDecodeError, TypeError) as exc:
                raise RuntimeError(
                    f"local OCR returned invalid structured data for page {page_no}"
                ) from exc

        try:
            response = call_ocr(endpoint, extra_form_fields)
            if source_kind in {"specification", "specification_table"}:
                # Keep the two local passes distinct.  The table-aware pass
                # supplies layout; the general OCR pass supplies flattened text
                # when table reconstruction drops cell values.
                page_result = response
                if response.get("pages"):
                    page_result = response["pages"][0]
            else:
                page_result = response["pages"][0]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(
                f"local OCR returned invalid structured data for page {page_no}"
            ) from exc
    return {
        "text": str(page_result.get("text") or page_result.get("markdown") or ""),
        "blocks": page_result.get("blocks") or [],
        "tables": page_result.get("tables") or [],
        "paddle_result": page_result.get("paddle_result") or {},
        "confidence": page_result.get("confidence", response.get("confidence")),
        "pipeline": response.get("pipeline"),
        "model_version": response.get("model_version"),
        "device": response.get("device"),
        "degraded": response.get("degraded"),
        "elapsed_ms": response.get("elapsed_ms"),
        "endpoint": endpoint,
        "strategy_version": SPECIFICATION_OCR_STRATEGY_VERSION
        if source_kind in {"specification", "specification_table"}
        else None,
    }


def _default_specification_ocr(page: fitz.Page, page_no: int) -> dict[str, Any]:
    """Use table-aware local OCR for production specification preparation."""
    try:
        structured = _default_ocr(
            page,
            page_no,
            "specification_table",
            retries=0,
        )
    except RuntimeError as exc:
        flat = _default_ocr(
            page,
            page_no,
            "specification",
            scale=1.1,
            retries=1,
        )
        return {
            **flat,
            "degraded": True,
            "structured_ocr_error": str(exc),
            "fallback_reason": "table_ocr_failed",
        }
    # Some dense control-valve sheets produce a structurally valid table but
    # lose a few values in the table reconstruction.  Use the text endpoint as
    # a bounded fallback only when the structured pass cannot supply the core
    # equipment fields; native text remains the first branch.
    from .specifications import parse_spec_fields

    fields = parse_spec_fields(str(structured.get("text") or ""))
    core = ("model_no", "valve_size", "pressure_class", "rated_cv", "fail_position")
    if not all(fields.get(key) for key in core):
        flat = _default_ocr(page, page_no, "specification")
        flat_text = str(flat.get("text") or "").strip()
        if flat_text:
            structured = {
                **structured,
                "text": f"{structured.get('text', '').rstrip()}\n{flat_text}",
                "flat_ocr_result": flat,
            }
    return structured


def _native_blocks(page: fitz.Page) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for raw in page.get_text("blocks"):
        if len(raw) < 5 or not str(raw[4]).strip():
            continue
        blocks.append(
            {
                "bbox": [round(float(value), 3) for value in raw[:4]],
                "text": str(raw[4]),
                "block_no": int(raw[5]) if len(raw) > 5 else len(blocks),
            }
        )
    return blocks


def _layout_page_texts(pdf: Path, page_count: int) -> list[str]:
    try:
        result = subprocess.run(
            ["pdftotext", "-layout", str(pdf), "-"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except FileNotFoundError:
        return [""] * page_count
    if result.returncode != 0:
        return [""] * page_count
    pages = result.stdout.split("\f")
    if pages and not pages[-1].strip():
        pages.pop()
    return (pages + [""] * page_count)[:page_count]


def _page_has_visual_content(page: fitz.Page) -> bool:
    return bool(page.get_images(full=True) or page.get_drawings())


def _generic_key_values(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        match = re.match(r"\s*([^:：]{1,80})\s*[:：]\s*(.+?)\s*$", line)
        if match:
            values[match.group(1).strip()] = match.group(2).strip()
    return values


def _structured_fields(source_kind: str, text: str) -> dict[str, Any]:
    fields: dict[str, Any] = {"key_values": _generic_key_values(text)}
    if source_kind == "specification":
        from .specifications import parse_spec_fields

        fields["specification"] = parse_spec_fields(text)
    return fields


def _is_usable_spec_text(text: str) -> bool:
    """Reject text layers/cache entries dominated by unmapped PDF font glyphs."""
    if not text.strip():
        return True
    private_use_count = sum(
        1
        for character in text
        if "\ue000" <= character <= "\uf8ff"
    )
    visible_count = sum(1 for character in text if not character.isspace())
    return (
        "\ufffd" not in text
        and private_use_count < 8
        and (not visible_count or private_use_count / visible_count < 0.01)
    )


def _is_complete(payload: object, source_hash: str) -> bool:
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION or payload.get("source_sha256") != source_hash:
        return False
    page_count = payload.get("page_count")
    pages = payload.get("pages")
    pages_complete = (
        type(page_count) is int
        and isinstance(pages, list)
        and len(pages) == page_count
        and all(
            isinstance(page, dict)
            and page.get("status") == "complete"
            and type(page.get("page_no")) is int
            and page["page_no"] == index
            and isinstance(page.get("text"), str)
            for index, page in enumerate(pages, 1)
        )
    )
    if not pages_complete:
        return False
    if payload.get("source_kind") == "diagnostic":
        return all(isinstance(page.get("parser_text"), str) for page in pages)
    if payload.get("source_kind") == "specification":
        if not all(_is_usable_spec_text(str(page["text"])) for page in pages):
            return False
    return True


def _is_prepared_with_recorded_failures(payload: object, source_hash: str) -> bool:
    """Reuse a hash-bound partial cache without repeatedly timing out OCR."""
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION or payload.get("source_sha256") != source_hash:
        return False
    pages = payload.get("pages")
    page_count = payload.get("page_count")
    if (
        type(page_count) is not int
        or not isinstance(pages, list)
        or len(pages) != page_count
        or not any(
            isinstance(page, dict) and page.get("status") == "failed"
            for page in pages
        )
        or not all(
            isinstance(page, dict)
            and page.get("status") in {"complete", "failed"}
            and type(page.get("page_no")) is int
            and page["page_no"] == index
            and isinstance(page.get("text"), str)
            for index, page in enumerate(pages, 1)
        )
    ):
        return False
    if payload.get("source_kind") == "diagnostic":
        return all(isinstance(page.get("parser_text"), str) for page in pages)
    if payload.get("source_kind") == "specification":
        return payload.get("ocr_strategy_version") == SPECIFICATION_OCR_STRATEGY_VERSION
    return True


def _sources(project_dir: Path) -> list[tuple[str, Path]]:
    sources: list[tuple[str, Path]] = []
    for directory_name, source_kind in (("raw", "diagnostic"), ("specs", "specification")):
        root = project_dir / directory_name
        if not root.is_dir():
            continue
        sources.extend((source_kind, path) for path in root.rglob("*") if path.is_file() and path.suffix.lower() == ".pdf")
    return sorted(sources, key=lambda item: item[1].relative_to(project_dir).as_posix())


def load_cached_pdf_document(project_dir: Path, pdf: Path) -> dict[str, Any] | None:
    """Read only a source- and role-bound unified document, including failed pages."""
    project_dir = project_dir.resolve()
    pdf = pdf.resolve()
    try:
        relative_path = pdf.relative_to(project_dir).as_posix()
    except ValueError:
        return None
    manifest_path = project_dir / "work" / "ocr" / "manifest.json"
    if not manifest_path.is_file() or not pdf.is_file():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        entry = manifest["documents"][relative_path]
        if manifest.get("schema_version") != SCHEMA_VERSION or entry.get("source_kind") not in {"diagnostic", "specification"}:
            return None
        source_hash = _sha256(pdf)
        if entry.get("sha256") != source_hash:
            return None
        payload_path = project_dir / "work" / "ocr" / "documents" / str(entry.get("source_kind")) / f"{source_hash}.json"
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, KeyError, AttributeError):
        return None
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != SCHEMA_VERSION
        or payload.get("source_sha256") != source_hash
        or entry.get("source_kind") not in {"diagnostic", "specification"}
        or payload.get("source_kind") != entry.get("source_kind")
    ):
        return None
    return payload


def load_cached_pdf_pages(project_dir: Path, pdf: Path) -> list[str] | None:
    """Return complete parser text while preserving physical PDF boundaries."""
    payload = load_cached_pdf_document(project_dir, pdf)
    if payload is None or not _is_complete(payload, payload["source_sha256"]):
        return None
    return [
        str(page.get("parser_text") or page.get("text") or "").strip()
        for page in payload["pages"]
    ]


def load_cached_pdf_text(project_dir: Path, pdf: Path) -> str | None:
    """Return prepared document text for compatibility with existing parsers."""
    pages = load_cached_pdf_pages(project_dir, pdf)
    return "\n".join(pages) if pages is not None else None


def load_specification_pages(project_dir: Path, specs_dir: Path) -> dict[tuple[str, int], str]:
    """Read current specification text without copying it to a second cache.

    Failed pages retain their available text; their failure details stay in the
    unified evidence cache. Reading never retries OCR or changes any approval.
    """
    from .specifications import iter_spec_pdfs

    project_dir, specs_dir = project_dir.resolve(), specs_dir.resolve()
    pdfs = iter_spec_pdfs(specs_dir)
    if not pdfs:
        return {}
    cache_dir = project_dir / "work/ocr"
    try:
        manifest = json.loads((cache_dir / "manifest.json").read_text(encoding="utf-8"))
        if type(manifest.get("schema_version")) is not int or manifest["schema_version"] != SCHEMA_VERSION:
            raise ValueError("unsupported OCR manifest schema")
        entries = manifest["documents"]
        expected = {pdf.relative_to(project_dir).as_posix() for pdf in pdfs}
        actual = {
            relative for relative in entries
            if (project_dir / relative).resolve().is_relative_to(specs_dir)
        }
        if actual != expected:
            raise ValueError("specification PDF set changed")
        texts: dict[tuple[str, int], str] = {}
        for pdf in pdfs:
            relative = pdf.relative_to(project_dir).as_posix()
            source_hash = _sha256(pdf)
            if entries[relative].get("sha256") != source_hash:
                raise ValueError(f"specification PDF changed: {relative}")
            payload = json.loads((cache_dir / "documents" / "specification" / f"{source_hash}.json").read_text(encoding="utf-8"))
            if (
                payload.get("schema_version") != SCHEMA_VERSION
                or payload.get("source_kind") != "specification"
                or payload.get("ocr_strategy_version") != SPECIFICATION_OCR_STRATEGY_VERSION
                or not (_is_complete(payload, source_hash) or _is_prepared_with_recorded_failures(payload, source_hash))
            ):
                raise ValueError(f"incomplete or stale specification cache: {relative}")
            with fitz.open(pdf) as document:
                page_count = len(document)
            pages = payload["pages"]
            if (
                type(payload["page_count"]) is not int
                or payload["page_count"] != page_count
                or any(type(page.get("page_no")) is not int for page in pages)
                or [page["page_no"] for page in pages] != list(range(1, page_count + 1))
            ):
                raise ValueError(f"incomplete specification page coverage: {relative}")
            texts.update({(str(pdf.resolve()), page["page_no"]): page["text"] for page in pages})
        return texts
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeError) as exc:
        raise RuntimeError(f"specification cache cannot be used: {exc}; run --extract-materials and review parameters again") from exc


def prepare_project_pdf_cache(
    project_dir: Path,
    *,
    ocr_page: OcrPage | None = None,
    retry_failed_ocr: bool = False,
) -> dict[str, int]:
    """Persist project-local PDF text, using OCR only when the text layer is empty."""
    project_dir = project_dir.resolve()
    cache_dir = project_dir / "work" / "ocr"
    documents_dir = cache_dir / "documents"
    extractor = ocr_page or _default_ocr
    stats = {"documents": 0, "cache_hits": 0, "native_pages": 0, "ocr_pages": 0}
    manifest_documents: dict[str, dict[str, Any]] = {}
    ocr_disabled_error = ""

    for source_kind, pdf in _sources(project_dir):
        stats["documents"] += 1
        relative_path = pdf.relative_to(project_dir).as_posix()
        source_hash = _sha256(pdf)
        document_path = documents_dir / source_kind / f"{source_hash}.json"
        force_spec_ocr = False
        retry_pages: set[int] = set()
        cached: object = None
        if document_path.is_file():
            try:
                cached = json.loads(document_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                cached = None
        if (
            source_kind == "specification"
            and isinstance(cached, dict)
            and cached.get("source_sha256") == source_hash
        ):
            if cached.get("source_kind") == source_kind and retry_failed_ocr and _is_prepared_with_recorded_failures(
                cached, source_hash
            ):
                retry_pages = {
                    page["page_no"] for page in cached["pages"]
                    if page["status"] == "failed"
                }
            # A changed OCR strategy requires fresh preparation, never an
            # automatic import of an old specification mirror.
            if cached.get("ocr_strategy_version") != SPECIFICATION_OCR_STRATEGY_VERSION:
                force_spec_ocr = True
            cached_pages = cached.get("pages")
            if isinstance(cached_pages, list) and any(
                isinstance(page, dict)
                and page.get("status") == "complete"
                and isinstance(page.get("text"), str)
                and not _is_usable_spec_text(page["text"])
                for page in cached_pages
            ):
                force_spec_ocr = True
        try:
            document = fitz.open(pdf)
        except Exception as exc:
            raise RuntimeError(f"cannot open PDF for text preparation: {pdf}") from exc
        try:
            if not force_spec_ocr and not retry_pages and (
                _is_complete(cached, source_hash)
                or _is_prepared_with_recorded_failures(cached, source_hash)
            ) and cached.get("source_kind") == source_kind and cached["page_count"] == len(document):
                stats["cache_hits"] += 1
                manifest_documents[relative_path] = {"sha256": source_hash, "source_kind": source_kind}
                continue
            if retry_pages and cached["page_count"] != len(document):
                retry_pages = set()
            layout_pages = (
                _layout_page_texts(pdf, len(document))
                if source_kind == "diagnostic"
                else []
            )
            pages: list[dict[str, Any]] = []
            repeated_spec_ocr: dict[str, dict[str, Any] | str] = {}
            for page_index, page in enumerate(document):
                page_no = page_index + 1
                if retry_pages and not force_spec_ocr and page_no not in retry_pages:
                    pages.append(cached["pages"][page_index])
                    continue
                native_text = page.get_text() or ""
                needs_spec_ocr = False
                if source_kind == "specification" and native_text.strip():
                    from .specifications import (
                        _has_report_equipment_fields,
                        _is_specification_data_page,
                    )

                    needs_spec_ocr = (
                        force_spec_ocr
                        or page_no in retry_pages
                        or not _is_usable_spec_text(native_text)
                        or (
                            _is_specification_data_page(native_text)
                            and not _has_report_equipment_fields(native_text)
                        )
                    )
                needs_ocr = needs_spec_ocr or (
                    not native_text.strip() and _page_has_visual_content(page)
                )
                if not needs_ocr:
                    stats["native_pages"] += 1
                    page_result = {
                        "page_no": page_no,
                        "method": "native_text" if native_text.strip() else "blank",
                        "text": native_text,
                        "blocks": _native_blocks(page),
                        "tables": [],
                        "confidence": None,
                        "status": "complete",
                    }
                    if source_kind == "diagnostic":
                        page_result["parser_text"] = layout_pages[page_index] or native_text
                else:
                    if ocr_disabled_error:
                        stats["ocr_failed_pages"] = stats.get("ocr_failed_pages", 0) + 1
                        page_result = {
                            "page_no": page_no,
                            "method": "ocr_skipped_after_failure",
                            "text": native_text,
                            "blocks": _native_blocks(page) if native_text.strip() else [],
                            "tables": [],
                            "confidence": None,
                            "status": "failed",
                            "ocr_error": ocr_disabled_error,
                        }
                        if source_kind == "diagnostic":
                            page_result["parser_text"] = (
                                layout_pages[page_index] or native_text
                            )
                        pages.append(page_result)
                        continue
                    repeat_key = (
                        hashlib.sha256(native_text.encode("utf-8")).hexdigest()
                        if source_kind == "specification" and native_text.strip()
                        else ""
                    )
                    try:
                        if repeat_key and repeat_key in repeated_spec_ocr:
                            raw_result = repeated_spec_ocr[repeat_key]
                        else:
                            raw_result = (
                                _default_specification_ocr(page, page_no)
                                if ocr_page is None and source_kind == "specification"
                                else extractor(page, page_no, source_kind)
                            )
                            if repeat_key:
                                repeated_spec_ocr[repeat_key] = raw_result
                    except RuntimeError as exc:
                        ocr_disabled_error = str(exc)
                        stats["ocr_failed_pages"] = stats.get("ocr_failed_pages", 0) + 1
                        page_result = {
                            "page_no": page_no,
                            "method": "ocr_failed",
                            "text": native_text,
                            "blocks": _native_blocks(page) if native_text.strip() else [],
                            "tables": [],
                            "confidence": None,
                            "status": "failed",
                            "ocr_error": str(exc),
                        }
                        if source_kind == "diagnostic":
                            page_result["parser_text"] = (
                                layout_pages[page_index] or native_text
                            )
                        pages.append(page_result)
                        continue
                    result = raw_result if isinstance(raw_result, dict) else {"text": str(raw_result)}
                    if (
                        native_text.strip()
                        and _is_usable_spec_text(native_text)
                        and str(result.get("text") or "").strip()
                    ):
                        result = {
                            **result,
                            "text": f"{native_text.rstrip()}\n{str(result['text']).strip()}",
                        }
                    stats["ocr_pages"] += 1
                    page_result = {
                        "page_no": page_no,
                        "method": "ocr",
                        "text": str(result.get("text") or ""),
                        "blocks": result.get("blocks") if isinstance(result.get("blocks"), list) else [],
                        "tables": result.get("tables") if isinstance(result.get("tables"), list) else [],
                        "confidence": result.get("confidence"),
                        "status": "complete",
                        "ocr_result": result,
                    }
                    if source_kind == "diagnostic":
                        page_result["parser_text"] = str(result.get("text") or "")
                pages.append(page_result)
        finally:
            document.close()

        combined_text = "\n".join(str(page["text"]) for page in pages)
        payload = {
            "schema_version": SCHEMA_VERSION,
            "source_sha256": source_hash,
            "source_kind": source_kind,
            "source_relative_paths": [relative_path],
            "page_count": len(pages),
            "prepared_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "pages": pages,
            "structured_fields": _structured_fields(source_kind, combined_text),
        }
        if source_kind == "specification":
            payload["ocr_strategy_version"] = SPECIFICATION_OCR_STRATEGY_VERSION
        _atomic_json(document_path, payload)
        manifest_documents[relative_path] = {"sha256": source_hash, "source_kind": source_kind}

    _atomic_json(
        cache_dir / "manifest.json",
        {"schema_version": SCHEMA_VERSION, "documents": manifest_documents},
    )
    return stats
