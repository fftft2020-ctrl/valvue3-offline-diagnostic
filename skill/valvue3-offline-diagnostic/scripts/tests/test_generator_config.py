import json
from pathlib import Path

import pytest

from offline_engine.config import load_project
from offline_engine import generator


def test_configure_points_generator_at_project_paths(tmp_path: Path) -> None:
    (tmp_path / "raw").mkdir()
    (tmp_path / "project.toml").write_text(
        """
[project]
customer = "Demo"
report_title = "Demo Report"
report_date = "2026-07-11"

[paths]
raw = "raw"
specs = "specs"
inbound = "inbound"
work = "work"
output = "output"
output_docx = "Demo.docx"
output_pdf = "Demo.pdf"
""".strip(),
        encoding="utf-8",
    )
    config = load_project(tmp_path)

    generator.configure(config)

    assert generator.BASE_DIR == tmp_path
    assert generator.RAW_DIR == tmp_path / "raw"
    assert generator.WORK_DIR == tmp_path / "work"


def test_configure_uses_formal_summaries_and_keeps_human_summary_priority(tmp_path: Path) -> None:
    (tmp_path / "raw").mkdir()
    (tmp_path / "experience").mkdir()
    (tmp_path / "experience/human.json").write_text(
        json.dumps({"FV-1": {"rating": 4, "main_issue": "人工最终确认"}}),
        encoding="utf-8",
    )
    (tmp_path / "project.toml").write_text(
        """
[project]
customer = "Demo"
report_title = "Demo Report"
report_date = "2026-07-11"
[paths]
raw = "raw"
work = "work"
output = "output"
output_docx = "Demo.docx"
diagnostic_summary_overrides = "experience/human.json"
""".strip(),
        encoding="utf-8",
    )
    config = load_project(tmp_path)

    generator.configure(
        config,
        formal_summary_overrides={
            "FV-1": {"rating": 3, "main_issue": "正式单阀诊断"},
            "FV-2": {"rating": 2, "main_issue": "正式单阀诊断"},
        },
    )

    assert generator.DIAGNOSTIC_SUMMARY_OVERRIDES["FV-1"] == {
        "rating": 4,
        "main_issue": "人工最终确认",
    }
    assert generator.DIAGNOSTIC_SUMMARY_OVERRIDES["FV-2"] == {
        "rating": 2,
        "main_issue": "正式单阀诊断",
    }


def test_load_valve_context_accepts_customer_safe_fields_and_fills_missing_values(tmp_path: Path) -> None:
    context_path = tmp_path / "valve_context.json"
    context_path.write_text(
        json.dumps(
            {
                "FV-101": {
                    "description": "反应进料流量调节阀",
                    "unit": "加氢装置",
                    "failure_consequence": "装置停车",
                    "bypass": "无副线",
                    "note": "现场测试完成",
                },
                "FV-102": {
                    "unit": "空分装置",
                    "failure_consequence": "待业主补充",
                    "bypass": "待业主补充",
                    "note": "OK",
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    loaded = generator.load_valve_context(context_path)

    assert loaded["FV-101"]["description"] == "反应进料流量调节阀"
    assert loaded["FV-101"]["failure_consequence"] == "装置停车"
    assert loaded["FV-102"] == {
        "unit": "空分装置",
        "failure_consequence": "待业主补充",
        "bypass": "待业主补充",
        "note": "OK",
    }


def test_apply_valve_context_uses_project_values_without_overwriting_valve_identity(monkeypatch) -> None:
    valve = generator.ValveReport(folder="FV-101")
    monkeypatch.setattr(
        generator,
        "VALVE_CONTEXT_BY_TAG",
        {
            "FV-101": {
                "description": "反应进料流量调节阀",
                "unit": "加氢装置",
                "failure_consequence": "装置停车",
                "bypass": "无副线",
                "note": "现场测试完成",
            }
        },
    )

    generator.apply_valve_context(valve)

    assert valve.folder == "FV-101"
    assert valve.description == "反应进料流量调节阀"
    assert valve.unit == "加氢装置"
    assert valve.failure_consequence == "装置停车"
    assert valve.bypass == "无副线"
    assert valve.site_note == "现场测试完成"


def test_specification_identity_fallback_fills_missing_serial_without_overwriting_raw() -> None:
    missing = generator.ValveReport(
        folder="FV-101",
        spec_fields={"serial_number": "TE12A0569-11"},
    )
    generator.apply_specification_identity_fallback(missing)
    assert missing.serial_number == "TE12A0569-11"

    raw = generator.ValveReport(
        folder="FV-102",
        serial_number="RAW-SERIAL",
        spec_fields={"serial_number": "SPEC-SERIAL"},
    )
    generator.apply_specification_identity_fallback(raw)
    assert raw.serial_number == "RAW-SERIAL"


def test_shared_engine_contains_no_customer_specific_defaults() -> None:
    source = Path(generator.__file__).read_text(encoding="utf-8")

    for forbidden in (
        "离线诊断/projects/",
        "/home/openre/",
        "/apps/bdpan",
    ):
        assert forbidden not in source


def test_select_report_valves_can_hide_data_insufficient_items() -> None:
    valves = [
        generator.ValveReport(folder="FV-101", final_rating=3),
        generator.ValveReport(folder="FV-102", final_rating=5),
    ]

    selected = generator.select_report_valves(
        valves, include_data_insufficient=False, only_valves=(), append_valves=()
    )
    assert [valve.folder for valve in selected] == ["FV-101"]


def test_select_report_valves_keeps_data_insufficient_items_by_default() -> None:
    valves = [
        generator.ValveReport(folder="FV-101", final_rating=3),
        generator.ValveReport(folder="FV-102", final_rating=5),
    ]

    assert generator.select_report_valves(
        valves, include_data_insufficient=True, only_valves=(), append_valves=()
    ) == valves


def test_select_report_valves_appends_configured_batch_without_reordering_existing() -> None:
    valves = [
        generator.ValveReport(folder="FV-NEW-2", final_rating=3),
        generator.ValveReport(folder="FV-OLD-1", final_rating=3),
        generator.ValveReport(folder="FV-NEW-1", final_rating=3),
        generator.ValveReport(folder="FV-OLD-2", final_rating=4),
    ]
    selected = generator.select_report_valves(
        valves,
        include_data_insufficient=False,
        only_valves=(),
        append_valves=("FV-NEW-1", "FV-NEW-2"),
    )

    assert [valve.folder for valve in selected] == [
        "FV-OLD-1",
        "FV-OLD-2",
        "FV-NEW-1",
        "FV-NEW-2",
    ]


def test_select_report_valves_can_limit_report_to_configured_batch() -> None:
    valves = [
        generator.ValveReport(folder="FV-OLD-1", final_rating=3),
        generator.ValveReport(folder="FV-NEW-1", final_rating=3),
        generator.ValveReport(folder="FV-NEW-2", final_rating=4),
    ]
    selected = generator.select_report_valves(
        valves,
        include_data_insufficient=False,
        only_valves=("FV-NEW-2", "FV-NEW-1"),
        append_valves=(),
    )

    assert [valve.folder for valve in selected] == ["FV-NEW-2", "FV-NEW-1"]


def test_collect_valves_prepares_all_before_report_selection(
    tmp_path: Path,
    monkeypatch,
) -> None:
    raw_dir = tmp_path / "raw"
    (raw_dir / "FV-KEEP").mkdir(parents=True)
    (raw_dir / "FV-DROP").mkdir()
    rendered: list[str] = []

    monkeypatch.setattr(generator, "RAW_DIR", raw_dir)
    monkeypatch.setattr(generator, "CURVE_INTERPRETATIONS_BY_IMAGE_KEY", {})
    monkeypatch.setattr(generator, "SPEC_PAGES_BY_VALVE", {})
    monkeypatch.setattr(generator, "ACCESSORY_PAGES_BY_VALVE", {})
    monkeypatch.setattr(
        generator,
        "render_images_for_valve",
        lambda folder, **_kwargs: rendered.append(folder.name) or [],
    )
    valves = generator.collect_valves()

    assert [valve.folder for valve in valves] == ["FV-DROP", "FV-KEEP"]
    assert rendered == ["FV-DROP", "FV-KEEP"]


def test_source_file_exclusion_uses_exact_project_relative_path(tmp_path: Path, monkeypatch) -> None:
    excluded = tmp_path / "raw/FV-101/later-report.pdf"
    included = tmp_path / "raw/FV-101/original-report.pdf"
    monkeypatch.setattr(generator, "BASE_DIR", tmp_path)
    monkeypatch.setattr(
        generator,
        "EXCLUDED_SOURCE_FILES",
        frozenset({"raw/FV-101/later-report.pdf"}),
    )

    assert generator.source_file_is_excluded(excluded)
    assert not generator.source_file_is_excluded(included)


def test_excluded_source_images_are_removed_from_rendered_curve_list(tmp_path: Path, monkeypatch) -> None:
    valve_dir = tmp_path / "raw/FV-101"
    monkeypatch.setattr(generator, "BASE_DIR", tmp_path)
    monkeypatch.setattr(
        generator,
        "EXCLUDED_SOURCE_FILES",
        frozenset({"raw/FV-101/later-report.pdf"}),
    )
    images = [
        {"source": "original-report.pdf", "test_key": "run_step"},
        {"source": "later-report.pdf", "test_key": "run_step"},
    ]

    filtered = generator.filter_excluded_source_images(valve_dir, images)

    assert filtered == [{"source": "original-report.pdf", "test_key": "run_step"}]


def test_project_curve_review_override_can_exclude_and_reclassify_images(monkeypatch) -> None:
    monkeypatch.setattr(
        generator,
        "CURVE_IMAGE_EXCLUSIONS",
        frozenset({"FV-101/report_other_p01_01.png"}),
    )
    monkeypatch.setattr(
        generator,
        "CURVE_TEST_KEY_OVERRIDES",
        {"FV-101/report_standard_signature_p07_08.png": "extended_signature"},
    )

    excluded = generator.apply_curve_review_override(
        "FV-101",
        {
            "path": "/tmp/report_other_p01_01.png",
            "test_key": "other",
            "label": "诊断图形",
        },
    )
    reclassified = generator.apply_curve_review_override(
        "FV-101",
        {
            "path": "/tmp/report_standard_signature_p07_08.png",
            "test_key": "standard_signature",
            "label": "标准性能曲线",
        },
    )

    assert excluded is None
    assert reclassified is not None
    assert reclassified["test_key"] == "extended_signature"
    assert reclassified["label"] == generator.TEST_LABELS["extended_signature"]


def test_summary_retains_hidden_valves_and_marks_report_membership(tmp_path: Path, monkeypatch) -> None:
    summary = tmp_path / "analysis_summary.json"
    monkeypatch.setattr(generator, "WORK_DIR", tmp_path)
    monkeypatch.setattr(generator, "SUMMARY_JSON", summary)
    valves = [
        generator.ValveReport(folder="FV-101", final_rating=3),
        generator.ValveReport(folder="FV-102", final_rating=5),
    ]

    generator.write_summary(valves, included_folders={"FV-101"})

    data = json.loads(summary.read_text(encoding="utf-8"))
    assert [item["folder"] for item in data] == ["FV-101", "FV-102"]
    assert [item["included_in_report"] for item in data] == [True, False]


def test_summary_retains_parsed_source_report_types(tmp_path: Path, monkeypatch) -> None:
    summary = tmp_path / "analysis_summary.json"
    monkeypatch.setattr(generator, "WORK_DIR", tmp_path)
    monkeypatch.setattr(generator, "SUMMARY_JSON", summary)
    valve = generator.ValveReport(folder="FV-101")
    generator.record_source_report(
        valve,
        "valvxpert",
        info={
            "dtm_source_status": "present",
            "dtm_parser_status": "parsed",
        },
    )

    generator.write_summary([valve])

    data = json.loads(summary.read_text(encoding="utf-8"))[0]
    assert data["source_report_types"] == {"valvxpert": 1}
    assert data["dtm_data_report_count"] == 1


def test_human_summary_fields_merge_over_frozen_fields_without_stale_aliases() -> None:
    formal = {
        "FV-101": {
            "rating": 2,
            "rating_conclusion": "冻结评级结论。",
            "main_issue": "冻结问题",
            "issues": ["冻结问题。"],
            "summary_issues": ["冻结问题。"],
            "action": "冻结建议。",
            "actions": ["冻结建议。"],
            "summary_actions": ["冻结建议。"],
            "overall_assessment": "冻结综合诊断。",
        }
    }
    human = {
        "FV-101": {
            "rating_conclusion": "人工评级结论。",
            "issues": [],
            "actions": ["人工建议一。", "人工建议二。"],
        }
    }

    merged = generator.merge_diagnostic_summary_overrides(formal, human)["FV-101"]

    assert merged["rating"] == 2
    assert merged["issues"] == merged["summary_issues"] == []
    assert merged["actions"] == merged["summary_actions"] == [
        "人工建议一。",
        "人工建议二。",
    ]
    assert merged["action"] == "人工建议一；人工建议二。"
    assert merged["overall_assessment"] == "人工评级结论。 主要问题：冻结问题"

    action_only = generator.merge_diagnostic_summary_overrides(
        formal,
        {"FV-101": {"action": "人工单项建议。"}},
    )["FV-101"]
    assert action_only["actions"] == action_only["summary_actions"] == [
        "人工单项建议。"
    ]

    summary_only = generator.merge_diagnostic_summary_overrides(
        formal,
        {"FV-101": {"summary_actions": ["人工摘要建议。"]}},
    )["FV-101"]
    assert summary_only["actions"] == ["人工摘要建议。"]
    assert summary_only["action"] == "人工摘要建议。"


def test_human_summary_allows_no_issues_but_rejects_no_actions(tmp_path: Path) -> None:
    path = tmp_path / "overrides.json"
    path.write_text(
        json.dumps({"FV-101": {"issues": [], "actions": ["维持周期复测。"]}}),
        encoding="utf-8",
    )

    assert generator.load_diagnostic_summary_overrides(path)["FV-101"]["issues"] == []

    path.write_text(
        json.dumps({"FV-101": {"issues": [], "actions": []}}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="actions must be a non-empty list"):
        generator.load_diagnostic_summary_overrides(path)


def test_summary_without_diagnosis_keeps_facts_but_not_default_rating(
    tmp_path: Path, monkeypatch
) -> None:
    summary = tmp_path / "analysis_summary.json"
    monkeypatch.setattr(generator, "WORK_DIR", tmp_path)
    monkeypatch.setattr(generator, "SUMMARY_JSON", summary)
    valve = generator.ValveReport(folder="FV-101")

    generator.write_summary([valve], include_diagnosis=False)

    record = json.loads(summary.read_text(encoding="utf-8"))[0]
    assert record["has_test_data"] is False
    assert record["rating"] is None
    assert record["final_rating"] is None
    assert record["algorithm_rating"] is None
    assert record["rating_evidence"] == {}
    assert record["main_issue"] == ""


def test_summary_writes_diagnosis_only_for_validated_frozen_scope(
    tmp_path: Path, monkeypatch
) -> None:
    summary = tmp_path / "analysis_summary.json"
    monkeypatch.setattr(generator, "WORK_DIR", tmp_path)
    monkeypatch.setattr(generator, "SUMMARY_JSON", summary)
    validated = generator.ValveReport(
        folder="FV-A",
        rating=2,
        final_rating=2,
        main_issue="冻结问题",
        action="冻结建议。",
        actions=["冻结建议。"],
        overall_assessment="冻结综合诊断。",
        algorithm_audit_status="available",
        algorithm_audit_snapshot={"algorithm_rating": 3},
    )
    outside = generator.ValveReport(folder="FV-B")

    generator.write_summary(
        [validated, outside],
        diagnosis_folders={"FV-A"},
        include_has_test_data_folders={"FV-A"},
    )

    records = {
        item["folder"]: item
        for item in json.loads(summary.read_text(encoding="utf-8"))
    }
    assert records["FV-A"]["rating"] == 2
    assert records["FV-A"]["algorithm_audit_status"] == "available"
    assert records["FV-A"]["algorithm_audit_snapshot"] == {"algorithm_rating": 3}
    assert records["FV-A"]["has_test_data"] is False
    assert records["FV-B"]["rating"] is None
    assert records["FV-B"]["final_rating"] is None
    assert records["FV-B"]["algorithm_audit_status"] == ""
    assert records["FV-B"]["algorithm_audit_snapshot"] is None
    assert records["FV-B"]["main_issue"] == ""
    assert "has_test_data" not in records["FV-B"]


def test_data_only_preparation_ignores_diagnosis_override_files(
    tmp_path: Path, monkeypatch
) -> None:
    (tmp_path / "raw").mkdir()
    (tmp_path / "work").mkdir()
    (tmp_path / "work/broken-summary.json").write_text("not json", encoding="utf-8")
    (tmp_path / "work/broken-curves.json").write_text("not json", encoding="utf-8")
    (tmp_path / "project.toml").write_text(
        """
[project]
customer = "Demo"
report_title = "Data"
report_date = "2026-09-05"
[paths]
raw = "raw"
work = "work"
output_docx = "data.docx"
diagnostic_summary_overrides = "work/broken-summary.json"
manual_interpretations = "work/broken-curves.json"
[presentation]
data_only = true
""".strip(),
        encoding="utf-8",
    )
    config = load_project(tmp_path)
    monkeypatch.setattr(generator, "collect_valves", lambda **_kwargs: [])

    generator.configure(config)
    valves = generator.prepare_valve_data()

    assert valves == []
    assert generator.DIAGNOSTIC_SUMMARY_OVERRIDES == {}
def test_pdf_text_reader_prefers_project_ocr_cache(monkeypatch, tmp_path: Path) -> None:
    from offline_engine import document_ocr_cache

    pdf = tmp_path / "raw/FV-101/report.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"placeholder")
    monkeypatch.setattr(generator, "BASE_DIR", tmp_path)
    monkeypatch.setattr(document_ocr_cache, "load_cached_pdf_text", lambda project, path: "cached text")
    monkeypatch.setattr(
        generator.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("pdftotext must not run")),
    )

    assert generator.run_pdftotext(pdf) == "cached text"
