from __future__ import annotations

import hashlib
import json
import posixpath
import re
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree


_RELATIONSHIP_NS = (
    "http://schemas.openxmlformats.org/package/2006/relationships"
)
_OFFICE_REL_NS = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
)
_WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_FORMAT_PROPERTY_TAGS = {
    "pPr",
    "rPr",
    "tblPr",
    "tblGrid",
    "trPr",
    "tcPr",
    "sectPr",
}
_STRUCTURAL_TAGS = {
    "document",
    "body",
    "p",
    "tbl",
    "tr",
    "tc",
    "hyperlink",
    "sdt",
    "sdtContent",
}
_TEXT_TAGS = {"t", "instrText", "delText"}
_BREAK_TAGS = {"tab", "br", "cr"}
_SEMANTIC_PART_PATTERN = re.compile(
    r"^word/(?:document|header\d+|footer\d+|footnotes|endnotes|comments)\.xml$"
)


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _relationship_part_name(part_name: str) -> str:
    directory, filename = posixpath.split(part_name)
    return posixpath.join(directory, "_rels", filename + ".rels")


def _relationship_map(
    package: zipfile.ZipFile,
    part_name: str,
) -> dict[str, tuple[str, str]]:
    relationship_name = _relationship_part_name(part_name)
    if relationship_name not in package.namelist():
        return {}
    root = ElementTree.fromstring(package.read(relationship_name))
    result: dict[str, tuple[str, str]] = {}
    for item in root.findall(f"{{{_RELATIONSHIP_NS}}}Relationship"):
        relationship_id = item.attrib.get("Id", "")
        target = item.attrib.get("Target", "")
        mode = item.attrib.get("TargetMode", "")
        if relationship_id:
            result[relationship_id] = (target, mode)
    return result


def _relationship_token(
    package: zipfile.ZipFile,
    part_name: str,
    relationship: tuple[str, str] | None,
) -> str:
    if relationship is None:
        return "missing"
    target, mode = relationship
    if mode.lower() == "external":
        return f"external:{target}"
    resolved = posixpath.normpath(
        posixpath.join(posixpath.dirname(part_name), target)
    )
    if resolved in package.namelist():
        return f"part:{_sha256_bytes(package.read(resolved))}"
    return f"target:{resolved}"


def _semantic_tokens(
    package: zipfile.ZipFile,
    part_name: str,
) -> list[str]:
    root = ElementTree.fromstring(package.read(part_name))
    relationships = _relationship_map(package, part_name)
    bookmark_names = {
        item.get(f"{{{_WORD_NS}}}id", ""): item.get(f"{{{_WORD_NS}}}name", "")
        for item in root.iter(f"{{{_WORD_NS}}}bookmarkStart")
    }
    tokens: list[str] = [f"part:{posixpath.basename(part_name)}"]

    def visit(node: ElementTree.Element) -> None:
        local = _local_name(node.tag)
        if local in _FORMAT_PROPERTY_TAGS:
            return
        if local in _STRUCTURAL_TAGS:
            tokens.append(f"<{local}>")
        if local in _TEXT_TAGS:
            tokens.append(f"{local}:{node.text or ''}")
        elif local in _BREAK_TAGS:
            tokens.append(f"<{local}/>")
        elif local == "bookmarkStart":
            tokens.append("bookmarkStart:" + json.dumps(
                {key: node.get(f"{{{_WORD_NS}}}{key}", "") for key in ("name", "colFirst", "colLast")},
                ensure_ascii=False, sort_keys=True,
            ))
        elif local == "bookmarkEnd":
            bookmark_id = node.get(f"{{{_WORD_NS}}}id", "")
            tokens.append("bookmarkEnd:" + bookmark_names.get(bookmark_id, "missing:" + bookmark_id))
        elif local in {"blip", "imagedata", "oleObject"}:
            relationship_id = (
                node.attrib.get(f"{{{_OFFICE_REL_NS}}}embed")
                or node.attrib.get(f"{{{_OFFICE_REL_NS}}}id")
            )
            if relationship_id:
                tokens.append(
                    f"{local}:"
                    + _relationship_token(
                        package,
                        part_name,
                        relationships.get(relationship_id),
                    )
                )
        elif local == "hyperlink":
            for attribute in ("anchor", "docLocation"):
                tokens.append(f"hyperlink-{attribute}:" + node.get(f"{{{_WORD_NS}}}{attribute}", ""))
            relationship_id = node.attrib.get(f"{{{_OFFICE_REL_NS}}}id")
            if relationship_id:
                tokens.append(
                    "hyperlink:"
                    + _relationship_token(
                        package,
                        part_name,
                        relationships.get(relationship_id),
                    )
                )
        elif local == "docPr":
            tokens.append(
                "docPr:"
                + json.dumps(
                    {
                        key: node.attrib.get(key, "")
                        for key in ("name", "descr", "title")
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
        elif local == "fldChar":
            tokens.append(
                "field:"
                + node.attrib.get(
                    "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}fldCharType",
                    "",
                )
            )
        for child in node:
            visit(child)
        if local in _STRUCTURAL_TAGS:
            tokens.append(f"</{local}>")

    visit(root)
    return tokens


def docx_content_identity(path: Path) -> dict[str, Any]:
    """Return a formatting-insensitive identity for customer-visible DOCX content."""
    path = path.resolve()
    if not path.is_file():
        raise RuntimeError(f"DOCX is missing: {path}")
    try:
        with zipfile.ZipFile(path) as package:
            names = package.namelist()
            semantic_parts = sorted(
                name for name in names if _SEMANTIC_PART_PATTERN.match(name)
            )
            if "word/document.xml" not in semantic_parts:
                raise RuntimeError(f"DOCX has no word/document.xml: {path}")
            tokens: list[str] = []
            for part_name in semantic_parts:
                tokens.extend(_semantic_tokens(package, part_name))
            media_hashes = sorted(
                _sha256_bytes(package.read(name))
                for name in names
                if name.startswith("word/media/") and not name.endswith("/")
            )
            embedded_hashes = sorted(
                _sha256_bytes(package.read(name))
                for name in names
                if (
                    name.startswith("word/embeddings/")
                    or name.startswith("word/activeX/")
                )
                and not name.endswith("/")
            )
    except (zipfile.BadZipFile, ElementTree.ParseError) as exc:
        raise RuntimeError(f"DOCX cannot be inspected: {path}") from exc

    semantic_payload = json.dumps(
        tokens,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "schema_version": 1,
        "semantic_sha256": _sha256_bytes(semantic_payload),
        "semantic_part_count": len(semantic_parts),
        "media_sha256": media_hashes,
        "embedded_sha256": embedded_hashes,
    }


def compare_format_only_content(
    source: Path,
    candidate: Path,
) -> dict[str, Any]:
    """Verify that a candidate differs from its parent only in formatting."""
    source_identity = docx_content_identity(source)
    candidate_identity = docx_content_identity(candidate)
    differences = [
        key
        for key in source_identity
        if key != "schema_version"
        and source_identity[key] != candidate_identity.get(key)
    ]
    return {
        "schema_version": 1,
        "ok": not differences,
        "source": str(source.resolve()),
        "candidate": str(candidate.resolve()),
        "source_identity": source_identity,
        "candidate_identity": candidate_identity,
        "differences": differences,
    }


def format_revision_manifest_path(
    project_dir: Path,
    candidate: Path,
) -> Path:
    return (
        project_dir.resolve()
        / "work"
        / "format-revisions"
        / candidate.stem
        / "format_revision_manifest.json"
    )
