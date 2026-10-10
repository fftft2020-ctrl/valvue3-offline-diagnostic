import json
from dataclasses import replace
from pathlib import Path

import fitz
import pytest
from PIL import Image, ImageDraw

from offline_engine import business_report, curve_identity, generator, presentation
from offline_engine.visual_review import CurveInterpretation


def test_algorithm_audit_uses_explicit_test_availability_without_report_text() -> None:
    interpretation = CurveInterpretation(
        image_key="FV-101/run-ramp.png",
        test_key="run_ramp",
        observation="曲线连续。",
        diagnosis="存在轻微回差。",
        rating=2,
        action="窗口期复测。",
        customer_text="曲线连续，存在轻微回差。",
        confidence="high",
        experience_rule_ids=(),
    )
    source = {
        "folder": "FV-101",
        "has_test_data": True,
        "metrics": {"Average Error": 5.0},
        "active_faults": [],
        "basic_action_failed": False,
    }

    audit = generator.calculate_algorithm_audit(source, (interpretation,))

    assert audit["status"] == "available"
    assert audit["data_rating"] == 3
    assert audit["visual_rating"] == 2
    assert audit["algorithm_rating"] == 3
    assert audit["rating_evidence"]["data"]["defect"]
    assert len(audit["input_digest"]) == 64
    assert not ({"main_issue", "action", "overall_assessment"} & audit.keys())
    rewritten = replace(
        interpretation,
        observation="人工修订观察。",
        diagnosis="人工修订判断。",
        action="人工修订建议。",
        customer_text="人工修订客户文本。",
    )
    assert generator.algorithm_audit_input_digest(source, (rewritten,)) == audit[
        "input_digest"
    ]


@pytest.mark.parametrize("report_type", ["sequence", "active"])
@pytest.mark.parametrize("fault_text, expected", [
    ("Active Faults Reset; NONE; No Error; Historical Faults Raw Data", []),
    ("Active Faults Low Power; Position Error; Low Power; Historical Faults Raw Data",
     ["Low Power", "Position Error", "Low Power"]),
    ("Raw Data", []),
])
def test_fault_sections_preserve_real_faults_and_ignore_empty_markers(
    monkeypatch, tmp_path, report_type, fault_text, expected,
) -> None:
    text = "ValvXpert Execution Report " + fault_text
    monkeypatch.setattr(generator, "run_pdftotext", lambda _path: text)
    monkeypatch.setattr(generator, "run_pdftotext_pages", lambda _path: [text])
    path = tmp_path / "report.pdf"
    if report_type == "sequence":
        _, info = generator.parse_sequence_pdf(path)
    else:
        info = generator.parse_active_report_pdf(path)
        if fault_text == "Raw Data":
            assert info["fault_data_status"] == "source_missing"
            assert "active_faults" not in info
            return
    assert info["active_faults"] == expected


def _structured_field(
    raw_value,
    normalized_value,
    *,
    status: str = "manual_override",
) -> dict:
    return {
        "status": status,
        "selected": {
            "raw_value": raw_value,
            "normalized_value": normalized_value,
        },
        "candidates": [],
        "conflict": status == "conflict",
    }


def _interpretation(*, image_key: str, rating: int, confidence: str = "high") -> CurveInterpretation:
    return CurveInterpretation(
        image_key=image_key,
        test_key="run_ramp",
        observation="人工已复核曲线。",
        diagnosis="图形诊断结论。",
        rating=rating,
        action="安排复核。",
        customer_text="已完成图形判读。",
        confidence=confidence,
        experience_rule_ids=(),
    )


def _write_curve_candidate_image(
    path: Path,
    *,
    result_table: bool,
    colored_table: bool = True,
) -> None:
    image = Image.new("RGB", (800, 500), "white")
    drawing = ImageDraw.Draw(image)
    if result_table:
        for row in range(10):
            if colored_table:
                color = "#c93a3a" if row % 2 else "#2f6fb3"
            else:
                color = "#777777"
            y = 25 + row * 42
            drawing.rectangle((40, y, 760, y + 10), fill=color)
            drawing.line((40, y + 18, 760, y + 18), fill="#777777", width=2)
    else:
        drawing.line((70, 430, 750, 430), fill="black", width=3)
        drawing.line((70, 40, 70, 430), fill="black", width=3)
        drawing.line((70, 360, 230, 300, 390, 240, 550, 170, 740, 110), fill="#c93a3a", width=5)
        drawing.line((70, 370, 230, 315, 390, 250, 550, 185, 740, 125), fill="#2f6fb3", width=5)
    image.save(path)


def test_data_rating_does_not_read_curve_text_or_images() -> None:
    valve = generator.ValveReport(
        folder="FV-ISOLATED",
        tests=[generator.TestRecord(source="test", source_type="devdata", test_type="STEP")],
        metrics={
            "Maximum Hysteresis + Dead Band": 1.0,
            "Average Hysteresis + Dead Band": None,
            "Average Error": 0.2,
            "T86": None,
            "T63": None,
            "Td": None,
            "Overshoot": None,
            "Max Friction": None,
        },
        images=[{"observation": "严重卡涩，评级4级", "diagnosis": "曲线文本不能进入数据评级"}],
    )
    result = generator.calculate_data_rating(valve)

    assert result.rating == 1


def test_report_projection_uses_approved_parameter_values_and_skips_conflicts() -> None:
    valve = generator.ValveReport(
        folder="VALVE-001",
        positioner_model="raw-model",
        spec_fields={"leakage_class": "raw-leakage"},
        metrics={"Average Error": 9.9},
        structured_evidence={
            "material_extraction": {
                "fields": {
                    "positioner.model": _structured_field(
                        "SVI II AP", "SVI II AP"
                    ),
                    "specification.leakage_class": _structured_field(
                        "0.001%/Max.Diff.Pr.", "V"
                    ),
                    "actuator.model_full": _structured_field(
                        "51-21129", "51-21129"
                    ),
                    "actuator.model_display": _structured_field(
                        "51-21129", "51"
                    ),
                    "runtime.time_open": _structured_field("10", 36000.0),
                    "status.task_execution": _structured_field(
                        [{"task_name": "Run Ramp Test", "result": "Succeeded"}],
                        [{"task_name": "Run Ramp Test", "result": "Succeeded"}],
                    ),
                    "metrics.average_error": _structured_field(0.5, 0.5),
                    "context.description": _structured_field(
                        "conflicting description",
                        "conflicting description",
                        status="conflict",
                    ),
                }
            }
        },
    )

    generator.apply_structured_parameter_projection(valve)

    assert valve.positioner_model == "SVI II AP"
    assert valve.spec_fields["leakage_class"] == "V"
    assert valve.spec_fields["actuator_model_full"] == "51-21129"
    assert valve.spec_fields["actuator_model"] == "51"
    assert valve.positioner_report_info["time_open"] == "10"
    assert valve.positioner_report_info["task_execution"][0]["result"] == "Succeeded"
    assert valve.metrics["Average Error"] == 0.5
    assert valve.description == "未提供"


def test_report_projection_does_not_label_device_family_as_order_model() -> None:
    valve = generator.ValveReport(
        folder="VALVE-002",
        positioner_model="SVI2AP (0xCA / 02)",
        positioner_type="SVI II AP",
        structured_evidence={
            "material_extraction": {
                "fields": {
                    "positioner.model": {
                        "status": "missing",
                        "selected": None,
                        "candidates": [],
                        "conflict": False,
                    },
                    "positioner.type": _structured_field(
                        "SVI II AP", "SVI II AP"
                    ),
                }
            }
        },
    )

    generator.apply_structured_parameter_projection(valve)

    assert valve.positioner_model == "未提供"
    assert valve.positioner_type == "SVI II AP"
    assert business_report.positioner_information_row(
        valve.positioner_model, valve.positioner_type
    ) == ("定位器类型", "SVI II AP")


def test_visual_rating_uses_only_verified_interpretations_not_metric_thresholds() -> None:
    interpretations = (_interpretation(image_key="FV-VISUAL:run_ramp:1", rating=3),)

    result = generator.calculate_visual_rating(interpretations)

    assert result.rating == 3
    assert result.evidence == [{"image_key": "FV-VISUAL:run_ramp:1", "test_key": "run_ramp", "rating": 3, "confidence": "high"}]


def test_low_confidence_single_visual_grade_four_requires_another_image() -> None:
    interpretation = _interpretation(image_key="FV-LOW:run_ramp:1", rating=4, confidence="low")
    corroborating_image = _interpretation(image_key="FV-LOW:standard_signature:2", rating=4)

    unsupported = generator.calculate_visual_rating((interpretation,))
    image_supported = generator.calculate_visual_rating((interpretation, corroborating_image))

    assert unsupported.rating == 3
    assert image_supported.rating == 4


def test_algorithm_audit_records_matrix_result_and_evidence() -> None:
    audit = generator.calculate_algorithm_audit(
        {
            "folder": "FV-SUMMARY",
            "has_test_data": True,
            "metrics": {"Maximum Hysteresis + Dead Band": 1.0},
            "active_faults": [],
            "basic_action_failed": False,
        },
        (_interpretation(image_key="FV-SUMMARY:run_ramp:1", rating=4),),
    )

    assert (audit["data_rating"], audit["visual_rating"], audit["algorithm_rating"]) == (1, 4, 3)
    assert audit["rating_inconsistency_reason"] == "data_visual_rating_difference_at_least_two_levels"
    assert audit["rating_evidence"]["visual"]["images"][0]["image_key"] == "FV-SUMMARY:run_ramp:1"


def test_explicit_basic_action_failure_is_direct_grade_four_and_no_evidence_is_five() -> None:
    base = {"has_test_data": False, "metrics": {}, "active_faults": []}
    failed = generator.calculate_algorithm_audit(
        {**base, "folder": "FV-FAILED", "basic_action_failed": True}, ()
    )
    unknown = generator.calculate_algorithm_audit(
        {**base, "folder": "FV-UNKNOWN", "basic_action_failed": False}, ()
    )

    assert failed["algorithm_rating"] == 4
    assert unknown["algorithm_rating"] == 5


def test_parse_test_execution_issues_keeps_failed_occurrence_and_ignores_success() -> None:
    text = """
Task Execution Summary
Run Step Test 7/20/2026 1:00:00 PM 7/20/2026 1:05:00 PM 300 Succeeded
Run Step Test 7/20/2026 1:05:00 PM 7/20/2026 1:10:00 PM 300 Failed
Run Ramp Test 7/20/2026 1:10:00 PM 7/20/2026 1:15:00 PM 300 Aborted
Extended Act. Signature 7/20/2026 1:15:00 PM 7/20/2026 1:20:00 PM 300 Succeeded
"""

    assert generator.parse_test_execution_issues(text) == [
        {
            "test_key": "run_step",
            "test_name": "Run Step Test",
            "occurrence": 2,
            "status": "Failed",
        },
        {
            "test_key": "run_ramp",
            "test_name": "Run Ramp Test",
            "occurrence": 1,
            "status": "Aborted",
        },
    ]


def test_parse_test_execution_issues_resolves_page_local_generic_aliases_once() -> None:
    text = "\f".join(
        [
            """
Extended Act. Signature
Statusi Failed
Run Signature Test Status Failed
""",
            """
Standard Signature
Status: Aborted
Run Signature Test Status Aborted
""",
            """
Run Step Test
Step Type Pattern Steps
Run Step Test Status Failed
Pattern Step Test Status Failed
""",
        ]
    )

    assert generator.parse_test_execution_issues(text) == [
        {
            "test_key": "extended_signature",
            "test_name": "Extended Actuator Signature",
            "occurrence": 1,
            "status": "Failed",
        },
        {
            "test_key": "standard_signature",
            "test_name": "Standard Signature",
            "occurrence": 1,
            "status": "Aborted",
        },
        {
            "test_key": "pattern_step",
            "test_name": "Pattern Step Test",
            "occurrence": 1,
            "status": "Failed",
        },
    ]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "Extended Act. Signature Statusi Succeeded "
            "Run Signature Test Status Succeeded",
            ("extended_signature",),
        ),
        (
            "Extended Actuator Signature Status: Succeeded",
            ("extended_signature",),
        ),
        (
            "Extended Act. Status: Signature Result",
            ("extended_signature",),
        ),
        (
            "Standard Signature Status Succeeded "
            "Run Signature Test Status Succeeded",
            ("standard_signature",),
        ),
        (
            "Run Signature Test Status Succeeded",
            ("standard_signature",),
        ),
        (
            "Standard Signature Status Succeeded "
            "Extended Act. Signature Status Succeeded",
            ("standard_signature", "extended_signature"),
        ),
        (
            "Run Step Test Step Type Pattern Steps "
            "Pattern Step Test Status Succeeded",
            ("pattern_step",),
        ),
        (
            "Run Step Test Step Type Multiple Steps "
            "Run Step Test Step Type Pattern Steps",
            ("run_step", "pattern_step"),
        ),
        ("Run Step Test Step Type Pattem Steps", ("pattern_step",)),
        ("Run Step Test Step Type Pattern Stopo", ("pattern_step",)),
        ("Run Step Test Step Type Pattern Stepa", ("pattern_step",)),
        (
            "Run Step Test Step Type Multiple Steps "
            "Run Ramp Test Status Succeeded",
            ("run_step", "run_ramp"),
        ),
    ],
)
def test_page_curve_test_keys_use_specific_semantics_and_remain_mutually_exclusive(
    text: str,
    expected: tuple[str, ...],
) -> None:
    assert generator.page_curve_test_keys(text) == expected


def test_signature_friction_does_not_duplicate_specific_title_with_generic_caption(
    monkeypatch,
    tmp_path,
) -> None:
    text = """
    Extended Act. Signature
    Statusi
    Succeeded
    Result: Parameters High Low Mean
    Friction 113.4 kPa 88.5 kPa 102.6 kPa
    Friction (%) 48.90 % 38.17 % 44.26 %
    Run Signature Test Status Succeeded
    """
    monkeypatch.setattr(generator, "run_pdftotext", lambda _path: text)

    records, _ = generator.parse_sequence_pdf(tmp_path / "specific-and-generic.pdf")
    signatures = [
        record for record in records if record.test_type in {"SIG-S", "SIG-EXT"}
    ]

    assert [(record.test_type, record.occurrence) for record in signatures] == [
        ("SIG-EXT", 1)
    ]
    assert signatures[0].attrs["maxFric"] == 48.9
    assert signatures[0].attrs["minFric"] == 38.17


def test_curve_inventory_counts_physical_step_sections_and_dedupes_continuation_caption() -> None:
    text = "\f".join(
        [
            """
Run Step Test
Task Parameters:
Step Type Multiple Steps
Run Step Test
Task Parameters:
Step Type Single Step
Run Step Test
Task Parameters:
Step Type Pattern Steps
""",
            """
Position (%)
Time (sec)
Pattern Step Test Status Succeeded
""",
        ]
    )

    records = generator.parse_curve_inventory(text, "combined.pdf")

    assert [
        (record["test_key"], record["page"], record["occurrence"])
        for record in records
    ] == [
        ("run_step", 1, 1),
        ("run_step", 1, 2),
        ("pattern_step", 1, 1),
    ]


def test_curve_inventory_keeps_adjacent_new_step_with_task_parameters() -> None:
    text = "\f".join(
        [
            "Run Step Test Status Succeeded Task Parameters: Step Type Multiple Steps",
            "Run Step Test Status Succeeded Task Parameters: Direction Both Ways",
        ]
    )

    records = generator.parse_curve_inventory(text, "adjacent-tests.pdf")

    assert [
        (record["test_key"], record["page"], record["occurrence"])
        for record in records
    ] == [
        ("run_step", 1, 1),
        ("run_step", 2, 2),
    ]


def test_sort_test_images_uses_fixed_type_source_and_numeric_page_order() -> None:
    images = [
        {"test_key": "extended_signature", "source": "b.pdf", "page": "3", "bbox": "0,0,1,1"},
        {"test_key": "run_step", "source": "a.pdf", "page": "10", "bbox": "0,0,1,1"},
        {"test_key": "run_ramp", "source": "a.pdf", "page": "2", "bbox": "0,0,1,1"},
        {"test_key": "run_step", "source": "a.pdf", "page": "9", "bbox": "0,0,1,1"},
    ]

    ordered = curve_identity.sort_test_images(images)

    assert [(item["test_key"], item["page"]) for item in ordered] == [
        ("run_step", "9"),
        ("run_step", "10"),
        ("run_ramp", "2"),
        ("extended_signature", "3"),
    ]


def test_curve_execution_times_follow_physical_curve_order_and_pattern_alias() -> None:
    images = [
        {"test_key": "run_step"},
        {"test_key": "pattern_step"},
        {"test_key": "extended_signature"},
        {"test_key": "run_ramp"},
        {"test_key": "run_step"},
    ]
    tasks = [
        {"test_key": "run_step", "start_time": "8/21/2026 5:56:45 PM", "end_time": "8/21/2026 5:58:37 PM", "result": "Succeeded"},
        {"test_key": "run_step", "start_time": "8/21/2026 5:58:37 PM", "end_time": "8/21/2026 6:07:24 PM", "result": "Succeeded"},
        {"test_key": "extended_signature", "start_time": "8/21/2026 6:07:25 PM", "end_time": "8/21/2026 6:18:17 PM", "result": "Succeeded"},
        {"test_key": "run_ramp", "start_time": "8/21/2026 6:18:18 PM", "end_time": "8/21/2026 6:23:15 PM", "result": "Succeeded"},
        {"test_key": "run_step", "start_time": "8/21/2026 6:23:15 PM", "end_time": "8/21/2026 6:27:05 PM", "result": "Succeeded"},
    ]

    generator.attach_curve_execution_times(images, tasks)

    assert [image["test_start_time"] for image in images] == [
        task["start_time"] for task in tasks
    ]
    assert presentation.curve_execution_time_label(images[0]) == (
        "2026-08-21 17:56:45–17:58:37"
    )


def test_validated_diagnostic_summary_updates_customer_fields_without_algorithm_text(monkeypatch) -> None:
    valve = generator.ValveReport(
        folder="FV-HUMAN",
        metrics={"test_types": ["run_step"], "step_count": 1},
    )
    monkeypatch.setattr(generator, "DIAGNOSTIC_SUMMARY_OVERRIDES", {
        "FV-HUMAN": {
            "rating": 2,
            "main_issue": "关阀端存在校准偏移。",
            "action": "复核零点与行程校准。",
            "issues": ["人工曲线判读确认关阀端偏移。"],
            "actions": ["停车窗口复核定位器零点。"],
            "overall_assessment": "综合诊断：阀门存在轻微校准偏移，当前可继续运行并计划复核。",
            "rating_conclusion": "人工确认：阀门可完成基本动作，存在轻微校准偏移。",
            "health_rating_detail_mode": "summary_only",
        }
    })

    generator.apply_report_diagnosis_summary(valve)

    assert valve.algorithm_rating is None
    assert valve.rating == valve.final_rating == 2
    assert valve.main_issue == "关阀端存在校准偏移。"
    assert valve.action == "复核零点与行程校准。"
    assert valve.issues == ["人工曲线判读确认关阀端偏移。"]
    assert valve.actions == ["停车窗口复核定位器零点。"]
    assert valve.overall_assessment.startswith("综合诊断：")
    assert valve.rating_conclusion_override == "人工确认：阀门可完成基本动作，存在轻微校准偏移。"
    assert valve.health_rating_detail_mode == "summary_only"
    assert valve.basis == []


def test_load_diagnostic_summary_overrides_rejects_invalid_rating(tmp_path: Path) -> None:
    path = tmp_path / "summary.json"
    path.write_text(json.dumps({"FV-BAD": {"rating": 6}}, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="FV-BAD.*rating"):
        generator.load_diagnostic_summary_overrides(path)


def test_load_diagnostic_summary_overrides_accepts_human_rating_summary_fields(tmp_path: Path) -> None:
    path = tmp_path / "summary.json"
    path.write_text(json.dumps({
        "FV-HUMAN": {
            "rating": 2,
            "rating_conclusion": "阀门可完成基本动作，但存在响应迟滞。",
            "health_rating_detail_mode": "summary_only",
        }
    }, ensure_ascii=False), encoding="utf-8")

    loaded = generator.load_diagnostic_summary_overrides(path)

    assert loaded["FV-HUMAN"]["rating_conclusion"] == "阀门可完成基本动作，但存在响应迟滞。"
    assert loaded["FV-HUMAN"]["health_rating_detail_mode"] == "summary_only"


def test_load_diagnostic_summary_overrides_accepts_up_to_three_summary_bullets(
    tmp_path: Path,
) -> None:
    path = tmp_path / "summary.json"
    path.write_text(json.dumps({
        "FV-SUMMARY": {
            "summary_issues": ["保留阶跃存在迟滞。", "主因指向填料摩擦。"],
            "summary_actions": ["复核填料压紧。", "完成小阶跃复测。"],
        }
    }, ensure_ascii=False), encoding="utf-8")

    loaded = generator.load_diagnostic_summary_overrides(path)

    assert loaded["FV-SUMMARY"]["summary_issues"] == [
        "保留阶跃存在迟滞。",
        "主因指向填料摩擦。",
    ]
    assert loaded["FV-SUMMARY"]["summary_actions"] == [
        "复核填料压紧。",
        "完成小阶跃复测。",
    ]


def test_load_diagnostic_summary_overrides_rejects_more_than_three_summary_bullets(
    tmp_path: Path,
) -> None:
    path = tmp_path / "summary.json"
    path.write_text(json.dumps({
        "FV-SUMMARY": {
            "summary_issues": ["一", "二", "三", "四"],
        }
    }, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="FV-SUMMARY.*summary_issues.*1 to 3"):
        generator.load_diagnostic_summary_overrides(path)


def test_human_verification_status_is_loaded_and_applied(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "summary.json"
    path.write_text(json.dumps({
        "FV-HUMAN": {
            "rating": 1,
            "rating_conclusion": "冻结评级结论。",
            "main_issue": "未见明显异常",
            "issues": [],
            "action": "维持周期复测。",
            "actions": ["维持周期复测。"],
            "overall_assessment": "冻结综合诊断结论。",
            "human_verification_status": "已纳入人工核对意见",
        }
    }, ensure_ascii=False), encoding="utf-8")

    loaded = generator.load_diagnostic_summary_overrides(path)
    valve = generator.ValveReport(
        folder="FV-HUMAN",
        metrics={"test_types": ["run_step"], "step_count": 1},
    )
    monkeypatch.setattr(generator, "DIAGNOSTIC_SUMMARY_OVERRIDES", loaded)

    generator.apply_report_diagnosis_summary(valve)

    assert valve.human_verification_status == "已纳入人工核对意见"


def test_algorithm_audit_preserves_data_evidence_availability() -> None:
    source = {
        "has_test_data": False,
        "metrics": {},
        "active_faults": [],
        "basic_action_failed": False,
    }
    visual_only = generator.calculate_algorithm_audit(
        {**source, "folder": "FV-VISUAL-ONLY"},
        (_interpretation(image_key="FV-VISUAL-ONLY:run_ramp:1", rating=3),),
    )
    missing = generator.calculate_algorithm_audit(
        {**source, "folder": "FV-MISSING"}, ()
    )

    assert visual_only["rating_evidence"]["data_evidence_status"] == "insufficient"
    assert visual_only["rating_evidence"]["data_evidence_reason"] == "data_rating_missing"
    assert missing["rating_evidence"]["data_evidence_status"] == "unavailable"
    assert missing["rating_evidence"]["data_evidence_reason"] == "data_and_visual_rating_missing"


def test_parse_sequence_pdf_extracts_spring_range(monkeypatch, tmp_path) -> None:
    text = """
    Positioner Tag: FV-1001 Device ID: 123
    Result: Parameters High Low Mean
    Spring Range 306.4 kPa 153.3 kPa
    Friction 19.9 kPa 16.1 kPa 19.0 kPa
    """
    monkeypatch.setattr(generator, "run_pdftotext", lambda _path: text)

    _, info = generator.parse_sequence_pdf(tmp_path / "sequence.pdf")

    assert info["spring_range"] == "153.3–306.4 kPa"


def test_parse_sequence_pdf_keeps_positioner_dtm_fields_and_provenance(monkeypatch, tmp_path) -> None:
    text = """
    Sequencer Execution Report
    Positioner Info Positioner Tag: FV-1001 Serial Number: C123
    Device ID: 123 Device Type ID SVI2AP (0xCA) Address: 0
    Firmware Version 3.2.3 Manufacturer ID Masoneilan (0x65)
    Start Time: 5/24/2025 9:10:53 AM End Time: 5/24/2025 9:18:10 AM
    Configuration Air Action Air To Close Actuator Type Single
    Diagnostic Level Standard Diagnostics Position Unit %
    """
    monkeypatch.setattr(generator, "run_pdftotext", lambda _path: text)

    _, info = generator.parse_sequence_pdf(tmp_path / "sequence.pdf")

    assert info["report_type"] == "sequencer_execution"
    assert info["serial_number"] == "C123"
    assert info["firmware"] == "3.2.3"
    assert info["diagnostics_level"] == "Standard Diagnostics"
    assert info["device_address"] == "0"
    assert info["dtm_source_status"] == "present"
    assert info["dtm_parser_status"] == "parsed"


def test_parse_sequence_pdf_supports_valvue_410_positioner_layout(monkeypatch, tmp_path) -> None:
    text = """
    Sequencer Execution Report
    Start Time 8/26/2026 3:08:53 PM End Time 8/26/2026 3:37:16 PM
    Positioner Info
    Device Tag V13003B1 Device S/N C319194587 Device ID 1133174
    Dev. Type/Rev SVI2AP (0xCA / 02) Polling Address 0
    Manufacturer ID Masoneilan (0x65) Diagnostics Level Advanced Diagnostics
    DTM Version 3.40.0 BuildID: 061126 Descriptor 8E12A1013-29
    Firmware Rev 3.2.8 / 58728 Message
    Option Config Advanced Diagnostics; Double Acting;
    Configuration
    Air Action ATO Actuator Type Double
    Operating Data
    Supply Pressure 381.40 kPa
    Active Faults Reset;
    Historical Faults Reset; Low Power; Marginal Power;
    Raw Data
    Task Execution Summary
    Extended Act. Signature 8/26/2026 3:27:15 PM 8/26/2026 3:36:50 PM 575 Failed
    Change Mode 8/26/2026 3:36:50 PM 8/26/2026 3:37:16 PM 26 Failed
    """
    monkeypatch.setattr(generator, "run_pdftotext", lambda _path: text)

    _, info = generator.parse_sequence_pdf(tmp_path / "sequence.pdf")

    assert info["device_tag"] == "V13003B1"
    assert info["serial_number"] == "C319194587"
    assert info["device_type"] == "SVI2AP (0xCA / 02)"
    assert info["manufacturer"] == "Masoneilan (0x65)"
    assert info["diagnostics_level"] == "Advanced Diagnostics"
    assert info["diagnostic_software_version"] == "3.40.0 BuildID: 061126"
    assert info["supply_pressure"] == "381.40 kPa"
    assert info["active_faults"] == []
    assert info["historical_faults"] == ["Low Power", "Marginal Power"]
    assert info["report_time"] == "2026-08-26 15:08:53–15:37:16"
    assert info["test_execution_issues"] == [
        {
            "test_key": "extended_signature",
            "test_name": "Extended Actuator Signature",
            "occurrence": 1,
            "status": "Failed",
        },
        {
            "test_key": "change_mode",
            "test_name": "Change Mode",
            "occurrence": 1,
            "status": "Failed",
        },
    ]


def test_signature_friction_parses_each_standard_and_extended_occurrence_with_provenance(
    monkeypatch, tmp_path
) -> None:
    text = """
    Device ID: 1
    Standard Signature Status:
      Result:
      Max Friction (psi): 3.82
      Max Friction ( %): 6.18
      Mean Friction (psi): 3.00
      Mean Friction ( %): 5.10
      Min Friction (psi): 2.10
      Min Friction ( %): 4.20
    Standard Signature Status:
      Result:
      Max Friction (psi): 4.82
      Max Friction ( %): 7.18
      Mean Friction (psi): 4.00
      Mean Friction ( %): 6.10
      Min Friction (psi): 3.10
      Min Friction ( %): 5.20
    Extended Act. Status: Signature
      Result:
      Max Friction (psi): 8.82
      Max Friction ( %): 9.18
      Mean Friction (psi): 8.00
      Mean Friction ( %): 8.10
      Min Friction (psi): 7.10
      Min Friction ( %): 7.20
    """
    monkeypatch.setattr(generator, "run_pdftotext", lambda _path: text)

    records, _ = generator.parse_sequence_pdf(tmp_path / "sequence.pdf")
    signatures = [record for record in records if record.test_type in {"SIG-S", "SIG-EXT"}]
    metrics = generator.compute_metrics(records)

    assert [(record.test_type, record.occurrence) for record in signatures] == [
        ("SIG-S", 1),
        ("SIG-S", 2),
        ("SIG-EXT", 1),
    ]
    assert signatures[0].attrs["friction_source"] == "sequence.pdf"
    assert signatures[1].attrs["friction_occurrence"] == 2
    assert signatures[0].attrs["minFric"] == 4.2
    assert signatures[2].attrs["maxFric"] == 9.18
    assert metrics["Max Friction"] == 9.18
    assert metrics["Min Friction"] == 4.2
    assert metrics["Friction Unit"] == "%"
    assert presentation.format_friction_metric(metrics, "max") == "9.18% / 8.82 psi"
    assert presentation.format_friction_metric(metrics, "min") == "4.2% / 2.1 psi"
    assert [(item["source"], item["occurrence"]) for item in metrics["Friction Measurements"]] == [
        ("sequence.pdf", 1),
        ("sequence.pdf", 2),
        ("sequence.pdf", 1),
    ]


def test_signature_friction_legacy_table_does_not_mix_psi_and_percent(monkeypatch, tmp_path) -> None:
    text = """
    Run Signature Test Status: Succeeded
    Parameter Max Min Average
    Friction 14.07 % 10.17 % 12.12 %
    Friction 3.31 psi 2.39 psi 2.85 psi
    """
    monkeypatch.setattr(generator, "run_pdftotext", lambda _path: text)

    records, _ = generator.parse_sequence_pdf(tmp_path / "legacy.pdf")
    metrics = generator.compute_metrics(records)

    signature = next(record for record in records if record.test_type == "SIG-S")
    assert signature.attrs["maxFric"] == 14.07
    assert signature.attrs["minFric"] == 10.17
    assert signature.attrs["maxFric_pres"] == 3.31
    assert signature.attrs["minFric_pres"] == 2.39
    assert metrics["Max Friction"] == 14.07
    assert metrics["Min Friction"] == 10.17


def test_signature_friction_accepts_valvxpert_high_low_mean_and_ocr_psi_units(
    monkeypatch, tmp_path
) -> None:
    text = """
    Extended Act. Signature Status: Succeeded
    Result: Parameters High Low Mean
    Friction 5.72 psí 4.15 píi 5.12 psl
    Friction (%) 15.47 % 11.24 % 13.84 %
    """
    monkeypatch.setattr(generator, "run_pdftotext", lambda _path: text)

    records, _ = generator.parse_sequence_pdf(tmp_path / "ocr-units.pdf")
    metrics = generator.compute_metrics(records)

    signature = next(record for record in records if record.test_type == "SIG-EXT")
    assert signature.attrs["maxFric_pres"] == 5.72
    assert signature.attrs["minFric_pres"] == 4.15
    assert signature.attrs["aveFric_pres"] == 5.12
    assert metrics["Max Friction"] == 15.47
    assert metrics["Min Friction"] == 11.24
    assert metrics["Mean Friction"] == 13.84
    assert metrics["Friction Unit"] == "%"
    assert presentation.format_friction_metric(metrics, "max") == "15.47% / 5.72 psi"
    assert presentation.format_friction_metric(metrics, "min") == "11.24% / 4.15 psi"
    assert presentation.format_friction_metric(metrics, "mean") == "13.84% / 5.12 psi"


def test_project_reviewed_friction_override_preserves_provenance(monkeypatch) -> None:
    monkeypatch.setattr(
        generator,
        "FRICTION_MEASUREMENT_OVERRIDES_BY_TAG",
        {
            "FV-REVIEWED": {
                "source": "diagnostic.pdf",
                "source_page": 5,
                "report_page": "12/20",
                "force": {"unit": "kPa", "max": 30.0, "min": 20.0, "mean": 25.0},
                "percent": {"max": 10.0, "min": 6.0, "mean": 8.0},
            }
        },
    )
    metrics = generator.compute_metrics([])

    generator.apply_friction_measurement_override("FV-REVIEWED", metrics)

    assert metrics["Max Friction"] == 10.0
    assert metrics["Min Friction"] == 6.0
    assert metrics["Mean Friction"] == 8.0
    assert metrics["Friction Unit"] == "%"
    assert presentation.format_friction_metric(metrics, "mean") == "8% / 25 kPa"
    assert metrics["Friction Measurements"] == [
        {
            "source": "diagnostic.pdf",
            "source_page": 5,
            "report_page": "12/20",
            "occurrence": 1,
            "test_type": "SIG-EXT",
            "review_status": "project_reviewed_override",
            "values": {
                "max": {"%": 10.0, "kPa": 30.0},
                "min": {"%": 6.0, "kPa": 20.0},
                "mean": {"%": 8.0, "kPa": 25.0},
            },
        }
    ]


def test_parse_sequence_pdf_reads_vertically_extracted_valvxpert_tables(monkeypatch, tmp_path) -> None:
    text = """
    ValvXpert Execution Report Device ID 1
    Run Step Test
    Supply Pressure 36.84 psi
    50.00 - > 51.00
    0.25
    0.62
    0.49
    0.30
    0.04
    51.00 -> 50.00
    121
    1.66
    1.23
    020
    225
    Run Ramp Test
    Hysteresis (%)
    1.42
    0.39
    0.10
    Dead Band (%)
    1.44
    0.39
    0.10
    """
    monkeypatch.setattr(generator, "run_pdftotext", lambda _path: text)
    monkeypatch.setattr(generator, "ALLOW_LOOSE_PDF_METRICS", True)

    records, _ = generator.parse_sequence_pdf(tmp_path / "diagnostic.pdf")
    metrics = generator.compute_metrics(records)

    assert metrics["step_count"] == 2
    assert metrics["Average Error"] == 0.25
    assert metrics["T86"] == 1.66
    assert metrics["Td"] == 0.3
    assert metrics["Overshoot"] == 0.04
    assert metrics["run_ramp"] == {
        "hysteresis": {"max": 1.42, "average": 0.1, "min": 0.39},
        "dead_band": {"max": 1.44, "average": 0.1, "min": 0.39},
        "linearity": {"max": None, "average": None, "min": None},
    }
    assert metrics["Maximum Hysteresis + Dead Band"] is None
    assert metrics["Average Hysteresis + Dead Band"] is None


def test_sequencer_keeps_complete_positioner_runtime_tasks_and_run_ramp(monkeypatch, tmp_path) -> None:
    text = """
    Sequencer Execution Report
    Start Time 8/26/2026 3:08:53 PM End Time 8/26/2026 3:37:16 PM Total Duration 1703
    Positioner Info
    Device Tag VALVE-001 Device S/N C000000001 Device ID 1133174
    Option Config Advanced Diagnostics; Double Acting;
    Operating Data
    Device Mode Setup Valve Position -0.0 % Manual Setpoint 15.0 %
    Input Signal 4.00 mA Board Temp. 34.3 degC Min Temp. -4.4 degC Max Temp. 39.7 degC
    Continuous Diagnostics
    Strokes (counts) 196 Cyeles (counts) 5517
    Time Open (hours) 501 Time Closed (hours) 48819 Time Near Closed (hours) 220
    Run Ramp Test
    Hysteresis (%)
    1.88
    0.00
    0.99
    Dead Band (%)
    1.85
    0.00
    1.00
    Linearity (%)
    1.07
    0.01
    0.66
    Task Execution Summary
    Extended Actuator Signature 8/26/2026 3:27:15 PM 8/26/2026 3:36:50 PM 575 Failed
    Change Mode 8/26/2026 3:36:50 PM 8/26/2026 3:37:16 PM 26 Succeeded
    """
    monkeypatch.setattr(generator, "run_pdftotext", lambda _path: text)

    records, info = generator.parse_sequence_pdf(tmp_path / "sequence.pdf")
    normalized = generator.sequence_positioner_report_info(info)
    metrics = generator.compute_metrics(records)

    for key in (
        "option_config", "device_mode", "valve_position", "manual_setpoint",
        "input_signal", "board_temperature", "min_temperature", "max_temperature",
        "strokes", "cycles", "time_open", "time_closed", "time_near_closed",
        "start_time", "end_time", "report_time", "task_execution",
    ):
        assert key in normalized
    assert normalized["cycles"] == "5517"
    assert normalized["option_config"] == "Advanced Diagnostics; Double Acting;"
    assert normalized["task_execution"] == [
        {
            "task_name": "Extended Actuator Signature",
            "occurrence": 1,
            "start_time": "8/26/2026 3:27:15 PM",
            "end_time": "8/26/2026 3:36:50 PM",
            "duration_seconds": 575,
            "result": "Failed",
            "test_key": "extended_signature",
        },
        {
            "task_name": "Change Mode",
            "occurrence": 1,
            "start_time": "8/26/2026 3:36:50 PM",
            "end_time": "8/26/2026 3:37:16 PM",
            "duration_seconds": 26,
            "result": "Succeeded",
            "test_key": "change_mode",
        },
    ]
    assert metrics["run_ramp"] == {
        "hysteresis": {"max": 1.88, "average": 0.99, "min": 0.0},
        "dead_band": {"max": 1.85, "average": 1.0, "min": 0.0},
        "linearity": {"max": 1.07, "average": 0.66, "min": 0.01},
    }


def test_task_execution_preserves_cross_page_order_and_repeated_occurrences() -> None:
    text = """
Task Execution Summary
Change Mode
8/26/2026 3:00:00 PM
8/26/2026 3:00:05 PM
5
Succeeded
Change Mode 8/26/2026 3:36:50 PM 8/26/2026 3:37:16 PM 26 Failed
"""

    tasks = generator.parse_task_execution(text)

    assert [(task["task_name"], task["occurrence"], task["result"]) for task in tasks] == [
        ("Change Mode", 1, "Succeeded"),
        ("Change Mode", 2, "Failed"),
    ]
    assert generator.task_execution_issues(tasks) == [
        {
            "test_key": "change_mode",
            "test_name": "Change Mode",
            "occurrence": 2,
            "status": "Failed",
        }
    ]


def test_task_execution_keeps_setup_first_row_and_filters_only_failure_statuses(
    monkeypatch, tmp_path
) -> None:
    text = """
Sequencer Execution Report
Start Time 8/26/2026 3:00:00 PM End Time 8/26/2026 3:40:00 PM Total Task Count 7
Task Execution Summary
Change Mode 8/26/2026 3:00:00 PM 8/26/2026 3:00:05 PM 5 Setup
Run Step Test 8/26/2026 3:00:05 PM 8/26/2026 3:05:05 PM 300 Succeeded
Pattern Step Test 8/26/2026 3:05:05 PM 8/26/2026 3:10:05 PM 300 Normal
Run Ramp Test 8/26/2026 3:10:05 PM 8/26/2026 3:15:05 PM 300 Succeeded
Standard Signature 8/26/2026 3:15:05 PM 8/26/2026 3:20:05 PM 300 Succeeded
Extended Actuator Signature 8/26/2026 3:20:05 PM 8/26/2026 3:30:05 PM 600 Failed
Change Mode 8/26/2026 3:30:05 PM 8/26/2026 3:30:10 PM 5 Normal
"""
    monkeypatch.setattr(generator, "run_pdftotext", lambda _path: text)

    _, info = generator.parse_sequence_pdf(tmp_path / "sequence.pdf")

    assert len(info["task_execution"]) == 7
    assert [task["result"] for task in info["task_execution"]] == [
        "Setup", "Succeeded", "Normal", "Succeeded", "Succeeded", "Failed", "Normal"
    ]
    assert [
        task["occurrence"]
        for task in info["task_execution"]
        if task["task_name"] == "Change Mode"
    ] == [1, 2]
    assert info["test_execution_issues"] == [
        {
            "test_key": "extended_signature",
            "test_name": "Extended Actuator Signature",
            "occurrence": 1,
            "status": "Failed",
        }
    ]
    assert info["task_execution_expected_count"] == 7
    assert info["task_execution_parser_status"] == "parsed"


def test_task_execution_count_mismatch_is_explicit_parser_missing(monkeypatch, tmp_path) -> None:
    text = """
Sequencer Execution Report Total Task Count: 2 Task Execution Summary
Change Mode 8/26/2026 3:00:00 PM 8/26/2026 3:00:05 PM 5 Setup
"""
    monkeypatch.setattr(generator, "run_pdftotext", lambda _path: text)

    _, info = generator.parse_sequence_pdf(tmp_path / "partial.pdf")

    assert len(info["task_execution"]) == 1
    assert info["task_execution_parser_status"] == "source_present_parser_missing"


def test_run_ramp_parser_uses_declared_max_average_min_column_order() -> None:
    attrs = generator.parse_hysteresis_dead_band(
        "Parameters Maximum Average Minimum\n"
        "Hysteresis (%) 1.88 0.99 0.00\n"
        "Dead Band (%) 1.85 1.00 0.00\n"
        "Linearity (%) 1.07 0.66 0.01"
    )

    assert attrs == {
        "HysteresisMax": "1.88", "HysteresisAverage": "0.99", "HysteresisMin": "0",
        "DeadBandMax": "1.85", "DeadBandAverage": "1", "DeadBandMin": "0",
        "LinearityMax": "1.07", "LinearityAverage": "0.66", "LinearityMin": "0.01",
    }


def test_supply_pressure_parser_rejects_impossible_ocr_values() -> None:
    values = generator.extract_supply_pressures_from_text(
        "Supply Pressure 350.0 kPa Supply Pressure 32020 psi Supply Pressure 48.2 psi"
    )

    assert values == pytest.approx([50.763208195, 48.2])


def test_non_seq_valvxpert_execution_pdf_contributes_test_records(monkeypatch, tmp_path) -> None:
    raw_dir = tmp_path / "raw"
    valve_dir = raw_dir / "FV-101"
    valve_dir.mkdir(parents=True)
    report = valve_dir / "diagnostic.pdf"
    report.write_bytes(b"placeholder")
    record = generator.TestRecord(
        source=report.name,
        source_type="pdf",
        test_type="STEP",
        device_id="1",
        steps=[{"AverageError": "0.5"}],
    )
    monkeypatch.setattr(generator, "RAW_DIR", raw_dir)
    monkeypatch.setattr(generator, "BASE_DIR", tmp_path)
    monkeypatch.setattr(generator, "EXCLUDED_SOURCE_FILES", frozenset())
    monkeypatch.setattr(
        generator,
        "parse_active_report_pdf",
        lambda _path: {
            "report_type": "valvxpert_execution",
            "device_id": "1",
            "device_tag": "FV-101",
        },
    )
    monkeypatch.setattr(
        generator,
        "parse_sequence_pdf",
        lambda _path: ([record], {"device_id": "1", "device_tag": "FV-101"}),
    )
    monkeypatch.setattr(generator, "run_pdftotext", lambda _path: "ValvXpert Execution Report")
    monkeypatch.setattr(generator, "is_diagnostic_pdf", lambda _path, _text: True)
    monkeypatch.setattr(generator, "render_images_for_valve", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(generator, "apply_valve_context", lambda _valve: None)

    valves = generator.collect_curve_review_valves()

    assert len(valves) == 1
    assert valves[0].metrics["step_count"] == 1
    assert valves[0].metrics["Average Error"] == 0.5


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Spring Range 14.55 8.15 psi", "8.15–14.55 psi"),
        ("Spring Range 0.6 1.2 bar", "0.6–1.2 bar"),
    ],
)
def test_parse_spring_range_supports_single_trailing_unit(text: str, expected: str) -> None:
    assert generator.parse_spring_range(text) == expected


def test_parse_active_report_pdf_extracts_svi_ii_ap_dtm_fields(monkeypatch, tmp_path) -> None:
    text = """
    SVI II AP Report Smart Valve Interface Results
    Device Tag 13FV1206 Device S/N 16190656 Device ID 16190656
    DTM Rev 3.30.0 BuildID: 092325 Firmware Rev 3.2.3
    Option Config Standard Diagnostics; Single Acting;
    Configuration Information
    Time : 2026-07-13 14:05 Operator : ladmin
    Air Action Air To Open Actuator Type Single Acting Open Stop Adj. 87.5
    Valve Position 117.20 % Manual Setpoint 132.70 % Supply Pressure 400.6 kPa
    Input Signal 25.24 mA I/P Current 1.17 mA
    Device Mode Normal Min Temp. -6.99 degC Max Temp. 60.55 degC
    Active Faults Reset; Low Power; Actuator Error; Historical Faults
    Strokes (counts) 580 Cycles (counts) 94441
    Time Open (hours) 70201 Time Closed (hours) 0
    Time Near Closed (hours) 1634
    """
    monkeypatch.setattr(generator, "run_pdftotext", lambda _path: text)

    info = generator.parse_active_report_pdf(tmp_path / "dtm.pdf")

    assert info["is_dtm_report"] is True
    assert info["dtm_report_type"] == "svi_ii_ap"
    assert info["device_type"] == "SVI II AP"
    assert info["device_tag"] == "13FV1206"
    assert info["device_id"] == "16190656"
    assert info["serial_number"] == "16190656"
    assert info["date_time"] == "2026-07-13 14:05"
    assert info["firmware"] == "3.2.3"
    assert info["dtm_revision"] == "3.30.0 BuildID: 092325"
    assert info["air_action"] == "Air To Open"
    assert info["actuator_style"] == "Single Acting"
    assert info["supply_pressure"] == "400.6 kPa"
    assert info["valve_position"] == "117.20 %"
    assert info["input_signal"] == "25.24 mA"
    assert info["device_mode"] == "Normal"
    assert info["min_temperature"] == "-6.99 degC"
    assert info["max_temperature"] == "60.55 degC"
    assert info["strokes"] == "580"
    assert info["cycles"] == "94441"
    assert info["time_open"] == "70201"
    assert info["time_closed"] == "0"
    assert info["time_near_closed"] == "1634"
    assert info["faults"] == ["Low Power", "Actuator Error"]


def test_parse_active_report_pdf_extracts_execution_time_window(monkeypatch, tmp_path) -> None:
    text = """
    ValvXpert Execution Report
    Start Time 7/20/2026 1:55:43 PM
    End Time
    Task Execution Summary
    Run Ramp Test 7/20/2026 2:20:00 PM 7/20/2026 2:34:43 PM Success
    """
    monkeypatch.setattr(generator, "run_pdftotext", lambda _path: text)

    info = generator.parse_active_report_pdf(tmp_path / "execution.pdf")

    assert info["report_time"] == "2026-07-20 13:55:43–14:34:43"


def test_parse_active_report_pdf_extracts_positioner_fields_without_separate_dtm(monkeypatch, tmp_path) -> None:
    text = """
    ValvXpert Execution Report
    Start Time 7/20/2026 2:59:23 PM
    Positioner Info
    Device Tag FV-10503 Device S/N C520300360 Device ID 3137920
    Dev. Type/Rev SVI2AP (0xCA / 02) Polling Address 0
    Manufacturer ID Masoneilan (0x65) Diagnostics Level Advanced Diagnostics
    DTM Version 3.40.0 BuildID: 061126 Descriptor F-21-002838 Firmware Rev 3.2.8 / 58728
    Message 9000-FV-9001 Valve S/N N/A
    Option Config Advanced Diagnostics; Single Acting;
    Configuration
    Air Action ATC Actuator Type Single Open Stop Adj. 100.0
    Operating Data
    Device Mode Setup Valve Position 61.7 % Manual Setpoint 0.2 %
    Input Signal 20.18 mA I/P Current N/A Bias 19840
    Board Temp. 26.8 degC Min Temp. 0.3 degC Max Temp. 55.7 degC
    Supply Pressure 47.22 psi
    Extended Act. Signature Result Parameters High Low Mean
    Spring Range 14.55 psi 8.15 psi
    Friction 2.14 psi 1.89 psi 2.01 psi
    Active Faults Reset; Bias Out Of Range;
    Historical Faults Reset; Actuator Error; Low Air Supply Warning; Position Error; Marginal Power;
    Raw Data
    Continuous Diagnostics
    Strokes (counts) 1633 Cycles (counts) 378432
    Time Open (hours) 34644 Time Closed (hours) 294 Time Near Closed (hours) 186
    Task Execution Summary
    Change Mode 7/20/2026 3:32:06 PM 7/20/2026 3:33:13 PM Failed
    """
    monkeypatch.setattr(generator, "run_pdftotext", lambda _path: text)

    info = generator.parse_active_report_pdf(tmp_path / "execution.pdf")

    assert info["report_type"] == "valvxpert_execution"
    assert info["device_type"] == "SVI2AP (0xCA / 02)"
    assert info["firmware"] == "3.2.8 / 58728"
    assert info["diagnostic_software_version"] == "3.40.0 BuildID: 061126"
    assert info["diagnostics_level"] == "Advanced Diagnostics"
    assert info["actuator_style"] == "Single Acting"
    assert info["air_action"] == "ATC"
    assert info["strokes"] == "1633"
    assert info["cycles"] == "378432"
    assert info["time_open"] == "34644"
    assert info["time_closed"] == "294"
    assert info["time_near_closed"] == "186"
    assert info["spring_range"] == "8.15–14.55 psi"
    assert info["active_faults"] == ["Bias Out Of Range"]
    assert info["historical_faults"] == [
        "Actuator Error",
        "Low Air Supply Warning",
        "Position Error",
        "Marginal Power",
    ]
    assert info["fault_data_status"] == "present"


def test_valvxpert_marks_missing_fault_fields_and_accepts_cyeles_ocr(monkeypatch, tmp_path) -> None:
    text = """
    ValvXpert Execution Report
    Control Parameters Air Action ATO Actuator Type Double
    Continuous Diagnostics Strokes (counts) 120 Cyeles (counts) 345
    Time Open (hours) 10 Time Closed (hours) 20 Time Near Closed (hours) 3
    """
    monkeypatch.setattr(generator, "run_pdftotext", lambda _path: text)

    info = generator.parse_active_report_pdf(tmp_path / "execution.pdf")

    assert info["cycles"] == "345"
    assert info["fault_data_status"] == "source_missing"


def test_parse_active_report_pdf_recovers_dtm_sections_when_valvxpert_heading_is_missing(monkeypatch, tmp_path) -> None:
    text = """
    Baker Hughes
    Control Parameters
    Air Action ATC
    Astuator Type Single
    Continuous Diagnostics
    Strokes (counts) 12041 Cycles (counts) 3049843
    Time Open (hours) 86887 Time Closed (hours) 1863 Time Near Closed (hours) 0
    Run Step Test Status: Succeeded
    """
    monkeypatch.setattr(generator, "run_pdftotext", lambda _path: text)

    info = generator.parse_active_report_pdf(tmp_path / "partial-export.pdf")

    assert info["report_type"] == "valvxpert_execution"
    assert info["is_dtm_report"] is False
    assert info["dtm_source_status"] == "present"
    assert info["dtm_parser_status"] == "parsed"
    assert info["actuator_style"] == "Single Acting"
    assert info["air_action"] == "ATC"
    assert info["strokes"] == "12041"
    assert info["cycles"] == "3049843"
    # The source has no identity/firmware fields; keep them absent.
    assert info["device_tag"] == ""
    assert info["firmware"] == ""


def test_parse_active_report_pdf_marks_dtm_source_missing_separately(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(generator, "run_pdftotext", lambda _path: "A plain report with no device data")

    info = generator.parse_active_report_pdf(tmp_path / "plain.pdf")

    assert info["dtm_source_status"] == "source_missing"
    assert info["dtm_parser_status"] == "not_applicable"


def test_execution_report_folder_match_accepts_unit_prefix_but_not_another_tag() -> None:
    assert generator.execution_report_matches_folder("9000-FV-9001", {"device_tag": "FV-9001"})
    assert not generator.execution_report_matches_folder("9000-FV-9001", {"device_tag": "TV-9001"})


def test_choose_device_id_prefers_report_whose_tag_matches_folder_over_mismatched_test_data() -> None:
    tests = [
        generator.TestRecord(
            source="old.devdata",
            source_type="devdata",
            test_type="STEP",
            device_id="1166153",
            device_tag="TV-9001",
        )
    ]
    reports = [{
        "device_id": "1161847",
        "device_tag": "FV-9001",
        "report_time": "2026-07-18 15:58:09–16:23:46",
    }]

    assert generator.choose_device_id("9000-FV-9001", tests, [], reports) == "1161847"


def test_select_positioner_report_uses_latest_trusted_report_without_unioning_current_faults() -> None:
    reports = [
        {
            "source": "older.pdf",
            "device_id": "123",
            "report_time": "2026-07-18 10:00:00–10:10:00",
            "active_faults": ["Old Fault"],
        },
        {
            "source": "newer.pdf",
            "device_id": "123",
            "report_time": "2026-07-19 10:00:00–10:10:00",
            "active_faults": [],
        },
        {
            "source": "other-device.pdf",
            "device_id": "999",
            "report_time": "2026-07-20 10:00:00–10:10:00",
            "active_faults": ["Wrong Device Fault"],
        },
    ]

    selected = generator.select_positioner_report(reports, "123")

    assert selected is not None
    assert selected["source"] == "newer.pdf"
    assert selected["active_faults"] == []


def test_select_positioner_report_rejects_newer_missing_id_wrong_tag_report() -> None:
    reports = [
        {
            "source": "trusted.pdf",
            "device_id": "123",
            "device_tag": "FV-1001",
            "report_time": "2026-07-18 10:00:00–10:10:00",
            "active_faults": [],
        },
        {
            "source": "wrong-no-id.pdf",
            "device_id": "",
            "device_tag": "TV-999",
            "report_time": "2026-07-20 10:00:00–10:10:00",
            "active_faults": ["Wrong Fault"],
        },
    ]

    selected = generator.select_positioner_report(reports, "123")

    assert selected is not None
    assert selected["source"] == "trusted.pdf"


def test_apply_positioner_report_rejects_missing_id_when_valve_has_trusted_id() -> None:
    valve = generator.ValveReport(folder="FV-1001", device_id="123")

    assert generator.apply_positioner_report(
        valve,
        {"device_id": "", "device_tag": "TV-999", "active_faults": ["Wrong Fault"]},
    ) is False
    assert valve.active_faults == []


def test_apply_positioner_report_rejects_mismatched_device_without_mutating_rating_evidence() -> None:
    valve = generator.ValveReport(
        folder="FV-1001",
        device_id="123",
        serial_number="SERIAL-123",
        spring_range="未提供",
    )
    mismatched = {
        "device_id": "999",
        "serial_number": "SERIAL-999",
        "spring_range": "8–16 psi",
        "active_faults": ["Bias Out Of Range"],
        "device_type": "SVI2AP (0xCA / 02)",
    }

    assert generator.apply_positioner_report(valve, mismatched) is False
    assert valve.serial_number == "SERIAL-123"
    assert valve.spring_range == "未提供"
    assert valve.active_faults == []
    assert valve.positioner_report_info == {}


def test_apply_positioner_report_keeps_generic_device_family_as_type_not_full_model() -> None:
    valve = generator.ValveReport(folder="FV-1001", device_id="123")

    applied = generator.apply_positioner_report(
        valve,
        {"device_id": "123", "device_type": "SVI2AP (0xCA / 02)"},
    )

    assert applied is True
    assert valve.positioner_type == "SVI2AP"
    assert valve.positioner_model == "未提供"


def test_sequence_fallback_does_not_overwrite_current_positioner_report() -> None:
    valve = generator.ValveReport(
        folder="FV-1001",
        device_id="123",
        positioner_report_info={"report_type": "valvxpert_execution", "strokes": "82376"},
    )

    generator.apply_sequence_positioner_fallback(
        valve,
        [{"report_type": "sequencer_execution", "device_id": "123"}],
        "123",
    )

    assert valve.positioner_report_info == {
        "report_type": "valvxpert_execution",
        "strokes": "82376",
    }


def test_parse_report_execution_window_prefers_explicit_end_time_over_later_unrelated_timestamp() -> None:
    text = """
    Start Time 7/20/2026 1:55:43 PM End Time 7/20/2026 2:34:43 PM
    Audit metadata 7/21/2026 9:00:00 AM
    """

    assert generator.parse_report_execution_window(text) == "2026-07-20 13:55:43–14:34:43"


def test_diagnostic_image_rect_includes_test_data_above_chart() -> None:
    page_rect = generator.fitz.Rect(0, 0, 612, 792)
    chart_rect = generator.fitz.Rect(23, 220, 593, 609)

    crop = generator.diagnostic_image_rect(page_rect, chart_rect, title_y=34)

    assert crop.y0 <= 30
    assert crop.y1 >= 617
    assert crop.x0 <= 15
    assert crop.x1 >= 601


def test_diagnostic_image_rect_keeps_rightmost_axis_tick() -> None:
    page_rect = generator.fitz.Rect(0, 0, 612, 792)
    chart_rect = generator.fitz.Rect(23, 220, 593, 609)

    crop = generator.diagnostic_image_rect(page_rect, chart_rect)

    assert crop.x1 == 609


def test_parse_active_report_pdf_extracts_localized_svi_ii_ap_dtm_fields(monkeypatch, tmp_path) -> None:
    text = """
    SVI II AP报表 智能阀门接口结果
    设备位号 FV1205B 设备序列号 C318504937 设备识别码 1124611
    DTM 版本 3.30.0 BuildID: 092325 固件版本 3.2.7 / 23285
    可选功能配置 标准诊断; 阀位反馈; 输出开关; 单作用; 数字输入;
    配置信息
    时间 ： 2026-07-13 13:20 操作员： ladmin
    气开/气关设置 气动打开 执行机构类型 单作用
    阀位 100.70 % 手动设定位置 132.70 % 供给压力 59.03 psi
    输入信号 25.22 mA I/P电流 1.12 mA
    设备模式 自动 最低温度 -7.06 degC 最高温度 38.33 degC
    当前故障 重置; 低功耗; 执行机构错误; 低气压供给警告; 供电临界值报警; 历史故障
    冲程（计数） 187 循环（计数） 25455
    开启时间（小时） 8902 闭合时间（小时） 7341 临近全关时间（小时） 1
    """
    monkeypatch.setattr(generator, "run_pdftotext", lambda _path: text)

    info = generator.parse_active_report_pdf(tmp_path / "dtm-cn.pdf")

    assert info["is_dtm_report"] is True
    assert info["dtm_report_type"] == "svi_ii_ap"
    assert info["device_tag"] == "FV1205B"
    assert info["device_id"] == "1124611"
    assert info["serial_number"] == "C318504937"
    assert info["date_time"] == "2026-07-13 13:20"
    assert info["firmware"] == "3.2.7 / 23285"
    assert info["dtm_revision"] == "3.30.0 BuildID: 092325"
    assert info["air_action"] == "气动打开"
    assert info["actuator_style"] == "单作用"
    assert info["supply_pressure"] == "59.03 psi"
    assert info["strokes"] == "187"
    assert info["cycles"] == "25455"
    assert info["time_open"] == "8902"
    assert info["time_closed"] == "7341"
    assert info["time_near_closed"] == "1"
    assert info["faults"] == ["低功耗", "执行机构错误", "低气压供给警告", "供电临界值报警"]


def test_render_images_skips_svi_ii_ap_dtm_chart_duplicates(monkeypatch, tmp_path) -> None:
    english = tmp_path / "DTM-English.pdf"
    localized = tmp_path / "DTM-Chinese.pdf"
    english.write_bytes(b"%PDF-placeholder")
    localized.write_bytes(b"%PDF-placeholder")
    opened: list[Path] = []

    def fake_text(path: Path) -> str:
        if path == english:
            return "SVI II AP Report Smart Valve Interface Results Step Test Information"
        return "SVI II AP报表 智能阀门接口结果 阶跃测试信息"

    def fake_open(path: Path):
        opened.append(path)
        raise RuntimeError("DTM PDF should not reach chart rendering")

    monkeypatch.setattr(generator, "IMAGE_DIR", tmp_path / "images")
    monkeypatch.setattr(generator, "run_pdftotext", fake_text)
    monkeypatch.setattr(generator, "is_diagnostic_pdf", lambda _path, _text: True)
    monkeypatch.setattr(generator.fitz, "open", fake_open)

    assert generator.render_images_for_valve(tmp_path) == []
    assert opened == []


def test_valvxpert_report_remains_diagnostic_when_extended_signature_failed(tmp_path) -> None:
    pdf = tmp_path / "FV-1001 ValvXpert Execution Report.pdf"
    text = """
    Run Step Test Status: Succeeded
    Run Ramp Test Status: Succeeded
    Extended Act. Signature Status: Failed
    HART command failed.
    """

    assert generator.is_diagnostic_pdf(pdf, text) is True


def test_render_images_rejects_silent_skip_when_unknown_pdf_contains_curve(monkeypatch, tmp_path) -> None:
    pdf = tmp_path / "unknown-report.pdf"
    document = fitz.open()
    page = document.new_page(width=612, height=792)
    page.insert_text((72, 72), "Position(%) Set Point(%)")
    page.draw_rect(fitz.Rect(72, 120, 500, 400))
    document.save(pdf)
    document.close()
    monkeypatch.setattr(generator, "IMAGE_DIR", tmp_path / "images")

    with pytest.raises(RuntimeError, match="unrecognized diagnostic PDF contains curve pages"):
        generator.render_images_for_valve(tmp_path, include_observation=False)


def test_synthetic_sequencer_pdf_keeps_step_and_ramp_page_identity(
    monkeypatch,
    tmp_path,
) -> None:
    valve_dir = tmp_path / "VALVE-001"
    valve_dir.mkdir()
    pdf = valve_dir / "Seq_anonymous.pdf"
    document = fitz.open()
    for title in ("Run Step Test", "Run Ramp Test"):
        page = document.new_page(width=612, height=792)
        page.insert_text((72, 72), title)
        page.insert_text((72, 100), "Position (%) Set Point (%)")
        page.draw_rect(fitz.Rect(72, 140, 500, 420))
    document.save(pdf)
    document.close()
    monkeypatch.setattr(generator, "IMAGE_DIR", tmp_path / "images")
    monkeypatch.setattr(
        generator,
        "run_pdftotext",
        lambda _path: "Sequencer Execution Report Device ID 1",
    )

    images = generator.render_images_for_valve(
        valve_dir,
        include_observation=False,
    )

    assert [image["test_key"] for image in images] == [
        "run_step",
        "run_ramp",
    ]


def test_rendered_pages_keep_pattern_and_specific_extended_identity(
    monkeypatch,
    tmp_path,
) -> None:
    valve_dir = tmp_path / "VALVE-001"
    valve_dir.mkdir()
    pdf = valve_dir / "Seq_anonymous.pdf"
    document = fitz.open()

    pattern_page = document.new_page(width=612, height=792)
    pattern_page.insert_text((72, 72), "Run Step Test")
    pattern_page.insert_text((72, 92), "Step Type Pattern Steps")
    pattern_page.insert_text((72, 112), "Pattern Step Test Status Succeeded")
    pattern_page.insert_text((72, 132), "Position (%) Set Point (%)")
    pattern_page.draw_rect(fitz.Rect(72, 160, 500, 420))

    signature_page = document.new_page(width=612, height=792)
    signature_page.insert_text((72, 72), "Extended Act. Signature")
    signature_page.insert_text((72, 92), "Statusi Succeeded")
    signature_page.insert_text((72, 112), "Run Signature Test Status Succeeded")
    signature_page.insert_text((72, 132), "Position (%) Pressure (kPa)")
    signature_page.draw_rect(fitz.Rect(72, 160, 500, 420))

    document.save(pdf)
    document.close()
    monkeypatch.setattr(generator, "IMAGE_DIR", tmp_path / "images")
    monkeypatch.setattr(
        generator,
        "run_pdftotext",
        lambda _path: "Sequencer Execution Report Device ID 1",
    )

    images = generator.render_images_for_valve(
        valve_dir,
        include_observation=False,
    )

    assert [image["test_key"] for image in images] == [
        "pattern_step",
        "extended_signature",
    ]


def test_rendered_combined_page_assigns_each_chart_from_physical_test_inventory(
    monkeypatch,
    tmp_path,
) -> None:
    valve_dir = tmp_path / "VALVE-001"
    valve_dir.mkdir()
    pdf = valve_dir / "Seq_anonymous.pdf"
    document = fitz.open()
    page = document.new_page(width=612, height=792)
    page.insert_text((72, 50), "Run Step Test")
    page.insert_text((72, 70), "Step Type Multiple Steps")
    page.insert_text((72, 90), "Position (%) Set Point (%)")
    page.draw_rect(fitz.Rect(72, 110, 500, 330))
    page.insert_text((72, 380), "Run Step Test")
    page.insert_text((72, 400), "Step Type Pattern Steps")
    page.insert_text((72, 420), "Position (%) Set Point (%)")
    page.draw_rect(fitz.Rect(72, 440, 500, 680))
    document.save(pdf)
    document.close()
    monkeypatch.setattr(generator, "IMAGE_DIR", tmp_path / "images")
    monkeypatch.setattr(
        generator,
        "run_pdftotext",
        lambda _path: "Sequencer Execution Report Device ID 1",
    )

    images = generator.render_images_for_valve(
        valve_dir,
        include_observation=False,
    )

    assert [image["test_key"] for image in images] == [
        "run_step",
        "pattern_step",
    ]


def test_rendering_skips_non_curve_pages_and_embedded_result_table_outliers(
    monkeypatch,
    tmp_path,
) -> None:
    valve_dir = tmp_path / "VALVE-001"
    valve_dir.mkdir()
    chart_image = tmp_path / "chart.png"
    table_image = tmp_path / "result-table.png"
    _write_curve_candidate_image(chart_image, result_table=False)
    _write_curve_candidate_image(table_image, result_table=True)
    pdf = valve_dir / "Seq_anonymous.pdf"
    document = fitz.open()

    cover = document.new_page(width=612, height=792)
    cover.insert_text((72, 72), "Positioner Info and Control Parameters")
    cover.insert_image(fitz.Rect(72, 180, 540, 440), filename=str(table_image))

    combined = document.new_page(width=612, height=792)
    combined.insert_text((72, 50), "Run Step Test")
    combined.insert_text((72, 70), "Step Type Multiple Steps")
    combined.insert_text((72, 90), "Position (%) Set Point (%) Time (sec)")
    combined.insert_image(fitz.Rect(72, 110, 540, 350), filename=str(chart_image))
    combined.insert_text((72, 390), "Run Step Test")
    combined.insert_text((72, 410), "Step Type Pattern Steps")
    combined.insert_image(fitz.Rect(72, 440, 540, 700), filename=str(table_image))

    continuation = document.new_page(width=612, height=792)
    continuation.insert_text((72, 70), "Pattern Step Test Status Succeeded")
    continuation.insert_text((72, 90), "Position (%) Time (sec)")
    continuation.insert_image(fitz.Rect(72, 120, 540, 420), filename=str(chart_image))

    document.save(pdf)
    document.close()
    monkeypatch.setattr(generator, "IMAGE_DIR", tmp_path / "images")
    monkeypatch.setattr(
        generator,
        "run_pdftotext",
        lambda _path: "Sequencer Execution Report Device ID 1",
    )

    images = generator.render_images_for_valve(
        valve_dir,
        include_observation=False,
    )

    assert [
        (image["page"], image["test_key"])
        for image in images
    ] == [
        ("2", "run_step"),
        ("3", "pattern_step"),
    ]


def test_rendering_carries_filtered_test_identity_to_next_page_chart(
    monkeypatch,
    tmp_path,
) -> None:
    valve_dir = tmp_path / "VALVE-001"
    valve_dir.mkdir()
    chart_image = tmp_path / "chart.png"
    table_image = tmp_path / "result-table.png"
    _write_curve_candidate_image(chart_image, result_table=False)
    _write_curve_candidate_image(table_image, result_table=True)
    pdf = valve_dir / "Seq_anonymous.pdf"
    document = fitz.open()

    first = document.new_page(width=612, height=792)
    first.insert_text((72, 50), "Run Step Test")
    first.insert_text((72, 70), "Step Type Multiple Steps")
    first.insert_text((72, 90), "Position (%) Set Point (%) Time (sec)")
    first.insert_image(fitz.Rect(72, 110, 540, 350), filename=str(chart_image))
    first.insert_text((72, 390), "Run Step Test")
    first.insert_text((72, 410), "Step Type Pattern Steps")
    first.insert_image(fitz.Rect(72, 440, 540, 700), filename=str(table_image))

    second = document.new_page(width=612, height=792)
    second.insert_text((72, 50), "Position (%) Time (sec)")
    second.insert_image(fitz.Rect(72, 100, 540, 320), filename=str(chart_image))
    second.insert_text((72, 350), "Run Step Test Status Succeeded")
    second.insert_text((72, 370), "Task Parameters: Direction Both Ways")
    second.insert_text((72, 390), "Position (%) Set Point (%) Time (sec)")
    second.insert_image(fitz.Rect(72, 420, 540, 700), filename=str(chart_image))

    document.save(pdf)
    document.close()
    monkeypatch.setattr(generator, "IMAGE_DIR", tmp_path / "images")
    monkeypatch.setattr(
        generator,
        "run_pdftotext",
        lambda _path: "Sequencer Execution Report Device ID 1",
    )

    images = generator.render_images_for_valve(
        valve_dir,
        include_observation=False,
    )

    assert [
        (image["page"], image["test_key"])
        for image in images
    ] == [
        ("1", "run_step"),
        ("2", "pattern_step"),
        ("2", "run_step"),
    ]


def test_unfinished_test_does_not_relabel_following_test_sections(monkeypatch, tmp_path):
    valve_dir = tmp_path / "VALVE-001"
    valve_dir.mkdir()
    chart = tmp_path / "chart.png"
    _write_curve_candidate_image(chart, result_table=False)
    pdf = valve_dir / "Seq_anonymous.pdf"
    document = fitz.open()
    missing = document.new_page(width=612, height=792)
    missing.insert_text((72, 50), "Run Step Test\nStep Type Pattern Steps\nStatus Failed")
    for title, params in (
        ("Run Step Test", "Step Type Single Step\nTask Parameters: Direction Both Ways"),
        ("Run Ramp Test", "Task Parameters: Speed Level 4"),
        ("Extended Act. Signature", "Task Parameters: Speed Level 4"),
    ):
        page = document.new_page(width=612, height=792)
        page.insert_text((72, 50), title + "\n" + params)
        page.insert_text((72, 110), "Position (%) Set Point (%) Time (sec)")
        page.insert_image(fitz.Rect(72, 140, 540, 440), filename=str(chart))
    document.save(pdf)
    document.close()
    monkeypatch.setattr(generator, "IMAGE_DIR", tmp_path / "images")
    monkeypatch.setattr(generator, "run_pdftotext", lambda _: "Sequencer Execution Report Device ID 1")
    images = generator.render_images_for_valve(valve_dir)
    assert [(image["page"], image["test_key"]) for image in images] == [
        ("2", "run_step"), ("3", "run_ramp"), ("4", "extended_signature")]


def test_rendering_keeps_only_trailing_plot_for_one_embedded_test(
    monkeypatch,
    tmp_path,
) -> None:
    valve_dir = tmp_path / "VALVE-001"
    valve_dir.mkdir()
    chart_image = tmp_path / "chart.png"
    table_image = tmp_path / "grayscale-result-table.png"
    _write_curve_candidate_image(chart_image, result_table=False)
    _write_curve_candidate_image(
        table_image,
        result_table=True,
        colored_table=False,
    )
    pdf = valve_dir / "Seq_anonymous.pdf"
    document = fitz.open()
    page = document.new_page(width=612, height=792)
    page.insert_text((72, 50), "Run Step Test")
    page.insert_text((72, 70), "Step Type Pattern Steps")
    page.insert_image(fitz.Rect(72, 100, 540, 340), filename=str(table_image))
    page.insert_text((72, 370), "Position (%) Time (sec)")
    page.insert_image(fitz.Rect(72, 400, 540, 700), filename=str(chart_image))
    document.save(pdf)
    document.close()
    monkeypatch.setattr(generator, "IMAGE_DIR", tmp_path / "images")
    monkeypatch.setattr(
        generator,
        "run_pdftotext",
        lambda _path: "Sequencer Execution Report Device ID 1",
    )

    images = generator.render_images_for_valve(
        valve_dir,
        include_observation=False,
    )

    assert [(image["page"], image["test_key"]) for image in images] == [
        ("1", "pattern_step")
    ]


def test_full_travel_single_step_can_be_excluded_from_step_metrics(monkeypatch) -> None:
    tests = [
        generator.TestRecord(
            source="sequence.pdf",
            source_type="pdf",
            test_type="STEP",
            steps=[
                {"StartPosition": "0", "EndPosition": "25", "AverageError": "1.2", "T86": "2.1", "T63": "1.5", "Td": "0.4", "MaxOverShoot": "0.3"},
                {"start": "0", "end": "100", "AverageError": "38.8", "T86": "9.9", "T63": "8.8", "Td": "7.7", "MaxOverShoot": "42.0"},
                {"start": "100", "end": "0", "AverageError": "12.0", "T86": "6.0", "T63": "5.0", "Td": "4.0", "MaxOverShoot": "20.0"},
            ],
        )
    ]
    monkeypatch.setattr(generator, "EXCLUDE_FULL_TRAVEL_SINGLE_STEP", True)

    metrics = generator.compute_metrics(tests)

    assert metrics["step_count"] == 1
    assert metrics["Average Error"] == 1.2
    assert metrics["T86"] == 2.1
    assert metrics["T63"] == 1.5
    assert metrics["Td"] == 0.4
    assert metrics["Overshoot"] == 0.3


def test_full_travel_single_step_page_is_recognized_semantically() -> None:
    text = """
    Run Step Test
    Task Parameters:
    Step Type Single Step Direction Both Ways Time 5s
    Position: Start 0 % End 100 % Sample Rate 10 samples/s
    """

    assert generator.is_full_travel_single_step_text(text) is True
    assert generator.is_full_travel_single_step_text(text.replace("Single Step", "Multiple Steps")) is False
    assert generator.is_full_travel_single_step_text(text.replace("End 100 %", "End 50 %")) is False
