# ValVue3 离线资料参数提取工作流

在完成第一阶段资料归档、向用户提交 `work/stage-gates/material-archive-report.md` 并取得有效批准后读取本文档。这是第二阶段入口：提取全部所需事实参数，不形成评级、诊断结论或检修建议。没有有效 `materials-approval.json` 时，共享入口必须拒绝本阶段命令。

## 1. 执行参数提取

先确认 `work/stage-gates/materials-approval.json` 仍绑定当前输入文件；新增、替换、移动或修改资料后必须回到第一阶段重新提交完整归档报告并取得批准。

```bash
python .agents/skills/valvue3-offline-diagnostic/scripts/run_offline_report.py \
  --project "离线诊断/projects/<项目名>" \
  --extract-materials
```

`--extract-materials` 会自动准备或复用 PDF 文本/OCR 缓存，无需事先运行 `--prepare-ocr`。`--prepare-ocr` 只保留为底层排障和兼容入口。

已绑定源文件哈希但上次本机 OCR 失败的规格书默认复用失败记录，避免每次重复超时。确认本机 OCR 恢复后，只能通过显式重试开关刷新这些页：

```bash
python .agents/skills/valvue3-offline-diagnostic/scripts/run_offline_report.py \
  --project "离线诊断/projects/<项目名>" \
  --extract-materials --retry-failed-ocr
```

`--retry-failed-ocr` 只能与 `--extract-materials` 或 `--prepare-ocr` 同用；不改变原始文件，也不绕过低置信度视觉复核。

优先读取共享入口已有的完整原生文本、结构化数据及有效 OCR 缓存。需要补充全文阅读或交叉核对，且现有结果不能满足时，对相应受支持文档使用 `$convert-documents-to-markdown` 获取辅助 Markdown 全文；不为所有文件重复增加转换前置步骤。大文档将转换结果写入 `work/document-markdown/<源文件SHA-256>.md`；转换结果是项目内部阅读缓存，不是新的证据源。必须保留源相对路径、源哈希和转换成功/失败状态；转换失败的文件仍按下述格式路由读取，不得静默跳过。

Markdown 阅读层的边界是：

- PDF 物理页码、局部标题、曲线区块和表格单元格归属仍以原 PDF 的逐页文本/OCR 和布局为准，不得根据扁平化 Markdown 重建页码。
- `.devdata`、XML、JSON 等不受支持或需要保留结构语义的文件继续使用结构化解析，不转成 Markdown 后再猜字段。
- OCR 可选：启用时扫描或纯图像 PDF 使用本机 OCR，不调用托管云端 Parse 上传客户文件；未启用时通过计量 `codex exec` 实际读原页，按下文视觉复核和人工确认路径补齐有依据的事实。脚本保留 OCR 失败记录，不保证自动提取完整；看不清或身份、页归属不明时保留待复核，不能跳过完整覆盖检查。
- 曲线形状、报告页面和规格书的视觉归属仍按工作区合同使用 `codex exec` 实际看图。

读取顺序是：

1. 优先使用完整原生/结构化读取，按需通过 `$convert-documents-to-markdown` 补充全文阅读层；
2. `.devdata`、JSON、XML 等继续读取结构化内容，CSV、XLSX/XLS 始终保留单元格结构，即使生成了辅助 Markdown；
3. PDF、DOCX/DOC、TXT/MD 继续读取原生文字、页码和表格，用于定位和证据引用；
4. OCR 启用时只对需要识别的页面/图片调用本机服务；未启用时直接实际读图，并保留原件定位、哈希、复核结果与参数确认；
5. 安全解包 ZIP 到 `work/intake-unpacked/<文件哈希>/`，拒绝路径越界、过多条目和过大解包。

旧诊断报告默认只进入补充料单，不作为当前证据。标准流程不读旧 `analysis_summary.json`、旧冻结包、旧评级或旧结论，也不覆盖它们；只有原文件哈希和 OCR 策略版本一致的页面文字缓存可以复用。

曲线清单必须保留 PDF 物理页边界。分类以源测试标题、`Step Type` 和局部测试参数为准；客户版式中的 `Run Signature Test Status`、`Run Step Test Status` 等通用说明不得覆盖同页的具体测试身份。同一物理测试跨页时只计一次，但同页的多个独立测试必须分别保留。纯参数表、结果表和封面参数块不得冒充诊断曲线；与当前页语义无法可靠对齐的裁图留待第三阶段曲线准备或本阶段的关系复核。

## 2. 读取输出

```text
work/evidence-extraction/
├── project-index.json
├── extraction.md
├── supplementary-materials.json
├── supplementary-materials.md
├── review-queue.json
├── codex-review-prompt.md
├── codex-review-schema.json
├── codex-review-results.json       # 完成 Codex 复核后才存在
├── review/                         # 待复核 PDF 页的渲染图
├── run-manifest.json
└── valves/<阀门位号>.json
```

第二阶段提交用户审核的冻结快照另写入：

```text
work/stage-gates/parameter-extraction-report.md
work/stage-gates/parameter-extraction-report.json
work/stage-gates/parameter-extraction-report.html
work/stage-gates/parameters-approval.json       # 用户批准后生成
```

先看 `extraction.md`，了解每台阀门已提取字段、缺失、冲突和待复核数量；再看 `supplementary-materials.md` 确认补充资料及采用情况。需要完整来源、原值、标准值、单位、置信度和候选值时，读取 `valves/*.json`。

对 Sequencer，每台阀的资料提取 JSON 必须保留以下规范化事实及来源：

- 完整开始/结束时间；Option Config、Device Mode、阀位、手动给定、输入信号、板卡/最低/最高温度；
- 行程、循环（同时接受 `Cycles` 与 OCR 变体 `Cyeles`）、全开/全关/近关累计时间；
- 活动故障和历史故障分开保存；Task Execution Summary 的每一行保留任务名、出现次序、开始/结束、持续时间和结果，非成功任务只是派生列表；
- Run Ramp 的回差、死区、线性度各自保存最大/平均/最小三值，共九值，不相加回差和死区。

每个字段必须保留原值/规范值、原单位/规范单位、规范化规则、源相对路径、源 SHA-256、物理页/记录定位、提取方法、置信度、候选值和冲突状态。如源中存在标签但解析为空，必须记为 `source_present_parser_missing` 或等价明确状态，不能记为 `source_missing`。

Specification、气路页和普通附件页必须分别记录 `specification`、`air_piping`、`accessory` 角色。页面引用至少包含源相对路径、`source_sha256`、一基页码、角色、图片路径和匹配依据。同一 `source_sha256 + one_based_page_number` 可同时具有多个角色，但物理页只渲染和插入一次；不同物理页不按标题或图像相似度合并。普通附件不得被标为气路页。

命令完成后会自动刷新 `work/material-readiness/readiness.json` 和 `readiness.md`。资料不全不会阻断第二阶段审核快照或用户批准；缺失影响必须保留，且没有有效第二阶段批准时不能进入第三阶段。

## 3. Codex Exec 视觉复核

自动队列复核只处理 `review-queue.json` 中具有非空 `image_path` 的条目。队列不等于全部扫描件：没有 OCR 且没有文字标签的页面可能无法自动生成字段复核项，主 Agent 仍须实际读原页，核对阀门与页归属，向用户提交事实参数确认；经确认的补充值按第 4 节人工覆盖写回，保留原件引用和确认依据，重跑提取。不得凭空编造 `review_id` 或把仅在聊天中的读图结果视为已入库。对每个不同图片添加一个 `-i`，并按工作区看图合同执行：

```bash
python .agents/skills/valvue3-offline-diagnostic/scripts/run_metered_codex.py run \
  --project-root "<项目绝对路径>" \
  --stage parameter_visual_review \
  --call-id "parameter-review-<唯一批次>" \
  --cwd /tmp \
  --ignore-user-config \
  --ignore-rules \
  --output-schema "<项目绝对路径>/work/evidence-extraction/codex-review-schema.json" \
  --output-last-message "<项目绝对路径>/work/evidence-extraction/codex-review-results.json" \
  --image "<项目绝对路径>/<image_path-1>" \
  --image "<项目绝对路径>/<image_path-2>" \
  --prompt-file "<项目绝对路径>/work/evidence-extraction/codex-review-prompt.md"
```

由主 Agent 串行发起 `gpt-6-sol`、`reasoning_effort=high` 的计量调用，不使用子代理编排。使用空白工作目录并忽略用户配置和规则，避免重新加载客户项目的 Skill 和运行流程；空白目录本身不构成文件读取隔离。默认为当前 `review-queue.json` 中每个不同的 `image_path` 各附加一次，与完整提示文件一次执行。图片过多时可按图片分批，但每批提示只能包含已附加图片的队列项，同一张图的所有字段必须同批处理；最终 `codex-review-results.json` 中的 `reviews` 必须合并为无重复 `review_id` 的单一数组。

Codex 只能确认页面上直接可见的文字和表格归属，不得推断缺失值、解释曲线形状或形成诊断。看不清时返回 `unresolved`。来源值冲突不由 Codex 自动消解。

页面角色无法由结构化证据可靠确定时，才通过一次 `codex exec` 实际查看并确认当前阀门归属和 `air_piping`、`specification` 或 `accessory` 角色；同批页面可在一次调用中复核。仅因页面首次出现或页面新增、但自动归属已可靠的页面，不强制视觉复核。这是资料页面角色观察，不是曲线诊断，不得输出评级、故障结论或建议。物理页身份改变后旧观察自动失效。

复核结果写入 `codex-review-results.json` 后，再次运行 `--extract-materials`。当前 `review_id` 与源文件 SHA-256 都一致时，结果才会作为 `codex_reviewed` 候选值被消费；源文件变化后旧结果自动失效。

## 4. 人工覆盖

来源冲突或客户确认值写入 `experience/extraction_overrides.json`：

```json
{
  "schema_version": 1,
  "valves": {
    "VALVE-001": {
      "fields": {
        "context.description": {
          "value": "人工确认的阀门描述",
          "reason": "业主确认"
        }
      }
    }
  }
}
```

每个覆盖必须提供 `value` 和非空 `reason`，需要时可添加 `unit`。人工值优先于其他来源，但原始候选值和来源仍保留在逐阀 JSON 中。修改覆盖后重跑 `--extract-materials`。

## 5. 第二阶段审核与批准

完成结构化批量提取及必要的低置信度视觉复核后，再运行一次 `--extract-materials` 合并结果并生成审核快照。Markdown 审核报告须对应当前完整快照；核对是否已有该版本的有效批准，没有时暂停下一阶段：

```text
work/stage-gates/parameter-extraction-report.md
work/stage-gates/parameter-extraction-report.json
work/stage-gates/parameter-extraction-report.html
```

人读 Markdown 采用精简审批结构：先给出项目结论和计数，再让每台阀门各占一行展示关键规格、定位器、执行机构和关键测试数据；随后只列逐阀审核状态、合并后的待确认/冲突和不阻断缺口。源未提供或栏位为空属于明确缺口，不得与待确认或冲突合并称为“不确定”。不得在 Markdown 中展开完整主参数矩阵、原始来源路径、任务 JSON、补充料单明细或逐字段重复缺失项。完整字段、来源、原始值、规范值、单位、置信度、候选值和补充资料仍保存在 `parameter-extraction-report.json`、`valves/*.json` 与 `supplementary-materials.md`。

HTML 与同一冻结 JSON 同源，作为电脑和手机的等价审核界面。仅用本机 HTTP 和 Tailscale Serve 发布，不得使用 Funnel 或公网托管。服务可用时同时给出实际请求验证的本地与手机端 URL；未指定手机审核且服务不可用时，提交同版本本地 HTML/Markdown；明确要求手机入口时继续修复或说明阻塞。
HTML 使用与第一阶段一致的逐阀表格，只分类展示后续报告直接使用的阀门规格、定位器、执行机构、关键检测指标和数据/状态。页面顶部缺口和冲突计数也只针对这些报告字段；其他完整提取事实继续保留在 JSON，不作为审批页提示。
执行机组合栏 `Model / Size` 必须拆分保存型号和尺寸，审批页按 `型号 / 尺寸` 显示。如果源页存在明确组合值但报告字段仍缺失，必须进入 `source_present_parser_missing` 复核队列，不得当作“规格书未提供”。

“定位器型号”在 Markdown/HTML 审批报告和后续正式报告中统一使用可靠匹配的 Specification/Accessory Sheet 完整订货型号；DTM/Sequencer 的设备类型/版本只作为当前设备身份字段，不与完整订货型号构成“型号冲突”。`include_accessory_pages=false` 只表示不把附件原页插入客户报告，不得停止该页字段提取。经用户/业主明确确认的定位器更换状态可作为项目事实显示在第二阶段审批报告，并参与审批指纹；正式客户 DOCX/PDF 不读取、不显示该更换标注。

收到任何含义明确的项目级批准后原样登记：

```bash
python .agents/skills/valvue3-offline-diagnostic/scripts/run_offline_report.py \
  --project "离线诊断/projects/<项目名>" \
  --approve-parameters "<用户批准原文>"
```

批准记录 `work/stage-gates/parameters-approval.json` 绑定第一阶段批准、主参数矩阵和完整事实 JSON 的语义指纹、提取 schema 与规范化策略、人工覆盖及已消费的低置信度复核结果。原始资料、归属、页面角色、人工覆盖、复核结果或提取/规范化语义变化均使批准失效；绝对路径、缓存位置、生成时间、输出版本及后续曲线观察不参与该指纹。变化后只重算受影响阀门，但仍提交新的完整项目报告并重新取得批准。

Continuous Diagnostics 在 Markdown 审核报告中统一显示“无数据”；JSON 保留 `not_provided`、`parse_failed` 或 `no_matching_data` 等原因码。缺失、冲突和未解决复核项允许知情批准，但必须保留原因及对后续诊断的影响。

未取得有效 `parameters-approval.json` 时，共享入口必须拒绝 `--prepare-curve-review`、正式证据、单阀诊断、冻结和所有新报告生成命令。第二阶段批准后，第三阶段才准备全部诊断曲线，并按单阀完整输入的一次模型调用同时形成客观观察与工程诊断；不另设逐图独立诊断环节。

## 6. 后续边界

- 缺失、冲突和未解决复核项进入后续诊断的不确定项，不得硬填。
- 参数提取输出是项目内部证据，`codex_reviewed`、置信度、内部路径和补充料单不进入最终客户报告。
- 含评级、逐图判读、整阀结论或建议的报告仍必须完成后续逐图视觉阅读、正式诊断和冻结校验。
- 只有逐阀字段与页面角色矩阵证明“源文件→资料提取”完整后，才能进入正式证据和诊断。本层失败时只重跑解析/提取单测、`--extract-materials` 和矩阵比对，不创建新冻结版本、不重看未变更的曲线、不生成客户 DOCX。
