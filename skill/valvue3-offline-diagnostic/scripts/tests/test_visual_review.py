from __future__ import annotations

import pytest
import json
from pathlib import Path

from offline_engine.visual_review import (
    CurveInterpretation,
    CurveReviewImage,
    CurveReviewManifest,
    DataEvidenceStatus,
    VisualReviewError,
    combine_ratings,
    merge_interpretations,
    load_curve_review_manifest,
    curve_review_readiness,
    validate_manifest,
    validate_interpretations,
)


def _manifest() -> dict:
    return {
        "schema_version": 1,
        "status": "curve_review_required",
        "images": [
            {"image_key": "FV-101:signature:1", "valve_tag": "FV-101", "test_key": "signature"},
            {"image_key": "FV-101:step:1", "valve_tag": "FV-101", "test_key": "step"},
        ],
    }


def _interpretation(image_key: str, test_key: str, rating: int = 2, **extra: object) -> dict:
    return {
        "image_key": image_key,
        "test_key": test_key,
        "observation": "曲线连续，未见明显突跳。",
        "diagnosis": "未见需要升级的异常。",
        "rating": rating,
        "action": "维持周期性复测。",
        "customer_text": "曲线连续，未见明显异常。",
        "confidence": "high",
        "experience_rule_ids": [],
        **extra,
    }


def test_combine_ratings_uses_symmetric_approved_matrix() -> None:
    expected = {
        (1, 1): 1, (1, 2): 2, (1, 3): 2, (1, 4): 3,
        (2, 2): 2, (2, 3): 3, (2, 4): 3,
        (3, 3): 3, (3, 4): 4, (4, 4): 4,
    }
    for (data_rating, visual_rating), final_rating in expected.items():
        result = combine_ratings(data_rating, visual_rating)
        reverse = combine_ratings(visual_rating, data_rating)
        assert result.final_rating == final_rating
        assert reverse.final_rating == final_rating


def test_combine_ratings_marks_large_disagreement_and_basic_action_failure() -> None:
    assert combine_ratings(1, 4).inconsistent is True
    assert combine_ratings(1, 4).final_rating == 3
    assert combine_ratings(1, 1, basic_action_failed=True).final_rating == 4
    assert combine_ratings(None, None).final_rating == 5


def test_combine_ratings_exposes_data_evidence_status_for_missing_channel_boundaries() -> None:
    visual_only = combine_ratings(None, 3)
    data_only = combine_ratings(3, None)
    no_channels = combine_ratings(None, None)

    assert visual_only.final_rating == 3
    assert isinstance(visual_only.data_evidence_status, DataEvidenceStatus)
    assert visual_only.data_evidence_status == "insufficient"
    assert visual_only.data_evidence_reason == "data_rating_missing"
    assert data_only.final_rating == 3
    assert data_only.data_evidence_status == "available"
    assert data_only.data_evidence_reason is None
    assert no_channels.final_rating == 5
    assert no_channels.data_evidence_status == "unavailable"
    assert no_channels.data_evidence_reason == "data_and_visual_rating_missing"


def test_combine_ratings_requires_a_boolean_basic_action_failure_flag() -> None:
    with pytest.raises(VisualReviewError, match="basic_action_failed"):
        combine_ratings(1, 1, basic_action_failed="true")  # type: ignore[arg-type]


def test_validate_interpretations_requires_every_manifest_image() -> None:
    entries = [_interpretation("FV-101:signature:1", "signature")]
    with pytest.raises(VisualReviewError, match="FV-101:step:1"):
        validate_interpretations(_manifest(), entries)


@pytest.mark.parametrize("field, value", [("image_key", "  "), ("test_key", "  "), ("image_key", None), ("test_key", None)])
def test_validate_interpretations_rejects_blank_or_missing_identity_fields(field: str, value: str | None) -> None:
    entry = _interpretation("FV-101:signature:1", "signature")
    if value is None:
        del entry[field]
    else:
        entry[field] = value

    with pytest.raises(VisualReviewError, match=field):
        validate_interpretations(_manifest(), [entry, _interpretation("FV-101:step:1", "step")])


def test_validate_interpretations_returns_typed_entries_for_pipeline_consumers() -> None:
    validated = validate_interpretations(
        _manifest(),
        [_interpretation("FV-101:signature:1", "signature"), _interpretation("FV-101:step:1", "step")],
    )

    assert isinstance(validated[0], CurveInterpretation)
    assert validated[0].image_key == "FV-101:signature:1"
    assert validated[0].rating == 2


def test_validate_interpretations_requires_rating_field() -> None:
    missing_rating = _interpretation("FV-101:signature:1", "signature")
    del missing_rating["rating"]

    with pytest.raises(VisualReviewError, match="rating"):
        validate_interpretations(
            _manifest(),
            [missing_rating, _interpretation("FV-101:step:1", "step")],
        )


@pytest.mark.parametrize("rating", [True, False, 0, 5, -1])
def test_validate_interpretations_rejects_non_grade_rating_values(rating: object) -> None:
    invalid_rating = _interpretation("FV-101:signature:1", "signature", rating=rating)

    with pytest.raises(VisualReviewError, match="rating"):
        validate_interpretations(
            _manifest(),
            [invalid_rating, _interpretation("FV-101:step:1", "step")],
        )


def test_validate_manifest_returns_typed_images_and_can_feed_interpretation_validation() -> None:
    manifest = validate_manifest(_manifest())

    assert isinstance(manifest, CurveReviewManifest)
    assert manifest.schema_version == 1
    assert manifest.status == "curve_review_required"
    assert manifest.images == (
        CurveReviewImage(image_key="FV-101:signature:1", test_key="signature"),
        CurveReviewImage(image_key="FV-101:step:1", test_key="step"),
    )
    assert validate_interpretations(
        manifest,
        [_interpretation("FV-101:signature:1", "signature"), _interpretation("FV-101:step:1", "step")],
    )


def test_validate_manifest_rejects_non_mapping_input() -> None:
    with pytest.raises(VisualReviewError, match="mapping"):
        validate_manifest([])  # type: ignore[arg-type]


def test_file_helper_type_validates_manifest(tmp_path: Path) -> None:
    manifest_path = tmp_path / "curve_review_manifest.json"
    manifest_path.write_text(json.dumps(_manifest(), ensure_ascii=False), encoding="utf-8")

    manifest = load_curve_review_manifest(manifest_path)

    assert isinstance(manifest, CurveReviewManifest)


def test_validate_interpretations_preserves_internal_experience_rule_ids() -> None:
    entries = [
        _interpretation("FV-101:signature:1", "signature", experience_rule_ids=["rule-a", "rule-b"]),
        _interpretation("FV-101:step:1", "step"),
    ]

    validated = validate_interpretations(_manifest(), entries)
    assert validated[0].experience_rule_ids == ("rule-a", "rule-b")


def test_validate_interpretations_accepts_results_without_experience_ids() -> None:
    missing_rule_ids = _interpretation("FV-101:signature:1", "signature")
    del missing_rule_ids["experience_rule_ids"]
    complete = _interpretation("FV-101:step:1", "step", experience_rule_ids=[])

    assert validate_interpretations(_manifest(), [missing_rule_ids, complete])[0].experience_rule_ids == ()

    assert validate_interpretations(_manifest(), [
        _interpretation("FV-101:signature:1", "signature", experience_rule_ids=[]),
        complete,
    ])[0].experience_rule_ids == ()


def test_validate_interpretations_rejects_duplicate_or_wrong_test_key() -> None:
    entries = [
        _interpretation("FV-101:signature:1", "signature"),
        _interpretation("FV-101:signature:1", "signature"),
        _interpretation("FV-101:step:1", "signature"),
    ]
    with pytest.raises(VisualReviewError, match="duplicate"):
        validate_interpretations(_manifest(), entries)


@pytest.mark.parametrize("field", ["observation", "diagnosis", "action", "customer_text", "confidence"])
def test_validate_interpretations_rejects_empty_required_text(field: str) -> None:
    entry = _interpretation("FV-101:signature:1", "signature")
    entry[field] = "  "
    entries = [entry, _interpretation("FV-101:step:1", "step")]

    with pytest.raises(VisualReviewError, match=field):
        validate_interpretations(_manifest(), entries)


@pytest.mark.parametrize("confidence", ["unknown", "", "  "])
def test_validate_interpretations_rejects_unknown_or_empty_confidence(confidence: str) -> None:
    entries = [
        _interpretation("FV-101:signature:1", "signature", confidence=confidence),
        _interpretation("FV-101:step:1", "step"),
    ]
    with pytest.raises(VisualReviewError, match="confidence"):
        validate_interpretations(_manifest(), entries)


def test_validate_interpretations_normalizes_recognized_confidence() -> None:
    entries = [
        _interpretation("FV-101:signature:1", "signature", confidence=" HIGH "),
        _interpretation("FV-101:step:1", "step"),
    ]
    assert validate_interpretations(_manifest(), entries)[0].confidence == "high"


def test_validate_interpretations_rejects_unknown_image_and_misaligned_test_key() -> None:
    entries = [
        _interpretation("FV-101:signature:1", "signature"),
        _interpretation("unexpected", "step"),
    ]
    with pytest.raises(VisualReviewError, match="unexpected"):
        validate_interpretations(_manifest(), entries)

    entries[1] = _interpretation("FV-101:step:1", "signature")
    with pytest.raises(VisualReviewError, match="test_key"):
        validate_interpretations(_manifest(), entries)


def test_low_confidence_grade_four_needs_corresponding_evidence() -> None:
    entries = [
        _interpretation("FV-101:signature:1", "signature", 4, confidence="low"),
        _interpretation("FV-101:step:1", "step"),
    ]
    with pytest.raises(VisualReviewError, match="low-confidence"):
        validate_interpretations(_manifest(), entries)

    entries[0]["evidence"] = "行程末端存在明显振荡。"
    validate_interpretations(_manifest(), entries)


@pytest.mark.parametrize("evidence", [False, 0, {}, []])
def test_low_confidence_grade_four_rejects_empty_or_false_evidence(evidence: object) -> None:
    entries = [
        _interpretation("FV-101:signature:1", "signature", 4, confidence="low", evidence=evidence),
        _interpretation("FV-101:step:1", "step"),
    ]
    with pytest.raises(VisualReviewError, match="low-confidence"):
        validate_interpretations(_manifest(), entries)


def test_human_interpretation_overrides_codex_for_same_image() -> None:
    codex = [_interpretation("FV-101:signature:1", "signature", 2)]
    human = [_interpretation("FV-101:signature:1", "signature", 3, source="human")]
    merged = merge_interpretations(codex, human)
    assert merged[0]["rating"] == 3
    assert merged[0]["source"] == "human"


def test_merge_interpretations_assigns_trusted_sources_over_spoofed_input() -> None:
    codex = [_interpretation("FV-101:signature:1", "signature", source="human")]
    human = [_interpretation("FV-101:step:1", "step", source="codex")]
    merged = merge_interpretations(codex, human)

    assert [entry["source"] for entry in merged] == ["codex", "human"]


@pytest.mark.parametrize("kind", ["Codex", "human"])
def test_merge_interpretations_rejects_duplicate_image_keys(kind: str) -> None:
    duplicate = [_interpretation("FV-101:signature:1", "signature")] * 2
    codex = duplicate if kind == "Codex" else []
    human = duplicate if kind == "human" else []
    with pytest.raises(VisualReviewError, match="duplicate"):
        merge_interpretations(codex, human)


def test_merge_interpretations_rejects_invalid_entries_before_applying_human_priority() -> None:
    codex = [_interpretation("FV-101:signature:1", "signature", confidence="unknown")]
    human = [_interpretation("FV-101:signature:1", "signature", rating=3)]
    with pytest.raises(VisualReviewError, match="confidence"):
        merge_interpretations(codex, human)


def test_merge_interpretations_uses_optional_manifest_to_reject_unmatched_images() -> None:
    with pytest.raises(VisualReviewError, match="missing interpretation"):
        merge_interpretations(
            [_interpretation("FV-101:signature:1", "signature")],
            [],
            manifest=validate_manifest(_manifest()),
        )


def test_curve_review_readiness_adapts_legacy_manual_mapping_and_human_wins(tmp_path: Path) -> None:
    manifest = _manifest()
    manifest["images"][0]["image_path"] = str(tmp_path / "report_images/FV-101/signature.png")
    manifest["images"][1]["image_path"] = str(tmp_path / "report_images/FV-101/step.png")
    manifest_path = tmp_path / "curve_review_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    codex_path = tmp_path / "codex_curve_interpretations.json"
    codex_path.write_text(json.dumps([
        _interpretation("FV-101:signature:1", "signature", 2),
        _interpretation("FV-101:step:1", "step", 2),
    ], ensure_ascii=False), encoding="utf-8")
    manual_path = tmp_path / "manual_curve_interpretations.json"
    manual_path.write_text(json.dumps({
        "FV-101/signature.png": {"severity": 3, "interpretation": "人工确认存在明显迟滞。"}
    }, ensure_ascii=False), encoding="utf-8")

    readiness = curve_review_readiness(manifest_path, codex_path=codex_path, manual_path=manual_path)

    assert readiness.status == "curve_review_complete"
    assert readiness.interpretations[0].image_key == "FV-101:signature:1"
    assert readiness.interpretations[0].rating == 3
    assert readiness.interpretations[0].customer_text == "人工确认存在明显迟滞。"


def test_curve_review_readiness_prefers_formal_diagnosis_but_keeps_human_priority(
    tmp_path: Path,
) -> None:
    manifest_path = tmp_path / "curve_review_manifest.json"
    manifest_path.write_text(json.dumps(_manifest()), encoding="utf-8")
    codex_path = tmp_path / "codex_curve_interpretations.json"
    codex_path.write_text(json.dumps([
        _interpretation("FV-101:signature:1", "signature", rating=1),
        _interpretation("FV-101:step:1", "step", rating=1),
    ]), encoding="utf-8")
    formal = [
        _interpretation("FV-101:signature:1", "signature", rating=3),
        _interpretation("FV-101:step:1", "step", rating=3),
    ]
    manual_path = tmp_path / "manual_curve_interpretations.json"
    manual_path.write_text(json.dumps({"interpretations": [
        _interpretation("FV-101:step:1", "step", rating=4),
    ]}), encoding="utf-8")

    readiness = curve_review_readiness(
        manifest_path,
        codex_path=codex_path,
        formal_interpretations=formal,
        manual_path=manual_path,
    )

    assert [item.rating for item in readiness.interpretations] == [3, 4]


def test_formal_diagnosis_ignores_codex_entries_from_another_period(tmp_path: Path) -> None:
    manifest_path = tmp_path / "curve_review_manifest.json"
    manifest_path.write_text(json.dumps(_manifest()), encoding="utf-8")
    codex_path = tmp_path / "codex_curve_interpretations.json"
    codex_path.write_text(json.dumps([
        _interpretation("FV-101:signature:1", "signature", rating=1),
        _interpretation("FV-101:step:1", "step", rating=1),
        _interpretation("OTHER-PERIOD:signature:1", "signature", rating=4),
    ]), encoding="utf-8")
    formal = [
        _interpretation("FV-101:signature:1", "signature", rating=3),
        _interpretation("FV-101:step:1", "step", rating=3),
    ]

    readiness = curve_review_readiness(
        manifest_path,
        codex_path=codex_path,
        formal_interpretations=formal,
    )

    assert [item.image_key for item in readiness.interpretations] == [
        "FV-101:signature:1",
        "FV-101:step:1",
    ]
    assert readiness.status == "curve_review_complete"
    assert len(readiness.interpretations) == 2


def test_new_manual_list_must_use_image_key_not_display_filename(tmp_path: Path) -> None:
    manifest_path = tmp_path / "curve_review_manifest.json"
    manifest_path.write_text(json.dumps(_manifest()), encoding="utf-8")
    manual_path = tmp_path / "manual_curve_interpretations.json"
    manual_path.write_text(json.dumps({"interpretations": [
        _interpretation("signature.png", "signature")
    ]}), encoding="utf-8")

    with pytest.raises(VisualReviewError, match="not in manifest"):
        curve_review_readiness(manifest_path, manual_path=manual_path)


def test_curve_review_readiness_blocks_finalize_and_reports_missing_keys(tmp_path: Path) -> None:
    manifest_path = tmp_path / "curve_review_manifest.json"
    manifest_path.write_text(json.dumps(_manifest()), encoding="utf-8")
    codex_path = tmp_path / "codex_curve_interpretations.json"
    codex_path.write_text(json.dumps([
        _interpretation("FV-101:signature:1", "signature")
    ]), encoding="utf-8")

    readiness = curve_review_readiness(manifest_path, codex_path=codex_path)

    assert readiness.status == "curve_review_required"
    assert readiness.missing_image_keys == ("FV-101:step:1",)
    assert readiness.interpretations == ()


def test_curve_review_readiness_is_ready_only_after_complete_valid_review(tmp_path: Path) -> None:
    manifest_path = tmp_path / "curve_review_manifest.json"
    manifest_path.write_text(json.dumps(_manifest()), encoding="utf-8")
    codex_path = tmp_path / "codex_curve_interpretations.json"
    codex_path.write_text(json.dumps([
        _interpretation("FV-101:signature:1", "signature", experience_rule_ids=["rule-a"]),
        _interpretation("FV-101:step:1", "step", confidence="medium"),
    ]), encoding="utf-8")

    readiness = curve_review_readiness(manifest_path, codex_path=codex_path)

    assert readiness.status == "curve_review_complete"
    assert readiness.missing_image_keys == ()
    assert len(readiness.interpretations) == 2
    assert all(isinstance(item, CurveInterpretation) for item in readiness.interpretations)
    assert readiness.interpretations[0].experience_rule_ids == ("rule-a",)
    assert readiness.interpretations[1].confidence == "medium"
