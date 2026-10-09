# ValVue3 离线诊断新项目工作流

多阶段和批量执行同时遵守[上下文与模型调用记账](context-and-cost-control.md)：主 Agent 直接串行执行，不使用子代理，项目内模型调用统一记录；主线程常规进度读取门禁摘要和阶段汇总，异常时定向核对完整证据。

在初始化新客户项目或将首批入站文件纳入标准项目时读取本文档。已有项目的报告生成、诊断复核和交付门禁以 `project-contract.md` 和 `SKILL.md` 为准。下文“提交后停止/等待”均先核对当前对象是否已有有效授权；内容核验的明确委托按项目合同登记，不能冒称人工校验。

## 1. 初始化

首次使用或更换 Python 环境时，先按[运行环境](runtime-environment.md)安装并核对依赖。然后在当前已激活的 Python 环境中执行：

```bash
python .agents/skills/valvue3-offline-diagnostic/scripts/init_offline_project.py \
  --project "离线诊断/projects/<项目名>" \
  --customer "<客户名称>" \
  --report-title "<报告标题>" \
  --report-date "YYYY-MM-DD"
```

- `--project` 必须是用户明确指定的目录；脚本不会根据客户名称推测路径。
- 脚本使用 Skill 内的 `assets/project.toml` 模板，不会把共享引擎或通用经验库复制进项目。
- 已存在 `project.toml`、`project_experience.json`、`extraction_overrides.json` 或 `experience_log.md` 时停止，不覆盖任何现有内容。
- 脚本不提供强制覆盖、删除或清空参数。新项目不再创建项目经验库或经验记录表；旧文件保留。人工修正直接提炼到共享经验正文。

初始化后的最小项目结构为：

```text
<项目>/
├── project.toml
├── raw/
├── specs/
├── inbound/
├── experience/
│   ├── extraction_overrides.json
│   └── feedback_inbox/
├── work/
└── output/
```

新项目模板的功能语义必须是：Specification 和气路页默认启用，普通 Accessory Sheet 默认关闭；“现场信息清单”“整体判断”“首要关注”“后续复测、维护和检修建议”四个总览板块默认关闭。对应配置为 `include_specifications = true`、`include_air_piping_pages = true`、`include_accessory_pages = false` 及四个值为 `false` 的独立 presentation 开关。

### 生产与调试物理隔离

正式项目位于 `离线诊断/projects/<项目名>/`。`work/` 保存流水线状态、内部检查及相关临时脚本/评审副本；`output/` 保存版本化报告候选与正式交付件。

一次性版式原型、手工试验文件和对比素材放 `离线诊断/development/<项目名>/<实验名>/`；已确认内容的排版修订走 format-only 路径。试验不创建生产 `project.toml`、不作正式入口或交付件。确认结果后修改共享 Skill 与测试，再从生产项目生成，不能复制试验成品交付。

## 2. 第一阶段：首批资料归档与用户批准

初始化之后，将新收到的 PDF、devdata、规格书、附件页和其他说明材料原样放入 `inbound/`，然后立即执行：

```bash
python .agents/skills/valvue3-offline-diagnostic/scripts/run_offline_report.py \
  --project "离线诊断/projects/<项目名>" \
  --prepare-materials
```

命令会加载并验证 `project.toml`，记录当前文件的相对路径、类型、大小和 SHA-256，并只用 PDF 原生文字识别资料类型、曲线数量及执行状态。它不会 OCR、提取设备/规格/运行参数、移动文件或形成诊断。输出：

- `work/material-readiness/readiness.json`：机器可读的逐阀资料、缺失项、一致性问题和下一步；
- `work/material-readiness/readiness.md`：逐阀报告/源数据矩阵、曲线数量与执行状态矩阵、规格与项目资料矩阵。
- `work/stage-gates/material-archive-report.json` 和 `.md`：提交用户批准的冻结副本，带当前输入与稳定报告指纹。
- `work/stage-gates/material-archive-report.html`：与冻结 JSON 同源的自包含审核网页，适配电脑和手机。

矩阵口径：

- Sequencer 与 ValvXpert 任一存在即可满足测试报告资料；
- DTM/Active 或含已解析 DTM 数据的 ValvXpert 任一存在即可满足 DTM 数据资料；
- 五类曲线分别显示已识别张数，Failed、Cancelled、Aborted、Incomplete 和 Not Completed 显示原状态；没有曲线显示“缺失”，但不阻断流程；
- Specification、气路页和普通 Accessory Sheet 按独立角色显示，只有可靠匹配到当前阀门才显示已具备；
- 每台阀门同时检查阀门描述、装置、失效后果、有无副线和现场备注；
- 原生文字不足时，报告类型、曲线和规格匹配显示“待识别/待匹配”，不得为填满第一阶段清单而启动 OCR。

根据准备报告逐项整理：

1. 通过报告内部位号、设备序列号或其他可核对证据确认归属。
2. 将诊断原始文件规范到 `raw/<阀门位号>/`；共享引擎只读取位号目录的直接子文件。
3. 将规格书和附件页保留在 `specs/` 或 `specs/<阀门位号>/`。
4. 未能可靠确认位号、文件冲突或哈希变化时，将文件保留在 `inbound/` 等待复核，不按文件名猜测。
5. 整理后重新运行 `--prepare-materials` 更新文件盘点和冻结归档报告。缺失项不自动禁止用户批准，但不得在未批准时进入参数提取。

生成冻结归档报告后，用本机只读 HTTP 服务发布 `material-archive-report.html`，并通过 Tailscale Serve（不得使用 Funnel 或公网托管）映射同一页面。服务可用时同时给出本机 URL 与 Tailscale 手机 URL；发送前分别请求确认网页可用且属于当前项目。若服务不可用且用户未指定手机审核，直接提供当前本地 HTML/Markdown 提交审核；明确要求手机入口时继续修复该入口，并如实区分其交付状态。网页发布不构成批准，也不允许继续参数提取。

持续使用的审核入口应由已登记的用户服务托管，确认服务目录、重启策略和实际可访问性，避免终端结束后失效；先检查现有服务再决定是否新建。以下命令只演示端口映射，不证明服务已持久化。

推荐使用固定本地端口和固定 tailnet 路径，便于后续项目复用：

```bash
python -m http.server 18767 \
  --bind 127.0.0.1 \
  --directory "离线诊断/projects/<项目名>/work/stage-gates"

sudo tailscale serve --bg \
  --set-path /valvue3-stage-gate \
  http://127.0.0.1:18767
```

对话中提供：

- 本机：`http://127.0.0.1:18767/material-archive-report.html`
- 手机：`https://<本机 MagicDNS 名称>/valvue3-stage-gate/material-archive-report.html`

若端口已有该服务，先确认它正在服务当前项目的 `stage-gates` 目录；不得把上一客户项目的网页继续暴露在固定 Tailscale 路径上。需要管理员密码时按工作区规则使用本机图形密码框，不在聊天中索取密码。

将当前审核报告和已验证的可用地址交给用户；尚无当前版本批准时暂停依赖该批准的下一阶段。收到任何含义明确的批准后，原样登记：

```bash
python .agents/skills/valvue3-offline-diagnostic/scripts/run_offline_report.py \
  --project "离线诊断/projects/<项目名>" \
  --approve-materials "<用户批准原文>"
```

批准写入 `work/stage-gates/materials-approval.json`。它绑定冻结报告、与归档有关的项目配置，以及 `inbound/`、`raw/`、`specs/` 和相关上下文文件指纹；任何绑定资料新增、移动、替换或修改后，批准自动失效。此时只重整受影响阀门，但仍需提交一份新的完整项目归档报告并再次获得批准。后续只改变报告输出或诊断运行绑定不使该批准失效。

`raw/` 保存原始诊断测试资料；可靠匹配的规格书、附件和现场上下文也是正式证据，必须保留各自来源绑定。客户位号映射、人工覆盖、客户事实和反馈来源留在当前项目；不得写入共享 Python 或通用经验库。

## 3. 配置预检

完成首次盘点后可以执行配置预检：

```bash
python .agents/skills/valvue3-offline-diagnostic/scripts/run_offline_report.py \
  --project "离线诊断/projects/<项目名>" \
  --dry-run
```

只有输出 `status=dry_run` 且 `work/run_manifest.json` 已生成时，才算初始配置可用。新模板不包含 `paths.formal_diagnosis_run`；该路径只能在正式诊断包已冻结且通过哈希校验后填写。

## 4. 第二阶段参数提取与用户批准

只有第一阶段批准有效时才能运行第二阶段。第二阶段从提取全部所需参数开始：

```bash
python .agents/skills/valvue3-offline-diagnostic/scripts/run_offline_report.py \
  --project "离线诊断/projects/<项目名>" --extract-materials
```

参数提取后先读取[资料参数提取工作流](material-extraction-workflow.md)中的输出合同，检查逐阀主参数矩阵、完整事实 JSON、`extraction.md`、补充料单和待复核队列。只对低置信度、表格关系不可靠或页面角色不确定的项目使用 `codex exec` 复核；高置信度结果及可靠匹配的新页面不因首次出现而强制看图。人工确认的冲突值写入 `experience/extraction_overrides.json`。资料不全仍可继续，但缺失、冲突和未解决项必须明确记录及其对后续诊断的影响。

第二阶段完成后提交以下审核快照；尚无当前版本有效批准时，暂停依赖该批准的步骤：

```text
work/stage-gates/parameter-extraction-report.md
work/stage-gates/parameter-extraction-report.json
```

收到用户明确批准后登记项目级批准：

```bash
python .agents/skills/valvue3-offline-diagnostic/scripts/run_offline_report.py \
  --project "离线诊断/projects/<项目名>" \
  --approve-parameters "<用户批准原文>"
```

批准写入 `work/stage-gates/parameters-approval.json`。原始资料、归属、页面角色、人工覆盖、低置信度复核、提取 schema 或规范化语义变化都会使批准失效；资料变化后只重算受影响阀门，但必须重新提交完整项目审核报告并重新批准。Continuous Diagnostics 缺失、无法解析或没有对应数据时，在人读审核报告中统一显示“无数据”，具体原因码只保留在 JSON。

如果 `--prepare-materials` 检测到上一冻结版本中存在来源哈希未变化的阀门，第一阶段批准必须同时登记 `--unchanged-reuse-policy reuse_unchanged` 或 `reanalyze_all`。归档报告先提示来源未变化，第二阶段报告再比较实际参数值、状态、单位、冲突和技术页物理绑定。选择复用不等于自动继承整阀结论；只有对应证据层的严格哈希一致时才可复用。选择全部重新处理时，不继承旧观察或整阀结论。

## 5. 第三阶段：每阀一体化读图与诊断、审核及冻结

只有第二阶段批准有效时，才能按顺序运行曲线准备、正式证据和诊断：

```bash
# 渲染曲线并生成逐图清单
python .agents/skills/valvue3-offline-diagnostic/scripts/run_offline_report.py \
  --project "离线诊断/projects/<项目名>" --prepare-curve-review

# 生成正式单阀诊断的结构化证据快照
python .agents/skills/valvue3-offline-diagnostic/scripts/run_offline_report.py \
  --project "离线诊断/projects/<项目名>" --prepare-formal-evidence

# 自动加载唯一通用经验全文，创建正式诊断运行
python .agents/skills/valvue3-offline-diagnostic/scripts/formal_diagnosis_tools/single_valve_workflow.py \
  prepare-project \
  --project-root "离线诊断/projects/<项目名>" \
  --project-id "<稳定项目 ID>" \
  --run-root "离线诊断/projects/<项目名>/work/formal_diagnosis/runs/<诊断版本>" \
  --diagnosis-version "<诊断版本>" \
  --parent-run "<父版本或 none>" \
  --revision-reason "<创建或修订原因>" \
  --report-period "<报告批次键>" \
  --model gpt-6-sol --reasoning-effort high
```

进入正式诊断前必须用机器可读矩阵证明逐阀字段完整：Sequencer 必须保留完整时间窗、定位器/运行快照、五个累计运行字段、活动与历史故障、完整任务表，以及 Run Ramp 回差/死区/线性度各自最大、平均、最小值；Specification 和气路页则按 `source_sha256 + one_based_page_number` 核对物理身份。源中有标签而解析为空、正式证据缺字段或报告未展示都是门禁失败。正式模型请求不得包含旧报告评级、旧诊断或 `analysis_summary.final_rating`。

气路页、Specification 或附件页的页面角色无法由结构化证据可靠确定时，才用一次 `codex exec` 实际复核这些页；同批页面可合并为一次调用。仅因页面首次出现或页面新增、但自动归属已可靠的页面，不强制看图。该复核只确认页面归属，不重看曲线，不产生诊断、评级或建议。

`--prepare-ocr` 只是排障或兼容旧自动化时单独准备缓存的底层命令，标准新项目不需要先运行它。

`prepare-project` 以 `--report-period` 选择本次批次，并按该批次核验资料与参数门禁。默认读取该批次工作目录的摘要；显式 `--analysis-summary` 若指向另一路径，必须位于项目 `work/` 内，且相邻 `analysis_summary.source.json` 须绑定项目绝对路径、批次键和摘要 SHA-256。新运行模型固定为 `gpt-6-sol` high；旧运行保留原清单约定。

初始化运行后，每台阀门先生成一体化模型请求：

```bash
python .agents/skills/valvue3-offline-diagnostic/scripts/formal_diagnosis_tools/single_valve_workflow.py \
  prepare-integrated-diagnosis \
  --run-dir "<运行根目录>/<阀门位号>"
```

按[第三阶段单阀一体化模型合同](integrated-valve-diagnosis.md)，主 Agent 直接逐阀串行执行，不使用子代理。每阀通过 `run_metered_codex.py` 执行一次 `gpt-6-sol`、`reasoning_effort=high` 的 `codex exec`，将完整证据请求与该阀全部曲线、技术页一次提交，并同时输出逐图客观观察、逐图工程判读、整阀评级与综合结论；不得另设单张读图环节，也不增加强制单阀探针。再提交该模型的原始结果：

```bash
python .agents/skills/valvue3-offline-diagnostic/scripts/formal_diagnosis_tools/single_valve_workflow.py \
  submit-integrated-diagnosis \
  --run-dir "<运行根目录>/<阀门位号>" \
  --result "<单次 codex exec 输出.json>"
```

提交工具验证模型来源、图像覆盖、证据引用和评级边界，将同一结果拆为观察与诊断字段，不计算或改写评级。整阀继承另按[严格复用规则](project-contract.md#reuse-and-version-rules)执行，不向新模型结果拼入旧结论。

5级只表示整阀有效证据整体不足、影响无法判断。性能测试缺失或失败时，仍保留阶跃、斜坡等独立证据支持的判断，写清缺口及影响；补测按关键判断需要与实施条件提出，必要安全验证保留条件和未验证前限制。单项缺口不自动判5级。

全部草稿完成后生成精简诊断审核报告：

```bash
python .agents/skills/valvue3-offline-diagnostic/scripts/formal_diagnosis_tools/single_valve_workflow.py \
  prepare-diagnosis-review \
  --run-root "<运行根目录>"
```

命令先验证每台阀门的诊断 schema、证据绑定和曲线覆盖，再生成：

```text
work/stage-gates/diagnosis-review-report.md
work/stage-gates/diagnosis-review-report.json
```

Markdown 只保留评级分布、逐阀一行结论，以及活动故障、未成功测试、信息不足和影响结论的不确定项。完整逐图诊断和内部引用只保留在草稿与机器 JSON 中。提交后核对当前版本有效批准；缺少批准时暂停冻结。

收到用户明确批准后，使用一条项目级命令原样登记批准并冻结全部阀门：

```bash
python .agents/skills/valvue3-offline-diagnostic/scripts/formal_diagnosis_tools/single_valve_workflow.py \
  approve-diagnosis \
  --run-root "<运行根目录>" \
  --approval-text "<用户批准原文>"

python .agents/skills/valvue3-offline-diagnostic/scripts/formal_diagnosis_tools/single_valve_workflow.py \
  status \
  --run-dir "<运行根目录>/<阀门位号>"
```

批准记录写入 `work/stage-gates/diagnosis-approval.json`，并绑定审核报告指纹和全部草稿哈希。`approve-diagnosis` 在冻结任何阀门前重新验证全部草稿；审核后任何草稿变化都会拒绝冻结。验证通过后，命令统一固定证据、观察、经验全文、诊断和版本元数据的哈希，并同步项目级 `00_project_run_manifest.json`。全部目标都为 `frozen` 且单阀 `status` 返回 `freeze_valid: true` 后，才在 `project.toml` 中将 `paths.formal_diagnosis_run` 指向该运行根目录。报告引擎直接验证并读取冻结包，不需要中间转换文件。

同一已批准版本冻结中断时，重跑 `approve-diagnosis --run-root "<运行根目录>"`，可以省略 `--approval-text`。命令保留原批准原文和时间，重新核对全部输入及草稿，验证已完成的冻结文件并续行剩余阀门；不覆盖已有冻结文件。首次批准仍需用户原文，输入变化或文件损坏时拒绝恢复，不能以重试替代新版本审核。

补全任一结构化证据字段都必须新建正式诊断子版本，不编辑父版冻结包，也不复制父版整阀结论后冻结。只有图片 SHA-256、裁图范围和已验证观察哈希全部一致时可复用该客观观察；整阀必须结合新的完整 `structured_evidence` 重新诊断。该投影的语义哈希覆盖值、单位、状态、任务结果、故障和页面物理身份，排除绝对路径、生成时间、临时缓存路径和输出版本号。

不含评级和诊断结论的纯数据版不需要冻结，也不读取冻结诊断、人工诊断/曲线覆盖、算法审计快照或项目经验评级。

## 6. 第四至第七阶段：报告、交付与反馈

### 第四阶段：报告内容成稿

全部正式诊断冻结并绑定到 `paths.formal_diagnosis_run` 后，使用 `--generate-review` 默认生成包含完整结论的“待人工校验版” DOCX；若旧配置显式设置了 `presentation.blank_conclusions = true`，须在内容成稿前改为 false。结论留空的草稿不能作为第五阶段内容确认母版。报告只读取该有效冻结运行及绑定同一审核母版和冻结身份的已登记人工覆盖；缺少必需最终字段、覆盖明确清空必需字段或绑定不一致时拒绝生成，不回退到算法、旧摘要或模板文字。随后使用 `--check-report-information <DOCX>`；如已有内部检查 PDF，可追加 `--check-report-pdf <PDF>`。用户只看到阀门、字段、曲线和技术资料页四组计数，详细缺项写入项目 `work/information-acceptance/`。纯数据版仍使用 `--generate-data-only`，不需要诊断冻结。

### 第五阶段：内容确认与定点修订

向用户提交实际 DOCX 和一键信息验收摘要，按项目合同完成内容确认或已明确委托的 Agent 内容核验；不把既有授权再问一遍。参数、客观观察、诊断、报告映射和版式问题必须回到各自拥有层，只重算受影响范围。人工回传使用 `--ingest-review`；只有用户明确要求时才使用 `--generate-business`。内容已经确认、只需分页或排版整改时，使用 `--prepare-format-only` 创建子版本，禁止重跑 OCR、参数提取、曲线观察、诊断或报告生成。

### 第六阶段：正式交付

正式 PDF 通过唯一报告入口的 `--export-wps-docx` 调用共享 WPS RPC 导出器生成。相同未变化产物不重复自动交付检查或代表页视觉复核；发生修复后重验受影响项，并完成登记命令要求的检查。`delivery_manifest.json` 达到 `wps_delivery_validated` 后才交付同名 DOCX 与 PDF。

### 第七阶段：反馈闭环（按需）

收到反馈后按[人工反馈学习](human-feedback-learning.md)核对原文差异与上下文，去客户化、提炼、去重并更新唯一经验正文。客户事实、原阀评级与数值留在原件；不增设候选库或逐条批准。

第一、第二阶段批准之后只保留两次用户确认：第三阶段诊断审核确认和第五阶段 DOCX 内容确认。内部自动检查、冻结哈希和代表页复核不拆成额外用户门禁。

客户项目是本机运行数据，不是 Skill 发布内容。不得将新客户项目、原始证据、冻结包、中间产物或交付件加入 Git。

## 7. 简化验收顺序

开发时只运行与当前修改直接相关的小测试；共享修改稳定后运行一次完整回归。相同未变化的客户报告只执行一次一键信息验收和一次代表页视觉复核；内容或版式修复后重验受影响项及登记命令要求的检查。默认验收不单独向用户暴露或阻断于哈希、书签、内部路径选择等实现细节；这些细检只在信息验收发现字段、曲线、页面或客户文字问题时用于定位根因。OCR、已验证观察和冻结诊断不得因纯报告验收失败而重跑。
