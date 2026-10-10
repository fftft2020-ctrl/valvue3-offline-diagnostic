# 上下文与模型调用记账

用于多阶段项目、批量单阀诊断和交付看图。目标是不改变诊断证据范围，只避免主线程重复读取长日志和完整模型输出。

## 主线程上下文

- 常规进度判断先读取当前门禁摘要、`work/workflow-summary.json` 和下一步必需文件；出现异常、反例或需要决策时可定向读取完整证据，不用摘要代替根因调查。
- 完整证据 JSON、模型全文、日志及媒体哈希保存在项目 `work/`，正常回传只给摘要和路径；主线程按问题读取所需原文，不强制整批灌入上下文，也不禁止必要核验。
- 全部工作由主 Agent 直接完成，不启动或续派子代理；主 Agent 亲自运行脚本、检查证据与核验结果。
- 项目门禁、冻结哈希和原始证据仍然是真实来源；精简主线程不等于精简诊断输入。

## 模型调用记账

所有项目内 `codex exec` 通过 `scripts/run_metered_codex.py run` 执行。终端只返回成败、返回码、token、耗时和文件位置；完整 stdout/stderr 保存在日志，模型最终结果按 `--output-last-message` 保存。失败返回码继续透传，排障时按日志路径定向读取。文件位置和执行成功只说明调用完成，诊断仍须经过原有提交校验。

每次尝试使用不同 `call-id`。已登记编号或已有日志在调用模型前拒绝，避免重跑和覆盖；即使上次中断也保留原日志，检查后用新的 attempt 编号重试。计量文件为：

```text
<project>/work/model-calls/ledger.jsonl
<project>/work/model-calls/<call-id>.log
<project>/work/workflow-summary.json
```

记录包含阶段、位号、模型、推理强度、开始/结束时间、耗时、token、返回码、输入哈希和日志位置。

新记录的 `skill_runtime` 同时保存发布版本及实际白名单内容、经验和模板哈希；旧记录保持原样。计量脚本沿用环境中的标准代理变量，不写死机器端口。

调用经后续门禁接受或拒绝后，使用 `mark` 追加结果：

```bash
python .agents/skills/valvue3-offline-diagnostic/scripts/run_metered_codex.py mark \
  --project-root "<project>" --call-id "<call-id>" \
  --outcome accepted
```

对于批量单阀诊断，完成确定性输入和 Schema 预检后，主 Agent 经同一计量入口逐阀串行调用 `gpt-6.1-sol`、`reasoning_effort=high` 并保存完整结果。不增加强制单阀探针门禁，不为降低 token 删减单阀完整证据、曲线或技术页。
