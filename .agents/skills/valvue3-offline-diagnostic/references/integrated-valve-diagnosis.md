# 第三阶段单阀一体化模型合同

本合同用于第二阶段参数批准后的正式诊断。由主 Agent 直接逐阀串行执行，不使用子代理，
每阀执行一次 `gpt-6.1-sol`、`reasoning_effort=high` 的 `codex exec`；同一次调用同时读取全部原始资料、完整结构化证据、离线经验全文、
该阀全部曲线和已绑定技术页，并直接产生逐图判读与整阀诊断。

## 职责边界

- 模型决定每张曲线的工程判读、单图评级、整阀评级、综合结论和建议。
- 脚本只准备证据、附加图片、生成 schema、检查覆盖、绑定哈希和冻结结果。
- 脚本不得根据固定阈值、可见特征数量或旧报告计算评级，不得填充严重影响、反证据或建议。
- `analysis_summary.final_rating`、旧 `05_diagnosis.json`、旧报告评级和人工校验版不得进入新诊断模型请求。
- 客观观察与工程判读在 JSON 中分字段保存，但必须来自同一次模型调用。

## 单阀输入

先运行：

```bash
python .agents/skills/valvue3-offline-diagnostic/scripts/formal_diagnosis_tools/single_valve_workflow.py \
  prepare-integrated-diagnosis \
  --run-dir "<运行根目录>/<阀门位号>"
```

命令生成完整证据请求、模型 prompt、JSON Schema、结果模板，以及该阀全部
曲线和技术页的绝对路径清单。`prepare-project` 自动保存唯一通用经验全文到每阀 `00_experience.md`；请求保存 `experience_text`，prompt 直接包含同一全文和本阀完整结构化证据。模型必须读取 `evidence.source_files` 的全部原始资料（相对路径以 `source_locator.project_root` 为根），完整阅读结构化规格、测试状态和曲线。正式请求的阀门证据只能来自当前已批准资料。

经验不按关键词、分数、测试标签或 top-k 预筛，不另建检索系统。用户无需选择经验版本；运行中更新正文不改变当次输入。

经验正文和完整结构化证据在模型 prompt 中各提供一次。诊断与写作要求放在完整证据之前；内嵌 JSON 仅去掉排版空白，字段和值完整保留；请求 JSON 仍使用可读排版留存备查，不要求模型重复读取副本。原始文件的读取和全部曲线、技术页的视觉检查仍须完成。

当前测试失败、活动故障及现场背景放在证据开头，完整证据字段和值保持不变。任何 Failed、Cancelled、Aborted、Incomplete 或 Not Completed 项均保留原状态，不作为正常测试数值；影响最终运行模式或控制权的失败必须在结论或行动中保留状态限制与恢复验证要求。

## Codex Exec 运行合同

主 Agent 通过同一计量入口逐阀串行执行，不启动子代理编排。新正式诊断 `codex exec` 必须使用 `gpt-6.1-sol`、`reasoning_effort=high`；运行清单、输出 schema 和提交校验绑定同一约定，不约束主 Agent 的模型。已有 `gpt-6-sol/high` 运行和旧冻结包仍按原运行来源只读验证，不回填新模型来源；未声明模型的旧清单保留原有 `gpt-5.6-sol/medium` 兼容路径。
不得使用 Luna，也不得为单张图片另建读图调用。每张图各传一个
`--image`，并通过统一计量入口执行：

```bash
python .agents/skills/valvue3-offline-diagnostic/scripts/run_metered_codex.py run \
  --project-root "<项目目录>" \
  --stage integrated_diagnosis \
  --call-id "<诊断版本>.<阀门位号>.attempt-1" \
  --valve-tag "<阀门位号>" \
  --model gpt-6.1-sol \
  --reasoning-effort high \
  --cwd /tmp --ignore-user-config --ignore-rules \
  --read-allowlist "<运行根目录>/<阀门位号>/allowed_reads.json" \
  --output-schema "<03_integrated_diagnosis_output_schema.json>" \
  --output-last-message "<本阀结果.json>" \
  --image "<曲线或技术页 1>" \
  --image "<曲线或技术页 2>" \
  --prompt-file "<03_integrated_diagnosis_prompt.md>"
```

同一阀门的图片不得拆给不同模型分别下结论。如单次调用因客观上下文上限无法完成，
必须停止并报告，不得退回“脚本先评分、模型补文字”。

## 模型必须同时输出

对每张曲线：`observation`、`visible_features`、`visual_checks`、`diagnosis`、
`rating`、`action` 和 `confidence`。

对整台阀门：`final_rating`、`rating_conclusion`、`main_issue`、具体
`supporting_evidence`、具体 `counter_evidence`、`uncertainties` 和 `actions`。

`report_writing` 随实际模型请求进入提示词。报告正文按现场交接的写法：直接说问题，用“检查、核对、调整、准备”等动作说明下一步。 术语采用仪表检修常用说法，如“关阀超调、核对实际阀位、定位器输出压力、检查反馈杆、复核整定参数”；根据实际现象和配置选词，不机械替换，不混淆超调、迟滞、卡涩与内漏。历史报警、次数和建议按本阀处置需要取舍。`rating_conclusion` 简短收束等级、主要问题、处理方向及影响结论的必要限制，完整论证留在证据和不确定性字段，不把每条建议写成排查方案。`main_issue`、`issues` 和 `actions` 直接用于报告，应先用自然短句讲清现象、功能影响和处理顺序，只保留影响判断或行动的关键参数及单位；详细数值保留在证据字段和原有数据表。不同曲线指向同一问题时合并解释，建议按当前证据与客户实施条件写清对象、目的及必要验证要求，不罗列所有可能部件或测试步长。简洁不得删去重要运行历史、工况限制和未确认项，也不设字数、条数或关键词风格门禁。报告排版阶段不重新诊断、截断或改写冻结文字。

- 1级表示当前证据未见需处理异常；2级保留有限偏差或待核实现象，尚无充分证据证明功能受损或需要纠正性维护；3级须说明具体证据为何支持纠正性维护，而不只写“存在缺陷、需计划处理”。缺口本身不决定等级，已有独立功能缺陷不能因缺少数值阈值被淡化。
- 4级时必须填写真实 `functional_impact_evidence`，不得只写“数值偏大可能影响”。
- 使用标准判断合格、满足要求、超限或数值偏高/偏慢时，`metric_threshold_basis` 必须写明同一指标、方向、单位和测量起止点下的阈值及来源。T86、T63、Td、Average Error、Maximum Hysteresis、Dead Band、Linearity、Max Friction、Overshoot使用这些规范名称；完整行程时间单列，不能用响应阶段指标代替。缺少适用依据时在 `uncertainties` 保留限制，不生成一整套“阈值未知”的占位条目。
- 局部缺测或阈值依据缺失不阻断其他有效诊断。在 `uncertainties` 写清缺口、影响范围和不能确认的判断；影响报告结论的限制同时写入 `rating_conclusion`；只有关键判断需要且具备条件的补测才写入 `actions`，条件不足则保留限制。保留有独立证据支持的结论，只有整阀有效证据整体不足才判数据不足。缺少阈值不等于已确认超限，也不等于设备正常。
- “未发现可推翻当前评级的明确反证据”不是有效反证据。
- 必须明确说明满行程、到位、连续性和独立正常测试如何支持或限制当前评级。

复测不是整阀或逐图建议的默认收尾，未提供设备、窗口或实施条件时不假定客户可以复测。优先使用现有证据和可实施的针对性检查；必要复测说明关键目的、最小范围和时机。确属安全功能或修后验收要求的验证不能因现场暂不具备条件而省略，应写清所需条件及未验证前的限制。不能把“复测”机械换成同样不可执行的“核对”。

模型在同一次调用内完成内容核对：证据是否支持处置强度、指标与标准是否同口径、关键运行历史与当前配置是否体现在建议中、问题和行动是否重复。修正体现在既有输出字段，不添加经验采用记录、自评分、独立审查产物或用户门禁。程序提交通过只证明结构与覆盖有效，不能代替工程内容复核。

## 提交与验收

```bash
python .agents/skills/valvue3-offline-diagnostic/scripts/formal_diagnosis_tools/single_valve_workflow.py \
  submit-integrated-diagnosis \
  --run-dir "<运行根目录>/<阀门位号>" \
  --result "<本阀 codex exec 结果.json>"
```

提交工具只验证：单次 `gpt-6.1-sol` high 模型来源、逐原件读取声明的来源/哈希/状态与完整覆盖、图像 SHA-256 完整覆盖、字段边界、
4级功能影响证据和具体反证据。阈值适用性和缺口影响由模型结合完整证据判断，脚本不再用指标名与评价词匹配阻断提交；阈值引用与不确定性仍随诊断保留。经验编号、采用清单和未采用理由不属于输出或验收要求。
验收后使用 `run_metered_codex.py mark` 把同一 `call-id` 登记为
`accepted` 或 `rejected`；拒绝时在 `--reason` 中保留简短原因。

验收通过后，工具将同一模型输出拆分保存为 `03_observations.json` 和
`05_diagnosis_draft.json`。这一拆分只是审计结构，不得改写模型文字或评级。

提交器还在第三阶段根据当前结构化证据和已提交逐图结果生成确定性的
`algorithm_audit_snapshot`，保存数据评级、图形评级、综合算法评级和证据，随后与诊断包一起冻结。它只用于审计，不覆盖模型或人工最终诊断；报告阶段只能读取并验证该快照，不能重新运行评级算法。旧冻结包缺少快照时按 `legacy_unavailable` 只读处理，不补算、不默认填 5 级，也不修改旧包。

对 `needs_review=true` 的任何图像，整台阀门不得进入诊断审核。需要时可重做该阀的完整单次模型调用，
但不得单独重做一张图后与原综合结论拼接。

`needs_review` 只表示图像缺失、无法辨认、归属错误，或具体歧义使当前单图
判读不可靠；它不是设备需要检修的标志。当模型能写出明确的客观观察、工程
判读和评级时必须设为 `false`，设备有缺陷或需检修不构成 `true` 的理由。

新运行不生成规则/案例检索文件。经验全文作为诊断输入随原有冻结流程留存；旧冻结包保持原样，只读兼容。


## 逐原件读取声明与失败防线（0.1.2）

新请求要求顶层 `source_file_reads`，按 `evidence.source_files` 注册的每份原件恰好一项。每项包含 `relative_path`、`sha256`、`status`、`read_method`、`read_reference`、`failure_reason`。相对路径和 SHA-256 必须与本次证据一致；原件集合还须与结构化证据登记一致。文件种类由实际注册列表决定，不强制假设每阀都有 devdata；没有注册原件时仍须显式返回空数组。

模板默认 `status=unread`，不预填成功。模型须在同次调用中实际读取原件，填写实际读取方式和可定位的工具轨迹引用。状态可为 `read`、`failed` 或 `unread`；失败和未读须如实保留原因，不能把外层隔离探针、白名单、图片、结构化转录或文件存在当作模型读取成功。成功声明须有读取方式、轨迹引用，且不得同时填失败原因。

`failed`、`unread`、漏项、重复路径、错误哈希或缺少字段均拒绝晋级待审诊断。提交器在本地 `03_source_read_rejections/` 保留拒绝原因、证据哈希与原始结果，原运行和项目阶段不变，不生成新诊断草稿或冻结。失败不等于设备5级，也不能靠修改不确定性文字绕过。失败输出、同次调用工具日志和计量记录必须留存；修复读取能力后按原有版本与调用合同重新准备/执行，模型调用仍须有授权。

这是可审计的模型自报声明和结构/来源覆盖门禁，`reported_complete` 不代表脚本独立证明了原件被实际读取、理解或工程结论正确。工具轨迹与内容仍须核验；本版本没有解决或实证定位此前原件读取工具的底层失败。

审核、冻结、冻结包加载和继承均复核此合同，不能只通过重新绑定草稿或重算冻结哈希绕过失败记录。只有没有新合同标记的旧版已冻结包可按 `legacy_unrecorded` 只读兼容，不回填读取声明，不改写旧冻结文件，也不提升为新合同已核验。旧版未提交请求和待审草稿须新建版本并完成新合同；旧冻结包的无声明状态不能作为新诊断或新版本继承的已完成读取依据。
