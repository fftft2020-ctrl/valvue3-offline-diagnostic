from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any


def image_content_sha256(image: dict[str, Any]) -> str:
    path = Path(str(image.get("path") or ""))
    if not path.is_file():
        return ""
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def curve_image_identity(valve_folder: str, image: dict[str, Any]) -> str:
    source = Path(str(image.get("source") or image.get("path") or "unknown")).name.strip()
    test_key = str(image.get("test_key") or "other").strip()
    page = str(image.get("page") or "0").strip()
    bbox = str(image.get("bbox") or "").replace(" ", "")
    return "\x1f".join(
        (valve_folder.strip(), source, test_key, page, bbox, image_content_sha256(image))
    )


def curve_image_key(valve_folder: str, image: dict[str, Any]) -> str:
    identity = curve_image_identity(valve_folder, image)
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
    test_key = str(image.get("test_key") or "other").strip()
    return f"{valve_folder}:{test_key}:{digest}"


TEST_ORDER = (
    "run_step",
    "pattern_step",
    "run_ramp",
    "standard_signature",
    "extended_signature",
    "other",
)


def sort_test_images(images: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the stable chart order shared by preparation and rendering."""
    order = {key: index for index, key in enumerate(TEST_ORDER)}

    def page_number(image: dict[str, Any]) -> int:
        try:
            return int(str(image.get("page") or "0"))
        except ValueError:
            return 0

    return sorted(
        images,
        key=lambda image: (
            order.get(str(image.get("test_key") or "other"), len(TEST_ORDER)),
            str(image.get("source") or "").casefold(),
            page_number(image),
            str(image.get("bbox") or ""),
        ),
    )


def curve_review_marker(image_key: str) -> str:
    """Return the stable DOCX marker for one validated curve review."""
    return "curve-review-" + hashlib.sha256(image_key.encode("utf-8")).hexdigest()[:24]


def spec_insertion_marker(valve_folder: str, page_kind: str) -> str:
    """Return the stable marker proving a technical page reached the DOCX."""
    prefix = {
        "specification": "spec-insert",
        "spec": "spec-insert",
        "air_piping": "air-piping-insert",
        "accessory": "accessory-insert",
    }.get(page_kind, "report-page-insert")
    identity = f"{valve_folder.strip()}\x1f{page_kind}"
    return f"{prefix}-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:24]}"
