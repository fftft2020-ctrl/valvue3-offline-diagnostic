# ValVue3 离线诊断 Skill

用于读取 ValVue3 / VV3 的 Sequencer、Active Report PDF、devdata、规格书和曲线，由 Codex 核对参数、逐阀诊断，再生成 Word 报告。“离线”指设备测试资料，模型诊断仍需联网。

**当前只分发 Skill，不需要 Docker。** 版本为 0.1.3 开发候选；首版支持 Linux + Codex，已验证 Python 3.12。Windows、macOS 尚未验收。克隆或解压只会取得代码，不会自动安装依赖、登录或开始诊断。

## 安装与开始

如果收到 Skill ZIP，在自己的业务工作区 `.agents/skills/` 下解压，得到 `.agents/skills/valvue3-offline-diagnostic/SKILL.md`；不要覆盖已有安装。也可以在有仓库权限时克隆：

```bash
git clone https://github.com/fftft2020-ctrl/valvue3-offline-diagnostic.git
cd valvue3-offline-diagnostic
```

以下步骤在该业务工作区或克隆目录执行。先准备 Linux、Python 3.11+、Codex CLI、Bubblewrap 和 Poppler；详细依赖集中在 [运行环境](.agents/skills/valvue3-offline-diagnostic/references/runtime-environment.md)。

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r .agents/skills/valvue3-offline-diagnostic/requirements.txt
codex -c 'cli_auth_credentials_store="file"' login --device-auth
python .agents/skills/valvue3-offline-diagnostic/scripts/check_environment.py
codex --model gpt-6.1-sol -c 'model_reasoning_effort="high"'
```

使用自己的账号和可访问模型服务的网络；不要复制别人的认证或代理配置。CLI 必须支持 Skill 的隔离调用参数；文件形式的本机认证是当前隔离流程所需。内部诊断调用固定 `gpt-6.1-sol / high`，不自动换模型。

自检不调用模型。未启动 OCR、未安装 WPS 时可能返回 `partial`，只限制对应阶段；先看 `docx` 和 `diagnosis` 的结果。Bubblewrap 不仅要安装，系统还必须允许它的用户命名空间，实际隔离探针通过后才能诊断。

进入 Codex 后可以这样说：

> 先读取 `.agents/skills/valvue3-offline-diagnostic/SKILL.md`，为我初始化当前工作区的 `离线诊断/projects/demo`。告诉我原件放在哪里；不启用 OCR，通过实际读图复核扫描件和参数，再按现有阶段给我确认。

初始化后将收到的资料放入该项目的 `inbound/`；归档原件位于 `raw/` 和 `specs/`，报告位于 `output/`。所有阶段复用 Skill 脚本，不为每个项目复制引擎。

## 依赖与可选能力

| 项目 | 是否需要 |
| --- | --- |
| Python 3.11+、锁定的 Python 包 | 必需；包统一列在 `requirements.txt` |
| Codex CLI、自己的账号、网络、目标模型权限 | 模型诊断必需 |
| Bubblewrap 和可用用户命名空间 | 当前诊断的文件权限限制必需 |
| Poppler、中文字体 | PDF 处理及报告渲染使用 |
| OCR 服务和模型 | 可选，不随 Skill 包提供 |
| LibreOffice | 可选，仅用于内部 PDF 检查，可只生成 DOCX |
| WPS Linux、RPC、显示环境 | 仅正式 PDF 交付需要；应用不随包提供 |
| pytest | 仅开发和匿名测试需要 |

原生 PDF 和 devdata 优先直接解析，曲线及规格图由模型实际读取。不开 OCR 时，通过原页读图、参数复核和确认写回事实；脚本不会自动补齐所有扫描字段。身份、页归属、曲线清单不清楚时先核实，不把空值当正常。OCR 如需启用，接收方提供兼容的本机服务，接口见安装合同；Skill 不负责部署该服务。

## Skill 的结构与处理流程

```text
.agents/skills/valvue3-offline-diagnostic/
├── SKILL.md                 Agent 入口与工作边界
├── scripts/                 解析、证据、逐阀诊断、报告、计量和自检
├── references/              阶段细则、依赖说明和通用离线经验
├── assets/                  通用模板与匿名资源
├── requirements.txt         锁定运行依赖
└── release.json             版本和模型合同
离线诊断/projects/           本机资料、配置、反馈、冻结与成果
```

流程是：**归档原件 → 核对参数 → 逐阀读图诊断 → 审核并冻结 → 生成/确认 DOCX → 可选正式 PDF**。资料、参数、诊断草稿和 DOCX 分别确认；工具成功不代表用户确认。唯一报告入口是 Skill 中的 `scripts/run_offline_report.py --project <明确项目目录>`。

- Skill 不包含客户资料、旧客户结论、账号、Python 环境或 OCR 模型；不要把这些文件放进 Skill。
- 客户原件、配置、反馈、批准、冻结、报告和引用的 `work/` 证据必须保留，不进入共享 Git。
- 默认生成 DOCX；LibreOffice PDF 只作内检，正式 PDF 须经 WPS RPC 导出及交付验收。
- 现有匿名回归包含详细测试数值，尚未证明每一项数值都为合成；当前仓库为私有，扩大公开范围或要求完全合成数据前须核对来源。
- 当前没有明确对外许可证；对外分发前须明确代码及第三方组件的使用权限。

## 维护与分享

只修改本机维护源码，GitHub 和 ZIP 是同一源码的生成产物，见 [单版本流程](docs/offline-source-workflow.md)。分享给别人使用 ZIP 即可，不发送本机 `.venv`、认证、代理配置或客户项目。

```bash
python scripts/build_offline_skill_package.py --output .scratch/valvue3-offline-diagnostic-new.zip
```

命令拒绝覆盖已有文件。ZIP 包含 Skill、匿名测试和完整性清单；接收方按本文安装依赖并使用自己的账号。Skill ZIP 本身不是已经装好环境的应用。

开发时另装测试依赖并检查：

```bash
python -m pip install -r .agents/skills/valvue3-offline-diagnostic/requirements-test.txt
python -m pytest -q tests .agents/skills/valvue3-offline-diagnostic/scripts/tests
python scripts/validate_offline_regressions.py
python scripts/build_offline_skill_package.py --repository-output .scratch/offline-skill-source-new
```

源码导出同样拒绝覆盖，不包含 Docker、在线代码、客户资料、环境或 Git 历史。Git 同步需要用户明确授权。既往测试结果保留在 [验收记录](docs/ACCEPTANCE.md)，历史 Docker 测试不代表当前接收方已安装；已知限制见 [验证缺口](docs/KNOWN_ISSUES.md)。
