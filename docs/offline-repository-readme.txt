# ValVue3 离线诊断

这是处理 **ValVue3 / VV3 离线测试资料** 的工具：读取 Sequencer、Active Report PDF、devdata、规格书和曲线，由 Codex 辅助核对参数、逐阀诊断，再生成 Word 报告。

“离线”指设备测试资料，**大模型诊断仍需联网**。克隆仓库只会下载代码和说明，不会自动安装、登录、下载 OCR 模型或开始诊断。当前源码版本为 **0.1.3 开发候选**，支持目标为 Linux x86_64；其他系统尚未验收。

## 新机器需要准备什么

| 项目 | 是否需要 | 说明 |
| --- | --- | --- |
| Docker Engine + Compose v2.40 或更新版本 | Docker 路线需要 | 帮你统一 Python、Codex CLI 等软件环境 |
| 自己的 Codex 登录和目标模型权限 | 模型诊断需要 | 固定使用 `gpt-6-sol`、推理强度 `high`；仓库不附账号 |
| 可访问模型服务的网络 | 模型诊断需要 | 在新机器自行配置网络；不复制别人的代理端口或认证 |
| Bubblewrap 可用的用户命名空间 | 模型诊断需要 | 用于证据隔离；部分 Ubuntu 主机需额外配置，见 Docker 说明 |
| OCR | **可选** | 辅助扫描件文字、表格提取；不启用时通过大模型读图并核对参数 |
| WPS Linux + RPC | 正式 PDF 需要 | 默认镜像可生成 DOCX；正式 PDF 另按交付流程安装和验收 |
| 自己的原始测试资料、规格和项目配置 | 处理项目需要 | 保存在本机项目目录；仓库不提供客户资料 |

## 默认开始：不安装 OCR

在已安装 Docker、拥有仓库权限的新机器上执行：

```bash
git clone https://github.com/fftft2020-ctrl/valvue3-offline-diagnostic.git
cd valvue3-offline-diagnostic
bash docker/run.sh setup
bash docker/run.sh build diagnostic
bash docker/run.sh login
bash docker/run.sh doctor --stage diagnosis
bash docker/run.sh doctor --stage docx
bash docker/run.sh agent --model gpt-6-sol -c 'model_reasoning_effort="high"'
```

`login` 会显示设备登录链接和验证码，在自己的浏览器完成登录即可。认证保存在本机 `.runtime/codex/`，容器登录与电脑上其他 Codex 安装的登录分开。镜像已包含 Codex CLI；无需再在宿主机安装一份。上面的 `agent` 命令指定模型和推理强度，内部诊断调用也按 Skill 固定这两项，不会因个人默认配置而自动换模型。

如希望省略启动命令中的模型参数，可在本机 `.runtime/codex/config.toml` 中合并以下设置（保留其他已有配置，不把此文件提交或分发）：

```toml
model = "gpt-6-sol"
model_reasoning_effort = "high"
```

内部计量调用会忽略个人配置并显式指定固定模型，因此改个人默认模型不能替代项目模型要求。

自检失败时先按 [Docker 说明](docs/DOCKER.md) 排查对应项；不要跳过证据隔离。默认没有 OCR 和 WPS，因此不要用“全部阶段自检通过”作为开始 DOCX/诊断工作的前提。自检不会发起模型请求；实际诊断会使用自己的账号额度。

进入 Codex 后可以直接说：

> 先读取 `.agents/skills/valvue3-offline-diagnostic/SKILL.md`。为我初始化 `/workspace/离线诊断/projects/demo`，告诉我把原始资料放在哪里。这个项目不启用 OCR，请通过实际读图核对扫描件和参数，保留来源与待复核项，再按阶段给我确认。

把资料放在宿主机 `离线诊断/projects/demo/inbound/`。Codex 容器中的项目路径为 `/workspace/离线诊断/projects/demo`，报告通常在同一项目的 `output/`。项目初始化会建立配置和目录；后续阶段都用现有入口，不另写一套诊断脚本。

## OCR 是可选助手

原生 PDF 文字和 devdata 优先直接解析；曲线、规格页及报告页面仍由大模型实际看图。OCR 可以辅助把扫描文字、表格转成可检索的数据，**不能替代看图、来源核对和参数确认**。

不启用 OCR 时，脚本不会自动从所有扫描件提取完整参数：缺失的 OCR 会留失败/缺口记录。Agent 须读取原页，按现有参数复核与人工确认路径补齐有依据的事实；看不清就保留“待复核”，不能把空值当正常。读图结果也必须写回项目证据并重跑提取，不能只存在聊天里。具体步骤见 [参数提取工作流](.agents/skills/valvue3-offline-diagnostic/references/material-extraction-workflow.md)。如果整份扫描件的阀门身份、页归属或曲线清单无法可靠建立，应先补做读图归档/确认，必要时启用 OCR，不直接跳到报告。

如需 OCR，在 `.env` 中增加或修改**唯一一行**：

```text
COMPOSE_FILE=compose.yaml:compose.ocr.yaml
```

然后执行：

```bash
bash docker/run.sh build ocr
bash docker/run.sh doctor --stage ocr
bash docker/run.sh ocr-smoke
```

首次启动会联网下载官方模型。若已启用隔离配置，则使用 `COMPOSE_FILE=compose.yaml:compose.ocr.yaml:compose.isolation.yaml`，保留自己的 AppArmor 配置。OCR 服务只供容器内部使用，不开放宿主机端口。取消 OCR 时退出当前诊断容器，把 `compose.ocr.yaml` 从配置中移除，保留模型缓存以便复用。

## 仓库怎么组织

```text
.agents/skills/valvue3-offline-diagnostic/
├── SKILL.md                 Codex 工作入口、边界与阶段指引
├── scripts/
│   ├── run_offline_report.py 唯一报告/资料处理入口
│   ├── offline_engine/      解析、证据、审核状态及报告生成
│   ├── formal_diagnosis_tools/ 逐阀模型诊断、审核与冻结
│   └── run_metered_codex.py 模型调用、token 和耗时记录
├── references/              工作流程、运行要求与通用离线经验
├── assets/                  通用项目模板和匿名资源
└── scripts/tests/           引擎测试
Dockerfile、compose*.yaml、docker/ 运行环境、可选 OCR、打包与导入
docs/                        安装、验收、已知限制和维护说明
scripts/                     白名单源码/ZIP 导出与匿名回归检查
tests/                       分发测试和匿名回归夹具
离线诊断/projects/           使用时本机创建，保存客户项目，不进入 Git
.runtime/                    本机认证与可选模型缓存，不进入 Git
```

处理顺序是：**归档原件 → 核对参数 → 逐阀读图诊断 → 审核并冻结 → 生成/确认 DOCX → 可选正式 PDF**。资料、参数、诊断草稿、DOCX 内容分别确认；工具运行成功不等于用户已确认。冻结结果用于可追溯报告，不能直接覆盖修改。每阀输入和通用经验完整保留，不能只看摘要或抽选几张图。

## 使用须知与复用

- 这是离线通用实现，包含匿名测试和经验；不包含在线诊断、客户原件、旧客户结论、个人配置或账号。
- 项目只存资料、配置、反馈、批准和成果，所有项目共用这一个引擎，不为每个客户复制代码。
- 必须保留原始输入、项目配置、反馈、批准、冻结包和报告，以及报告引用的 `work/` 证据；更新代码不等于可以删除这些目录。认证不能发给别人。
- 给另一台机器复用：可克隆代码重新构建，也可接收镜像分发包直接导入。默认包只含运行镜像；显式启用 OCR 后打包才带 OCR 镜像和模型，接收方仍须自行登录。默认包不带 OCR 镜像，后续需要 OCR 时另取含 OCR 的包或从完整源码构建。旧分发包的行为以随包说明为准。
- 默认不带 WPS。LibreOffice 生成的 PDF 只供内部检查；正式 PDF 必须由已确认 DOCX 经 WPS RPC 导出并通过验收。
- 当前没有明确对外许可证；外部分发前需明确代码及第三方依赖、模型、WPS 的分发权限。

日常维护沿用 [单版本流程](docs/offline-source-workflow.md)：修改维护源码、验证、获明确 Git 授权后同步，生成新分发包；不在生成副本中再维护另一套代码。

## 开发与验证

不使用 Docker 时，先准备 Python 3.11+（已验证 3.12）的隔离环境，以及 Codex CLI、Bubblewrap、Poppler 等系统依赖，详见 [运行环境](.agents/skills/valvue3-offline-diagnostic/references/runtime-environment.md)。此路线需要自行安装和登录 Codex，不能只安装 Python 后宣称可用。

```bash
python -m pip install -r .agents/skills/valvue3-offline-diagnostic/requirements-test.txt
python -m pytest -q
python scripts/validate_offline_regressions.py
python .agents/skills/valvue3-offline-diagnostic/scripts/check_environment.py
```

自检的 `partial` 返回码为 1，只限制对应阶段。源码与 ZIP 导出必须指定新目录/新文件，拒绝覆盖已有产物：

```bash
python scripts/build_offline_skill_package.py --repository-output .scratch/offline-release-new
python scripts/build_offline_skill_package.py --output .scratch/valvue3-offline-diagnostic-new.zip
```

[Docker 详细操作](docs/DOCKER.md)、[验收记录](docs/ACCEPTANCE.md)、[已知缺口](docs/KNOWN_ISSUES.md) 区分自动测试、真实模型、OCR、WPS 和另一台机器安装。测试通过不等于真实设备诊断准确或全部交付通过。
