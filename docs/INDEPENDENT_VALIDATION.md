# 独立测试与修复记录

日期：2026-10-09。上游基线：`45e36d189bc65097f7c5731fcba350e23d7ac43e`。本分支独立测试，不在 DOT 工作区执行，也不修改主分支。

## 修改及验证

执行机构型号缺失时，报告不再从阀门型号前缀补填。示例：规格只有 `88-21124`，没有执行机构型号，原先显示 `88 / -`，现在显示 `- / -`；若明确提供执行机构型号 `87`、尺寸 `#10`，仍显示 `87 / #10`。

新增 6 项回归：四种缺失表示、明确型号与尺寸保留、实际生成 DOCX 的字段检查。同步更新原先要求前缀回填的测试。环境前置条件单元测试独立模拟包校验结果，并验证包校验失败仍阻断全部阶段；生产代码的完整性检查未放宽。

## 测试结果

命令：`python -m pytest -q skill/valvue3-offline-diagnostic/scripts/tests`。

| 检查 | 结果 |
| --- | --- |
| 修改前完整测试 | 659 passed，10 failed，55.34 秒 |
| 修复并补中文字体后的完整测试 | 672 passed，3 failed，51.33 秒；0 skipped |
| 新增型号回归 | 6 passed |
| 原先失败的字体相关测试 | 7 passed |
| 匿名回归数据校验 | PASS |
| `pip check` | No broken requirements found |
| Git 空白检查 | PASS |

中文测试需要 `/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc`。本环境从 Ubuntu `fonts-noto-cjk` 包提取字体；完整下载包 SHA-256 与软件源声明一致：`7d64b985f6fe128c99eae5610d5c047338e572bdcfb2bb09736be01b824a7f6c`。系统工具和字体不加入仓库。

剩余失败为：

- `test_real_python_namespace_without_model[current]`
- `test_real_python_namespace_without_model[base]`
- `test_evidence_namespace_hides_answers_and_keeps_inputs_read_only`

三项均报告 `bwrap: setting up uid map: Operation not permitted`。这是当前容器的隔离权限限制，尚不能验证此环境中的真实只读证据隔离；未跳过测试，也未降低隔离要求。

## 合成源文件与内部报告检查

使用一台合成阀门的原生文字诊断 PDF、XML devdata 和规格 PDF。实际打开并解析全部三个源文件，检查源哈希未改变；原生提取后的 `actuator.model`、`actuator.model_full`、`actuator.model_display` 均为 missing。用真实报告渲染器生成内部纯数据 DOCX，执行机构行显示 `- / -`。

此检查只覆盖源文件解析、原生字段提取和内部报告渲染；不是整套阶段流程验收，也不是模型对原件的实读证明。阶段一未登记批准时，参数报告门禁按预期拒绝继续；未补造批准。现有提取函数会先写部分证据再检查参数报告门禁，这些文件不代表阶段二已完成。

另复现通用英文标签 `Model Number:`、`Valve Size:`、`Pressure Class:` 未映射到规格字段的限制。该问题本次未修改；没有运行 OCR 服务，不能据此评价 OCR 准确性。

## 剩余限制和发布状态

- Codex CLI、可用 Bubblewrap 隔离、OCR 服务和 WPS/显示环境仍缺少相应运行能力。没有新增模型请求或正式 WPS 导出。
- DOT 上完整诊断未实读原件的根因仍未查明；这里没有其原始失败日志或运行环境。本次解析成功不能证明远端模型会读取原件。
- 保留 0.1.1 原清单及版本。源码修改后 `verify_package` 如实拒绝旧发布清单，`check_environment.py` 的 package 检查失败并阻断阶段。这是开发快照，不是可安装的新发布包。
- 新发布仍需由发布流程重建版本及清单，并在支持命名空间、模型、OCR 和 WPS 的环境中补齐必要验收。
- 合成资料、内部输出、原始日志、认证和本机配置均不纳入提交。测试通过不能证明真实设备诊断准确性。
