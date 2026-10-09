"""Load frozen single-valve diagnoses for direct report consumption."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .visual_review import CurveInterpretation, validate_manifest
from .evidence_extraction import structured_evidence_accounting
from .generator import algorithm_audit_input_digest


class FormalDiagnosisError(ValueError):
    """Raised when frozen formal diagnosis evidence is incomplete or stale."""


@dataclass(frozen=True)
class FormalDiagnosis:
    """Validated report-facing content from one completed project diagnosis run."""

    curve_interpretations: tuple[CurveInterpretation, ...]
    summary_overrides: dict[str, dict[str, Any]]
    algorithm_audit_snapshots: dict[str, dict[str, Any] | None] = field(
        default_factory=dict
    )
    algorithm_audit_statuses: dict[str, str] = field(default_factory=dict)


_LEGACY_FROZEN_FILES = (
    "01_evidence_bundle.json",
    "02_retrieved_cases.json",
    "03_observations.json",
    "04_retrieved_rules.json",
    "05_diagnosis.json",
)
_VERSIONED_FROZEN_FILES = (
    "00_version.json",
    *_LEGACY_FROZEN_FILES,
)
_ALLOWED_FROZEN_FILES = frozenset((*_VERSIONED_FROZEN_FILES, "00_experience_snapshot.json", "00_experience.md"))
_ALGORITHM_AUDIT_CONTRACT = "deterministic_legacy_rating_v1"
_VOLATILE_SEMANTIC_FIELDS = {
    "created_at",
    "generated_at",
    "updated_at",
    "extracted_at",
    "prepared_at",
    "frozen_at",
    "image_path",
    "cache_path",
    "ocr_cache_path",
    "temporary_path",
    "temp_path",
    "output_path",
    "project_root",
    "raw_folder",
    "output_version",
    "report_version",
}


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FormalDiagnosisError(f"invalid formal diagnosis JSON: {path}") from exc


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise FormalDiagnosisError(f"formal diagnosis file is missing: {path}") from exc


def _sha256_payload(payload: Any) -> str:
    serialized = (
        json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def _stable_evidence_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _stable_evidence_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if str(key) not in _VOLATILE_SEMANTIC_FIELDS
        }
    if isinstance(value, (list, tuple)):
        return [_stable_evidence_value(item) for item in value]
    if isinstance(value, str) and Path(value).is_absolute():
        path = Path(value)
        for anchor in ("raw", "specs", "inbound", "experience", "work"):
            if anchor in path.parts:
                return Path(*path.parts[path.parts.index(anchor) :]).as_posix()
        return path.name
    return value


def _diagnostic_evidence_sha256(evidence_bundle: Mapping[str, Any]) -> str:
    evidence = json.loads(
        json.dumps(evidence_bundle.get("evidence") or {}, ensure_ascii=False)
    )
    for field in ("curve_images", "specification_images"):
        for image in evidence.get(field) or []:
            if isinstance(image, dict):
                image.pop("image_path", None)
    return _sha256_payload(_stable_evidence_value(evidence))


def _validate_diagnosis_basis(
    run_dir: Path,
    evidence: Mapping[str, Any],
    diagnosis: Mapping[str, Any],
) -> None:
    basis = diagnosis.get("diagnosis_basis")
    if not isinstance(basis, Mapping):
        raise FormalDiagnosisError(
            f"formal diagnosis requires diagnosis_basis: {run_dir}"
        )
    mode = str(basis.get("mode") or "").strip()
    if mode not in {
        "fresh_reanalysis",
        "inherited_frozen",
        "inherited_legacy_frozen",
        "inherited_source_bound",
    }:
        raise FormalDiagnosisError(
            f"formal diagnosis_basis mode is invalid: {run_dir}"
        )
    expected = {
        "evidence_semantic_sha256": _diagnostic_evidence_sha256(evidence),
        "evidence_file_sha256": _sha256(run_dir / "01_evidence_bundle.json"),
        "observations_sha256": _sha256(run_dir / "03_observations.json"),
    }
    manifest = _read_json(run_dir / "00_run_manifest.json")
    if manifest.get("experience_snapshot") == "00_experience.md":
        expected["experience_snapshot_sha256"] = _sha256(run_dir / "00_experience.md")
    else:
        expected["retrieved_cases_sha256"] = _sha256(run_dir / "02_retrieved_cases.json")
        expected["retrieved_rules_sha256"] = _sha256(run_dir / "04_retrieved_rules.json")
        if (run_dir / "00_experience_snapshot.json").is_file():
            expected["experience_snapshot_sha256"] = _sha256(run_dir / "00_experience_snapshot.json")
    if any(basis.get(key) != value for key, value in expected.items()):
        raise FormalDiagnosisError(
            f"formal diagnosis_basis input hash mismatch: {run_dir}"
        )
    if mode in {
        "inherited_frozen",
        "inherited_legacy_frozen",
        "inherited_source_bound",
    }:
        parent_value = str(basis.get("parent_run_dir") or "").strip()
        if not parent_value:
            raise FormalDiagnosisError(
                f"inherited diagnosis_basis requires parent_run_dir: {run_dir}"
            )
        parent_run = Path(parent_value)
        for field, filename in (
            ("parent_diagnosis_sha256", "05_diagnosis.json"),
            ("parent_freeze_sha256", "06_freeze.sha256"),
        ):
            if basis.get(field) != _sha256(parent_run / filename):
                raise FormalDiagnosisError(
                    f"inherited diagnosis_basis parent hash mismatch: {run_dir}"
                )
        if mode == "inherited_legacy_frozen" and not str(
            basis.get("legacy_parent_reason") or ""
        ).strip():
            raise FormalDiagnosisError(
                f"legacy inheritance requires a reason: {run_dir}"
            )
        if mode == "inherited_source_bound" and not str(
            basis.get("source_binding_reason") or ""
        ).strip():
            raise FormalDiagnosisError(
                f"source binding inheritance requires a reason: {run_dir}"
            )


def _validate_evidence_accounting(
    run_dir: Path,
    evidence: Mapping[str, Any],
    observations: Mapping[str, Any],
    diagnosis: Mapping[str, Any],
) -> None:
    """Require the v2 accounting projection for newly-created fresh runs."""
    basis = diagnosis.get("diagnosis_basis")
    if not isinstance(basis, Mapping) or basis.get("mode") != "fresh_reanalysis":
        return
    if basis.get("evidence_contract_version") != "2":
        # A pre-contract frozen package is deliberately readable.
        return
    structured = evidence.get("evidence", {}).get("structured_evidence")
    if not isinstance(structured, Mapping) or not structured:
        raise FormalDiagnosisError(
            f"{run_dir} fresh_reanalysis requires structured_evidence"
        )
    accounting = observations.get("evidence_accounting")
    if not isinstance(accounting, Mapping):
        raise FormalDiagnosisError(
            f"{run_dir} fresh_reanalysis requires evidence_accounting"
        )
    try:
        expected = structured_evidence_accounting(dict(structured))
    except (TypeError, ValueError, AttributeError) as exc:
        raise FormalDiagnosisError(
            f"{run_dir} structured_evidence cannot be accounted"
        ) from exc
    if dict(accounting) != expected:
        raise FormalDiagnosisError(
            f"{run_dir} evidence_accounting does not match structured_evidence"
        )


def _validate_evidence_pages(
    run_dir: Path,
    evidence: Mapping[str, Any],
    structured_evidence: Mapping[str, Any],
) -> None:
    """Keep the bound page-role projection aligned with report page images."""
    images = evidence.get("evidence", {}).get("specification_images") or []
    image_ids = {
        str(item.get("physical_identity") or "").strip()
        for item in images
        if isinstance(item, Mapping)
        and str(item.get("physical_identity") or "").strip()
    }
    pages = structured_evidence.get("evidence_pages") or []
    page_ids = {
        str(item.get("physical_identity") or "").strip()
        for item in pages
        if isinstance(item, Mapping)
        and str(item.get("physical_identity") or "").strip()
    }
    page_by_id = {
        str(item.get("physical_identity")): item
        for item in pages
        if isinstance(item, Mapping) and item.get("physical_identity")
    }
    missing_image_pages = page_ids - image_ids
    if image_ids - page_ids or any(
        set(page_by_id[identity].get("roles") or []) != {"accessory"}
        for identity in missing_image_pages
    ):
        raise FormalDiagnosisError(
            f"{run_dir} frozen evidence pages do not match report pages"
        )
    image_by_id = {
        str(item.get("physical_identity")): item
        for item in images
        if isinstance(item, Mapping) and item.get("physical_identity")
    }
    for identity in image_ids:
        image = image_by_id[identity]
        page = page_by_id[identity]
        if set(image.get("roles") or []) != set(page.get("roles") or []):
            raise FormalDiagnosisError(
                f"{run_dir} frozen evidence page roles do not match report pages"
            )


def _stable_report_pages(
    pages: Any,
    *,
    context: str,
) -> tuple[tuple[str, int, tuple[str, ...]], ...]:
    """Project attachment pages to their semantic physical-page identity.

    ``analysis_summary.json`` carries presentation-only fields such as rendered
    image paths and insertion markers.  They must not affect the comparison
    with frozen evidence.  A physical page is identified by source SHA-256 and
    one-based page number; repeated references to that page merge their roles.
    """
    if pages is None:
        pages = []
    if not isinstance(pages, list):
        raise FormalDiagnosisError(f"{context} report_pages must be a list")
    merged: dict[tuple[str, int], set[str]] = {}
    for index, raw_page in enumerate(pages):
        if not isinstance(raw_page, Mapping):
            raise FormalDiagnosisError(
                f"{context} report_pages[{index}] must be an object"
            )
        source_sha256 = str(raw_page.get("source_sha256") or "").strip()
        page_number = raw_page.get("page")
        if not source_sha256 or isinstance(page_number, bool) or not isinstance(
            page_number, int
        ):
            raise FormalDiagnosisError(
                f"{context} report page requires source_sha256 and integer page"
            )
        roles = {
            str(role).strip()
            for role in raw_page.get("roles") or []
            if str(role).strip()
        }
        merged.setdefault((source_sha256, page_number), set()).update(roles)
    return tuple(
        (source_sha256, page_number, tuple(sorted(roles)))
        for (source_sha256, page_number), roles in sorted(merged.items())
    )


def _report_pages_by_valve(
    *,
    analysis_summary_path: Path | None = None,
    current_report_pages: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Load current report-page records from a summary or stable projection.

    The stable mapping form is useful to callers that already have a parsed
    summary.  It deliberately remains optional so legacy direct loader users
    and non-formal reports keep their existing contract.
    """
    if analysis_summary_path is not None and current_report_pages is not None:
        raise FormalDiagnosisError(
            "provide either analysis_summary_path or current_report_pages, not both"
        )
    if analysis_summary_path is None and current_report_pages is None:
        return None

    if analysis_summary_path is not None:
        return {
            valve_tag: item.get("report_pages") or []
            for valve_tag, item in _analysis_summary_by_valve(
                analysis_summary_path
            ).items()
        }

    pages_by_valve = {}
    for valve_tag, value in current_report_pages.items():
        tag = str(valve_tag).strip()
        if not tag:
            raise FormalDiagnosisError("current_report_pages contains an empty valve tag")
        if isinstance(value, Mapping) and "report_pages" in value:
            value = value.get("report_pages")
        pages_by_valve[tag] = value or []
    return pages_by_valve


def _analysis_summary_by_valve(
    analysis_summary_path: Path,
) -> dict[str, Mapping[str, Any]]:
    """Load complete current valve snapshots from ``analysis_summary.json``.

    Formal report loading used to retain only ``report_pages`` from this file.
    That allowed a frozen diagnosis to be combined with a newly extracted
    Sequencer/specification snapshot.  Keep the record intact here so the
    v2 evidence contract can compare the full semantic evidence projection.
    """
    raw_summary = _read_json(analysis_summary_path)
    if isinstance(raw_summary, Mapping):
        raw_summary = raw_summary.get("valves")
    if not isinstance(raw_summary, list):
        raise FormalDiagnosisError(
            f"current analysis summary must be a list: {analysis_summary_path}"
        )
    records_by_valve: dict[str, Mapping[str, Any]] = {}
    for index, item in enumerate(raw_summary):
        if not isinstance(item, Mapping):
            raise FormalDiagnosisError(
                f"current analysis summary valve[{index}] must be an object"
            )
        valve_tag = str(
            item.get("folder")
            or item.get("valve_tag")
            or item.get("target_valve_tag")
            or ""
        ).strip()
        if not valve_tag:
            raise FormalDiagnosisError(
                f"current analysis summary valve[{index}] requires folder"
            )
        if valve_tag in records_by_valve:
            raise FormalDiagnosisError(
                f"duplicate current analysis summary valve: {valve_tag}"
            )
        records_by_valve[valve_tag] = item
    return records_by_valve


def _validate_current_report_pages(
    run_dir: Path,
    valve_tag: str,
    structured_evidence: Mapping[str, Any],
    current_pages_by_valve: Mapping[str, Any],
) -> None:
    if valve_tag not in current_pages_by_valve:
        raise FormalDiagnosisError(
            f"{valve_tag} current analysis summary has no report_pages; "
            "run prepare-formal-evidence first"
        )
    frozen_pages = _stable_report_pages(
        structured_evidence.get("evidence_pages") or [],
        context=f"{valve_tag} frozen evidence",
    )
    current_pages = _stable_report_pages(
        current_pages_by_valve[valve_tag],
        context=f"{valve_tag} current analysis",
    )
    if frozen_pages != current_pages:
        raise FormalDiagnosisError(
            f"{valve_tag} current analysis report_pages do not match "
            "frozen evidence_pages; run prepare-formal-evidence and create a "
            "new formal diagnosis"
        )


def _validate_current_structured_evidence(
    run_dir: Path,
    valve_tag: str,
    frozen_structured_evidence: Mapping[str, Any],
    current_structured_evidence: Any,
) -> None:
    """Bind a v2 frozen diagnosis to the current full evidence snapshot.

    ``structured_evidence`` is already the stable semantic projection emitted
    by the extraction workflow.  Re-project both sides here nevertheless so
    path and timestamp-only changes in a current summary do not invalidate a
    diagnosis, while source hashes, normalized fields, faults, task rows,
    metrics, and page roles remain strictly bound.
    """
    if not isinstance(current_structured_evidence, Mapping) or not current_structured_evidence:
        raise FormalDiagnosisError(
            f"{valve_tag} current analysis requires structured_evidence; "
            "run prepare-formal-evidence"
        )
    frozen_projection = _stable_evidence_value(frozen_structured_evidence)
    current_projection = _stable_evidence_value(current_structured_evidence)
    if frozen_projection != current_projection:
        raise FormalDiagnosisError(
            f"{valve_tag} current analysis structured_evidence does not match "
            "frozen structured_evidence; create a new formal diagnosis"
        )


def _safe_project_source_path(
    project_root: Path,
    relative_path: Any,
    *,
    context: str,
) -> Path:
    """Resolve a source path without permitting absolute or escaping paths."""
    relative = Path(str(relative_path or "").strip())
    if not str(relative) or relative.is_absolute() or ".." in relative.parts:
        raise FormalDiagnosisError(
            f"{context} source path must be a safe relative path"
        )
    root = project_root.resolve()
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise FormalDiagnosisError(
            f"{context} source path escapes project root"
        ) from exc
    return candidate


def _validate_project_source_files(
    analysis_summary_path: Path,
    valve_tag: str,
    structured_evidence: Mapping[str, Any],
    *,
    project_root: Path | None = None,
) -> None:
    """Ensure v2 frozen source hashes still describe the current project.

    The report pipeline supplies the configured project root, including when
    its summary lives in a batch subdirectory. Callers using the default
    ``<project>/work/analysis_summary.json`` layout may omit it. The frozen
    source list prevents a stale summary from hiding edited evidence.
    """
    project_root = (project_root or analysis_summary_path.parent.parent).resolve()
    source_files = structured_evidence.get("source_files")
    if source_files is not None and not isinstance(source_files, list):
        raise FormalDiagnosisError(
            f"{valve_tag} frozen structured_evidence source_files must be a list"
        )
    for index, item in enumerate(source_files or []):
        if not isinstance(item, Mapping):
            raise FormalDiagnosisError(
                f"{valve_tag} frozen source_files[{index}] must be an object"
            )
        context = f"{valve_tag} frozen source_files[{index}]"
        source_path = _safe_project_source_path(
            project_root,
            item.get("relative_path"),
            context=context,
        )
        if not source_path.is_file():
            raise FormalDiagnosisError(
                f"{context} source file is missing: {source_path}"
            )
        declared_size = item.get("size_bytes")
        if declared_size is not None:
            if isinstance(declared_size, bool) or not isinstance(declared_size, int):
                raise FormalDiagnosisError(
                    f"{context} size_bytes must be an integer"
                )
            if source_path.stat().st_size != declared_size:
                raise FormalDiagnosisError(
                    f"{context} source file size mismatch: {source_path}"
                )
        declared_sha256 = str(item.get("sha256") or "").strip().lower()
        if not declared_sha256:
            raise FormalDiagnosisError(f"{context} requires sha256")
        if _sha256(source_path) != declared_sha256:
            raise FormalDiagnosisError(
                f"{context} source file sha256 mismatch: {source_path}"
            )

    # ``source_files`` covers raw valve inputs; matched specification pages
    # live under ``specs/`` and carry their own source SHA in evidence_pages.
    pages = structured_evidence.get("evidence_pages") or []
    if not isinstance(pages, list):
        raise FormalDiagnosisError(
            f"{valve_tag} frozen structured_evidence evidence_pages must be a list"
        )
    for index, page in enumerate(pages):
        if not isinstance(page, Mapping):
            continue
        source = str(page.get("source") or "").strip()
        declared_sha256 = str(page.get("source_sha256") or "").strip().lower()
        if not source or not declared_sha256:
            continue
        context = f"{valve_tag} frozen evidence_pages[{index}]"
        source_path = _safe_project_source_path(
            project_root,
            source,
            context=context,
        )
        if not source_path.is_file() and Path(source).parts[:1] != ("specs",):
            # SpecPage records historically store paths relative to the
            # ``specs/`` directory (for example ``F13007B3/file.pdf``), while
            # newer records may include the explicit ``specs/`` prefix.
            source_path = _safe_project_source_path(
                project_root / "specs",
                source,
                context=context,
            )
        if not source_path.is_file():
            raise FormalDiagnosisError(
                f"{context} source file is missing: {source_path}"
            )
        if _sha256(source_path) != declared_sha256:
            raise FormalDiagnosisError(
                f"{context} source file sha256 mismatch: {source_path}"
            )


def _required_text(item: Mapping[str, Any], key: str, context: str) -> str:
    value = str(item.get(key) or "").strip()
    if not value:
        raise FormalDiagnosisError(f"{context} requires {key}")
    return value


def _sentence(text: str) -> str:
    value = text.strip()
    if not value:
        return ""
    return value if value.endswith(("。", "！", "？", "；")) else value + "。"


def _customer_text(observation: str, diagnosis: str, action: str, explicit: Any) -> str:
    provided = str(explicit or "").strip()
    if provided:
        return provided
    parts = [_sentence(observation), _sentence(diagnosis)]
    if action.strip():
        parts.append("建议" + _sentence(action))
    return "".join(parts)


def _verify_freeze(
    run_dir: Path,
    *,
    require_versioned: bool = False,
) -> None:
    freeze_path = run_dir / "06_freeze.sha256"
    try:
        lines = freeze_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise FormalDiagnosisError(f"freeze manifest is missing: {freeze_path}") from exc
    expected: dict[str, str] = {}
    for line in lines:
        digest, separator, name = line.partition("  ")
        if (
            not separator
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            or name not in _ALLOWED_FROZEN_FILES
        ):
            raise FormalDiagnosisError(f"invalid freeze manifest entry: {line}")
        expected[name] = digest
    run_manifest = _read_json(run_dir / "00_run_manifest.json")
    versioned = require_versioned or (run_dir / "00_version.json").exists() or any(
        str(run_manifest.get(field) or "").strip()
        for field in (
            "diagnosis_version",
            "parent_run",
            "revision_reason",
        )
    )
    frozen_names = tuple(expected)
    expected_names = set(expected)
    required_names = set(
        _VERSIONED_FROZEN_FILES
        if versioned
        else _LEGACY_FROZEN_FILES
    )
    if run_manifest.get("experience_snapshot") == "00_experience.md":
        required_names.difference_update({"02_retrieved_cases.json", "04_retrieved_rules.json"})
        required_names.add("00_experience.md")
    elif run_manifest.get("experience_snapshot"):
        required_names.add("00_experience_snapshot.json")
    if expected_names != required_names:
        raise FormalDiagnosisError(f"freeze manifest is incomplete: {freeze_path}")
    for name in frozen_names:
        if _sha256(run_dir / name) != expected[name]:
            raise FormalDiagnosisError(f"freeze hash mismatch: {run_dir / name}")


def _manifest_images_by_sha(curve_manifest_path: Path) -> dict[str, list[dict[str, str]]]:
    raw_manifest = _read_json(curve_manifest_path)
    typed_manifest = validate_manifest(raw_manifest)
    raw_images = raw_manifest["images"]
    by_key = {item.image_key: item for item in typed_manifest.images}
    by_sha: dict[str, list[dict[str, str]]] = {}
    for raw_image in raw_images:
        image_key = str(raw_image.get("image_key") or "").strip()
        image = by_key[image_key]
        image_path = Path(_required_text(raw_image, "image_path", f"manifest image {image_key}"))
        if not image_path.is_absolute():
            image_path = curve_manifest_path.parent / image_path
        digest = _sha256(image_path)
        by_sha.setdefault(digest, []).append(
            {
                "image_key": image.image_key,
                "test_key": image.test_key,
                "image_path": str(image_path),
            }
        )
    return by_sha


def _select_manifest_image(
    candidates: list[dict[str, str]],
    *,
    valve_tag: str,
    test_key: str,
    evidence_path: str,
    image_sha256: str,
) -> dict[str, str]:
    matching_test = [item for item in candidates if item["test_key"] == test_key]
    if len(matching_test) == 1:
        return matching_test[0]
    matching_path = [
        item
        for item in matching_test
        if Path(item["image_path"]).name == Path(evidence_path).name
        and valve_tag in Path(item["image_path"]).parts
    ]
    if len(matching_path) == 1:
        return matching_path[0]
    raise FormalDiagnosisError(
        f"{valve_tag} curve image cannot be uniquely matched by SHA-256: {image_sha256}"
    )


def _summary_override(valve_tag: str, whole: Mapping[str, Any]) -> dict[str, Any]:
    rating = whole.get("final_rating")
    if isinstance(rating, bool) or not isinstance(rating, int) or rating not in range(1, 6):
        raise FormalDiagnosisError(f"{valve_tag} final_rating must be an integer from 1 to 5")
    rating_conclusion = _required_text(whole, "rating_conclusion", f"{valve_tag} whole_valve")
    main_issue = _required_text(whole, "main_issue", f"{valve_tag} whole_valve")
    issues = whole.get("issues")
    actions = whole.get("actions")
    if not isinstance(issues, list) or not all(
        isinstance(item, str) and item.strip() for item in issues
    ):
        raise FormalDiagnosisError(f"{valve_tag} whole_valve issues must be a text list")
    if (
        not isinstance(actions, list)
        or not actions
        or not all(isinstance(item, str) and item.strip() for item in actions)
    ):
        raise FormalDiagnosisError(f"{valve_tag} whole_valve actions must be a text list")
    clean_issues = [item.strip() for item in issues]
    clean_actions = [item.strip() for item in actions]
    return {
        "rating": rating,
        "rating_conclusion": rating_conclusion,
        "main_issue": main_issue,
        "issues": clean_issues,
        "action": "；".join(item.rstrip("。；") for item in clean_actions) + ("。" if clean_actions else ""),
        "actions": clean_actions,
        "overall_assessment": f"{rating_conclusion} 主要问题：{main_issue}",
        "health_rating_detail_mode": "summary_only",
    }


def validate_legacy_algorithm_audit_parent(
    parent_run: Path,
    *,
    seen: set[Path] | None = None,
) -> None:
    """Prove that an inherited unavailable audit traces to a real legacy package."""
    resolved = parent_run.resolve()
    visited = set() if seen is None else seen
    if resolved in visited:
        raise FormalDiagnosisError("algorithm audit inheritance contains a cycle")
    visited.add(resolved)
    _verify_freeze(resolved)
    manifest = _read_json(resolved / "00_run_manifest.json")
    diagnosis = _read_json(resolved / "05_diagnosis.json")
    contract = str(diagnosis.get("algorithm_audit_contract") or "").strip()
    status = str(diagnosis.get("algorithm_audit_status") or "").strip()
    snapshot = diagnosis.get("algorithm_audit_snapshot")
    manifest_contract = str(
        manifest.get("algorithm_audit_contract") or ""
    ).strip()
    if not contract and not status and snapshot is None and not manifest_contract:
        return
    if (
        contract != _ALGORITHM_AUDIT_CONTRACT
        or manifest_contract != contract
        or status != "legacy_unavailable"
        or snapshot is not None
    ):
        raise FormalDiagnosisError(
            "available or invalid parent algorithm audit cannot be downgraded"
        )
    basis = diagnosis.get("diagnosis_basis")
    if not isinstance(basis, Mapping) or not str(
        basis.get("mode") or ""
    ).startswith("inherited_"):
        raise FormalDiagnosisError("legacy algorithm audit parent is not inherited")
    evidence = _read_json(resolved / "01_evidence_bundle.json")
    _validate_diagnosis_basis(resolved, evidence, diagnosis)
    ancestor = str(basis.get("parent_run_dir") or "").strip()
    if not ancestor:
        raise FormalDiagnosisError("legacy algorithm audit parent is missing")
    validate_legacy_algorithm_audit_parent(Path(ancestor), seen=visited)


def _algorithm_audit(
    run_dir: Path,
    valve_tag: str,
    run_manifest: Mapping[str, Any],
    diagnosis: Mapping[str, Any],
    source_record: Mapping[str, Any] | None,
    curve_interpretations: tuple[CurveInterpretation, ...],
) -> tuple[str, dict[str, Any] | None]:
    """Read a frozen audit snapshot without calculating a replacement."""
    manifest_contract = str(
        run_manifest.get("algorithm_audit_contract") or ""
    ).strip()
    contract = str(diagnosis.get("algorithm_audit_contract") or "").strip()
    status = str(diagnosis.get("algorithm_audit_status") or "").strip()
    snapshot = diagnosis.get("algorithm_audit_snapshot")
    audit_declared = bool(manifest_contract or contract)
    if audit_declared and (
        manifest_contract != _ALGORITHM_AUDIT_CONTRACT
        or contract != _ALGORITHM_AUDIT_CONTRACT
    ):
        raise FormalDiagnosisError(
            f"{valve_tag} algorithm audit contract is missing or invalid"
        )
    if not contract:
        if status or snapshot is not None:
            raise FormalDiagnosisError(
                f"{valve_tag} algorithm audit metadata is incomplete"
            )
        return "legacy_unavailable", None
    if contract != _ALGORITHM_AUDIT_CONTRACT:
        raise FormalDiagnosisError(
            f"{valve_tag} algorithm audit contract is unsupported"
        )
    if status == "legacy_unavailable":
        if snapshot is not None:
            raise FormalDiagnosisError(
                f"{valve_tag} unavailable algorithm audit must not contain a snapshot"
            )
        basis = diagnosis.get("diagnosis_basis")
        if not isinstance(basis, Mapping) or not str(
            basis.get("mode") or ""
        ).startswith("inherited_"):
            raise FormalDiagnosisError(
                f"{valve_tag} current diagnosis cannot downgrade algorithm audit"
            )
        parent_value = str(basis.get("parent_run_dir") or "").strip()
        parent_run = Path(parent_value)
        if not parent_value:
            raise FormalDiagnosisError(
                f"{valve_tag} legacy algorithm audit parent is not frozen"
            )
        validate_legacy_algorithm_audit_parent(parent_run)
        return status, None
    if status != "available" or not isinstance(snapshot, Mapping):
        raise FormalDiagnosisError(
            f"{valve_tag} algorithm audit snapshot is missing"
        )
    if snapshot.get("status") != "available":
        raise FormalDiagnosisError(
            f"{valve_tag} algorithm audit snapshot status is invalid"
        )
    if snapshot.get("schema_version") != 1:
        raise FormalDiagnosisError(
            f"{valve_tag} algorithm audit schema is unsupported"
        )
    for field_name in ("data_rating", "visual_rating"):
        rating = snapshot.get(field_name)
        if rating is not None and (
            isinstance(rating, bool)
            or not isinstance(rating, int)
            or rating not in range(1, 6)
        ):
            raise FormalDiagnosisError(
                f"{valve_tag} algorithm audit {field_name} is invalid"
            )
    algorithm_rating = snapshot.get("algorithm_rating")
    if (
        isinstance(algorithm_rating, bool)
        or not isinstance(algorithm_rating, int)
        or algorithm_rating not in range(1, 6)
    ):
        raise FormalDiagnosisError(
            f"{valve_tag} algorithm audit algorithm_rating is invalid"
        )
    if not isinstance(snapshot.get("rating_evidence"), Mapping):
        raise FormalDiagnosisError(
            f"{valve_tag} algorithm audit rating_evidence is invalid"
        )
    digest = str(snapshot.get("input_digest") or "")
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise FormalDiagnosisError(
            f"{valve_tag} algorithm audit input_digest is invalid"
        )
    if not isinstance(source_record, Mapping):
        raise FormalDiagnosisError(
            f"{valve_tag} algorithm audit requires frozen source_record"
        )
    if (
        not isinstance(source_record.get("has_test_data"), bool)
        or not isinstance(source_record.get("metrics"), Mapping)
        or not isinstance(source_record.get("active_faults"), list)
        or not all(
            isinstance(item, str)
            for item in source_record.get("active_faults", [])
        )
        or not isinstance(source_record.get("basic_action_failed"), bool)
    ):
        raise FormalDiagnosisError(
            f"{valve_tag} algorithm audit inputs are invalid"
        )
    try:
        expected_digest = algorithm_audit_input_digest(
            source_record, curve_interpretations
        )
    except (TypeError, ValueError) as exc:
        raise FormalDiagnosisError(
            f"{valve_tag} algorithm audit inputs are invalid"
        ) from exc
    if digest != expected_digest:
        raise FormalDiagnosisError(
            f"{valve_tag} algorithm audit input_digest mismatch"
        )
    return status, dict(snapshot)


def load_formal_diagnosis(
    run_root: Path,
    curve_manifest_path: Path,
    *,
    required_valve_tags: tuple[str, ...] = (),
    require_versioned: bool = False,
    analysis_summary_path: Path | None = None,
    current_report_pages: Mapping[str, Any] | None = None,
    project_root: Path | None = None,
) -> FormalDiagnosis:
    """Validate and adapt a frozen project run without writing conversion files."""
    run_root = run_root.resolve()
    project_manifest = _read_json(run_root / "00_project_run_manifest.json")
    targets = project_manifest.get("targets") if isinstance(project_manifest, Mapping) else None
    if not isinstance(targets, list):
        raise FormalDiagnosisError("formal project run manifest requires targets")
    target_by_tag: dict[str, Mapping[str, Any]] = {}
    for target in targets:
        if not isinstance(target, Mapping):
            raise FormalDiagnosisError("formal project run target must be an object")
        valve_tag = _required_text(target, "valve_tag", "formal project run target")
        if valve_tag in target_by_tag:
            raise FormalDiagnosisError(f"duplicate formal diagnosis valve: {valve_tag}")
        target_by_tag[valve_tag] = target

    required = tuple(dict.fromkeys(tag.strip() for tag in required_valve_tags if tag.strip()))
    missing = [tag for tag in required if tag not in target_by_tag]
    if missing:
        raise FormalDiagnosisError(f"missing required valve diagnosis: {', '.join(missing)}")
    selected_tags = required or tuple(target_by_tag)
    manifest_images_by_sha = _manifest_images_by_sha(curve_manifest_path)
    current_summary_by_valve = (
        _analysis_summary_by_valve(analysis_summary_path)
        if analysis_summary_path is not None
        else None
    )
    current_pages_by_valve = (
        {
            valve_tag: item.get("report_pages") or []
            for valve_tag, item in current_summary_by_valve.items()
        }
        if current_summary_by_valve is not None
        else _report_pages_by_valve(current_report_pages=current_report_pages)
    )

    interpretations: list[CurveInterpretation] = []
    summaries: dict[str, dict[str, Any]] = {}
    algorithm_audit_snapshots: dict[str, dict[str, Any] | None] = {}
    algorithm_audit_statuses: dict[str, str] = {}
    used_image_keys: set[str] = set()
    for valve_tag in selected_tags:
        interpretation_start = len(interpretations)
        target = target_by_tag[valve_tag]
        run_dir = (run_root / valve_tag).resolve()
        try:
            run_dir.relative_to(run_root)
        except ValueError as exc:
            raise FormalDiagnosisError(f"unsafe formal diagnosis run directory: {run_dir}") from exc
        if str(target.get("stage") or "").strip() != "frozen":
            raise FormalDiagnosisError(f"{valve_tag} formal diagnosis is not frozen")
        run_manifest = _read_json(run_dir / "00_run_manifest.json")
        if (
            not isinstance(run_manifest, Mapping)
            or str(run_manifest.get("stage") or "").strip() != "frozen"
            or str(run_manifest.get("target_valve_tag") or "").strip() != valve_tag
        ):
            raise FormalDiagnosisError(f"{valve_tag} run manifest is not a matching frozen diagnosis")
        _verify_freeze(
            run_dir,
            require_versioned=require_versioned,
        )

        evidence = _read_json(run_dir / "01_evidence_bundle.json")
        observations = _read_json(run_dir / "03_observations.json")
        diagnosis = _read_json(run_dir / "05_diagnosis.json")
        if require_versioned:
            if not isinstance(evidence, Mapping) or not isinstance(
                diagnosis,
                Mapping,
            ):
                raise FormalDiagnosisError(
                    f"{valve_tag} formal diagnosis inputs must be objects"
                )
            structured_evidence = evidence.get("evidence", {}).get(
                "structured_evidence"
            )
            if not isinstance(structured_evidence, Mapping) or not structured_evidence:
                raise FormalDiagnosisError(
                    f"{valve_tag} formal diagnosis requires structured_evidence"
                )
            _validate_diagnosis_basis(
                run_dir,
                evidence,
                diagnosis,
            )
            _validate_evidence_accounting(
                run_dir,
                evidence,
                observations,
                diagnosis,
            )
            if (
                isinstance(diagnosis.get("diagnosis_basis"), Mapping)
                and diagnosis["diagnosis_basis"].get("evidence_contract_version")
                == "2"
            ):
                _validate_evidence_pages(
                    run_dir,
                    evidence,
                    structured_evidence,
                )
                if current_summary_by_valve is not None:
                    current_record = current_summary_by_valve.get(valve_tag)
                    _validate_current_structured_evidence(
                        run_dir,
                        valve_tag,
                        structured_evidence,
                        current_record.get("structured_evidence")
                        if isinstance(current_record, Mapping)
                        else None,
                    )
                    _validate_project_source_files(
                        analysis_summary_path,
                        valve_tag,
                        structured_evidence,
                        project_root=project_root,
                    )
            if current_pages_by_valve is not None:
                _validate_current_report_pages(
                    run_dir,
                    valve_tag,
                    structured_evidence,
                    current_pages_by_valve,
                )
        curve_evidence = evidence.get("evidence", {}).get("curve_images", [])
        curve_observations = observations.get("curve_observations", [])
        curve_diagnoses = diagnosis.get("curve_interpretations", [])
        retrieved_rules = (
            {} if _read_json(run_dir / "00_run_manifest.json").get("experience_snapshot") == "00_experience.md"
            else _read_json(run_dir / "04_retrieved_rules.json")
        )
        if isinstance(retrieved_rules, list):
            retrieved_rules = {"rules": retrieved_rules}
        rules = retrieved_rules.get("rules", []) if isinstance(retrieved_rules, Mapping) else []
        rule_by_id = {
            str(rule.get("id")): rule
            for rule in rules
            if isinstance(rule, Mapping) and str(rule.get("id") or "").strip()
        }
        if not all(isinstance(items, list) for items in (curve_evidence, curve_observations, curve_diagnoses)):
            raise FormalDiagnosisError(f"{valve_tag} formal curve records must be lists")
        observation_by_sha = {
            _required_text(item, "image_sha256", f"{valve_tag} curve observation"): item
            for item in curve_observations
            if isinstance(item, Mapping)
        }
        diagnosis_by_sha = {
            _required_text(item, "image_sha256", f"{valve_tag} curve diagnosis"): item
            for item in curve_diagnoses
            if isinstance(item, Mapping)
        }
        for item in curve_evidence:
            if not isinstance(item, Mapping):
                raise FormalDiagnosisError(f"{valve_tag} curve evidence must be an object")
            image_sha256 = _required_text(item, "image_sha256", f"{valve_tag} curve evidence")
            test_key = _required_text(item, "test_key", f"{valve_tag} curve evidence")
            evidence_path = _required_text(item, "image_path", f"{valve_tag} curve evidence")
            candidates = manifest_images_by_sha.get(image_sha256, [])
            if not candidates:
                raise FormalDiagnosisError(
                    f"{valve_tag} frozen curve is stale or absent from report manifest: {image_sha256}"
                )
            manifest_image = _select_manifest_image(
                candidates,
                valve_tag=valve_tag,
                test_key=test_key,
                evidence_path=evidence_path,
                image_sha256=image_sha256,
            )
            image_key = manifest_image["image_key"]
            if image_key in used_image_keys:
                raise FormalDiagnosisError(f"duplicate formal curve match: {image_key}")
            used_image_keys.add(image_key)
            observation_item = observation_by_sha.get(image_sha256)
            diagnosis_item = diagnosis_by_sha.get(image_sha256)
            if observation_item is None or diagnosis_item is None:
                raise FormalDiagnosisError(f"{valve_tag} curve diagnosis is incomplete: {image_sha256}")
            if (
                _required_text(observation_item, "test_key", f"{valve_tag} curve observation") != test_key
                or _required_text(diagnosis_item, "test_key", f"{valve_tag} curve diagnosis") != test_key
            ):
                raise FormalDiagnosisError(f"{valve_tag} curve test_key mismatch: {image_sha256}")
            rating = diagnosis_item.get("rating")
            if isinstance(rating, bool) or not isinstance(rating, int) or rating not in range(1, 5):
                raise FormalDiagnosisError(f"{valve_tag} curve rating must be an integer from 1 to 4")
            observation = _required_text(
                observation_item,
                "observation",
                f"{valve_tag} curve observation",
            )
            curve_diagnosis = _required_text(
                diagnosis_item,
                "diagnosis",
                f"{valve_tag} curve diagnosis",
            )
            action = _required_text(diagnosis_item, "action", f"{valve_tag} curve diagnosis")
            confidence = _required_text(
                diagnosis_item,
                "confidence",
                f"{valve_tag} curve diagnosis",
            )
            rule_ids = diagnosis_item.get("experience_rule_ids", [])
            if not isinstance(rule_ids, list):
                raise FormalDiagnosisError(f"{valve_tag} experience_rule_ids must be a list")
            basis = diagnosis.get("diagnosis_basis")
            if (
                isinstance(basis, Mapping)
                and basis.get("evidence_contract_version") == "2"
                and "customer_text" in diagnosis_item
                and str(diagnosis_item.get("customer_text") or "").strip()
                != _customer_text(observation, curve_diagnosis, action, None)
            ):
                raise FormalDiagnosisError(
                    f"{valve_tag} curve customer_text is not bound to observation, diagnosis, and action"
                )
            for rule_id in rule_ids:
                rule = rule_by_id.get(str(rule_id))
                if rule is None:
                    raise FormalDiagnosisError(
                        f"{valve_tag} curve interpretation uses unknown rule id: {rule_id}"
                    )
                applicable = set(
                    rule.get("applicable_test_keys")
                    or [rule.get("test_key") or "other"]
                )
                if test_key not in applicable and "other" not in applicable:
                    raise FormalDiagnosisError(
                        f"{valve_tag} experience rule is not applicable to test_key: {rule_id}"
                    )
            interpretations.append(
                CurveInterpretation(
                    image_key=image_key,
                    test_key=test_key,
                    observation=observation,
                    diagnosis=curve_diagnosis,
                    rating=rating,
                    action=action,
                    customer_text=_customer_text(
                        observation,
                        curve_diagnosis,
                        action,
                        diagnosis_item.get("customer_text"),
                    ),
                    confidence=confidence,
                    experience_rule_ids=tuple(str(rule_id).strip() for rule_id in rule_ids),
                )
            )
        if set(observation_by_sha) != {
            _required_text(item, "image_sha256", f"{valve_tag} curve evidence")
            for item in curve_evidence
        } or set(diagnosis_by_sha) != set(observation_by_sha):
            raise FormalDiagnosisError(f"{valve_tag} formal curve coverage does not match evidence")
        whole = diagnosis.get("whole_valve")
        if not isinstance(whole, Mapping):
            raise FormalDiagnosisError(f"{valve_tag} formal diagnosis requires whole_valve")
        summaries[valve_tag] = _summary_override(valve_tag, whole)
        frozen_source_record = (
            evidence.get("evidence", {})
            .get("structured_evidence", {})
            .get("source_record")
        )
        audit_status, audit_snapshot = _algorithm_audit(
            run_dir,
            valve_tag,
            run_manifest,
            diagnosis,
            frozen_source_record,
            tuple(interpretations[interpretation_start:]),
        )
        algorithm_audit_statuses[valve_tag] = audit_status
        algorithm_audit_snapshots[valve_tag] = audit_snapshot

    return FormalDiagnosis(
        curve_interpretations=tuple(interpretations),
        summary_overrides=summaries,
        algorithm_audit_snapshots=algorithm_audit_snapshots,
        algorithm_audit_statuses=algorithm_audit_statuses,
    )
