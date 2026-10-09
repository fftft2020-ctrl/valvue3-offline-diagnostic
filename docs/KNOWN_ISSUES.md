# 已知问题与验证缺口

状态日期：2026-10-09 UTC。本次建库未修复以下问题，也没有追加模型调用。

## 1. 完整诊断未完成原始文件实读

匿名完整单阀调用虽返回退出码 0，但模型承认未直接打开原始 `diagnostic.pdf` 与 `diagnostic.devdata`，未完成原件一致性复核。日志记录 3 次 `remote_desktop_commander.read_file` 失败；缺少完整工具清单、底层错误与权限证据，不能断言根因是文件权限、缺少 shell 或 MCP 配置。

该结果已 rejected、未冻结。后续需在授权范围内查明受支持的原件读取方式，证明全部源文件实际可读并被复核，再重新验收。不能以结构化转录或图片代替合同要求的全部原件实读。

## 2. 执行机构型号前缀回填缺少独立依据

位置：[`business_report.py`，`specification_supplement_rows`](../skill/valvue3-offline-diagnostic/scripts/offline_engine/business_report.py)。当执行机构型号缺失时，现有代码会尝试从阀门型号的两位前缀回填。

匿名样例参数中的 `actuator.model`、`actuator.model_full`、`actuator.model_display` 都为 missing，规格页只有阀门型号 `88-21124`，纯数据 DOCX 却显示执行机构型号/尺寸 `88/-`。该推导对样例设备没有独立依据，因此不能当作已确认型号。后续需决定是否移除回填或显式标明推断，并补充有业务依据的验证；当前代码保持原样。

## 3. OCR 与规格模板覆盖有限

通用英文负例中的 `Model Number`、`Valve Size`、`Pressure Class` 及冒号标签可被 OCR 正确读出，但现有映射没有识别相应规格，复核队列也未产生这些候选。使用随包已有支持标签的正例则提取成功。正例通过不代表任意规格书版式通用。

单独验收的 Tesseract 英文兼容适配器不是原作者 OCR 服务，也不包含在本仓库；它输出 `tables=[]`、`confidence=null`、`degraded=true`。中文、复杂表格、模糊或倾斜现场扫描未验证。仓库内的 OCR 客户端与模拟测试不能替代可用的外部服务。

## 4. WPS 正式交付未验证

WPS 应用未安装验收，适用许可证未核实，正式 PDF 与分页视觉验收均未完成。`pywpsrpc` 依赖存在不等于 WPS 可用。LibreOffice 只用于内部检查，不能冒充 WPS 正式交付。

## 5. 分发与开发注意事项

- 原完整性检查严格要求分发目录仅包含清单文件。仓库说明放在其外；不要把 `.git`、虚拟环境、项目数据或额外说明复制进分发目录
- 原随包安装文档提及的上游 `scripts/build_offline_skill_package.py` 未在收到的分发包内，本仓库未补造。此前修复包通过独立重建脚本生成，此脚本未纳入本次源码上传范围
- 当前支持目标是 Linux + Codex；其他平台与 Agent 未验证
- 尚无 CI 配置；此仓库中的“669 项通过”仅指既有独立分发验收，不能推定未来提交仍通过
- 未新增许可证声明；再分发及外部依赖的授权仍需核实
