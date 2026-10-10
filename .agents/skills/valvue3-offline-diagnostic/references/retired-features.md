# 已退役功能与迁移

2026-09-05：保留当前完整主流程，移除旧报告模式和历史迁移工具。

| 已退役功能 | 当前行为与替代方式 |
| --- | --- |
| `presentation.profile = "standard"` 及其独立封面、目录、表格和逐阀排版 | 配置预检和生成均明确拒绝。删除该配置或改为 `business_dashboard`；未设置 profile 时也使用当前模板。 |
| `report_layout.toc_append_label`，包括报告周期覆盖中的同名配置 | 预检拒绝；删除旧目录专用设置。阀门选择、追加顺序和报告周期继续保留。 |
| 无 `project.toml` 的历史项目双摘要对比 | `validate_offline_regressions.py` 只验收当前项目合同；缺配置、当前摘要、阶段批准或提取证据均报错，不回退到 `work/shared_engine`。 |
| `scripts/extract_legacy_project_data.py` | 已删除。新项目通过 `init_offline_project.py` 初始化；项目差异写入配置和项目 JSON，不再从旧 Python 常量搬运。 |
| `scripts/extract_offline_regression_fixtures.py` 及旧项目夹具构建逻辑 | 已删除。已提交的匿名回归夹具、schema、敏感信息检查和回归比较保留，既有基准不改写。 |
| `formal_diagnosis_tools/build_blind_pilot.py` 独立试验 CLI | 已删除。新诊断完整加载通用经验正文；`case_retrieval.py` 匹配代码也已删除，仅保留当前阀门证据整理。 |
| 单阀工作流的独立 `prepare` / `submit-observations` | 已退役；使用 `prepare-project`、`prepare-integrated-diagnosis` 与 `submit-integrated-diagnosis`，随后执行项目诊断审核和批准。共享准备/观察校验与历史冻结读取保留。 |
| `--prepare-spec-ocr` 与独立规格 OCR 实现 | 明确报退役错误。使用 `--extract-materials`；仅需 OCR 排障时使用 `--prepare-ocr`。两者继续受原有阶段门禁约束。 |
| `spec_ocr_cache.json` 镜像、旧规格缓存自动迁移、`specification_audit.json` 写出 | 不再生成或读取镜像。当前规格读取直接核对统一缓存的来源哈希、策略与物理页覆盖。旧文件保持原样；缺少有效统一缓存时要求重新提取并审核参数。失败页已有文字与失败状态保留，默认不重试。 |
| `prepare-project` 默认读取旧 `work/shared_engine/analysis_summary.json` | 不再自动回退。默认通过 `load_project` 读取当前批次有效 `paths.work` 下的 `analysis_summary.json`；缺失时明确拒绝，需先准备正式证据。显式 `--analysis-summary` 输入约定保留。 |
| 旧分步 `03_observation_template.json` 与旧工作流初始化标记 | 新运行直接采用一体化工作流。正式请求/结果模板仍由 `prepare-integrated-diagnosis` 生成；不删除观察验证或冻结包的证据账目。 |
| `visual_review` 中旧 `load_codex_curve_interpretations`、`customer_curve_interpretations`、`load_curve_interpretations` 包装器 | 删除无生产调用的接口。当前流水线统一使用 `curve_review_readiness`，保留人工优先、跨周期过滤、图像严格对齐与缺项阻断。 |
| 报告阶段的 `rate_valve`、经验自动评级、默认问题/结论/建议及自动曲线判读模板 | 已删除。含诊断报告只消费有效冻结诊断和绑定该版本的人工覆盖；必需字段缺失时拒绝生成。总体文字仅汇总已确认逐阀结论与建议。纯数据版不读取诊断、不评级。 |
| 重复的 `05_diagnosis_template.json` 写出 | 已删除；一体化提交只保留实际消费的 `05_diagnosis_draft.json`。 |

保留资料归档与参数审批、逐阀诊断审核与冻结、审核版/纯数据版/商务版、人工回传合并、纯排版子版本、LibreOffice 内部检查及 WPS 正式交付。原始资料、旧报告、项目经验和冻结版本不做迁移或删除。

旧 `standard` 排版专用测试随模式退役；字段展示、内部文字过滤和故障状态检查继续验证共享实现。当前模板由 `test_business_report.py` 验证，默认模板和退役配置拒绝由 `test_presentation_profile.py` 验证。旧项目提取/双摘要对比测试退役，匿名基准完整性检查和当前项目验收测试保留，并增加禁止历史回退的检查。

旧规格 OCR 与迁移专用测试转为统一缓存读取测试：保留原生/扫描文字、递归同名文件、来源与策略失效、物理页缺失/重复、失败缓存与重试等行为验证。原有继承、审批、冻结测试改用一体化路径准备诊断，不删除这些保护性测试。

退役加载包装器的行为测试迁到当前曲线审核入口；仅删除无生产用途的客户字段裁剪测试，以及已由当前入口覆盖的重复缺项测试。曲线校验合并为一次执行，保留最终生成和普通生成的原有门禁。

算法评级函数仅用于第三阶段的审计快照，快照随诊断包冻结。报告读取时只核对结构、输入绑定与冻结哈希，不重新计算算法评级。旧冻结包没有审计快照但最终字段完整时仍可只读生成报告，审计状态为 `legacy_unavailable`，数值为空；不得补算、默认填 5 级或原地修改旧包。新快照不能降级为旧包状态。

人工回传按字段覆盖同一冻结版本，缺省字段继承，明确清空必需结论或建议时拒绝生成。回传必须绑定实际审核母版 DOCX 及其归档运行清单；未绑定的旧人工映射不能自动套用当前冻结运行。项目阶段批准、冻结校验和正式交付门禁继续有效。

## 经验正文迁移（2026-09-16）

旧 JSON 规则/案例库、独立案例 schema、候选检索、采用记录、`--prepare-learning` 和 `--apply-learning` 已移除。唯一维护入口为 `common-offline-experience.md`；Agent 在人工回传后直接提炼并更新正文。原始回传及历史冻结包保留，旧冻结格式只用于兼容读取。

一次性混合 JSON 分库脚本 `scripts/partition_valve_experience.py` 已退役：没有生产调用，输出的离线 JSON 格式已不再维护。在线/离线隔离及在线历史脚本不改写通用库的验证继续保留。
