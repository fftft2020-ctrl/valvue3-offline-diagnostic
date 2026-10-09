"""Validate source-bound read declarations, not proof of a model's tool use.

Only pre-contract frozen artifacts may lack declarations. Nothing here turns a
model's self-report into independently verified reading or engineering review.
"""

from __future__ import annotations

from pathlib import PurePosixPath
import re
from typing import Any, Mapping


SOURCE_READ_CONTRACT = "per_original_read_status_v1"
SOURCE_READ_FIELDS = (
    "relative_path", "sha256", "status", "read_method", "read_reference",
    "failure_reason",
)


def source_read_template(bundle: Mapping[str, Any]) -> list[dict[str, str]]:
    return [
        {"relative_path": path, "sha256": digest, "status": "unread",
         "read_method": "", "read_reference": "", "failure_reason": ""}
        for path, digest in expected_source_reads(bundle).items()
    ]


def expected_source_reads(bundle: Mapping[str, Any]) -> dict[str, str]:
    evidence = bundle.get("evidence", {})
    originals = evidence.get("source_files", [])
    if not isinstance(originals, list):
        raise ValueError("source_files must be a list")
    registered = (evidence.get("structured_evidence") or {}).get("source_files")
    if registered is not None and originals != registered:
        raise ValueError("source_files do not match registered structured originals")
    expected: dict[str, str] = {}
    for source in originals:
        if not isinstance(source, Mapping):
            raise ValueError("source_files entries must be objects")
        path, digest = source.get("relative_path"), source.get("sha256")
        if (not isinstance(path, str) or not path or "\\" in path
                or PurePosixPath(path).is_absolute()
                or ".." in PurePosixPath(path).parts
                or str(PurePosixPath(path)) != path):
            raise ValueError("source_files require canonical relative_path")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("source_files require SHA-256")
        if path in expected:
            raise ValueError("source_files contain duplicate relative_path")
        expected[path] = digest
    return expected


def validate_source_reads(bundle: Mapping[str, Any], reads: Any) -> None:
    expected = expected_source_reads(bundle)
    if not isinstance(reads, list):
        raise ValueError("source_file_reads must list every original")
    submitted: dict[str, str] = {}
    for item in reads:
        if (not isinstance(item, dict) or set(item) != set(SOURCE_READ_FIELDS)
                or any(not isinstance(item[key], str) for key in SOURCE_READ_FIELDS)):
            raise ValueError("source_file_reads entries require exact text fields")
        path = item["relative_path"]
        if path in submitted:
            raise ValueError("source_file_reads contain duplicate relative_path")
        if path not in expected or item["sha256"] != expected[path]:
            raise ValueError("source_file_reads source path or SHA-256 does not match")
        submitted[path] = item["sha256"]
        if item["status"] not in {"read", "failed", "unread"}:
            raise ValueError("source_file_reads status is invalid")
        if item["status"] != "read":
            raise ValueError(f"source_file_reads contains {item['status']}: {path}")
        if not item["read_method"].strip() or not item["read_reference"].strip():
            raise ValueError("source_file_reads read entries require method and trace reference")
        if item["failure_reason"].strip():
            raise ValueError("source_file_reads read entries cannot contain a failure_reason")
    if submitted != expected:
        raise ValueError("source_file_reads must cover every original exactly once")


def validate_source_read_acceptance(
    bundle: Mapping[str, Any], observations: Mapping[str, Any],
    diagnosis: Mapping[str, Any], manifest: Mapping[str, Any],
) -> str:
    """Gate new acceptance; preserve genuine pre-contract frozen records read-only."""
    markers = [value.get("source_read_contract")
               for value in (observations, diagnosis, manifest)]
    runtime_version = (manifest.get("skill_runtime") or {}).get("version", "")
    version = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", str(runtime_version))
    current_runtime = bool(version and tuple(map(int, version.groups())) >= (0, 1, 2))
    declared = (any(marker is not None for marker in markers)
                or "source_file_reads" in observations or current_runtime)
    if not declared and manifest.get("stage") == "frozen":
        return "legacy_unrecorded"
    if any(marker != SOURCE_READ_CONTRACT for marker in markers):
        raise ValueError("source_read_contract is missing or invalid; fresh source reads required")
    validate_source_reads(bundle, observations.get("source_file_reads"))
    return "reported_complete"
