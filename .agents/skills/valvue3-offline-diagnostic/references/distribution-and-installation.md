# 独立安装与分发合同

首次安装或脱离原工作区使用时读本文，再读 [运行环境](runtime-environment.md)。本 Skill 的“离线”指 ValVue3 离线测试资料；模型诊断仍需联网。当前只分发 Skill，不要求 Docker。首版只面向 Linux + Codex，Windows、macOS 与其他 Agent 未验证。模型固定为 `gpt-6.1-sol` / `high`，不可自动降级或替换。

## 安装与环境检查

分发 ZIP 只有 `valvue3-offline-diagnostic/` 一个顶层目录。先选择一个本机业务工作区，在其 `.agents/skills/` 下解压；不覆盖已有安装。客户项目位于该工作区 Git 忽略的 `离线诊断/projects/`，与 Skill 分开保存。不要在 Skill 内创建客户项目。该布局可直接使用本文及其他 reference 中的 `.agents/skills/...` 命令。

也可以安装到接收环境的个人 Skill 目录；此时所有命令中的 `.agents/skills/valvue3-offline-diagnostic` 必须替换为实际安装目录，客户项目仍使用明确的独立绝对路径。Skill 自动发现取决于接收环境；没有自动发现时先显式读取 `SKILL.md`，不能以文件已解压宣称已被 Agent 加载。

使用接收方的隔离 Python 环境，按锁定依赖安装。不要复制本机 `.venv`、认证、代理配置或模型缓存。只使用 Skill 时先准备运行环境和自己的登录：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r .agents/skills/valvue3-offline-diagnostic/requirements.txt
codex -c 'cli_auth_credentials_store="file"' login --device-auth
python .agents/skills/valvue3-offline-diagnostic/scripts/check_environment.py
```

诊断还需 Codex CLI、Bubblewrap 和实际通过的系统隔离；Python 包安装成功并不代替这些要求。不开 OCR、不导出正式 PDF 时，只看 `docx` 与 `diagnosis` 的前置结果，无需为可选阶段安装全部组件。开发和独立匿名验收时再装测试依赖：

```bash
python -m pip install -r .agents/skills/valvue3-offline-diagnostic/requirements-test.txt
python .agents/skills/valvue3-offline-diagnostic/scripts/check_environment.py
python -m pytest -q .agents/skills/valvue3-offline-diagnostic/scripts/tests
python .agents/skills/valvue3-offline-diagnostic/scripts/validate_offline_regressions.py
```

后一个脚本及匿名夹具由发布工具从共享仓库白名单带入，源仓库中仍沿用根目录 `scripts/validate_offline_regressions.py`。测试安装包含 pytest；仅运行时使用 `requirements.txt`。

自检仅运行本机探针，不登录、不提交模型请求、不安装应用、不修改配置、不启动 OCR 或 WPS。输出按 `docx`、`diagnosis`、`ocr`、`wps_pdf` 分别列出缺失依赖；`partial` 返回码为 1，只限制受影响阶段。`prerequisites_available` 只证明检查到的前置能力，不等于远端模型访问、OCR 准确率或 WPS 交付已验收。模型缓存出现目标模型也不证明当前权限。缺失模型能力须报告，不自动切换模型。

OCR 是可选外部本机服务；未启用时大模型仍可实际读图，按参数复核及确认路径写回事实，脚本不会自动补齐所有扫描字段。启用 OCR 时需要提供 `/ready`（JSON `ready=true`）、`/v1/ocr` 和 `/v1/document`，地址及数据格式见引擎 `document_ocr_cache.py`。Skill 不包含 OCR 服务实现、模型权重或安装器；只有需要 OCR 的输入受其缺失影响；当前分发不负责部署 OCR 服务。正式 PDF 还需要 WPS、RPC 和显示环境；LibreOffice 仅生成内部检查 PDF。模型调用沿用环境中的标准代理变量，未设置时不强制本机代理端口；证据隔离保留。

## 随包生效的业务边界

以下规则脱离原工作区仍适用，阶段命令以 [项目合同](project-contract.md) 为准：

- 在线与离线物理分区、经验分库、脚本分流，不读取在线经验。离线唯一通用经验为 `references/common-offline-experience.md`，每阀全文入模并留存；不预筛、不裁剪、不另建规则或案例库。
- 客户原件、现场事实、反馈、批准、冻结和交付件留在接收方本机忽略目录，不进入共享 Git 或分发包，不用 `git add -f`。新项目只保存数据与配置，复用已安装引擎，不复制维护另一份引擎或通用经验。
- 当前主 Agent 直接完成准备、执行和核验，不启动子代理或另开任务代办。曲线、技术页、报告页及联系表实际视觉读取都通过 `run_metered_codex.py` 串行执行 `codex exec`，固定模型与推理强度，不删减单阀完整输入。
- 资料归档（含适用复用选择）、参数、诊断草稿、实际 DOCX 内容分别确认，互不替代。前三项用已有批准命令绑定用户原文与版本；DOCX 在既有记录绑定实际母版。有效授权直接续行；工具成功、文件回传、ingest 或笼统“继续”不代表新版本批准。维护和匿名测试不启动客户审批，排版和交付检查不增加用户门禁。
- 明确委托 Agent 核验并交付时记录授权原文、范围及实际版本，标注“Agent 核验、用户授权交付”，不能冒称人工已校验。额外消息、邮件、外发、公开发布、收费及删除须有对应授权。安装和使用本 Skill 不自动授权这些动作。
- 客观观察只写可见事实。判断综合全部原始资料、批准参数、规格、检测值、故障、测试状态、曲线及完整经验，保留失败、取消、中止、冲突与缺口。未知阈值不编造、不判正常；否定、假设和待核实内容不靠关键词判异常。局部缺测只限制相应判断，保留其他有依据的结论，必要补测说明实施条件。
- 新诊断经第三阶段审核后冻结。诊断报告只读取同一有效冻结版本及绑定同母版、同诊断版本的登记 overlay；算法快照仅供审计，报告阶段不重算。纯数据报告不读取诊断、不评级。冻结包不原地改写；输入变化按现有合同建立新版本，图片相同不代表整阀可继承。
- 内容已确认的 DOCX 只改排版时建立 format-only 子版本，只改不改变内容的分页、间距、尺寸和字体格式。正文结构、媒体、书签、链接或嵌入对象变化须拒绝按纯排版交付。
- DOCX 是主输出。正式 PDF 从对应已确认 DOCX 经 WPS RPC 导出，同目录同主文件名；完成 A4、分页、内容、覆盖和代表页实际视觉检查，且 `delivery_manifest.json` 为 `wps_delivery_validated` 才能交付。禁止坐标菜单回退或交付 LibreOffice 内检 PDF。
- 保留原始资料、配置、反馈、批准绑定、冻结包、正式成果和被结论或验收引用的 `work/` 证据。`work/` 不整体删除；渲染等按来源哈希、参数及目的复用。清理须有授权，收尾交代成果、保留项、清理候选和重建方式。

## 发布与版本追溯

源仓库维护 `release.json` 与 `distribution-files.txt`：前者声明版本及运行合同，后者逐文件列出共享 Skill 白名单。新增运行资源或测试时同步更新白名单。工作区匿名回归工具及夹具的额外映射固定在发布脚本中；不扫描或携带客户项目、Git 历史、环境、认证或缓存。

在源仓库完成必要回归后，指定一个尚不存在的 ZIP 路径导出：

```bash
python scripts/build_offline_skill_package.py --output ".scratch/offline-skill-distribution/valvue3-offline-diagnostic-0.1.3.zip"
```

包内 `package-manifest.json` 保存逐文件 SHA-256、大小、发布版本及运行指纹；自检核对已安装文件，修改、遗漏或未声明文件会报错。清单用于完整性核对，不是数字签名，应从可信渠道接收 ZIP 并独立核对发布时给出的 ZIP SHA-256。不带整个仓库历史。

新的单阀运行清单、模型计量记录及报告运行清单保存版本、白名单内容指纹、经验和项目模板哈希；实际报告代码也受内容指纹绑定。旧记录没有这些字段时保留真实来源，不补写冻结文件，也不改变既有继承判断。升级使用新的安装目录，保留被诊断或交付引用的旧发布包和原项目状态，再按既有输入与版本规则继续。

分发验收分两层：在全新目录解包、校验清单、运行随包匿名测试与回归，证明文件和合同独立；在接收机器按授权运行真实资料、模型和 WPS 全链路，证明现场环境可交付。前一层不能代替后一层。

源代码、白名单、锁定依赖和匿名夹具为重建所需保留项；经确认后可清理无引用的解包测试目录、pytest 缓存和探针产物，不能清理客户项目证据。ZIP 可用发布命令重建；保持相同输入会得到相同 ZIP。真实诊断重建可能重新调用模型，存在 token、时间和结果差异，优先恢复已保存冻结包。


维护源码包含上述构建器；GitHub 与 Skill ZIP 使用同一白名单源码，GitHub 源码由 `--repository-output` 导出到新目录，不包含 Docker 部署文件。只在维护源码中修改，不在生成副本里开发。历史分发包与被诊断引用的版本继续保留；统一维护版本不删除历史证据。
