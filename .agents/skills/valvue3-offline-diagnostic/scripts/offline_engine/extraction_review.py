from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import ProjectConfig


@dataclass(frozen=True)
class CodexReviewApplication:
    candidates: dict[str, list[dict[str, Any]]]
    applied_review_ids: frozenset[str]


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _load_results(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"invalid Codex extraction review results: {path}") from exc
    reviews = payload.get("reviews") if isinstance(payload, dict) else None
    if not isinstance(payload, dict) or payload.get("schema_version") != 1 or not isinstance(reviews, list):
        raise RuntimeError("Codex extraction review results must use schema_version 1")
    seen: set[str] = set()
    validated: list[dict[str, Any]] = []
    for review in reviews:
        if not isinstance(review, dict):
            raise RuntimeError("Codex extraction review entry must be an object")
        review_id = str(review.get("review_id") or "").strip()
        status = str(review.get("status") or "").strip()
        reason = str(review.get("reason") or "").strip()
        if not review_id or review_id in seen:
            raise RuntimeError("Codex extraction review IDs must be non-empty and unique")
        if status not in {"confirmed", "corrected", "unresolved"}:
            raise RuntimeError(f"unsupported Codex extraction review status: {status}")
        if not reason:
            raise RuntimeError(f"Codex extraction review requires a reason: {review_id}")
        if status != "unresolved" and review.get("value") is None:
            raise RuntimeError(f"Codex extraction review requires a value: {review_id}")
        seen.add(review_id)
        validated.append(review)
    return validated


def load_codex_review_candidates(
    config: ProjectConfig,
    valves: list[dict[str, Any]],
    *,
    extra_review_items: tuple[dict[str, Any], ...] | list[dict[str, Any]] = (),
    field_names: set[str],
) -> CodexReviewApplication:
    results_path = config.project_dir / "work/evidence-extraction/codex-review-results.json"
    if not results_path.is_file():
        return CodexReviewApplication(candidates={}, applied_review_ids=frozenset())
    current_items = {
        str(item["review_id"]): item
        for item in [
            *(item for valve in valves for item in valve["review_items"]),
            *extra_review_items,
        ]
        if item.get("review_id")
    }
    valve_tags = {str(valve["valve_tag"]) for valve in valves}
    candidates: dict[str, list[dict[str, Any]]] = {}
    applied_review_ids: set[str] = set()
    for review in _load_results(results_path):
        item = current_items.get(str(review["review_id"]))
        if item is None or review["status"] == "unresolved":
            continue
        if item.get("reason") == "source_conflict":
            continue
        source_path = (config.project_dir / str(item.get("source_path") or "")).resolve()
        try:
            source_path.relative_to(config.project_dir)
        except ValueError:
            continue
        if not source_path.is_file() or _sha256(source_path) != item.get("source_sha256"):
            continue
        review_id = str(review["review_id"])
        if item.get("field") == "supplementary.image_content":
            value = review.get("value")
            if not isinstance(value, dict):
                continue
            raw_fields = value.get("fields")
            if isinstance(raw_fields, dict):
                visible_fields = list(raw_fields.items())
            elif isinstance(raw_fields, list):
                visible_fields = [
                    (str(candidate.get("field") or ""), candidate.get("value"))
                    for candidate in raw_fields
                    if isinstance(candidate, dict)
                ]
            else:
                continue
            valve_tag = str(value.get("valve_tag") or item.get("valve_tag") or "")
            if valve_tag not in valve_tags:
                continue
            added = False
            seen_fields: set[str] = set()
            for field_name, field_value in visible_fields:
                if field_name not in field_names or field_value is None:
                    continue
                if field_name in seen_fields:
                    continue
                seen_fields.add(field_name)
                candidates.setdefault(valve_tag, []).append(
                    {
                        "field": field_name,
                        "value": field_value,
                        "path": source_path,
                        "record": review_id,
                        "method": "codex_review",
                        "source_role": "codex_review",
                        "priority": 900,
                        "candidate_status": "codex_reviewed",
                        "reason": str(review["reason"]),
                    }
                )
                added = True
            if added:
                applied_review_ids.add(review_id)
            continue
        field_name = str(item["field"])
        valve_tag = str(item["valve_tag"])
        if field_name not in field_names or valve_tag not in valve_tags:
            continue
        candidates.setdefault(valve_tag, []).append(
            {
                "field": field_name,
                "value": review["value"],
                "path": source_path,
                "record": review_id,
                "method": "codex_review",
                "source_role": "codex_review",
                "priority": 900,
                "candidate_status": "codex_reviewed",
                "reason": str(review["reason"]),
                "normalization_rule": str(item.get("normalization_rule") or ""),
            }
        )
        applied_review_ids.add(review_id)
    return CodexReviewApplication(
        candidates=candidates,
        applied_review_ids=frozenset(applied_review_ids),
    )
