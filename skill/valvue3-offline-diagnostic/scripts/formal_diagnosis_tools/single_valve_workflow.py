from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TOOLS_DIR = Path(__file__).resolve().parent
SCRIPT_ROOT = TOOLS_DIR.parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from formal_diagnosis_tools import evidence_bundle
from offline_engine.config import load_project
from offline_engine.distribution import runtime_identity
from offline_engine.source_reads import (
    SOURCE_READ_CONTRACT, SOURCE_READ_FIELDS, source_read_template,
    validate_source_reads, validate_source_read_acceptance,
)
from offline_engine.experience import load_offline_experience
from offline_engine.curve_identity import curve_image_key
from offline_engine.evidence_extraction import (
    EVIDENCE_ACCOUNTING_VERSION,
    evidence_nested_value,
    project_evidence_pages,
    stable_evidence_value,
    structured_evidence_accounting,
    validate_project_structured_evidence,
)
from offline_engine.formal_diagnosis import validate_legacy_algorithm_audit_parent
from offline_engine.visual_review import CurveInterpretation

HUMAN_WRITING_FORBIDDEN_PHRASES = (
    "综合来看",
    "综上所述",
    "证据链",
    "构成明确缺陷",
    "同向佐证",
    "建议围绕",
    "值得关注的是",
    "不容忽视",
    "系统性排查",
    "持续性的综合分析",
)
HUMAN_WRITING_RULES = (
    "按回传报告的现场写法输出：直接说哪里有问题、检查什么、怎样处理。读者是仪表、设备和生产人员，正文不是推理过程或审计说明。只学表达方法，不复制其他阀门的事实或评级。",
    "main_issue 用一个自然短句说最主要的问题。issues 是直接进入报告的正文，每项只讲一个需要处理的现象或风险，必要时带关键数值；优先写决定处置的现象及其相关机械或气路线索；历史报警和累计次数只有改变当前判断或维护安排时才进入正文，不能挤掉主要问题。正常表现只在限定异常范围时简短带出。不同曲线支持同一问题时合并，不重复列曲线名称和统计指标。",
    "actions 用‘核对、检查、调整、准备’等动作直接起句，写清对象，每项建议须对应本阀的具体问题或已说明的预防性维护依据，不固定附加供气、填料、密封、控制权和备件全套检查。只在确实影响该动作时加条件，例如‘检查后决定是否更换’。不要把每条建议写成试验方案，也不要反复铺垫‘在许可条件下、依据结果、尚不能确认’；必要的投用或安全条件仍须保留。",
    "rating_conclusion 简短收束等级、主要问题及处理方向；决定结论的限制用现场能懂的短句说明，如‘现有记录不能确认完整关断时间’。不重抄问题和建议，不长篇解释为什么不是另一等级。",
    "supporting_evidence、counter_evidence、uncertainties 和 metric_threshold_basis 完整保存判读依据、正常反证、阈值来源与具体缺口。正文保留会改变处置的限制，其余分析放在这些字段，不在正文重复展开；完整分析不等于正文必须很长。",
    "使用仪表检修报告常用的具体说法：‘关向越位’在反馈越过给定阀位时写‘关阀超调’；‘核实实位’写‘核对实际阀位’；‘平台稳定性’写‘到位后阀位是否稳定’；‘事件是否复现’写‘报警是否再次出现’。物理开关方向须有依据，不能仅按曲线升降改称开阀或关阀。",
    "‘跟随不足’要说明具体表现：已证实起动延迟就写‘开阀起动延迟’，已证实动作迟缓就写‘关阀动作慢’，仅见反馈落后就写‘阀位未跟上给定信号’；本次记录未到位保留测试范围。超调、响应滞后、卡涩、死区、回差、内漏不是同一问题，不能靠换词改变诊断。保留程度、测量口径、方向和未确认条件。",
    "检查对象按资料写清，如定位器输出压力、反馈杆、填料、阀杆导向、气路放大器及整定参数，少用无具体对象的‘气动输出、传动、控制参数’。部件或配置未核实时先核对，不为专业措辞增加部件或故障。报警优先写适用中文名称，必要时在首次出现后括注原故障代码，不整串堆英文。",
    "短句示意，仅在当前证据支持时使用：‘供气低于规格要求，先核对设定和实际压力。’‘动作次数多，检查填料和导向，按型号准备备件。’‘本次记录内未关到位，尚不能确认完整关阀时间。’‘检查后决定是否更换。’不要照搬例句的事实或把所有例句都写进每台阀。",
    "复测不是默认建议；本规则同时适用于整阀 actions、结论和每张曲线的 action。客户未说明有测试设备、停机窗口和实施条件时，不假定具备。优先利用现有资料和确实可开展的针对性检查，不能把复测换成同样不可执行的核对或扩大拆检。只有结果会改变关键判断且具备条件时才提出复测，写清目的、最小必要项目与时机；不因缺测或短记录自动要求补测、延长记录或重做整套测试。条件不足时保留判断限制；确属必要的安全功能或修后验收，说明原因、所需条件及未验证前的限制，不能因此认定正常或可投用。正常结果不例行附加定期复测。",
    "运行历史、工况和故障只取会改变本阀维护决定的内容，落到具体问题或行动中。保留必要数值、单位及当前/历史状态；可能原因仍写‘可能’，待核实项仍写‘待核实’，不能为简短改成已确认、合格或安全运行。输出前删去重复解释和空泛套话，不设固定条数或机械字数限制。",
)
DIAGNOSIS_DRAFT_FILENAME = "05_diagnosis_draft.json"
INTEGRATED_REQUEST_FILENAME = "03_integrated_diagnosis_request.json"
INTEGRATED_TEMPLATE_FILENAME = "03_integrated_diagnosis_result_template.json"
INTEGRATED_SCHEMA_FILENAME = "03_integrated_diagnosis_output_schema.json"
INTEGRATED_PROMPT_FILENAME = "03_integrated_diagnosis_prompt.md"
INTEGRATED_MODEL = "gpt-6-sol"
INTEGRATED_REASONING_EFFORT = "high"
LEGACY_INTEGRATED_MODEL = "gpt-5.6-sol"
LEGACY_INTEGRATED_REASONING_EFFORT = "medium"
DIAGNOSIS_REVIEW_JSON = "diagnosis-review-report.json"
DIAGNOSIS_REVIEW_MARKDOWN = "diagnosis-review-report.md"
DIAGNOSIS_APPROVAL_JSON = "diagnosis-approval.json"
ALGORITHM_AUDIT_CONTRACT = "deterministic_legacy_rating_v1"


def _require_new_model_contract(model: str, reasoning_effort: str) -> None:
    if (model, reasoning_effort) != (INTEGRATED_MODEL, INTEGRATED_REASONING_EFFORT):
        raise ValueError("new offline diagnosis runs require gpt-6-sol/high")


def _run_model_contract(manifest: dict[str, Any]) -> tuple[str, str]:
    model = manifest.get("worker_model")
    effort = manifest.get("worker_reasoning_effort")
    if model is None and effort is None:
        return LEGACY_INTEGRATED_MODEL, LEGACY_INTEGRATED_REASONING_EFFORT
    if (model, effort) != (INTEGRATED_MODEL, INTEGRATED_REASONING_EFFORT):
        raise ValueError("invalid run model contract")
    return model, effort


def require_project_materials_stage(project_root: Path, report_period: str | None = None) -> dict[str, Any]:
    """Keep the direct formal workflow behind the Stage 1 approval gate."""
    from offline_engine.project_materials import require_material_archive_approval

    return require_material_archive_approval(load_project(project_root, report_period=report_period))


def require_project_parameters_stage(project_root: Path, report_period: str | None = None) -> dict[str, Any]:
    """Keep direct formal diagnosis behind the Stage 2 approval gate."""
    from offline_engine.project_materials import (
        require_material_archive_approval,
        require_parameter_extraction_approval,
    )

    config = load_project(project_root, report_period=report_period)
    require_material_archive_approval(config)
    return require_parameter_extraction_approval(config)
SIGNATURE_TEST_KEYS = {
    "standard_signature",
    "extended_signature",
}
ACTUATOR_PERFORMANCE_TEST_KEYS = SIGNATURE_TEST_KEYS | {
    "performance_check",
}
_SUCCESS_TASK_RESULTS = {
    "success",
    "succeeded",
    "passed",
    "completed",
}
_NON_SUCCESS_TASK_RESULTS = {
    "failed",
    "cancelled",
    "canceled",
    "aborted",
    "incomplete",
    "not_completed",
}
SIGNATURE_VISUAL_CHECKS = (
    "local_jaggedness_or_discontinuity",
    "endpoint_folding_or_jump",
    "negative_pressure_axis",
)
STEP_TEST_KEYS = {
    "run_step",
    "pattern_step",
}
STEP_VISUAL_CHECKS = (
    "opening_overshoot_or_undershoot",
    "closing_overshoot_or_undershoot",
    "directional_response_delay",
    "endpoint_residual_or_travel_loss",
)
VISUAL_CHECK_VALUES = {"present", "absent", "uncertain"}


def select_project_targets(
    analysis: list[dict[str, Any]],
    *,
    included_only: bool = True,
) -> list[dict[str, Any]]:
    selected = [
        item
        for item in analysis
        if (not included_only or item.get("included_in_report") is True)
    ]
    folders = [str(item.get("folder") or "").strip() for item in selected]
    if any(not folder for folder in folders):
        raise ValueError("project target is missing canonical folder tag")
    if len(folders) != len(set(folders)):
        raise ValueError("project target folder tags must be unique")
    return sorted(selected, key=lambda item: item["folder"])


def _project_displayed_data(source: dict[str, Any]) -> dict[str, Any]:
    displayed = {
        "位号": source["folder"],
        "检测日期": source.get("detection_time"),
        "设备 ID / 序列号": (
            source.get("device_id") or source.get("serial_number")
        ),
        "定位器类型": (
            source.get("positioner_type")
            or source.get("positioner_model")
        ),
        "阀门型号 / 类型": source.get("valve_model"),
        "阀门尺寸 / 压力等级": source.get("valve_spec"),
        "弹簧范围": source.get("spring_range"),
        "执行机构形式 / 气路动作": source.get("actuator_type"),
        "DTM运行快照": source.get("dtm_info"),
    }
    displayed.update(source.get("metrics") or {})
    return {
        key: value
        for key, value in displayed.items()
        if value not in (None, "", [], {})
    }


def _project_image(path_value: str, **extra: Any) -> dict[str, Any]:
    path = Path(path_value)
    if not path.is_file():
        raise ValueError(f"project evidence image does not exist: {path}")
    return {
        "path": str(path.resolve()),
        "sha256": _sha256_file(path),
        **extra,
    }


def build_project_target(
    source: dict[str, Any],
    *,
    project_root: Path,
    project_id: str,
) -> dict[str, Any]:
    valve_tag = str(source.get("folder") or "").strip()
    if not valve_tag:
        raise ValueError("project target is missing canonical folder tag")
    structured_evidence = deepcopy(
        validate_project_structured_evidence(
            source,
            project_root=project_root,
        )
    )
    curve_images = [
        _project_image(
            image["path"],
            test_key=image["test_key"],
            image_key=curve_image_key(valve_tag, image),
            source_kind="project_rendered_curve",
            document_order=index,
        )
        for index, image in enumerate(source.get("images") or [], 1)
    ]
    evidence_pages = project_evidence_pages(source, project_root=project_root)
    specification_images: list[dict[str, Any]] = []
    for page in evidence_pages:
        if not page["image_path"]:
            if set(page["roles"]) == {"accessory"}:
                # Accessory metadata remains in structured evidence even when
                # the project excludes that page from report/model images.
                continue
            raise ValueError(
                "formal report page requires a rendered image_path"
            )
        specification_images.append(
            _project_image(
                page["image_path"],
                test_key="report_page",
                evidence_kind="report_page",
                roles=page["roles"],
                source_sha256=page["source_sha256"],
                page=page["page"],
                physical_identity=page["physical_identity"],
                match_basis=page["match_basis"],
                source_kind="project_matched_report_page",
            )
        )
    if not evidence_pages and source.get("spec_image"):
        specification_images.append(
            _project_image(
                source["spec_image"],
                test_key="specification",
                evidence_kind="specification",
                roles=["specification"],
                source_kind="project_matched_specification",
            )
        )
    if not evidence_pages and source.get("accessory_image"):
        specification_images.append(
            _project_image(
                source["accessory_image"],
                test_key="accessory",
                evidence_kind="accessory",
                roles=["accessory"],
                source_kind="project_matched_accessory",
            )
        )
    source_files = list(structured_evidence["source_files"])
    evidence_accounting = structured_evidence_accounting(
        structured_evidence
    )
    identity_hash = _sha256_payload(
        {
            "project_id": project_id,
            "valve_tag": valve_tag,
            "curve_hashes": [
                image["sha256"] for image in curve_images
            ],
            "specification_hashes": [
                image["sha256"] for image in specification_images
            ],
            "source_hashes": [
                image["sha256"] for image in source_files
            ],
            "structured_evidence_sha256": _sha256_payload(
                structured_evidence
            ),
        }
    )
    return {
        "schema_version": 1,
        "case_id": (
            f"project-{project_id}-{valve_tag.lower()}-"
            f"{identity_hash[:12]}"
        ),
        "source_id": f"project-{project_id}",
        "project": project_id,
        "valve_tag": valve_tag,
        "source_locator": {
            "project_root": str(project_root.resolve()),
            "raw_folder": str((project_root / "raw" / valve_tag).resolve()),
        },
        "evidence": {
            "source_files": source_files,
            "structured_evidence": structured_evidence,
            "displayed_data": _project_displayed_data(source),
            "displayed_test_execution_issues": list(
                source.get("test_execution_issues") or []
            ),
            "displayed_active_faults": list(
                evidence_accounting["active_faults"]["value"]
            ),
            "displayed_specification": dict(
                source.get("spec_fields") or {}
            ),
            "displayed_operating_context": {
                key: source[key]
                for key in (
                    "description",
                    "unit",
                    "failure_consequence",
                    "bypass",
                    "site_note",
                )
                if source.get(key) not in (None, "", [], {})
            },
            "embedded_curve_images": curve_images,
            "embedded_specification_images": specification_images,
            "embedded_accessory_images": [],
        },
        "completeness": (
            "illustrated" if curve_images else "conclusion_only"
        ),
    }


def _json_bytes(payload: Any) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"
    ).encode("utf-8")


def _sha256_payload(payload: Any) -> str:
    return hashlib.sha256(_json_bytes(payload)).hexdigest()


def diagnostic_evidence_sha256(bundle: dict[str, Any]) -> str:
    """Hash every diagnostic evidence field while ignoring local file paths."""
    evidence = json.loads(
        json.dumps(bundle.get("evidence") or {}, ensure_ascii=False)
    )
    for field in ("curve_images", "specification_images"):
        for image in evidence.get(field) or []:
            if isinstance(image, dict):
                image.pop("image_path", None)
    return _sha256_payload(stable_evidence_value(evidence, Path("/")))


def _diagnostic_evidence_without_source_files_sha256(
    bundle: dict[str, Any],
) -> str:
    reduced = json.loads(json.dumps(bundle, ensure_ascii=False))
    reduced.setdefault("evidence", {}).pop("source_files", None)
    return diagnostic_evidence_sha256(reduced)


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _project_manifest_binding(
    run_dir: Path,
) -> tuple[Path, dict[str, Any], dict[str, Any]] | None:
    project_manifest_path = (
        run_dir.parent / "00_project_run_manifest.json"
    )
    if not project_manifest_path.is_file():
        return None
    project_manifest = _read_json(project_manifest_path)
    targets = project_manifest.get("targets")
    if not isinstance(targets, list):
        raise ValueError("project run manifest requires targets")
    run_manifest = _read_json(run_dir / "00_run_manifest.json")
    valve_tag = str(run_manifest.get("target_valve_tag") or "").strip()
    matches = [
        target
        for target in targets
        if isinstance(target, dict)
        and str(target.get("valve_tag") or "").strip() == valve_tag
    ]
    if len(matches) != 1:
        raise ValueError(
            "project run manifest must contain one matching target"
        )
    return project_manifest_path, project_manifest, matches[0]


def _update_project_target_stage(
    binding: tuple[Path, dict[str, Any], dict[str, Any]] | None,
    stage: str,
) -> None:
    if binding is None:
        return
    project_manifest_path, project_manifest, target = binding
    target["stage"] = stage
    targets = project_manifest["targets"]
    frozen_target_count = sum(
        1
        for item in targets
        if isinstance(item, dict) and item.get("stage") == "frozen"
    )
    project_manifest["frozen_target_count"] = frozen_target_count
    if frozen_target_count == len(targets):
        project_manifest["stage"] = "frozen"
        project_manifest["frozen_at"] = datetime.now(
            timezone.utc
        ).isoformat()
    else:
        project_manifest["stage"] = "in_progress"
        project_manifest.pop("frozen_at", None)
    _write_json(project_manifest_path, project_manifest)


def _integrated_result_template(
    bundle: dict[str, Any],
    model: str = INTEGRATED_MODEL,
    reasoning_effort: str = INTEGRATED_REASONING_EFFORT,
) -> dict[str, Any]:
    curve_rows: list[dict[str, Any]] = []
    for image in bundle["evidence"]["curve_images"]:
        curve_rows.append(
            {
                "image_sha256": image["image_sha256"],
                "test_key": image["test_key"],
                "observation": "",
                "visible_features": [],
                "visual_checks": {
                    key: "uncertain"
                    for key in (*STEP_VISUAL_CHECKS, *SIGNATURE_VISUAL_CHECKS)
                },
                "needs_review": False,
                "diagnosis": "",
                "rating": None,
                "action": "",
                "confidence": "",
            }
        )
    return {
        "schema_version": 1,
        "valve_tag": bundle["target_valve_tag"],
        "model_provenance": {
            "decision_source": "codex_exec_integrated",
            "model": model,
            "reasoning_effort": reasoning_effort,
            "all_evidence_and_images_in_single_call": True,
            "rating_decided_by": "model",
        },
        "evidence_review": {
            "displayed_data_reviewed": False,
            "structured_evidence_reviewed": False,
            "active_faults_reviewed": False,
            "test_execution_issues_reviewed": False,
        },
        "source_file_reads": source_read_template(bundle),
        "curve_assessments": curve_rows,
        "specification_observations": [
            {
                "image_sha256": image["image_sha256"],
                "physical_identity": str(
                    image.get("physical_identity") or ""
                ),
                "observation": "",
            }
            for image in bundle["evidence"]["specification_images"]
        ],
        "whole_valve": {
            "information_complete": None,
            "final_rating": None,
            "rating_conclusion": "",
            "main_issue": "",
            "issues": [],
            "actions": [],
            "supporting_evidence": [],
            "counter_evidence": [],
            "uncertainties": [],
            "severe_functional_impact": "",
            "functional_impact_evidence": [],
            "independent_pressure_fault_evidence": "",
            "metric_threshold_basis": [],
        },
    }


def _integrated_output_schema(
    bundle: dict[str, Any],
    model: str = INTEGRATED_MODEL,
    reasoning_effort: str = INTEGRATED_REASONING_EFFORT,
) -> dict[str, Any]:
    template = _integrated_result_template(bundle, model, reasoning_effort)

    def string_array() -> dict[str, Any]:
        return {"type": "array", "items": {"type": "string"}}

    visual_properties = {
        key: {
            "type": "string",
            "enum": sorted(VISUAL_CHECK_VALUES),
        }
        for key in (*STEP_VISUAL_CHECKS, *SIGNATURE_VISUAL_CHECKS)
    }
    curve_properties = {
        "image_sha256": {"type": "string"},
        "test_key": {"type": "string"},
        "observation": {"type": "string", "minLength": 1},
        "visible_features": string_array(),
        "visual_checks": {
            "type": "object",
            "properties": visual_properties,
            "required": list(visual_properties),
            "additionalProperties": False,
        },
        "needs_review": {"type": "boolean"},
        "diagnosis": {"type": "string", "minLength": 1},
        "rating": {"type": "integer", "enum": [1, 2, 3, 4, 5]},
        "action": {"type": "string", "minLength": 1},
        "confidence": {
            "type": "string",
            "enum": ["high", "medium", "low"],
        },
    }
    spec_properties = {
        "image_sha256": {"type": "string"},
        "physical_identity": {"type": "string"},
        "observation": {"type": "string", "minLength": 1},
    }
    whole_properties: dict[str, Any] = {}
    for key, value in template["whole_valve"].items():
        if isinstance(value, list):
            whole_properties[key] = string_array()
        elif isinstance(value, bool) or value is None and key == "information_complete":
            whole_properties[key] = {"type": "boolean"}
        elif key == "final_rating":
            whole_properties[key] = {
                "type": "integer",
                "enum": [1, 2, 3, 4, 5],
            }
        else:
            whole_properties[key] = {"type": "string"}
    whole_properties["metric_threshold_basis"] = {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "metric": {"type": "string"},
                "threshold": {"type": "string"},
                "source": {"type": "string"},
            },
            "required": ["metric", "threshold", "source"],
            "additionalProperties": False,
        },
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {
            "schema_version": {"type": "integer", "const": 1},
            "valve_tag": {
                "type": "string",
                "const": bundle["target_valve_tag"],
            },
            "model_provenance": {
                "type": "object",
                "properties": {
                    "decision_source": {
                        "type": "string",
                        "const": "codex_exec_integrated",
                    },
                    "model": {
                        "type": "string",
                        "const": model,
                    },
                    "reasoning_effort": {
                        "type": "string",
                        "const": reasoning_effort,
                    },
                    "all_evidence_and_images_in_single_call": {
                        "type": "boolean",
                        "const": True,
                    },
                    "rating_decided_by": {
                        "type": "string",
                        "const": "model",
                    },
                },
                "required": list(template["model_provenance"]),
                "additionalProperties": False,
            },
            "evidence_review": {
                "type": "object",
                "properties": {
                    key: {"type": "boolean", "const": True}
                    for key in template["evidence_review"]
                },
                "required": list(template["evidence_review"]),
                "additionalProperties": False,
            },
            "source_file_reads": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        key: ({"type": "string", "enum": ["read", "failed", "unread"]}
                              if key == "status" else {"type": "string"})
                        for key in SOURCE_READ_FIELDS
                    },
                    "required": list(SOURCE_READ_FIELDS),
                    "additionalProperties": False,
                },
            },
            "curve_assessments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": curve_properties,
                    "required": list(curve_properties),
                    "additionalProperties": False,
                },
            },
            "specification_observations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": spec_properties,
                    "required": ["image_sha256", "physical_identity", "observation"],
                    "additionalProperties": False,
                },
            },
            "whole_valve": {
                "type": "object",
                "properties": whole_properties,
                "required": list(template["whole_valve"]),
                "additionalProperties": False,
            },
        },
        "required": list(template),
        "additionalProperties": False,
    }


def _version_metadata(
    diagnosis_version: str | None,
    parent_run: str | None,
    revision_reason: str | None,
) -> dict[str, str]:
    metadata = {
        "diagnosis_version": diagnosis_version,
        "parent_run": parent_run,
        "revision_reason": revision_reason,
    }
    for field, value in metadata.items():
        if not str(value or "").strip():
            raise ValueError(f"{field} is required for a new run")
    return {
        field: str(value).strip()
        for field, value in metadata.items()
    }


def _validate_version_file(
    run_dir: Path,
    manifest: dict[str, Any],
) -> None:
    version_path = run_dir / "00_version.json"
    if not version_path.is_file():
        raise ValueError("00_version.json is required for a new run")
    expected = {
        field: manifest.get(field)
        for field in (
            "diagnosis_version",
            "parent_run",
            "revision_reason",
        )
    }
    if _read_json(version_path) != expected:
        raise ValueError(
            "00_version.json does not match run manifest"
        )


def prepare_run(
    target: dict[str, Any],
    experience_text: str,
    run_dir: Path,
    *,
    diagnosis_version: str | None = None,
    parent_run: str | None = None,
    revision_reason: str | None = None,
    report_period: str | None = None,
    model: str = INTEGRATED_MODEL,
    reasoning_effort: str = INTEGRATED_REASONING_EFFORT,
) -> dict[str, Any]:
    _require_new_model_contract(model, reasoning_effort)
    version_metadata = _version_metadata(
        diagnosis_version,
        parent_run,
        revision_reason,
    )
    if run_dir.exists() and any(run_dir.iterdir()):
        raise ValueError("run directory must be empty")
    run_dir.mkdir(parents=True, exist_ok=True)

    bundle = evidence_bundle.build_blind_bundle(target)
    target_evidence = target.get("evidence") or {}
    if target.get("source_locator"):
        bundle["source_locator"] = target["source_locator"]
    for field in (
        "source_files",
        "structured_evidence",
        "displayed_specification",
        "displayed_operating_context",
    ):
        if field in target_evidence:
            bundle["evidence"][field] = target_evidence[field]
    for image, target_image in zip(
        bundle["evidence"]["curve_images"],
        target_evidence.get("embedded_curve_images", []),
        strict=True,
    ):
        image["image_key"] = target_image["image_key"]
    target_specification_images = target_evidence.get(
        "embedded_specification_images",
        [],
    )
    if len(target_specification_images) != len(
        bundle["evidence"]["specification_images"]
    ):
        raise ValueError("formal specification evidence projection is incomplete")
    for image, target_image in zip(
        bundle["evidence"]["specification_images"],
        target_specification_images,
        strict=True,
    ):
        for field in (
            "evidence_kind",
            "roles",
            "source_sha256",
            "page",
            "physical_identity",
            "match_basis",
        ):
            if field in target_image:
                image[field] = target_image[field]
    manifest = {
        "schema_version": 1,
        "workflow": "single_valve_integrated_codex_v3",
        "stage": "awaiting_observation",
        "diagnosis_method_required": "codex_exec_integrated",
        "target_case_id": target["case_id"],
        "target_valve_tag": target["valve_tag"],
        "report_period_key": report_period,
        "worker_model": model,
        "worker_reasoning_effort": reasoning_effort,
        "skill_runtime": runtime_identity(),
        "source_read_contract": SOURCE_READ_CONTRACT,
        **version_metadata,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "common_experience_sha256": hashlib.sha256(experience_text.encode("utf-8")).hexdigest(),
        "next_file": None,
        "next_operation": "prepare-integrated-diagnosis",
    }
    manifest["experience_snapshot"] = "00_experience.md"
    (run_dir / "00_experience.md").write_text(experience_text, encoding="utf-8")
    _run_experience(run_dir, manifest=manifest)
    _write_json(run_dir / "00_version.json", version_metadata)
    _write_json(run_dir / "00_run_manifest.json", manifest)
    _write_json(run_dir / "01_evidence_bundle.json", bundle)
    return manifest


def prepare_project_runs(
    analysis: list[dict[str, Any]],
    *,
    project_root: Path,
    project_id: str,
    experience_text: str,
    run_root: Path,
    diagnosis_version: str | None = None,
    parent_run: str | None = None,
    revision_reason: str | None = None,
    report_period: str | None = None,
    model: str = INTEGRATED_MODEL,
    reasoning_effort: str = INTEGRATED_REASONING_EFFORT,
) -> dict[str, Any]:
    _require_new_model_contract(model, reasoning_effort)
    version_metadata = _version_metadata(
        diagnosis_version,
        parent_run,
        revision_reason,
    )
    folders = [str(item.get("folder") or "").strip() for item in analysis]
    if any(not folder for folder in folders):
        raise ValueError("project target is missing canonical folder tag")
    if len(folders) != len(set(folders)):
        raise ValueError("project target folder tags must be unique")
    project_manifest_path = run_root / "00_project_run_manifest.json"
    if project_manifest_path.exists():
        raise ValueError("project run manifest already exists")
    for folder in folders:
        target_dir = run_root / folder
        if target_dir.exists() and any(target_dir.iterdir()):
            raise ValueError(
                f"project target run directory must be empty: {folder}"
            )

    targets = [
        build_project_target(
            item,
            project_root=project_root,
            project_id=project_id,
        )
        for item in analysis
    ]
    run_root.mkdir(parents=True, exist_ok=True)
    entries = []
    for target in targets:
        target_dir = run_root / target["valve_tag"]
        manifest = prepare_run(
            target,
            experience_text,
            target_dir,
            **version_metadata,
            report_period=report_period,
            model=model,
            reasoning_effort=reasoning_effort,
        )
        entries.append(
            {
                "valve_tag": target["valve_tag"],
                "target_case_id": target["case_id"],
                "stage": manifest["stage"],
                "curve_image_count": len(
                    target["evidence"]["embedded_curve_images"]
                ),
                "specification_image_count": len(
                    target["evidence"][
                        "embedded_specification_images"
                    ]
                ),
                "run_dir": str(target_dir.resolve()),
            }
        )
    project_manifest = {
        "schema_version": 1,
        "workflow": "single_valve_integrated_codex_project_v3",
        "stage": "in_progress",
        "project_id": project_id,
        "project_root": str(project_root.resolve()),
        "report_period_key": report_period,
        "worker_model": model,
        "worker_reasoning_effort": reasoning_effort,
        **version_metadata,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "target_count": len(entries),
        "frozen_target_count": 0,
        "curve_image_count": sum(
            item["curve_image_count"] for item in entries
        ),
        "specification_image_count": sum(
            item["specification_image_count"] for item in entries
        ),
        "zero_curve_targets": [
            item["valve_tag"]
            for item in entries
            if item["curve_image_count"] == 0
        ],
        "targets": entries,
    }
    _write_json(project_manifest_path, project_manifest)
    return project_manifest


def _validate_observations(
    bundle: dict[str, Any],
    observations: dict[str, Any],
) -> None:
    expected_curves = Counter(
        (image["image_sha256"], image["test_key"])
        for image in bundle["evidence"]["curve_images"]
    )
    submitted_curves = observations.get("curve_observations")
    if not isinstance(submitted_curves, list):
        raise ValueError("curve observations are required")
    submitted_curve_entries = Counter(
        (item.get("image_sha256"), item.get("test_key"))
        for item in submitted_curves
    )
    if submitted_curve_entries != expected_curves:
        raise ValueError("curve observations must cover every curve image")
    allowed_curve_fields = {
        "image_sha256",
        "test_key",
        "observation",
        "visual_checks",
    }
    for item in submitted_curves:
        if set(item) - allowed_curve_fields:
            raise ValueError(
                "curve entries must contain objective observation only"
            )
        if not str(item.get("observation") or "").strip():
            raise ValueError("curve observations cannot be empty")
        test_key = item.get("test_key")
        if test_key in SIGNATURE_TEST_KEYS:
            expected_visual_checks = SIGNATURE_VISUAL_CHECKS
            check_label = "signature"
        elif test_key in STEP_TEST_KEYS:
            expected_visual_checks = STEP_VISUAL_CHECKS
            check_label = "step"
        else:
            expected_visual_checks = ()
            check_label = ""
        if expected_visual_checks:
            visual_checks = item.get("visual_checks")
            if not isinstance(visual_checks, dict):
                raise ValueError(
                    f"{check_label} visual checks are required"
                )
            if set(visual_checks) != set(expected_visual_checks):
                raise ValueError(
                    f"{check_label} visual checks are incomplete"
                )
            if any(
                value not in VISUAL_CHECK_VALUES
                for value in visual_checks.values()
            ):
                raise ValueError(
                    f"{check_label} visual checks must be "
                    "present, absent, or uncertain"
                )
        elif "visual_checks" in item:
            raise ValueError(
                "visual checks are only allowed for step "
                "or signature curves"
            )

    expected_specifications = Counter(
        image.get("physical_identity") or image["image_sha256"]
        for image in bundle["evidence"]["specification_images"]
    )
    if "reviewed_specification_physical_identities" in observations:
        reviewed_specifications = set(
            observations.get(
                "reviewed_specification_physical_identities"
            )
            or []
        )
    else:
        identities_by_image_sha256: dict[str, set[str]] = {}
        for image in bundle["evidence"]["specification_images"]:
            identities_by_image_sha256.setdefault(
                image["image_sha256"],
                set(),
            ).add(
                image.get("physical_identity")
                or image["image_sha256"]
            )
        reviewed_specifications = set()
        for image_sha256 in (
            observations.get("reviewed_specification_image_sha256s")
            or []
        ):
            mapped = identities_by_image_sha256.get(image_sha256)
            if not mapped:
                raise ValueError(
                    "legacy specification review contains an unknown image SHA-256"
                )
            if len(mapped) != 1:
                raise ValueError(
                    "legacy specification image SHA-256 maps to multiple physical pages"
                )
            reviewed_specifications.update(mapped)
    if reviewed_specifications != set(expected_specifications):
        raise ValueError(
            "specification evidence review is incomplete"
        )
    specification_observations = observations.get(
        "specification_observations"
    )
    if not isinstance(specification_observations, list):
        raise ValueError("specification observations are required")
    submitted_specification_hashes = Counter(
        item.get("physical_identity") or item.get("image_sha256")
        for item in specification_observations
    )
    if submitted_specification_hashes != expected_specifications:
        raise ValueError(
            "specification observations must cover every image"
        )
    for item in specification_observations:
        if set(item) not in (
            {"image_sha256", "observation"},
            {"image_sha256", "physical_identity", "observation"},
        ):
            raise ValueError(
                "specification entries must contain observation only"
            )
        if not str(item.get("observation") or "").strip():
            raise ValueError(
                "specification observations cannot be empty"
            )

    for field in (
        "displayed_data_reviewed",
        "active_faults_reviewed",
        "test_execution_issues_reviewed",
    ):
        if observations.get(field) is not True:
            raise ValueError(f"{field} must be true")
    if (
        bundle["evidence"].get("structured_evidence")
        and observations.get("structured_evidence_reviewed") is not True
    ):
        raise ValueError("structured_evidence_reviewed must be true")
    structured_evidence = bundle["evidence"].get("structured_evidence") or {}
    if structured_evidence:
        expected_accounting = structured_evidence_accounting(
            structured_evidence
        )
        submitted_accounting = observations.get("evidence_accounting")
        if submitted_accounting != expected_accounting:
            raise ValueError(
                "evidence_accounting must match structured_evidence item by item"
            )


def _double_acting_evidence(
    bundle: dict[str, Any],
    observations: dict[str, Any],
) -> bool:
    evidence_text = "\n".join(
        [
            *(
                str(value)
                for value in (
                    bundle.get("evidence", {})
                    .get("displayed_data", {})
                    .values()
                )
            ),
            *(
                str(item.get("observation") or "")
                for item in observations.get(
                    "specification_observations",
                    [],
                )
            ),
        ]
    ).lower()
    return any(
        phrase in evidence_text
        for phrase in (
            "double acting",
            "double-acting",
            "双作用",
            "双动",
        )
    )


def _canonical_task_test_key(row: dict[str, Any]) -> str:
    explicit = re.sub(
        r"[^a-z0-9]+",
        "_",
        str(row.get("test_key") or "").casefold(),
    ).strip("_")
    task_name = " ".join(
        str(row.get("task_name") or "").casefold().split()
    )
    combined = f"{explicit} {task_name}"
    for phrases, test_key in (
        (("extended actuator signature", "extended_signature"), "extended_signature"),
        (("standard signature", "standard_signature"), "standard_signature"),
        (("performance check", "performance_check"), "performance_check"),
        (("pattern step", "pattern_step"), "pattern_step"),
        (("run step", "run_step"), "run_step"),
        (("run ramp", "run_ramp"), "run_ramp"),
        (("change mode", "change_mode"), "change_mode"),
    ):
        if any(phrase in combined for phrase in phrases):
            return test_key
    return explicit


def _task_result(row: dict[str, Any]) -> str:
    return re.sub(
        r"[^a-z0-9]+",
        "_",
        str(row.get("result") or row.get("status") or "").casefold(),
    ).strip("_")


def _task_execution_rows(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    structured = (
        bundle.get("evidence", {}).get("structured_evidence") or {}
    )
    rows = evidence_nested_value(
        structured,
        ("source_record", "positioner_report_info", "status", "task_execution"),
        ("source_record", "positioner_report_info", "task_execution"),
        ("source_record", "status", "task_execution"),
        ("source_record", "task_execution"),
    )
    return [row for row in rows or [] if isinstance(row, dict)]


def _step_and_ramp_evidence_precludes_level_five(
    bundle: dict[str, Any],
) -> bool:
    """Return true when only performance/procedural gaps remain.

    Successful step and ramp tests provide independent functional evidence.  If
    neither basic action nor another diagnostic family failed, a missing
    actuator signature remains a disclosed gap but cannot make the whole valve
    impact unknown.
    """
    structured = (
        bundle.get("evidence", {}).get("structured_evidence") or {}
    )
    source_record = structured.get("source_record") or {}
    if source_record.get("basic_action_failed") is True:
        return False

    rows = _task_execution_rows(bundle)
    succeeded_keys = {
        _canonical_task_test_key(row)
        for row in rows
        if _task_result(row) in _SUCCESS_TASK_RESULTS
    }
    if not (succeeded_keys & STEP_TEST_KEYS):
        return False
    if "run_ramp" not in succeeded_keys:
        return False

    for row in rows:
        if _task_result(row) not in _NON_SUCCESS_TASK_RESULTS:
            continue
        test_key = _canonical_task_test_key(row)
        if test_key in ACTUATOR_PERFORMANCE_TEST_KEYS:
            continue
        if test_key == "change_mode":
            continue
        return False
    return True


def _diagnostic_guardrails(
    bundle: dict[str, Any],
    observations: dict[str, Any],
) -> list[dict[str, str]]:
    guardrails = [
        {
            "id": "experience-evidence-only",
            "rule": (
                "经验提供判断方法；其他阀门的数字等级不得通过计数、"
                "平均或多数投票覆盖本阀当前证据评级。"
            ),
        }
    ]
    signature_checks = [
        item.get("visual_checks") or {}
        for item in observations.get("curve_observations", [])
        if item.get("test_key") in SIGNATURE_TEST_KEYS
    ]
    if any(
        checks.get("local_jaggedness_or_discontinuity")
        == "present"
        for checks in signature_checks
    ):
        guardrails.append(
            {
                "id": "signature-local-irregularity",
                "rule": (
                    "签名已确认局部锯齿或不连续，逐图诊断必须明确"
                    "记录该现象，不能只用总体摩擦数值代替。"
                ),
            }
        )
    if any(
        checks.get("endpoint_folding_or_jump") == "present"
        for checks in signature_checks
    ):
        guardrails.append(
            {
                "id": "signature-endpoint-irregularity",
                "rule": (
                    "签名已确认端点回折或突跳，逐图诊断必须明确"
                    "记录端点现象并与快速行程证据对照。"
                ),
            }
        )
    step_checks = [
        item.get("visual_checks") or {}
        for item in observations.get("curve_observations", [])
        if item.get("test_key") in STEP_TEST_KEYS
    ]
    for check, guardrail_id, rule in (
        (
            "opening_overshoot_or_undershoot",
            "step-opening-transient",
            "开向阶跃已确认过冲或下冲，逐图诊断必须记录开向瞬态。",
        ),
        (
            "closing_overshoot_or_undershoot",
            "step-closing-transient",
            "关向阶跃已确认过冲或下冲，逐图诊断必须记录关向瞬态。",
        ),
        (
            "directional_response_delay",
            "step-directional-delay",
            "阶跃已确认方向性响应迟滞，逐图诊断必须记录迟滞方向。",
        ),
        (
            "endpoint_residual_or_travel_loss",
            "step-endpoint-residual",
            "阶跃已确认端点残差或行程不足，逐图诊断必须记录端点现象。",
        ),
    ):
        if any(
            checks.get(check) == "present"
            for checks in step_checks
        ):
            guardrails.append({"id": guardrail_id, "rule": rule})
    if (
        _double_acting_evidence(bundle, observations)
        and any(
            checks.get("negative_pressure_axis") == "present"
            for checks in signature_checks
        )
    ):
        guardrails.append(
            {
                "id": "double-acting-negative-pressure-axis",
                "rule": (
                    "双作用执行机构签名的负压力坐标可表示方向性"
                    "压差或通道组合；负值本身不能证明压力零点、"
                    "压力通道或数据可信度异常。"
                ),
            }
        )
    if _step_and_ramp_evidence_precludes_level_five(bundle):
        guardrails.append(
            {
                "id": "performance-gap-not-level-five",
                "rule": (
                    "阶跃和斜坡已成功且无基本动作失败时，"
                    "执行机构性能测试缺失或失败只作为未测项，"
                    "不得单独判5级；应按其余证据评级并保留相关判断限制，是否补测取决于关键判断需要及现场条件。"
                ),
            }
        )
    return guardrails


def _validate_diagnostic_guardrails(
    bundle: dict[str, Any],
    observations: dict[str, Any],
    diagnosis: dict[str, Any],
) -> None:
    integrated = (
        (diagnosis.get("model_provenance") or {}).get("decision_source")
        == "codex_exec_integrated"
    )

    def checked_text(
        observation: dict[str, Any],
        matching: list[dict[str, Any]],
    ) -> str:
        parts = [str(item.get("diagnosis") or "") for item in matching]
        if integrated:
            parts.insert(0, str(observation.get("observation") or ""))
        return " ".join(parts)

    guardrail_ids = {
        item["id"]
        for item in _diagnostic_guardrails(bundle, observations)
    }
    interpretations = diagnosis.get("curve_interpretations") or []
    # As for step curves below, integrated judgments do not require keywords.
    if not integrated and "signature-local-irregularity" in guardrail_ids:
        for observation in observations["curve_observations"]:
            checks = observation.get("visual_checks") or {}
            if (
                checks.get("local_jaggedness_or_discontinuity")
                != "present"
            ):
                continue
            matching = [
                item
                for item in interpretations
                if item.get("image_sha256")
                == observation.get("image_sha256")
                and item.get("test_key")
                == observation.get("test_key")
            ]
            text = checked_text(observation, matching)
            if not any(
                phrase in text
                for phrase in (
                    "锯齿",
                    "不连续",
                    "粘滑",
                    "卡涩",
                    "摩擦",
                )
            ):
                raise ValueError(
                    "signature diagnosis must record local irregularity"
                )
    if not integrated and "signature-endpoint-irregularity" in guardrail_ids:
        for observation in observations["curve_observations"]:
            checks = observation.get("visual_checks") or {}
            if checks.get("endpoint_folding_or_jump") != "present":
                continue
            matching = [
                item
                for item in interpretations
                if item.get("image_sha256")
                == observation.get("image_sha256")
                and item.get("test_key")
                == observation.get("test_key")
            ]
            text = checked_text(observation, matching)
            if not any(
                phrase in text
                for phrase in (
                    "端点",
                    "高端",
                    "低端",
                    "回折",
                    "突跳",
                    "止挡",
                )
            ):
                raise ValueError(
                    "signature diagnosis must record endpoint irregularity"
                )
    step_requirements = (
        (
            "step-opening-transient",
            "opening_overshoot_or_undershoot",
            ("开向", "开阀", "0→100", "0%→100%"),
            ("过冲", "下冲", "欠调", "欠跟踪", "突跳", "回跳", "回弹"),
            "opening step transient",
        ),
        (
            "step-closing-transient",
            "closing_overshoot_or_undershoot",
            ("关向", "关阀", "下降", "100→0", "100%→0%"),
            ("过冲", "下冲", "欠调", "欠跟踪", "突跳", "回跳", "回弹"),
            "closing step transient",
        ),
        (
            "step-directional-delay",
            "directional_response_delay",
            ("开向", "开阀", "关向", "关阀", "方向"),
            ("迟滞", "偏慢", "拖尾", "响应慢"),
            "directional step delay",
        ),
        (
            "step-endpoint-residual",
            "endpoint_residual_or_travel_loss",
            ("端点", "全开", "全关", "零位", "行程"),
            ("残差", "偏差", "不足", "不到位", "丢失"),
            "step endpoint residual",
        ),
    )
    for guardrail_id, check, directions, phenomena, error in (
        step_requirements
    ):
        if guardrail_id not in guardrail_ids:
            continue
        for observation in observations["curve_observations"]:
            if integrated:
                # One-call diagnoses already bind the model's observation,
                # visual flags and engineering text in one immutable result.
                # Legacy keyword matching must not rewrite or second-guess
                # that model judgment.
                continue
            if (
                (observation.get("visual_checks") or {}).get(check)
                != "present"
            ):
                continue
            matching = [
                item
                for item in interpretations
                if item.get("image_sha256")
                == observation.get("image_sha256")
                and item.get("test_key")
                == observation.get("test_key")
            ]
            text = checked_text(observation, matching)
            if not (
                any(phrase in text for phrase in directions)
                and any(phrase in text for phrase in phenomena)
            ):
                raise ValueError(
                    f"diagnosis must record {error}"
                )
    whole = diagnosis.get("whole_valve") or {}
    diagnosis_text = "\n".join(
        [
            *(
                str(item.get("diagnosis") or "")
                for item in interpretations
            ),
            str(whole.get("rating_conclusion") or ""),
            str(whole.get("main_issue") or ""),
            *(
                str(value)
                for value in whole.get("issues") or []
            ),
        ]
    )
    if any(
        phrase in diagnosis_text
        for phrase in (
            "候选案例均为",
            "多数案例为",
            "案例评级平均",
            "按案例投票",
        )
    ):
        raise ValueError(
            "candidate ratings are reference only and cannot be voted"
        )
    if (
        "double-acting-negative-pressure-axis" in guardrail_ids
        and any(
            phrase in diagnosis_text
            for phrase in (
                "压力零点异常",
                "压力零点错误",
                "压力通道异常",
                "压力数据不可信",
                "压力数据需复核",
            )
        )
        and not str(
            whole.get("independent_pressure_fault_evidence") or ""
        ).strip()
    ):
        raise ValueError(
            "independent pressure fault evidence is required"
        )


def _current_diagnosis_inputs(run_dir: Path) -> dict[str, str]:
    bundle_path = run_dir / "01_evidence_bundle.json"
    bundle = _read_json(bundle_path)
    inputs = {
        "evidence_semantic_sha256": diagnostic_evidence_sha256(bundle),
        "evidence_file_sha256": _sha256_file(bundle_path),
        "observations_sha256": _sha256_file(run_dir / "03_observations.json"),
    }
    manifest = _read_json(run_dir / "00_run_manifest.json")
    if manifest.get("experience_snapshot") == "00_experience.md":
        inputs["experience_snapshot_sha256"] = _sha256_file(run_dir / "00_experience.md")
    else:  # Historical frozen inputs keep their original contract.
        for key, name in (("retrieved_cases_sha256", "02_retrieved_cases.json"),
                          ("retrieved_rules_sha256", "04_retrieved_rules.json"),
                          ("experience_snapshot_sha256", "00_experience_snapshot.json")):
            if key != "experience_snapshot_sha256" or (run_dir / name).is_file():
                inputs[key] = _sha256_file(run_dir / name)
    return inputs


def _diagnosis_basis(run_dir: Path) -> dict[str, str]:
    return {
        "mode": "fresh_reanalysis",
        "evidence_contract_version": str(EVIDENCE_ACCOUNTING_VERSION),
        **_current_diagnosis_inputs(run_dir),
    }


def _retrieved_case_candidates(run_dir: Path) -> list[dict[str, Any]]:
    payload = _read_json(run_dir / "02_retrieved_cases.json")
    candidates = payload.get("retrieved_cases")
    if not isinstance(candidates, list):
        raise ValueError("retrieved case candidates are invalid")
    return candidates


def _assert_inheritable_inputs(
    parent_run_dir: Path,
    child_run_dir: Path,
    *,
    allow_legacy_parent: bool = False,
    allow_source_file_binding: bool = False,
) -> None:
    for ancestor in child_run_dir.resolve().parents:
        if ancestor.name != "work":
            continue
        approval_path = ancestor / "stage-gates/materials-approval.json"
        if approval_path.is_file():
            approval = _read_json(approval_path)
            if approval.get("unchanged_reuse_policy") == "reanalyze_all":
                raise ValueError(
                    "Stage 1 reuse policy requires full reanalysis; "
                    "frozen diagnosis inheritance is forbidden"
                )
        break
    parent_manifest = _read_json(
        parent_run_dir / "00_run_manifest.json"
    )
    child_manifest = _read_json(
        child_run_dir / "00_run_manifest.json"
    )
    if (
        parent_manifest.get("stage") != "frozen"
        or not _verify_freeze(
            parent_run_dir,
            require_versioned=True,
        )
    ):
        raise ValueError("parent diagnosis must be a valid frozen run")
    parent_diagnosis = _read_json(
        parent_run_dir / "05_diagnosis.json"
    )
    validate_source_read_acceptance(
        _read_json(parent_run_dir / "01_evidence_bundle.json"),
        _read_json(parent_run_dir / "03_observations.json"),
        parent_diagnosis, parent_manifest,
    )
    if (
        not isinstance(parent_diagnosis.get("diagnosis_basis"), dict)
        and not allow_legacy_parent
    ):
        raise ValueError(
            "parent diagnosis is not input-bound; "
            "explicit legacy migration is required"
        )
    if child_manifest.get("stage") == "frozen":
        if not _verify_freeze(child_run_dir, require_versioned=True):
            raise ValueError("child diagnosis must be a valid frozen run")
    elif child_manifest.get("stage") != "awaiting_diagnosis":
        raise ValueError("child run is not awaiting diagnosis")
    if (
        parent_manifest.get("target_valve_tag")
        != child_manifest.get("target_valve_tag")
    ):
        raise ValueError("parent and child valve tags do not match")

    parent_bundle = _read_json(
        parent_run_dir / "01_evidence_bundle.json"
    )
    child_bundle = _read_json(
        child_run_dir / "01_evidence_bundle.json"
    )
    # Fresh inheritance targets must already satisfy the new read contract;
    # copying an old observation file must not create a newly accepted result.
    if child_manifest.get("stage") != "frozen":
        if child_manifest.get("source_read_contract") != SOURCE_READ_CONTRACT:
            raise ValueError("inheritance target requires the current source_read_contract")
        child_observations = _read_json(child_run_dir / "03_observations.json")
        if child_observations.get("source_read_contract") != SOURCE_READ_CONTRACT:
            raise ValueError("inheritance target requires source read declarations")
        validate_source_reads(child_bundle, child_observations.get("source_file_reads"))
    if diagnostic_evidence_sha256(
        parent_bundle
    ) != diagnostic_evidence_sha256(child_bundle):
        parent_sources = (
            parent_bundle.get("evidence", {}).get("source_files")
        )
        child_sources = (
            child_bundle.get("evidence", {}).get("source_files")
        )
        source_binding_only = (
            allow_source_file_binding
            and not parent_sources
            and isinstance(child_sources, list)
            and bool(child_sources)
            and _diagnostic_evidence_without_source_files_sha256(
                parent_bundle
            )
            == _diagnostic_evidence_without_source_files_sha256(
                child_bundle
            )
        )
        if not source_binding_only:
            raise ValueError(
                "complete diagnostic evidence changed; "
                "a fresh diagnosis is required"
            )
    if _read_json(
        parent_run_dir / "03_observations.json"
    ) != _read_json(child_run_dir / "03_observations.json"):
        raise ValueError(
            "objective observations changed; "
            "a fresh diagnosis is required"
        )
    if parent_manifest.get("experience_snapshot") != child_manifest.get("experience_snapshot"):
        raise ValueError("experience input contract changed; a fresh diagnosis is required")
    if child_manifest.get("experience_snapshot") == "00_experience.md":
        if _run_experience(parent_run_dir) != _run_experience(child_run_dir):
            raise ValueError("common experience changed; a fresh diagnosis is required")
    else:
        if _retrieved_case_candidates(parent_run_dir) != _retrieved_case_candidates(child_run_dir):
            raise ValueError("retrieved cases changed; a fresh diagnosis is required")
        if _read_json(parent_run_dir / "04_retrieved_rules.json") != _read_json(child_run_dir / "04_retrieved_rules.json"):
            raise ValueError("retrieved rules changed; a fresh diagnosis is required")
        if parent_manifest.get("common_experience_sha256") != child_manifest.get("common_experience_sha256"):
            raise ValueError("common experience changed; a fresh diagnosis is required")


def _inherited_diagnosis_basis(
    parent_run_dir: Path,
    child_run_dir: Path,
    *,
    allow_legacy_parent: bool = False,
    legacy_parent_reason: str | None = None,
    allow_source_file_binding: bool = False,
    source_binding_reason: str | None = None,
) -> dict[str, str]:
    _assert_inheritable_inputs(
        parent_run_dir,
        child_run_dir,
        allow_legacy_parent=allow_legacy_parent,
        allow_source_file_binding=allow_source_file_binding,
    )
    basis = {
        "mode": (
            "inherited_source_bound"
            if allow_source_file_binding
            else "inherited_legacy_frozen"
            if allow_legacy_parent
            else "inherited_frozen"
        ),
        **_current_diagnosis_inputs(child_run_dir),
        "parent_run_dir": str(parent_run_dir.resolve()),
        "parent_diagnosis_sha256": _sha256_file(
            parent_run_dir / "05_diagnosis.json"
        ),
        "parent_freeze_sha256": _sha256_file(
            parent_run_dir / "06_freeze.sha256"
        ),
    }
    if allow_legacy_parent:
        reason = str(legacy_parent_reason or "").strip()
        if not reason:
            raise ValueError(
                "legacy_parent_reason is required for legacy inheritance"
            )
        basis["legacy_parent_reason"] = reason
    if allow_source_file_binding:
        reason = str(source_binding_reason or "").strip()
        if not reason:
            raise ValueError(
                "source_binding_reason is required for source binding"
            )
        basis["source_binding_reason"] = reason
    return basis


def inherit_frozen_diagnosis(
    parent_run_dir: Path,
    child_run_dir: Path,
    *,
    allow_legacy_parent: bool = False,
    legacy_parent_reason: str | None = None,
    allow_source_file_binding: bool = False,
    source_binding_reason: str | None = None,
) -> dict[str, Any]:
    """Carry a diagnosis forward only when every diagnostic input is equal."""
    if _read_json(child_run_dir / "00_run_manifest.json").get("stage") != "awaiting_diagnosis":
        raise ValueError("child run is not awaiting diagnosis")
    basis = _inherited_diagnosis_basis(
        parent_run_dir,
        child_run_dir,
        allow_legacy_parent=allow_legacy_parent,
        legacy_parent_reason=legacy_parent_reason,
        allow_source_file_binding=allow_source_file_binding,
        source_binding_reason=source_binding_reason,
    )
    diagnosis = deepcopy(
        _read_json(parent_run_dir / "05_diagnosis.json")
    )
    diagnosis["diagnosis_basis"] = basis
    diagnosis["algorithm_audit_contract"] = ALGORITHM_AUDIT_CONTRACT
    if isinstance(diagnosis.get("algorithm_audit_snapshot"), dict):
        diagnosis["algorithm_audit_status"] = "available"
    else:
        validate_legacy_algorithm_audit_parent(parent_run_dir)
        diagnosis["algorithm_audit_status"] = "legacy_unavailable"
        diagnosis["algorithm_audit_snapshot"] = None
    child_manifest_path = child_run_dir / "00_run_manifest.json"
    child_manifest = _read_json(child_manifest_path)
    child_manifest["algorithm_audit_contract"] = ALGORITHM_AUDIT_CONTRACT
    _write_json(child_manifest_path, child_manifest)
    return diagnosis


def _validate_diagnosis_basis(
    run_dir: Path,
    diagnosis: dict[str, Any],
) -> None:
    basis = diagnosis.get("diagnosis_basis")
    if not isinstance(basis, dict):
        raise ValueError(
            "diagnosis_basis does not match current diagnostic inputs"
        )
    mode = basis.get("mode")
    if mode == "fresh_reanalysis":
        expected = _diagnosis_basis(run_dir)
    elif mode in {
        "inherited_frozen",
        "inherited_legacy_frozen",
        "inherited_source_bound",
    }:
        parent_value = basis.get("parent_run_dir")
        if not isinstance(parent_value, str) or not parent_value.strip():
            raise ValueError("diagnosis_basis requires parent_run_dir")
        expected = _inherited_diagnosis_basis(
            Path(parent_value),
            run_dir,
            allow_legacy_parent=(
                mode == "inherited_legacy_frozen"
            ),
            legacy_parent_reason=basis.get(
                "legacy_parent_reason"
            ),
            allow_source_file_binding=(
                mode == "inherited_source_bound"
            ),
            source_binding_reason=basis.get(
                "source_binding_reason"
            ),
        )
    else:
        raise ValueError("diagnosis_basis mode is invalid")
    if basis != expected:
        raise ValueError(
            "diagnosis_basis does not match current diagnostic inputs"
        )


def _human_writing_reference() -> dict[str, Any]:
    return {
        "priority": "面向现场读者，保留证据和判断边界",
        "rules": list(HUMAN_WRITING_RULES),
    }


def _run_experience(run_dir: Path, *, manifest: dict[str, Any] | None = None) -> str:
    manifest = manifest if manifest is not None else _read_json(run_dir / "00_run_manifest.json")
    if manifest.get("experience_snapshot") != "00_experience.md":
        raise ValueError("Historical runs are read-only; prepare a new diagnosis with the full experience text")
    path = run_dir / "00_experience.md"
    text = load_offline_experience(path)
    if _sha256_file(path) != manifest.get("common_experience_sha256"):
        raise ValueError("experience snapshot changed after preparation")
    return text


def prepare_integrated_model_request(
    run_dir: Path,
) -> dict[str, Any]:
    """Prepare one Codex call containing all evidence and all valve images."""
    run_dir = run_dir.resolve()
    manifest_path = run_dir / "00_run_manifest.json"
    manifest = _read_json(manifest_path)
    if manifest.get("stage") != "awaiting_observation":
        raise ValueError("run is not awaiting integrated model diagnosis")
    model, reasoning_effort = _run_model_contract(manifest)
    _validate_version_file(run_dir, manifest)
    experience_text = _run_experience(run_dir)
    paths = {
        "request": run_dir / INTEGRATED_REQUEST_FILENAME,
        "template": run_dir / INTEGRATED_TEMPLATE_FILENAME,
        "schema": run_dir / INTEGRATED_SCHEMA_FILENAME,
        "prompt": run_dir / INTEGRATED_PROMPT_FILENAME,
    }
    if any(path.exists() for path in paths.values()):
        raise ValueError("integrated model request already exists")

    bundle = _read_json(run_dir / "01_evidence_bundle.json")
    evidence = deepcopy(bundle["evidence"])
    state_first = {
        key: evidence[key]
        for key in ("test_execution_issues", "active_faults", "displayed_operating_context")
        if key in evidence
    }
    state_first.update(evidence)
    request = {
        "schema_version": 1,
        "workflow": "single_valve_integrated_codex_v3",
        "valve_tag": bundle["target_valve_tag"],
        "report_period_key": manifest.get("report_period_key"),
        "model_contract": {
            "model": model,
            "reasoning_effort": reasoning_effort,
        },
        "source_read_contract": SOURCE_READ_CONTRACT,
        "rating_source": "model_evidence_synthesis_only",
        "legacy_ratings_allowed": False,
        "experience_text": experience_text,
        "source_locator": deepcopy(bundle.get("source_locator") or {}),
        "report_writing": list(HUMAN_WRITING_RULES),
        "rating_contract": {
            "level_1": "当前有效证据未见需处理的异常，主要功能及已验证性能正常。",
            "level_2": "存在有限偏差或待核实的局部现象，尚无充分证据证明功能受损或需要纠正性维护，可观察或针对性复核。",
            "level_3": (
                "有具体证据支持需要纠正的功能或性能缺陷，主要功能仍可完成。"
                "应说明为什么仅观察或复核已不足，不能用自写的‘需计划处理’反过来证明存在缺陷。"
            ),
            "level_4": (
                "存在拒动、方向性失效、无法到位、严重跟踪失真"
                "或等价的严重功能影响证据。单一数值不得自动判4级。"
            ),
            "level_5": "整阀有效证据整体不足，无法判断影响。",
        },
        "required_reasoning": [
            "完整阅读通用经验正文，结合本阀全部原始资料、规格、工况、测试状态、曲线及反证自行判断。",
            "经验只提供判断方法；客户事实、评级、单阀数值不得直接迁移。",
            (
                "逐项区分成功、失败、中止和不完整测试。退出测试或模式切换失败涉及最终运行模式、"
                "控制权或投用条件时，在结论或行动中保留当前状态的证据、未知项和恢复验证要求；"
                "不能因动作曲线正常或活动故障为空而省略。"
            ),
            (
                "局部缺测或阈值依据缺失不阻断其他有效诊断。"
                "在 uncertainties 写清缺口、影响范围和不能确认的判断，"
                "保留有独立证据支持的结论；只在整阀有效证据整体不足时判数据不足。"
                "影响报告结论的限制在 rating_conclusion 简短点明，具体缺口留在 uncertainties，补测要求写入 actions。"
                "不得把缺少依据写成已确认超限，也不得自动把已有异常降为正常。"
            ),
            "对每张曲线分开输出客观观察与工程判读。",
            (
                "needs_review 只表示图像缺失、无法辨认、归属错误或具体歧义"
                "使当前判读不可靠，不表示设备需要检修。能形成明确观察、"
                "工程判读和评级时必须为 false，即使设备存在缺陷并需检修。"
            ),
            "整阀结论必须列出具体支持证据和具体反证据。",
            (
                "区分观察到的偏差、已证实的功能影响与维护必要性，在 supporting_evidence 中完整说明关联；"
                "rating_conclusion 和 actions 只保留简短结论与对应行动。反复可见只证明现象可重复，"
                "不自动证明超出用途要求；证据不足以确认影响时保留条件，"
                "也不得忽略有独立证据的真实功能缺陷。"
            ),
            (
                "使用标准判断合格、满足要求、超限、偏高或偏慢时，metric_threshold_basis "
                "须给出同一指标、方向、测量起止点和单位下适用的阈值及来源；结论强度不得超过证据。"
                "T86、T63、Td、Average Error、Maximum Hysteresis、Dead Band、Linearity、"
                "Max Friction、Overshoot 使用上述完全相同的规范名称；完整行程时间单独写明，"
                "不得借用响应阶段指标证明完整到位或密封。没有适用依据时将限制写入 uncertainties，"
                "不以‘阈值未知’填满阈值清单，也不据此宣称合格或超限。"
            ),
            (
                "提交前核对既有输出的一致性：判断依据是否支持处置强度，引用指标与标准是否同口径，"
                "关键运行背景和实际配置是否影响建议，主要问题及行动是否有重点且无重复。"
                "直接完善最终正文，不增加经验采用清单、自评分或额外审查字段。"
            ),
            "禁止读取或继承项目旧报告、旧诊断、旧评级、旧结论、analysis_summary.final_rating 或任何自动评分。",
            "单张读图与整阀综合诊断必须在当前同一次调用内完成。",
        ],
        "evidence": state_first,
    }
    template = _integrated_result_template(bundle, model, reasoning_effort)
    schema = _integrated_output_schema(bundle, model, reasoning_effort)
    prompt = (
        f"# {bundle['target_valve_tag']} 单阀一体化离线诊断\n\n"
        "当前进程就是本阀唯一一次 codex exec 模型调用；"
        "禁止再启动或嵌套调用 codex exec。\n\n"
        f"当前调用必须使用 {model}、"
        f"reasoning_effort={reasoning_effort}；不得使用 Luna。\n\n"
        "本提示下文已完整包含本阀结构化证据、判断要求和通用经验正文；"
        "直接完整阅读下文，无需再读取请求 JSON 或经验副本中的相同内容。"
        "必须实际查看"
        "本次 codex exec 附加的该阀全部曲线及技术页图像。"
        "同时读取 evidence.source_files 列出的全部原始资料；相对路径以 source_locator.project_root 为根。"
        "逐原件填写 source_file_reads，保留 relative_path、sha256、status、实际 read_method、"
        "同次调用工具轨迹 read_reference 及 failure_reason。未读或失败必须如实填 unread/failed 和原因，"
        "不得把外层文件探针、图片或转录当作本次原件读取成功。读取失败不能提交为待审诊断，"
        "但保留完整失败输出与计量日志；不得为通过检查伪造读取记录。"
        "这些声明只供来源/覆盖核对，不能代替实际工具轨迹与工程内容核验。"
        "不得抽样、截断或按测试标签筛选经验；无需填写经验采用编号或未采用原因。\n\n"
        f"输出必须符合 `{paths['schema']}`，可参考"
        f" `{paths['template']}` 的字段布局。\n\n"
        "以下是本阀完整结构化证据与判断要求（原始文件仍须按上述位置读取）：\n\n"
        + json.dumps({key: value for key, value in request.items() if key != "experience_text"}, ensure_ascii=False, separators=(",", ":"))
        + "\n\n以下是当次完整加载的通用经验正文：\n\n"
        + experience_text
    )
    _write_json(paths["request"], request)
    _write_json(paths["template"], template)
    _write_json(paths["schema"], schema)
    paths["prompt"].write_text(prompt, encoding="utf-8")
    project_value = (bundle.get("source_locator") or {}).get("project_root")
    if project_value and (Path(project_value) / "project.toml").is_file():
        from run_metered_codex import build_diagnosis_allowlist

        allowlist = build_diagnosis_allowlist(run_dir, Path(project_value))
        _write_json(run_dir / "allowed_reads.json", allowlist)
    manifest["workflow"] = "single_valve_integrated_codex_v3"
    manifest["diagnosis_method_required"] = "codex_exec_integrated"
    manifest["algorithm_audit_contract"] = ALGORITHM_AUDIT_CONTRACT
    manifest["source_read_contract"] = SOURCE_READ_CONTRACT
    manifest["next_file"] = INTEGRATED_TEMPLATE_FILENAME
    manifest.pop("next_operation", None)
    _write_json(manifest_path, manifest)
    return {
        "workflow": manifest["workflow"],
        "valve_tag": bundle["target_valve_tag"],
        "request": str(paths["request"]),
        "template": str(paths["template"]),
        "schema": str(paths["schema"]),
        "prompt": str(paths["prompt"]),
        "image_paths": [
            image["image_path"]
            for image in (
                bundle["evidence"]["curve_images"]
                + bundle["evidence"]["specification_images"]
            )
        ],
        "worker_model": model,
        "worker_reasoning_effort": reasoning_effort,
        "read_allowlist": str(run_dir / "allowed_reads.json") if project_value and (Path(project_value) / "project.toml").is_file() else None,
        "single_call_includes_visual_and_whole_valve_diagnosis": True,
    }


def _algorithm_audit_interpretations(
    run_dir: Path,
    diagnosis: dict[str, Any],
) -> tuple[CurveInterpretation, ...]:
    observations = _read_json(run_dir / "03_observations.json")
    observation_by_key = {
        (item.get("image_sha256"), item.get("test_key")): str(
            item.get("observation") or ""
        )
        for item in observations.get("curve_observations") or []
        if isinstance(item, dict)
    }
    bundle = _read_json(run_dir / "01_evidence_bundle.json")
    image_keys: dict[tuple[Any, Any], list[str]] = {}
    for image in bundle.get("evidence", {}).get("curve_images") or []:
        if isinstance(image, dict):
            image_keys.setdefault(
                (image.get("image_sha256"), image.get("test_key")), []
            ).append(str(image.get("image_key") or ""))
    interpretations: list[CurveInterpretation] = []
    for item in diagnosis.get("curve_interpretations") or []:
        if not isinstance(item, dict):
            continue
        identity = (item.get("image_sha256"), item.get("test_key"))
        candidates = image_keys.get(identity) or []
        image_key = candidates.pop(0) if candidates else ""
        if not image_key:
            raise ValueError("algorithm audit curve image identity is missing")
        interpretations.append(CurveInterpretation(
            image_key=image_key,
            test_key=str(item.get("test_key") or ""),
            observation=observation_by_key.get(
                (item.get("image_sha256"), item.get("test_key")), ""
            ),
            diagnosis=str(item.get("diagnosis") or ""),
            rating=item.get("rating"),
            action=str(item.get("action") or ""),
            customer_text=str(item.get("customer_text") or ""),
            confidence=str(item.get("confidence") or ""),
            experience_rule_ids=tuple(
                str(value) for value in item.get("experience_rule_ids") or []
            ),
        ))
    return tuple(interpretations)


def _calculate_algorithm_audit(
    run_dir: Path,
    diagnosis: dict[str, Any],
) -> dict[str, Any]:
    from offline_engine.generator import calculate_algorithm_audit

    bundle = _read_json(run_dir / "01_evidence_bundle.json")
    source_record = (
        bundle.get("evidence", {})
        .get("structured_evidence", {})
        .get("source_record")
    )
    if not isinstance(source_record, dict):
        raise ValueError("algorithm audit requires structured source_record")
    return calculate_algorithm_audit(
        source_record,
        _algorithm_audit_interpretations(run_dir, diagnosis),
    )


def _validate_algorithm_audit(
    run_dir: Path,
    diagnosis: dict[str, Any],
) -> None:
    contract = diagnosis.get("algorithm_audit_contract")
    status = diagnosis.get("algorithm_audit_status")
    snapshot = diagnosis.get("algorithm_audit_snapshot")
    manifest_contract = _read_json(run_dir / "00_run_manifest.json").get(
        "algorithm_audit_contract"
    )
    if not manifest_contract and not contract and not status and snapshot is None:
        return
    if contract != ALGORITHM_AUDIT_CONTRACT or manifest_contract != contract:
        raise ValueError("algorithm audit contract is missing or invalid")
    if status == "available":
        if not isinstance(snapshot, dict) or snapshot != _calculate_algorithm_audit(
            run_dir, diagnosis
        ):
            raise ValueError("algorithm audit snapshot does not match diagnostic inputs")
        return
    if status != "legacy_unavailable" or snapshot is not None:
        raise ValueError("algorithm audit status is invalid")
    basis = diagnosis.get("diagnosis_basis") or {}
    if not str(basis.get("mode") or "").startswith("inherited_"):
        raise ValueError("fresh diagnosis requires an algorithm audit snapshot")
    parent_value = str(basis.get("parent_run_dir") or "").strip()
    if not parent_value:
        raise ValueError("legacy algorithm audit requires a parent diagnosis")
    validate_legacy_algorithm_audit_parent(Path(parent_value))


def _validate_integrated_model_provenance(
    result: dict[str, Any], model: str, reasoning_effort: str
) -> None:
    provenance = result.get("model_provenance")
    if not isinstance(provenance, dict):
        raise ValueError("integrated diagnosis requires model_provenance")
    expected = {
        "decision_source": "codex_exec_integrated",
        "all_evidence_and_images_in_single_call": True,
        "rating_decided_by": "model",
    }
    for field, value in expected.items():
        if provenance.get(field) != value:
            raise ValueError(f"invalid integrated model provenance: {field}")
    if provenance.get("model") != model:
        raise ValueError(
            f"integrated diagnosis requires model {model}"
        )
    if provenance.get("reasoning_effort") != reasoning_effort:
        raise ValueError(
            f"integrated diagnosis requires {reasoning_effort} reasoning effort"
        )


def submit_integrated_model_result(
    run_dir: Path,
    result: dict[str, Any],
) -> dict[str, Any]:
    """Accept one model result; scripts bind and validate but never rate."""
    manifest_path = run_dir / "00_run_manifest.json"
    manifest = _read_json(manifest_path)
    if manifest.get("stage") != "awaiting_observation" or manifest.get(
        "diagnosis_method_required"
    ) != "codex_exec_integrated":
        raise ValueError("run is not awaiting an integrated model result")
    _validate_version_file(run_dir, manifest)
    _run_experience(run_dir)
    _validate_integrated_model_provenance(result, *_run_model_contract(manifest))
    bundle = _read_json(run_dir / "01_evidence_bundle.json")
    if result.get("valve_tag") != bundle["target_valve_tag"]:
        raise ValueError("integrated diagnosis valve_tag does not match")

    try:
        if manifest.get("source_read_contract") != SOURCE_READ_CONTRACT:
            raise ValueError("source_read_contract missing; prepare a new versioned request")
        validate_source_reads(bundle, result.get("source_file_reads"))
    except ValueError as exc:
        # Append-only failed-attempt audit. No draft, approval, or stage advance.
        rejection = {
            "schema_version": 1,
            "source_read_contract": SOURCE_READ_CONTRACT,
            "status": "rejected",
            "reason": str(exc),
            "attempted_at": datetime.now(timezone.utc).isoformat(),
            "evidence_bundle_sha256": _sha256_file(run_dir / "01_evidence_bundle.json"),
            "result_sha256": _sha256_payload(result),
            "result": result,
        }
        rejected_path = (run_dir / "03_source_read_rejections"
                         / f"{rejection['result_sha256']}.json")
        rejected_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with rejected_path.open("x", encoding="utf-8") as stream:
                stream.write(json.dumps(rejection, ensure_ascii=False, indent=2) + "\n")
        except FileExistsError:
            pass
        raise

    assessments = result.get("curve_assessments")
    if not isinstance(assessments, list):
        raise ValueError("curve_assessments are required")
    expected_curves = Counter(
        (image["image_sha256"], image["test_key"])
        for image in bundle["evidence"]["curve_images"]
    )
    submitted_curves = Counter(
        (item.get("image_sha256"), item.get("test_key"))
        for item in assessments
    )
    if submitted_curves != expected_curves:
        raise ValueError("integrated result must cover every curve exactly once")

    curve_observations: list[dict[str, Any]] = []
    curve_interpretations: list[dict[str, Any]] = []
    for item in assessments:
        if item.get("needs_review") is True:
            raise ValueError("unresolved curve review cannot enter diagnosis")
        test_key = item["test_key"]
        raw_checks = item.get("visual_checks") or {}
        if test_key in STEP_TEST_KEYS:
            check_keys = STEP_VISUAL_CHECKS
        elif test_key in SIGNATURE_TEST_KEYS:
            check_keys = SIGNATURE_VISUAL_CHECKS
        else:
            check_keys = ()
        observation = {
            "image_sha256": item["image_sha256"],
            "test_key": test_key,
            "observation": item.get("observation"),
        }
        if check_keys:
            observation["visual_checks"] = {
                key: raw_checks.get(key) for key in check_keys
            }
        curve_observations.append(observation)
        curve_interpretations.append(
            {
                "image_sha256": item["image_sha256"],
                "test_key": test_key,
                "diagnosis": item.get("diagnosis"),
                "rating": item.get("rating"),
                "action": item.get("action"),
                "confidence": item.get("confidence"),
            }
        )

    review = result.get("evidence_review") or {}
    observations = {
        "schema_version": 1,
        "reviewed_specification_image_sha256s": [
            item["image_sha256"]
            for item in result.get("specification_observations") or []
        ],
        "reviewed_specification_physical_identities": [
            item.get("physical_identity") or item["image_sha256"]
            for item in result.get("specification_observations") or []
        ],
        **review,
        "source_read_contract": SOURCE_READ_CONTRACT,
        "source_file_reads": deepcopy(result["source_file_reads"]),
        "curve_observations": curve_observations,
        "specification_observations": list(
            result.get("specification_observations") or []
        ),
    }
    structured_evidence = bundle["evidence"].get("structured_evidence") or {}
    if structured_evidence:
        observations["evidence_accounting"] = (
            structured_evidence_accounting(structured_evidence)
        )
    _validate_observations(bundle, observations)

    staged_paths = [run_dir / "03_observations.json", run_dir / DIAGNOSIS_DRAFT_FILENAME]
    try:
        _write_json(staged_paths[0], observations)
        diagnosis = {
            "schema_version": 1,
            "diagnosis_basis": _diagnosis_basis(run_dir),
            "algorithm_audit_contract": ALGORITHM_AUDIT_CONTRACT,
            "source_read_contract": SOURCE_READ_CONTRACT,
            "algorithm_audit_status": "available",
            "diagnostic_guardrails": _diagnostic_guardrails(bundle, observations),
            "human_writing_reference": _human_writing_reference(),
            "model_provenance": deepcopy(result["model_provenance"]),
            "curve_interpretations": curve_interpretations,
            "whole_valve": deepcopy(result.get("whole_valve") or {}),
        }
        diagnosis["algorithm_audit_snapshot"] = _calculate_algorithm_audit(
            run_dir, diagnosis
        )
        _validate_diagnosis(run_dir, diagnosis)
        _write_json(staged_paths[1], diagnosis)
    except Exception:
        for path in staged_paths:
            if path.exists():
                path.unlink()
        raise
    manifest["stage"] = "awaiting_diagnosis"
    manifest["next_file"] = DIAGNOSIS_DRAFT_FILENAME
    _write_json(manifest_path, manifest)
    _update_project_target_stage(
        _project_manifest_binding(run_dir), "awaiting_diagnosis"
    )
    return manifest


def _require_text(payload: dict[str, Any], field: str) -> None:
    if not str(payload.get(field) or "").strip():
        raise ValueError(f"{field} is required")


def _require_list(payload: dict[str, Any], field: str) -> list[Any]:
    value = payload.get(field)
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list")
    return value


def _require_text_list(
    payload: dict[str, Any], field: str, *, require_items: bool = False
) -> list[str]:
    value = payload.get(field)
    if (
        not isinstance(value, list)
        or (require_items and not value)
        or any(not isinstance(item, str) or not item.strip() for item in value)
    ):
        qualifier = "a non-empty text list" if require_items else "a text list"
        raise ValueError(f"{field} must be {qualifier}")
    return value


def _validate_rating(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer from 1 to 5")
    if value not in {1, 2, 3, 4, 5}:
        raise ValueError(f"{field} must be an integer from 1 to 5")
    return value


def _validate_human_style_text(
    value: Any,
    *,
    field: str,
    maximum: int,
) -> None:
    text = " ".join(str(value or "").split())
    for phrase in HUMAN_WRITING_FORBIDDEN_PHRASES:
        if phrase in text:
            raise ValueError(
                "human writing style rejects "
                f"{field}: forbidden phrase {phrase}"
            )
    if len(text) > maximum:
        raise ValueError(
            "human writing style rejects "
            f"{field}: {len(text)} characters exceeds {maximum}"
        )


def _validate_human_writing_style(
    diagnosis: dict[str, Any],
) -> None:
    limits = {
        "curve diagnosis": 90,
        "curve action": 80,
        "rating_conclusion": 120,
        "main_issue": 80,
        "issue": 80,
        "action": 80,
    }
    for item in diagnosis.get("curve_interpretations") or []:
        _validate_human_style_text(
            item.get("diagnosis"),
            field="curve diagnosis",
            maximum=limits["curve diagnosis"],
        )
        _validate_human_style_text(
            item.get("action"),
            field="curve action",
            maximum=limits["curve action"],
        )
    whole = diagnosis.get("whole_valve") or {}
    for field in ("rating_conclusion", "main_issue"):
        _validate_human_style_text(
            whole.get(field),
            field=field,
            maximum=limits[field],
        )
    for field, singular in (
        ("issues", "issue"),
        ("actions", "action"),
    ):
        for index, value in enumerate(whole.get(field) or [], 1):
            _validate_human_style_text(
                value,
                field=f"{singular} {index}",
                maximum=limits[singular],
            )


def _deterministic_customer_text(
    observation: str,
    diagnosis: str,
    action: str,
) -> str:
    def sentence(value: str) -> str:
        value = value.strip()
        return value if value.endswith(("。", "！", "？", "；")) else value + "。"

    parts = [sentence(observation), sentence(diagnosis)]
    if action.strip():
        parts.append("建议" + sentence(action))
    return "".join(parts)


def _validate_diagnosis(
    run_dir: Path,
    diagnosis: dict[str, Any],
) -> None:
    bundle = _read_json(run_dir / "01_evidence_bundle.json")
    _validate_diagnosis_basis(run_dir, diagnosis)
    _validate_algorithm_audit(run_dir, diagnosis)
    manifest = _read_json(run_dir / "00_run_manifest.json")
    integrated = (
        manifest.get("diagnosis_method_required")
        == "codex_exec_integrated"
    )
    if integrated:
        _validate_integrated_model_provenance(diagnosis, *_run_model_contract(manifest))
    validate_source_read_acceptance(
        bundle, _read_json(run_dir / "03_observations.json"), diagnosis, manifest,
    )
    expected_curves = Counter(
        (image["image_sha256"], image["test_key"])
        for image in bundle["evidence"]["curve_images"]
    )
    interpretations = diagnosis.get("curve_interpretations")
    if not isinstance(interpretations, list):
        raise ValueError("curve interpretations are required")
    submitted_entries = Counter(
        (item.get("image_sha256"), item.get("test_key"))
        for item in interpretations
    )
    if submitted_entries != expected_curves:
        raise ValueError(
            "curve interpretations must cover every observed curve"
        )
    submitted_observations = _read_json(
        run_dir / "03_observations.json"
    ).get("curve_observations", [])
    observation_by_curve = {
        (item.get("image_sha256"), item.get("test_key")): item
        for item in submitted_observations
    }

    for item in interpretations:
        _require_text(item, "diagnosis")
        _validate_rating(item.get("rating"), "curve rating")
        _require_text(item, "action")
        if item.get("confidence") not in {"high", "medium", "low"}:
            raise ValueError(
                "confidence must be high, medium, or low"
            )
        if (
            diagnosis.get("diagnosis_basis", {}).get("evidence_contract_version")
            == str(EVIDENCE_ACCOUNTING_VERSION)
            and "customer_text" in item
        ):
            matching_observation = observation_by_curve.get(
                (item.get("image_sha256"), item.get("test_key"))
            )
            expected_text = _deterministic_customer_text(
                str((matching_observation or {}).get("observation") or ""),
                str(item.get("diagnosis") or ""),
                str(item.get("action") or ""),
            )
            if str(item.get("customer_text") or "").strip() != expected_text:
                raise ValueError(
                    "customer_text must be deterministically bound to observation, diagnosis, and action"
                )

    whole = diagnosis.get("whole_valve")
    if not isinstance(whole, dict):
        raise ValueError("whole_valve diagnosis is required")
    if not isinstance(whole.get("information_complete"), bool):
        raise ValueError("information_complete must be true or false")
    final_rating = _validate_rating(
        whole.get("final_rating"),
        "final_rating",
    )
    if whole["information_complete"] and final_rating == 5:
        raise ValueError(
            "complete evidence cannot receive level five"
        )
    if (
        final_rating == 5
        and _step_and_ramp_evidence_precludes_level_five(bundle)
    ):
        raise ValueError(
            "missing actuator performance data alone cannot receive level five "
            "when step and ramp tests succeeded"
        )
    if final_rating == 4 and not str(
        whole.get("severe_functional_impact") or ""
    ).strip():
        raise ValueError(
            "level four requires severe functional impact"
        )
    if integrated:
        functional_evidence = _require_list(
            whole, "functional_impact_evidence"
        )
        if final_rating == 4 and not any(
            str(value or "").strip() for value in functional_evidence
        ):
            raise ValueError(
                "integrated level four requires concrete functional impact evidence"
            )
        if whole.get("counter_evidence") == [
            "未发现可推翻当前评级的明确反证据。"
        ]:
            raise ValueError(
                "integrated diagnosis rejects generic counterevidence"
            )
    _require_text(whole, "rating_conclusion")
    _require_text(whole, "main_issue")
    _require_text_list(whole, "issues")
    _require_text_list(whole, "actions", require_items=True)
    for field in (
        "supporting_evidence",
        "counter_evidence",
        "uncertainties",
    ):
        _require_list(whole, field)
    if not whole["supporting_evidence"]:
        raise ValueError("supporting_evidence cannot be empty")
    if not whole["counter_evidence"]:
        raise ValueError(
            "counter_evidence must state evidence or no clear counterevidence"
        )

    if (
        diagnosis.get("diagnosis_basis", {}).get("mode")
        == "fresh_reanalysis"
    ):
        observations = _read_json(
            run_dir / "03_observations.json"
        )
        _validate_diagnostic_guardrails(
            bundle,
            observations,
            diagnosis,
        )
        if not integrated:
            _validate_human_writing_style(diagnosis)


def _sha256_file(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _diagnosis_stage_gate_paths(run_root: Path) -> dict[str, Path]:
    project_manifest_path = run_root / "00_project_run_manifest.json"
    if not project_manifest_path.is_file():
        raise ValueError("project run manifest is required")
    project_manifest = _read_json(project_manifest_path)
    project_root_value = str(project_manifest.get("project_root") or "").strip()
    if not project_root_value:
        raise ValueError("project run manifest requires project_root")
    project_root = Path(project_root_value)
    resolved_run_root = run_root.resolve()
    if len(resolved_run_root.parents) >= 4:
        layout_project_root = resolved_run_root.parents[3]
        if resolved_run_root.parents[2].name == "work":
            project_root = layout_project_root
    stage_gate_root = project_root / "work" / "stage-gates"
    return {
        "review_json": stage_gate_root / DIAGNOSIS_REVIEW_JSON,
        "review_markdown": stage_gate_root / DIAGNOSIS_REVIEW_MARKDOWN,
        "approval": stage_gate_root / DIAGNOSIS_APPROVAL_JSON,
    }


def _diagnosis_review_fingerprint(report: dict[str, Any]) -> str:
    return _sha256_payload(
        {
            "schema_version": report.get("schema_version"),
            "project_id": report.get("project_id"),
            "diagnosis_version": report.get("diagnosis_version"),
            "parent_run": report.get("parent_run"),
            "revision_reason": report.get("revision_reason"),
            "target_count": report.get("target_count"),
            "rating_distribution": report.get("rating_distribution"),
            "information_incomplete_count": report.get(
                "information_incomplete_count"
            ),
            "test_issue_target_count": report.get(
                "test_issue_target_count"
            ),
            "targets": report.get("targets"),
        }
    )


def _test_execution_issue_text(value: Any) -> str:
    if not isinstance(value, dict):
        return " ".join(str(value or "").split())
    name = str(
        value.get("task_name")
        or value.get("test_key")
        or value.get("name")
        or "测试"
    ).strip()
    occurrence = value.get("occurrence")
    if occurrence not in (None, ""):
        name = f"{name}#{occurrence}"
    result = str(value.get("result") or value.get("status") or "").strip()
    return f"{name}：{result}" if result else name


def _build_diagnosis_review(
    run_root: Path,
    *,
    require_awaiting: bool,
) -> dict[str, Any]:
    project_manifest_path = run_root / "00_project_run_manifest.json"
    if not project_manifest_path.is_file():
        raise ValueError("project run manifest is required")
    project_manifest = _read_json(project_manifest_path)
    targets = project_manifest.get("targets")
    if not isinstance(targets, list) or not targets:
        raise ValueError("project run manifest requires targets")

    rows: list[dict[str, Any]] = []
    allowed_stages = (
        {"awaiting_diagnosis"}
        if require_awaiting
        else {"awaiting_diagnosis", "frozen"}
    )
    for target in sorted(
        targets,
        key=lambda item: str(item.get("valve_tag") or ""),
    ):
        valve_tag = str(target.get("valve_tag") or "").strip()
        if not valve_tag:
            raise ValueError("project diagnosis target requires valve_tag")
        if target.get("stage") not in allowed_stages:
            raise ValueError(
                f"diagnosis target is not ready for review: {valve_tag}"
            )
        run_dir = run_root / valve_tag
        draft_path = run_dir / DIAGNOSIS_DRAFT_FILENAME
        if not draft_path.is_file():
            raise ValueError(
                f"diagnosis draft is missing for {valve_tag}: "
                f"{DIAGNOSIS_DRAFT_FILENAME}"
            )
        diagnosis = _read_json(draft_path)
        _validate_diagnosis(run_dir, diagnosis)
        whole = diagnosis["whole_valve"]
        evidence = _read_json(run_dir / "01_evidence_bundle.json")["evidence"]
        test_execution_issues = [
            _test_execution_issue_text(item)
            for item in evidence.get("test_execution_issues") or []
        ]
        rows.append(
            {
                "valve_tag": valve_tag,
                "diagnosis_sha256": _sha256_payload(diagnosis),
                "evidence_semantic_sha256": str(
                    diagnosis.get("diagnosis_basis", {}).get(
                        "evidence_semantic_sha256"
                    )
                    or ""
                ),
                "information_complete": whole["information_complete"],
                "final_rating": whole["final_rating"],
                "rating_conclusion": whole["rating_conclusion"],
                "main_issue": whole["main_issue"],
                "actions": list(whole["actions"]),
                "supporting_evidence": list(whole["supporting_evidence"]),
                "counter_evidence": list(whole["counter_evidence"]),
                "uncertainties": list(whole["uncertainties"]),
                "active_faults": list(evidence.get("active_faults") or []),
                "test_execution_issues": [
                    item for item in test_execution_issues if item
                ],
                "curve_interpretation_count": len(
                    diagnosis["curve_interpretations"]
                ),
            }
        )

    rating_distribution = Counter(
        int(item["final_rating"]) for item in rows
    )
    report = {
        "schema_version": 1,
        "status": "awaiting_user_approval",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "project_id": project_manifest.get("project_id"),
        "diagnosis_version": project_manifest.get("diagnosis_version"),
        "parent_run": project_manifest.get("parent_run"),
        "revision_reason": project_manifest.get("revision_reason"),
        "target_count": len(rows),
        "rating_distribution": {
            str(rating): rating_distribution.get(rating, 0)
            for rating in range(1, 6)
        },
        "information_incomplete_count": sum(
            not item["information_complete"] for item in rows
        ),
        "test_issue_target_count": sum(
            bool(item["test_execution_issues"]) for item in rows
        ),
        "targets": rows,
    }
    report["review_fingerprint"] = _diagnosis_review_fingerprint(report)
    return report


def _markdown_cell(value: Any) -> str:
    text = " ".join(str(value or "").split()).replace("|", "\\|")
    return text or "-"


def _compact_review_list(values: list[Any], *, limit: int = 2) -> str:
    items = [" ".join(str(value or "").split()) for value in values]
    items = [item for item in items if item]
    if not items:
        return "-"
    selected = items[:limit]
    if len(items) > limit:
        selected.append(f"另有{len(items) - limit}项")
    return "；".join(selected)


def _render_diagnosis_review_markdown(report: dict[str, Any]) -> str:
    rating_summary = "、".join(
        f"{rating}级 {report['rating_distribution'][str(rating)]} 台"
        for rating in range(1, 6)
        if report["rating_distribution"][str(rating)]
    ) or "尚无评级"
    lines = [
        "# 第三阶段诊断审核报告",
        "",
        f"- 诊断版本：{_markdown_cell(report.get('diagnosis_version'))}",
        f"- 阀门数量：{report['target_count']} 台",
        f"- 评级分布：{rating_summary}",
        f"- 信息不足：{report['information_incomplete_count']} 台",
        f"- 存在未成功测试：{report['test_issue_target_count']} 台",
        "",
        "## 逐阀结论",
        "",
        (
            "| 位号 | 等级 | 主要判断 | 关键证据 | 反证据 | "
            "建议行动 | 不确定项 |"
        ),
        "|---|---:|---|---|---|---|---|",
    ]
    for item in report["targets"]:
        lines.append(
            "| "
            + " | ".join(
                (
                    _markdown_cell(item["valve_tag"]),
                    str(item["final_rating"]),
                    _markdown_cell(item["rating_conclusion"]),
                    _markdown_cell(
                        _compact_review_list(item["supporting_evidence"])
                    ),
                    _markdown_cell(
                        _compact_review_list(item["counter_evidence"])
                    ),
                    _markdown_cell(_compact_review_list(item["actions"])),
                    _markdown_cell(
                        _compact_review_list(item["uncertainties"])
                    ),
                )
            )
            + " |"
        )

    lines.extend(["", "## 需重点确认", ""])
    focus_lines: list[str] = []
    for item in report["targets"]:
        details: list[str] = []
        if item["active_faults"]:
            details.append(
                "活动故障：" + _compact_review_list(item["active_faults"])
            )
        if item["test_execution_issues"]:
            details.append(
                "未成功测试："
                + _compact_review_list(
                    item["test_execution_issues"],
                    limit=4,
                )
            )
        if not item["information_complete"]:
            details.append("诊断信息不足")
        if item["uncertainties"]:
            details.append(
                "不确定项：" + _compact_review_list(item["uncertainties"])
            )
        if details:
            focus_lines.append(
                f"- {item['valve_tag']}：" + "；".join(details)
            )
    lines.extend(
        focus_lines
        or [
            "无需要单独展开的活动故障、未成功测试或数据不足项。"
        ]
    )
    lines.extend(
        [
            "",
            (
                "完整逐图诊断保留在机器文件中，"
                "本报告只展示需要确认的整阀结论。"
            ),
            "批准前只修订受影响阀门；批准后才冻结本诊断版本。",
            "",
        ]
    )
    return "\n".join(lines)


def prepare_diagnosis_review(run_root: Path) -> dict[str, Any]:
    report = _build_diagnosis_review(run_root, require_awaiting=True)
    paths = _diagnosis_stage_gate_paths(run_root)
    paths["review_json"].parent.mkdir(parents=True, exist_ok=True)
    _write_json(paths["review_json"], report)
    paths["review_markdown"].write_text(
        _render_diagnosis_review_markdown(report),
        encoding="utf-8",
    )
    return report


def _require_approved_project_diagnosis(
    run_dir: Path,
    diagnosis: dict[str, Any],
) -> None:
    if _project_manifest_binding(run_dir) is None:
        return
    run_root = run_dir.parent
    paths = _diagnosis_stage_gate_paths(run_root)
    if not paths["approval"].is_file():
        raise ValueError("project diagnosis requires user approval before freeze")
    if not paths["review_json"].is_file():
        raise ValueError("diagnosis review report is missing")
    approval = _read_json(paths["approval"])
    if approval.get("status") not in {"approved", "approved_freeze_incomplete", "approved_and_frozen"}:
        raise ValueError("project diagnosis approval is not active")
    if approval.get("review_report_sha256") != _sha256_file(
        paths["review_json"]
    ):
        raise ValueError("diagnosis review report changed after approval")
    if approval.get("review_markdown_sha256") != _sha256_file(paths["review_markdown"]):
        raise ValueError("diagnosis review Markdown changed after approval")
    current = _build_diagnosis_review(run_root, require_awaiting=False)
    if approval.get("review_fingerprint") != current.get(
        "review_fingerprint"
    ):
        raise ValueError("diagnosis draft changed after approval")
    valve_tag = str(
        _read_json(run_dir / "00_run_manifest.json").get("target_valve_tag")
        or ""
    )
    matches = [
        item for item in current["targets"] if item["valve_tag"] == valve_tag
    ]
    if len(matches) != 1:
        raise ValueError("approved diagnosis target is missing")
    if matches[0]["diagnosis_sha256"] != _sha256_payload(diagnosis):
        raise ValueError("submitted diagnosis does not match approved draft")


def approve_project_diagnosis(
    run_root: Path,
    approval_text: str = "",
) -> dict[str, Any]:
    approval_text = str(approval_text or "").strip()
    paths = _diagnosis_stage_gate_paths(run_root)
    if not paths["review_json"].is_file() or not paths[
        "review_markdown"
    ].is_file():
        raise ValueError("diagnosis review report must be prepared first")
    stored_report = _read_json(paths["review_json"])
    if stored_report.get("status") != "awaiting_user_approval":
        raise ValueError("diagnosis review report is not awaiting approval")
    if stored_report.get("review_fingerprint") != (
        _diagnosis_review_fingerprint(stored_report)
    ):
        raise ValueError("diagnosis review report fingerprint is invalid")
    approval = _read_json(paths["approval"]) if paths["approval"].is_file() else {}
    resume = (
        approval.get("status") in {"approved", "approved_freeze_incomplete", "approved_and_frozen"}
        and approval.get("project_id") == stored_report.get("project_id")
        and approval.get("diagnosis_version") == stored_report.get("diagnosis_version")
    )
    if not resume and not approval_text:
        raise ValueError("approval_text is required")
    current_report = _build_diagnosis_review(
        run_root,
        require_awaiting=not resume,
    )
    if stored_report.get("review_fingerprint") != current_report.get(
        "review_fingerprint"
    ):
        raise ValueError("diagnosis draft changed after review report")
    if paths["review_markdown"].read_text(encoding="utf-8") != (
        _render_diagnosis_review_markdown(stored_report)
    ):
        raise ValueError("diagnosis review Markdown changed after preparation")

    if resume:
        for item in current_report["targets"]:
            run_dir = run_root / item["valve_tag"]
            _require_approved_project_diagnosis(
                run_dir, _read_json(run_dir / DIAGNOSIS_DRAFT_FILENAME)
            )
    else:
        for item in current_report["targets"]:
            run_dir = run_root / item["valve_tag"]
            if (run_dir / "05_diagnosis.json").exists() or (
                run_dir / "06_freeze.sha256"
            ).exists():
                raise ValueError("diagnosis run already contains frozen output")

        approval = {
            "schema_version": 1,
            "status": "approved",
            "approved_at": datetime.now(timezone.utc).isoformat(),
            "approval_text": approval_text,
            "project_id": current_report.get("project_id"),
            "diagnosis_version": current_report.get("diagnosis_version"),
            "review_fingerprint": current_report["review_fingerprint"],
            "review_report_sha256": _sha256_file(paths["review_json"]),
            "review_markdown_sha256": _sha256_file(paths["review_markdown"]),
            "targets": [
                {
                    "valve_tag": item["valve_tag"],
                    "diagnosis_sha256": item["diagnosis_sha256"],
                }
                for item in current_report["targets"]
            ],
        }
        _write_json(paths["approval"], approval)

    try:
        for item in current_report["targets"]:
            run_dir = run_root / item["valve_tag"]
            submit_diagnosis(
                run_dir,
                _read_json(run_dir / DIAGNOSIS_DRAFT_FILENAME),
            )
    except Exception:
        approval["status"] = "approved_freeze_incomplete"
        _write_json(paths["approval"], approval)
        raise

    project_manifest = _read_json(
        run_root / "00_project_run_manifest.json"
    )
    if project_manifest.get("stage") != "frozen":
        raise ValueError("approved project diagnosis did not fully freeze")
    approval["status"] = "approved_and_frozen"
    approval.setdefault("frozen_at", datetime.now(timezone.utc).isoformat())
    _write_json(paths["approval"], approval)
    return project_manifest


def submit_diagnosis(
    run_dir: Path,
    diagnosis: dict[str, Any],
) -> dict[str, Any]:
    manifest_path = run_dir / "00_run_manifest.json"
    manifest = _read_json(manifest_path)
    if manifest.get("stage") not in {"awaiting_diagnosis", "frozen"}:
        raise ValueError("run is not awaiting diagnosis")
    project_binding = _project_manifest_binding(run_dir)
    if (project_binding is not None and project_binding[2].get("stage") == "frozen"
            and manifest.get("stage") != "frozen"):
        raise ValueError("frozen project target has inconsistent valve stage")
    _validate_version_file(run_dir, manifest)
    diagnosis_path = run_dir / "05_diagnosis.json"
    freeze_path = run_dir / "06_freeze.sha256"
    _validate_diagnosis(run_dir, diagnosis)
    _require_approved_project_diagnosis(run_dir, diagnosis)
    _run_experience(run_dir)
    if diagnosis_path.exists():
        if _sha256_payload(_read_json(diagnosis_path)) != _sha256_payload(diagnosis):
            raise ValueError("frozen diagnosis does not match approved draft")
    elif freeze_path.exists() or manifest.get("stage") == "frozen":
        raise ValueError("frozen diagnosis output is missing")
    else:
        with diagnosis_path.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(diagnosis, ensure_ascii=False, indent=2) + "\n")
    frozen_names = (
        "00_version.json", "00_experience.md", "01_evidence_bundle.json",
        "03_observations.json", "05_diagnosis.json",
    )
    if freeze_path.exists():
        if not _verify_freeze(run_dir, require_versioned=True):
            raise ValueError("frozen diagnosis hash mismatch")
    elif manifest.get("stage") == "frozen":
        raise ValueError("frozen diagnosis hash file is missing")
    else:
        freeze_text = "".join(
            f"{_sha256_file(run_dir / name)}  {name}\n"
            for name in frozen_names
        )
        with freeze_path.open("x", encoding="utf-8") as stream:
            stream.write(freeze_text)
    if manifest.get("stage") != "frozen":
        manifest["stage"] = "frozen"
        manifest["next_file"] = None
        manifest["frozen_at"] = datetime.now(timezone.utc).isoformat()
        _write_json(manifest_path, manifest)
    if project_binding is not None and project_binding[2].get("stage") != "frozen":
        _update_project_target_stage(project_binding, "frozen")
    return manifest


def _verify_freeze(
    run_dir: Path,
    *,
    require_versioned: bool = False,
) -> bool:
    freeze_path = run_dir / "06_freeze.sha256"
    if not freeze_path.exists():
        return False
    manifest = _read_json(run_dir / "00_run_manifest.json")
    versioned = require_versioned or (run_dir / "00_version.json").exists() or any(
        str(manifest.get(field) or "").strip()
        for field in (
            "diagnosis_version",
            "parent_run",
            "revision_reason",
        )
    )
    required = {
        "01_evidence_bundle.json",
        "02_retrieved_cases.json",
        "03_observations.json",
        "04_retrieved_rules.json",
        "05_diagnosis.json",
    }
    if versioned:
        required.add("00_version.json")
    if manifest.get("experience_snapshot") == "00_experience.md":
        required.difference_update({"02_retrieved_cases.json", "04_retrieved_rules.json"})
        required.add("00_experience.md")
    elif manifest.get("experience_snapshot"):
        required.add("00_experience_snapshot.json")
    entries: dict[str, str] = {}
    for line in freeze_path.read_text(encoding="utf-8").splitlines():
        if "  " not in line:
            return False
        expected, filename = line.split("  ", 1)
        entries[filename] = expected
        path = run_dir / filename
        if not path.is_file() or _sha256_file(path) != expected:
            return False
    if set(entries) != required:
        return False
    try:
        validate_source_read_acceptance(
            _read_json(run_dir / "01_evidence_bundle.json"),
            _read_json(run_dir / "03_observations.json"),
            _read_json(run_dir / "05_diagnosis.json"), manifest,
        )
    except ValueError:
        return False
    return True


def get_status(
    run_dir: Path,
    *,
    require_versioned: bool = False,
) -> dict[str, Any]:
    manifest = _read_json(run_dir / "00_run_manifest.json")
    return {
        "workflow": manifest["workflow"],
        "target_case_id": manifest["target_case_id"],
        "target_valve_tag": manifest["target_valve_tag"],
        "stage": manifest["stage"],
        "next_file": manifest.get("next_file"),
        "next_operation": manifest.get("next_operation"),
        "freeze_valid": (
            _verify_freeze(
                run_dir,
                require_versioned=require_versioned,
            )
            if manifest["stage"] == "frozen"
            else None
        ),
    }


def resolve_project_analysis_summary(
    project_root: Path,
    explicit_path: Path | None = None,
    report_period: str | None = None,
) -> Path:
    """Resolve a current or source-bound formal-evidence snapshot."""
    config = load_project(project_root, report_period=report_period)
    current = config.work_dir / "analysis_summary.json"
    if explicit_path is not None:
        explicit = explicit_path.resolve()
        if explicit == current.resolve():
            return current
        if explicit.name != "analysis_summary.json" or not explicit.is_file():
            raise ValueError("formal evidence summary must be analysis_summary.json")
        if not explicit.is_relative_to((project_root.resolve() / "work")):
            raise ValueError("formal evidence summary must be within project work")
        source_path = explicit.with_name("analysis_summary.source.json")
        source = _read_json(source_path) if source_path.is_file() else None
        expected = {
            "schema_version": 1,
            "purpose": "formal_evidence_preparation",
            "project_root": str(project_root.resolve()),
            "report_period_key": config.report_period_key,
            "summary_sha256": _sha256_file(explicit),
        }
        if not isinstance(source, dict) or source != expected:
            raise ValueError("formal evidence summary source binding is invalid")
        return explicit
    if not current.is_file():
        raise ValueError(
            "current formal evidence snapshot is missing: "
            f"{current}"
        )
    return current


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Formal one-valve Codex diagnostic workflow"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare_project = subparsers.add_parser("prepare-project")
    prepare_project.add_argument(
        "--project-root",
        type=Path,
        required=True,
    )
    prepare_project.add_argument("--project-id", required=True)
    prepare_project.add_argument("--run-root", type=Path, required=True)
    prepare_project.add_argument("--diagnosis-version", required=True)
    prepare_project.add_argument("--parent-run", required=True)
    prepare_project.add_argument("--revision-reason", required=True)
    prepare_project.add_argument("--report-period")
    prepare_project.add_argument("--model", default=INTEGRATED_MODEL)
    prepare_project.add_argument("--reasoning-effort", default=INTEGRATED_REASONING_EFFORT)
    prepare_project.add_argument(
        "--analysis-summary",
        type=Path,
    )
    prepare_project.add_argument(
        "--only-valve",
        action="append",
        default=[],
    )

    integrated_prepare = subparsers.add_parser(
        "prepare-integrated-diagnosis"
    )
    integrated_prepare.add_argument("--run-dir", type=Path, required=True)

    integrated_submit = subparsers.add_parser(
        "submit-integrated-diagnosis"
    )
    integrated_submit.add_argument("--run-dir", type=Path, required=True)
    integrated_submit.add_argument("--result", type=Path, required=True)

    diagnosis = subparsers.add_parser("submit-diagnosis")
    diagnosis.add_argument("--run-dir", type=Path, required=True)
    diagnosis.add_argument("--diagnosis", type=Path, required=True)

    diagnosis_review = subparsers.add_parser("prepare-diagnosis-review")
    diagnosis_review.add_argument("--run-root", type=Path, required=True)

    diagnosis_approval = subparsers.add_parser("approve-diagnosis")
    diagnosis_approval.add_argument("--run-root", type=Path, required=True)
    diagnosis_approval.add_argument("--approval-text", default="")

    status = subparsers.add_parser("status")
    status.add_argument("--run-dir", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "prepare-project":
        _require_new_model_contract(args.model, args.reasoning_effort)
        if args.report_period is None:
            require_project_materials_stage(args.project_root)
            require_project_parameters_stage(args.project_root)
        else:
            require_project_materials_stage(args.project_root, args.report_period)
            require_project_parameters_stage(args.project_root, args.report_period)
        analysis_path = resolve_project_analysis_summary(
            args.project_root,
            args.analysis_summary,
            args.report_period,
        )
        selected = select_project_targets(_read_json(analysis_path))
        if args.only_valve:
            requested = set(args.only_valve)
            selected = [
                item
                for item in selected
                if item["folder"] in requested
            ]
            found = {item["folder"] for item in selected}
            missing = sorted(requested - found)
            if missing:
                raise ValueError(
                    "project target not found: " + ", ".join(missing)
                )
        experience_text = load_offline_experience()
        result = prepare_project_runs(
            selected,
            project_root=args.project_root,
            project_id=args.project_id,
            experience_text=experience_text,
            run_root=args.run_root,
            diagnosis_version=args.diagnosis_version,
            parent_run=args.parent_run,
            revision_reason=args.revision_reason,
            report_period=load_project(args.project_root, report_period=args.report_period).report_period_key,
            model=args.model,
            reasoning_effort=args.reasoning_effort,
        )
    elif args.command == "prepare-integrated-diagnosis":
        result = prepare_integrated_model_request(
            args.run_dir,
        )
    elif args.command == "submit-integrated-diagnosis":
        result = submit_integrated_model_result(
            args.run_dir,
            _read_json(args.result),
        )
    elif args.command == "submit-diagnosis":
        result = submit_diagnosis(
            args.run_dir,
            _read_json(args.diagnosis),
        )
    elif args.command == "prepare-diagnosis-review":
        result = prepare_diagnosis_review(args.run_root)
    elif args.command == "approve-diagnosis":
        result = approve_project_diagnosis(
            args.run_root,
            args.approval_text,
        )
    else:
        result = get_status(args.run_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
