"""Shared validation and rating rules for offline curve visual review."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
import json
from pathlib import Path, PurePosixPath
from typing import Any


class VisualReviewError(ValueError):
    """Raised when curve-review evidence cannot safely be used."""


class DataEvidenceStatus(StrEnum):
    """Availability of the numerical data evidence used for an assessment."""

    AVAILABLE = "available"
    INSUFFICIENT = "insufficient"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class RatingCombination:
    """The reconciled data and visual ratings for one offline assessment."""

    data_rating: int | None
    visual_rating: int | None
    final_rating: int
    inconsistent: bool
    basic_action_failed: bool
    data_evidence_status: DataEvidenceStatus
    data_evidence_reason: str | None


@dataclass(frozen=True)
class CurveInterpretation:
    """A validated curve interpretation ready for later pipeline consumption."""

    image_key: str
    test_key: str
    observation: str
    diagnosis: str
    rating: int
    action: str
    customer_text: str
    confidence: str
    experience_rule_ids: tuple[str, ...] = ()  # Historical records only.


@dataclass(frozen=True)
class CurveReviewImage:
    """One curve image that requires a visual interpretation."""

    image_key: str
    test_key: str


@dataclass(frozen=True)
class CurveReviewManifest:
    """Validated curve-review requirements from a run manifest."""

    images: tuple[CurveReviewImage, ...]
    schema_version: int | None = None
    status: str | None = None


@dataclass(frozen=True)
class CurveReviewReadiness:
    """Whether report finalization has a complete validated curve review."""

    status: str
    missing_image_keys: tuple[str, ...]
    interpretations: tuple[CurveInterpretation, ...] = ()


_RATING_MATRIX = {
    (1, 1): 1,
    (1, 2): 2,
    (1, 3): 2,
    (1, 4): 3,
    (2, 2): 2,
    (2, 3): 3,
    (2, 4): 3,
    (3, 3): 3,
    (3, 4): 4,
    (4, 4): 4,
}
_REQUIRED_TEXT_FIELDS = ("image_key", "test_key", "observation", "diagnosis", "action", "customer_text", "confidence")
_EVIDENCE_FIELDS = ("evidence", "supporting_evidence", "supporting_evidence_text")


def combine_ratings(
    data_rating: int | None,
    visual_rating: int | None,
    *,
    basic_action_failed: bool = False,
) -> RatingCombination:
    """Combine 1--4 data and visual ratings through the approved equal-weight matrix."""
    if not isinstance(basic_action_failed, bool):
        raise VisualReviewError("basic_action_failed must be a boolean")
    _validate_rating(data_rating, "data_rating", allow_none=True)
    _validate_rating(visual_rating, "visual_rating", allow_none=True)

    if basic_action_failed:
        final_rating = 4
    elif data_rating is None and visual_rating is None:
        final_rating = 5
    elif data_rating is None:
        final_rating = visual_rating
    elif visual_rating is None:
        final_rating = data_rating
    else:
        final_rating = _RATING_MATRIX[tuple(sorted((data_rating, visual_rating)))]

    if data_rating is not None:
        data_evidence_status = DataEvidenceStatus.AVAILABLE
        data_evidence_reason = None
    elif visual_rating is not None:
        data_evidence_status = DataEvidenceStatus.INSUFFICIENT
        data_evidence_reason = "data_rating_missing"
    else:
        data_evidence_status = DataEvidenceStatus.UNAVAILABLE
        data_evidence_reason = "data_and_visual_rating_missing"

    return RatingCombination(
        data_rating=data_rating,
        visual_rating=visual_rating,
        final_rating=final_rating,
        inconsistent=(
            data_rating is not None
            and visual_rating is not None
            and abs(data_rating - visual_rating) >= 2
        ),
        basic_action_failed=basic_action_failed,
        data_evidence_status=data_evidence_status,
        data_evidence_reason=data_evidence_reason,
    )


def validate_manifest(raw: Mapping[str, Any]) -> CurveReviewManifest:
    """Validate a raw curve-review manifest into its minimal typed representation."""
    if not isinstance(raw, Mapping):
        raise VisualReviewError("manifest must be a mapping")
    manifest_images = raw.get("images")
    if not isinstance(manifest_images, list):
        raise VisualReviewError("manifest images must be a list")

    images: list[CurveReviewImage] = []
    image_keys: set[str] = set()
    for index, image in enumerate(manifest_images):
        if not isinstance(image, Mapping):
            raise VisualReviewError(f"manifest image at index {index} must be an object")
        image_key = _required_text(image, "image_key", f"manifest image at index {index}")
        test_key = _required_text(image, "test_key", f"manifest image {image_key}")
        if image_key in image_keys:
            raise VisualReviewError(f"duplicate manifest image_key: {image_key}")
        image_keys.add(image_key)
        images.append(CurveReviewImage(image_key=image_key, test_key=test_key))
    schema_version = raw.get("schema_version")
    if schema_version is not None and (isinstance(schema_version, bool) or not isinstance(schema_version, int)):
        raise VisualReviewError("manifest schema_version must be an integer")
    status = raw.get("status")
    if status is not None and (not isinstance(status, str) or not status.strip()):
        raise VisualReviewError("manifest status must be a non-empty string")
    return CurveReviewManifest(
        images=tuple(images),
        schema_version=schema_version,
        status=status.strip() if isinstance(status, str) else None,
    )


def validate_interpretations(
    manifest: Mapping[str, Any] | CurveReviewManifest, interpretations: Iterable[Mapping[str, Any]]
) -> tuple[CurveInterpretation, ...]:
    """Ensure every manifest image has one complete, aligned visual interpretation."""
    typed_manifest = manifest if isinstance(manifest, CurveReviewManifest) else validate_manifest(manifest)
    manifest_tests = {image.image_key: image.test_key for image in typed_manifest.images}

    reviewed: set[str] = set()
    validated: list[CurveInterpretation] = []
    for index, interpretation in enumerate(interpretations):
        typed_interpretation = _validate_interpretation(interpretation, f"interpretation at index {index}")
        image_key = typed_interpretation.image_key
        if image_key in reviewed:
            raise VisualReviewError(f"duplicate interpretation image_key: {image_key}")
        reviewed.add(image_key)
        if image_key not in manifest_tests:
            raise VisualReviewError(f"interpretation image_key is not in manifest: {image_key}")
        if typed_interpretation.test_key != manifest_tests[image_key]:
            raise VisualReviewError(f"interpretation test_key does not match manifest for {image_key}")
        validated.append(typed_interpretation)

    missing = [image_key for image_key in manifest_tests if image_key not in reviewed]
    if missing:
        raise VisualReviewError(f"missing interpretation for manifest image_key: {', '.join(missing)}")
    return tuple(validated)


def merge_interpretations(
    codex_interpretations: Iterable[Mapping[str, Any]],
    human_interpretations: Iterable[Mapping[str, Any]],
    *,
    manifest: Mapping[str, Any] | CurveReviewManifest | None = None,
) -> list[dict[str, Any]]:
    """Return Codex interpretations with same-image human review taking precedence."""
    codex_items = _validate_merge_entries(codex_interpretations, "Codex")
    human_items = _validate_merge_entries(human_interpretations, "human")
    human_by_image: dict[str, dict[str, Any]] = {}
    for item in human_items:
        image_key = item["image_key"]
        human_by_image[image_key] = item

    merged: list[dict[str, Any]] = []
    for item in codex_items:
        image_key = item["image_key"]
        override = human_by_image.pop(image_key, None)
        merged.append(override if override is not None else item)
    merged.extend(human_by_image.values())
    if manifest is not None:
        validate_interpretations(manifest, merged)
    return merged


def load_curve_review_manifest(path: Path) -> CurveReviewManifest:
    """Load and type-validate a ``curve_review_manifest.json`` document."""
    return validate_manifest(_load_json(path, required=True))


def curve_review_readiness(
    manifest_path: Path,
    *,
    codex_path: Path | None = None,
    formal_interpretations: Iterable[Mapping[str, Any]] = (),
    manual_path: Path | None = None,
) -> CurveReviewReadiness:
    """Return the blocking status consumed by a later report-finalize entry point."""
    raw_manifest = _load_json(manifest_path, required=True)
    manifest = validate_manifest(raw_manifest)
    codex_entries = _interpretation_entries(_load_json(codex_path, required=False), "Codex")
    formal_entries = list(formal_interpretations)
    if formal_entries:
        codex_entries = _entries_for_manifest(codex_entries, manifest)
    preferred = merge_interpretations(codex_entries, formal_entries)
    manual_entries = _manual_entries(_load_json(manual_path, required=False), raw_manifest, preferred)
    merged = merge_interpretations(preferred, manual_entries)
    missing = _validate_alignment(manifest, merged, require_complete=False)
    if missing:
        return CurveReviewReadiness("curve_review_required", missing)
    validated = validate_interpretations(manifest, merged)
    return CurveReviewReadiness("curve_review_complete", (), validated)


def _load_json(path: Path | None, *, required: bool) -> Any:
    if path is None or not path.is_file():
        if required:
            raise VisualReviewError(f"curve review file is missing: {path}")
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VisualReviewError(f"invalid curve review JSON: {path}") from exc


def _interpretation_entries(raw: Any, source: str) -> list[Mapping[str, Any]]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, Mapping) and isinstance(raw.get("interpretations"), list):
        return raw["interpretations"]
    raise VisualReviewError(f"{source} interpretation file must be a list or contain an interpretations list")


def _entries_for_manifest(
    entries: Iterable[Mapping[str, Any]],
    manifest: CurveReviewManifest,
) -> list[Mapping[str, Any]]:
    """Keep legacy Codex observations only for the active formal-report period."""
    allowed = {image.image_key for image in manifest.images}
    return [
        entry
        for entry in entries
        if isinstance(entry, Mapping) and str(entry.get("image_key") or "").strip() in allowed
    ]


def _manual_entries(
    raw: Any,
    manifest_raw: Mapping[str, Any],
    codex_entries: list[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    if raw is None:
        return []
    if isinstance(raw, list) or (isinstance(raw, Mapping) and "interpretations" in raw):
        return _interpretation_entries(raw, "human")
    if not isinstance(raw, Mapping):
        raise VisualReviewError("human interpretation file must be an object")
    legacy = raw.get("images", raw)
    if not isinstance(legacy, Mapping):
        raise VisualReviewError("legacy human interpretations must be a mapping")
    codex_by_key = {str(item.get("image_key")): item for item in codex_entries if isinstance(item, Mapping)}
    manifest_images = manifest_raw.get("images", [])
    adapted: list[Mapping[str, Any]] = []
    for legacy_key, value in legacy.items():
        if legacy_key in {"schema_version", "review_method", "review_scope"}:
            continue
        image = _legacy_manifest_image(str(legacy_key), manifest_images)
        image_key = str(image["image_key"])
        base = codex_by_key.get(image_key, {})
        payload = value if isinstance(value, Mapping) else {"interpretation": value}
        text = payload.get("interpretation")
        if not isinstance(text, str) or not text.strip():
            raise VisualReviewError(f"legacy human interpretation requires non-empty text: {legacy_key}")
        rating = payload.get("severity", base.get("rating", 2))
        adapted.append({
            "image_key": image_key,
            "test_key": str(image["test_key"]),
            "observation": text.strip(),
            "diagnosis": text.strip(),
            "rating": rating,
            "action": str(base.get("action") or "按人工确认结论复核并处理。"),
            "customer_text": text.strip(),
            "confidence": "high",
            "experience_rule_ids": list(base.get("experience_rule_ids") or []),
        })
    return adapted


def _legacy_manifest_image(legacy_key: str, images: Any) -> Mapping[str, Any]:
    normalized = PurePosixPath(legacy_key.replace("\\", "/")).as_posix().lstrip("./")
    matches: list[Mapping[str, Any]] = []
    for image in images if isinstance(images, list) else []:
        if not isinstance(image, Mapping):
            continue
        image_path = str(image.get("image_path") or "").replace("\\", "/")
        if image_path == normalized or image_path.endswith("/" + normalized):
            matches.append(image)
    if len(matches) != 1:
        detail = "not found" if not matches else "ambiguous"
        raise VisualReviewError(f"legacy human image key is {detail} in manifest: {legacy_key}")
    return matches[0]


def _validate_alignment(
    manifest: CurveReviewManifest,
    entries: list[dict[str, Any]],
    *,
    require_complete: bool,
) -> tuple[str, ...]:
    manifest_tests = {item.image_key: item.test_key for item in manifest.images}
    present: set[str] = set()
    for item in entries:
        typed = _validate_interpretation(item, "merged interpretation")
        if typed.image_key not in manifest_tests:
            raise VisualReviewError(f"interpretation image_key is not in manifest: {typed.image_key}")
        if typed.test_key != manifest_tests[typed.image_key]:
            raise VisualReviewError(f"interpretation test_key does not match manifest for {typed.image_key}")
        present.add(typed.image_key)
    missing = tuple(key for key in manifest_tests if key not in present)
    if require_complete and missing:
        raise VisualReviewError(f"missing interpretation for manifest image_key: {', '.join(missing)}")
    return missing


def _validate_interpretation(interpretation: Mapping[str, Any], context: str) -> CurveInterpretation:
    if not isinstance(interpretation, Mapping):
        raise VisualReviewError(f"{context} must be an object")
    fields = {field: _required_text(interpretation, field, context) for field in _REQUIRED_TEXT_FIELDS}
    image_key = fields["image_key"]
    rating = interpretation.get("rating")
    _validate_rating(rating, f"rating for {image_key}")
    confidence = fields["confidence"].lower()
    if confidence not in {"high", "medium", "low"}:
        raise VisualReviewError(f"confidence for {image_key} must be high, medium, or low")
    if confidence == "low" and rating == 4:
        if not any(_has_content(interpretation.get(field)) for field in _EVIDENCE_FIELDS):
            raise VisualReviewError(f"low-confidence grade 4 interpretation requires supporting evidence: {image_key}")
    return CurveInterpretation(
        image_key=image_key,
        test_key=fields["test_key"],
        observation=fields["observation"],
        diagnosis=fields["diagnosis"],
        rating=rating,
        action=fields["action"],
        customer_text=fields["customer_text"],
        confidence=confidence,
        experience_rule_ids=_experience_rule_ids(interpretation.get("experience_rule_ids", []), image_key),
    )


def _validate_merge_entries(entries: Iterable[Mapping[str, Any]], source: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    image_keys: set[str] = set()
    for index, interpretation in enumerate(entries):
        typed_interpretation = _validate_interpretation(interpretation, f"{source} interpretation at index {index}")
        if typed_interpretation.image_key in image_keys:
            raise VisualReviewError(f"duplicate {source} interpretation image_key: {typed_interpretation.image_key}")
        image_keys.add(typed_interpretation.image_key)
        item = dict(interpretation)
        item["confidence"] = typed_interpretation.confidence
        item["source"] = source.lower()
        items.append(item)
    return items


def _required_text(value: Mapping[str, Any], field: str, context: str) -> str:
    candidate = value.get(field)
    if not isinstance(candidate, str) or not candidate.strip():
        raise VisualReviewError(f"{context} requires non-empty {field}")
    return candidate.strip()


def _has_content(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Mapping):
        return any(_has_content(item) for item in value.values())
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes)):
        return any(_has_content(item) for item in value)
    return False


def _experience_rule_ids(value: Any, image_key: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise VisualReviewError(f"experience_rule_ids for {image_key} must be a list of non-empty strings")
    return tuple(item.strip() for item in value)


def _validate_rating(value: int | None, name: str, *, allow_none: bool = False) -> None:
    if value is None and allow_none:
        return
    if isinstance(value, bool) or not isinstance(value, int) or value not in (1, 2, 3, 4):
        raise VisualReviewError(f"{name} must be an integer from 1 to 4")
