from __future__ import annotations

import json
from typing import Any


SAFE_DATA_FIELDS = (
    "位号",
    "检测日期",
    "厂家",
    "设备 ID / 序列号",
    "定位器类型",
    "阀门型号 / 类型",
    "阀门尺寸 / 压力等级",
    "失效位置",
    "弹簧范围",
    "执行机构形式 / 气路动作",
    "Average Error",
    "T86",
    "T63",
    "Td",
    "Overshoot",
    "Maximum Hysteresis + Dead Band",
    "Average Hysteresis + Dead Band",
    "Supply Pressure",
    "Max Friction",
    "Mean Friction",
    "累计行程 / 循环次数",
    "全开 / 全关 / 近关时间",
    "DTM运行快照",
)


SAFE_LEGACY_TEST_FIELDS = {
    "Run Step Test",
    "Pattern Step Test",
    "Run Ramp Test",
    "Standard Signature",
    "Extended Act. Signature",
    "Extended Act.\nSignature",
}


_FAULT_SENTINELS = {
    "",
    "reset",
    "none",
    "no error",
    "no errors",
    "重置",
    "无",
    "无故障",
}


def _normalize_structured_fault_fields(value: Any, key: str = "") -> Any:
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for item_key, item_value in value.items():
            if item_key in {"active_faults", "historical_faults"} and isinstance(item_value, list):
                cleaned: list[Any] = []
                for item in item_value:
                    text = item.get("name") or item.get("fault") or item.get("value") if isinstance(item, dict) else item
                    if str(text or "").strip().casefold() not in _FAULT_SENTINELS:
                        cleaned.append(item)
                normalized[item_key] = cleaned
            else:
                normalized[item_key] = _normalize_structured_fault_fields(item_value, item_key)
        return normalized
    if isinstance(value, list):
        return [_normalize_structured_fault_fields(item, key) for item in value]
    return value


def _curve_images(case: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "image_path": image["path"],
            "image_sha256": image["sha256"],
            "test_key": image["test_key"],
        }
        for image in case["evidence"]["embedded_curve_images"]
    ]


def build_blind_bundle(case: dict[str, Any]) -> dict[str, Any]:
    displayed = case["evidence"]["displayed_data"]
    safe_data = {
        key: displayed[key]
        for key in SAFE_DATA_FIELDS
        if key in displayed
    }
    legacy_test_data = {
        key: displayed[key]
        for key in SAFE_LEGACY_TEST_FIELDS
        if key in displayed
    }
    structured_evidence = _normalize_structured_fault_fields(
        json.loads(
            json.dumps(
                case["evidence"].get("structured_evidence") or {},
                ensure_ascii=False,
            )
        )
    )
    return {
        "schema_version": 1,
        "mode": "current_valve_evidence",
        "target_case_id": case["case_id"],
        "target_valve_tag": case["valve_tag"],
        "exclusion_policy": (
            "目标案例及其人工评级、结论、问题、建议和逐图判读"
            "不得进入新诊断输入。"
        ),
        "rating_standard": {
            "1": {
                "meaning": "当前有效证据未见需处理的异常",
                "confirmed_defect_required": False,
                "boundary": (
                    "主要功能及已验证性能正常；局部待核实现象"
                    "或需处理的偏差应按证据保留，不自动判正常。"
                ),
            },
            "2": {
                "meaning": "有限偏差或待核实的局部现象",
                "boundary": (
                    "尚无充分证据证明功能受损或需要纠正性维护；"
                    "可观察或针对性复核。"
                ),
            },
            "3": {
                "meaning": "有缺陷",
                "basic_action_may_still_complete": True,
                "boundary": (
                    "有具体证据支持需要纠正的功能或性能缺陷；"
                    "阀门仍可能完成主要功能。"
                ),
            },
            "4": {
                "meaning": "严重缺陷",
                "severe_functional_impact_required": True,
                "boundary": (
                    "必须有动作失败、严重失控、无法完成必要行程，"
                    "或其他明确严重功能影响；多项一般异常不能自动"
                    "累加为4级。"
                ),
            },
            "5": {
                "meaning": "数据不足或影响未知",
                "boundary": "整阀有效证据整体不足，无法判断影响；单项缺口只限制相关判断。",
            },
        },
        "evidence": {
            "displayed_data": safe_data,
            "legacy_test_data": legacy_test_data,
            "structured_evidence": structured_evidence,
            "test_execution_issues": list(
                case["evidence"]["displayed_test_execution_issues"]
            ),
            "active_faults": _normalize_structured_fault_fields(
                {"active_faults": list(case["evidence"]["displayed_active_faults"])}
            )["active_faults"],
            "curve_images": _curve_images(case),
            "specification_images": [
                {
                    "image_path": image["path"],
                    "image_sha256": image["sha256"],
                }
                for image in case["evidence"][
                    "embedded_specification_images"
                ]
            ],
        },
        "required_output": {
            "evidence_review": [
                "reviewed_curve_image_sha256s",
                "reviewed_specification_image_sha256s",
                "reviewed_specification_physical_identities",
                "displayed_data_reviewed",
                "structured_evidence_reviewed",
                "active_faults_reviewed",
                "test_execution_issues_reviewed",
            ],
            "curve_interpretations": [
                "image_sha256",
                "test_key",
                "observation",
                "diagnosis",
                "rating",
                "action",
                "confidence",
            ],
            "whole_valve": [
                "rating",
                "rating_conclusion",
                "issues",
                "actions",
                "evidence",
                "uncertainties",
            ],
        },
    }
