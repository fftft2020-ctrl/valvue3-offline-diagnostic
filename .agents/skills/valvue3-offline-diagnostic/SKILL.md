---
name: valvue3-offline-diagnostic
description: 用于处理 ValVue3/VV3 离线诊断项目、Sequencer 或 Active Report PDF、devdata、离线曲线、规格书、报告、人工反馈及共享引擎维护。不用于仅涉及监测窗口或在线趋势的诊断。
---

# ValVue3 离线诊断

## 范围与入口

处理 ValVue3 离线证据，在线任务用 `online-valve-diagnostic`，两域数据与经验隔离。当前以独立 Skill 分发，不要求 Docker。共享引擎只在本 Skill；正式项目位于 Git 忽略的 `离线诊断/projects/`，只保存输入、配置、反馈和产物。试验及历史目录不作生产入口。

报告、参数、曲线准备和交付的唯一入口：

```bash
python .agents/skills/valvue3-offline-diagnostic/scripts/run_offline_report.py \
  --project "<明确的正式项目目录>" <阶段参数>
```

运行前按 `project.toml` 和当前批次解析路径，核对产物、指纹及批准，从首个未完成或失效阶段继续。第三阶段使用 `scripts/formal_diagnosis_tools/single_valve_workflow.py`，命令见下表。

## 按任务读取

首次使用本 Skill 先读[独立安装与分发合同](references/distribution-and-installation.md)，其中业务边界脱离原工作区仍生效；运行自检不代表实际诊断或正式 PDF 已验收。

首次进入正式项目读[授权与状态](references/project-contract.md#authorization-and-report-status)和[目录约定](references/project-contract.md#required-layout)，再选当前任务章节；已读且未变更的内容不重复加载。共享代码维护只读受影响路径，不启动客户审批。

| 当前任务 | 所需细则与命令 |
| --- | --- |
| 独立安装、环境自检、分发或版本追溯 | [独立安装与分发合同](references/distribution-and-installation.md)、[运行环境](references/runtime-environment.md) |
| 初始化、首批归档、补充资料 | [新项目工作流第 1–3 节](references/new-project-workflow.md)、[资料归档](references/project-contract.md#material-readiness-report)、[输入规则](references/project-contract.md#input-rules) |
| 参数提取、冲突或低置信度复核 | [参数提取工作流](references/material-extraction-workflow.md)、[输入规则](references/project-contract.md#input-rules) |
| 逐阀诊断、审核、冻结或继承 | [一体化模型合同](references/integrated-valve-diagnosis.md)、[新项目工作流第 5 节](references/new-project-workflow.md)、[诊断与冻结](references/project-contract.md#integrated-diagnosis-and-freeze)、[继承规则](references/project-contract.md#reuse-and-version-rules) |
| 生成或修订报告内容、纯数据报告 | [输出规则](references/project-contract.md#output-rules)、[信息完整性验收](references/project-contract.md#single-required-information-acceptance)、[报告质量](references/project-contract.md#report-quality-checks) |
| 人工回传与经验积累 | [人工回传路径](references/project-contract.md#final-human-calibration-fast-path)、[人工反馈学习](references/human-feedback-learning.md) |
| 已确认内容的排版修订 | [纯排版子版本](references/project-contract.md#confirmed-content-formatting-revision)、[报告质量](references/project-contract.md#report-quality-checks) |
| 按装置汇编已确认报告或已核验派生稿 | [输出规则中的 `--assemble-units`](references/project-contract.md#output-rules) |
| 正式 PDF 导出与交付 | [WPS 交付命令](references/project-contract.md#wps-delivery-command-sequence)、[报告质量](references/project-contract.md#report-quality-checks) |

批量或看图调用读取[上下文与计量](references/context-and-cost-control.md)；首次使用或更换 Python 环境读取[运行环境](references/runtime-environment.md)；遇到旧配置/命令读取[退役功能](references/retired-features.md)。旧分步读图方案只作历史参考。

## 执行要点

- 四个决策分别绑定资料、参数、诊断草稿和实际 DOCX；已有有效批准续行，明确委托 Agent 核验按授权登记，不冒称人工校验。未批准资料不做 OCR/参数提取，未批准参数不准备曲线或新报告，未批准诊断不冻结；只读检查、人工终校、纯排版和 WPS 走各自路径。
- 主 Agent 直接执行，不用子代理。新诊断逐阀串行，经 `run_metered_codex.py` 以 `gpt-6.1-sol`、`reasoning_effort=high` 在同次调用中读取全部原始资料、结构化证据、经验、曲线和技术页，输出观察与诊断；不拆图拼结论。逐原件记录路径、哈希和真实读取状态；未读或失败只留失败记录，不晋级诊断草稿或冻结。读取声明不代替工具轨迹及工程内容复核。失败与缺口保留，补测按必要性和实施条件提出；具体评级、反证与写作要求见一体化模型合同。
- 唯一经验为 [common-offline-experience.md](references/common-offline-experience.md)，每阀全文入 prompt 一次并自动留存，不预筛、不裁剪、不要求采用编号或额外批准。人工修正按反馈学习细则去客户化合并到同一正文。
- 冻结包只读；整阀继承须全部诊断输入一致，变化则建子版本。诊断报告只读有效冻结结果及同版本已登记人工覆盖，算法快照不作最终结论；纯数据报告不读诊断或评级。
- DOCX 为主输出，新审核版含完整冻结结论；客户正文按报告质量细则检查，不泄露内部路径、反馈来源或审核状态。内容确认后只改排版须建 format-only 子版本。正式 PDF 经共享 WPS RPC 导出、自动检查和实际视觉复核，取得 `wps_delivery_validated` 才能交付；LibreOffice PDF 仅作内检。
