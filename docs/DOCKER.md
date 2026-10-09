# Docker 本地安装与分发

本方案把程序和依赖放进镜像，客户资料和报告留在电脑上的 `projects/`，
登录信息留在 `.runtime/codex/`，OCR 模型留在 `.runtime/models/`。
删除、更新容器不会删除这些文件。`bash docker/run.sh stop` 也保留它们。

**支持目标为 Linux x86_64 + Docker Engine + Compose v2。** Windows、macOS、
ARM、GPU、嵌套容器平台尚未验收。先建议准备至少 16 GB 内存、4 个 CPU 核和
20 GB 可用空间；这是起步配置建议，实际峰值需要测量。CPU 表格 OCR 可能较慢。
Docker 标准化软件环境，宿主机仍须允许 Bubblewrap 使用用户命名空间。

当前是 Docker 分发候选方案，原 Skill 仍为 0.1.1，原已知问题仍见
[KNOWN_ISSUES.md](KNOWN_ISSUES.md)。不能把镜像构建通过当作真实诊断或正式交付通过。

## 两种镜像

| 镜像 | 内容 | 首次使用 |
| --- | --- | --- |
| `valvue3-runtime:docker-v1` | 原 Skill、Python 依赖、Codex CLI 0.159.2、Bubblewrap、Poppler、LibreOffice、Xvfb；可选 WPS | 使用者自行登录 Codex |
| `valvue3-ocr:docker-v1` | PaddleOCR 3.3.2、PaddlePaddle 3.2.0、PaddleX 3.3.5、CPU 文字与表格服务 | 首次启动下载官方模型；后续复用缓存 |

CPU OCR 是本次新增的适配服务，不是对之前未随包提供的 OCR 实现的复刻。
`/v1/ocr` 用 PP-OCRv5，`/v1/document` 用 PP-StructureV3，保留表格结构与原始
Paddle 结果。文档方向、去弯曲、公式、印章、图表识别关闭；本版面向正常朝向的
文字和表格。模型身份、设备、耗时和失败会如实返回，不改用 Tesseract 或假识别。
正式使用前应对自己的匿名样本检查中文、表格、单位和设备参数的识别质量。

## 开发者构建

在有仓库访问权限的电脑上下载此分支，进入仓库根目录：

```bash
bash docker/run.sh setup
bash docker/run.sh build
```

构建会下载镜像、系统工具和 Python 依赖。`.env` 是配置模板，`setup` 会填写
当前 Linux 用户的 UID/GID，不覆盖已有配置。不要以 root 运行 setup 或诊断。
使用自定义 `PROJECTS_DIR` / `STATE_DIR` 时，先自行创建对应目录并保证运行用户
可写；Compose 不会替你创建 root 所有的空目录。

原 Skill 目录逐字节保留，只读安装到 `/workspace/.agents/skills/valvue3-offline-diagnostic`。
构建检查原清单哈希与 CLI 必需参数。Docker 构建上下文使用明确文件列表，排除
客户资料、认证、缓存和工作目录。系统软件的补丁版本与间接 Python 依赖并未全部
锁定；实际重建应保存镜像 ID/摘要，向接收方分发同一镜像。

### 加入正式 PDF 所需的 WPS

1. 从 [WPS Linux 官方网站](https://linux.wps.cn/) 取得有权安装的 Linux amd64 `.deb`。
2. 放到 `docker/installers/wps-office.deb`，执行 `sha256sum docker/installers/wps-office.deb`。
3. 编辑 `.env`，将 `RUNTIME_TARGET=with-wps`，将计算出的哈希填入 `WPS_DEB_SHA256`。
4. 再运行 `bash docker/run.sh build diagnostic`。

不把安装包放进 Git。哈希只校验使用的文件一致性，不替代官方来源核验。
容器自动启动仅有 UNIX socket 的 Xvfb 显示环境。WPS 程序版本与 RPC 兼容性仍须
实测。没有 WPS 的默认镜像支持准备和 DOCX，`doctor --stage wps_pdf` 会返回阻塞。
LibreOffice 的内部检查 PDF 不作为正式交付 PDF。

## 登录、自检和真实 OCR 检查

```bash
bash docker/run.sh login
bash docker/run.sh doctor --stage docx
bash docker/run.sh doctor --stage ocr
bash docker/run.sh ocr-smoke
bash docker/run.sh doctor --stage diagnosis
bash docker/run.sh doctor --stage wps_pdf
```

登录使用设备码：在自己的浏览器打开 CLI 显示的链接并登录，不需要把账号交给
开发者。认证采用文件存储，仅放在外部挂载目录。需要模型 `gpt-6-sol` 的实际访问
权限，模型调用仍需联网；“离线”指设备测试资料。检查登录和模型缓存不提交模型请求。

首次加载模型可能需要数分钟至数十分钟。`docker compose logs ocr` 查看下载状态。
如果服务退出或下载失败，先排查网络和 `.runtime/models/` 写权限，随后重新启动。
OCR 不发布宿主机端口；诊断容器与 OCR 共享网络命名空间，因此原引擎固定的
`127.0.0.1:8765` 能直接访问，无需修改原引擎。不要在使用诊断容器期间重建 OCR
容器；先退出诊断，再重新启动两者。

`ocr-smoke` 真正调用两个 OCR 接口，检查匿名图片中的已知文字和表格；它不调用
远端模型。`doctor` 仅检查前置条件，返回码 0 不证明端到端验收通过。

### 宿主机限制证据隔离时

默认使用 Docker 的常规限制，不自动放宽。如果 `diagnosis` 自检报告 `isolation`
失败，先查 Docker 的 seccomp、AppArmor 和宿主机用户命名空间设置。
本仓库提供显式选择的 Linux 配置：在 `.env` 增加

```text
COMPOSE_FILE=compose.yaml:compose.isolation.yaml
```

再运行 `bash docker/run.sh doctor --stage diagnosis`。该配置仅对诊断容器解除
seccomp/AppArmor 过滤，以允许内部 Bubblewrap 创建隔离空间；仍以普通用户运行、
移除全部额外能力、禁止获得新权限，没有 `privileged`、`SYS_ADMIN`、宿主根目录或
Docker socket 挂载。它扩大了容器可用的系统调用范围，使用前须理解这一取舍。
仍失败时换到允许用户命名空间的 Linux 主机，不能关闭原证据隔离或伪造检查结果。
也可运行 `bash docker/run.sh isolation-smoke`，以临时假认证实测文件隔离和 CLI
启动；它不读取你的真实认证、不请求模型，也不证明目标模型访问权。

## 放资料并开始工作

初始化示例项目：

```bash
bash docker/run.sh init-project \
  --project /workspace/离线诊断/projects/demo \
  --customer 示例客户 --report-title 示例报告 --report-date 2026-10-09
```

把资料放进电脑上的 `projects/demo/inbound/`，对应容器中的同名项目目录。
`projects/demo/output/` 是报告输出目录。不要把客户文件放进镜像或 Skill 目录。

```bash
bash docker/run.sh agent
```

进入 Codex 后可以说：“读取 `.agents/skills/valvue3-offline-diagnostic/SKILL.md`，
处理 `/workspace/离线诊断/projects/demo`，先准备资料并向我展示核对结果。”
确认 Skill 实际加载；每个业务阶段仍按现有资料、参数、诊断和 DOCX 审核流程执行。
也可用 `bash docker/run.sh report --help` 查看原入口参数。所有项目路径必须使用
容器内路径。Docker 不替你自动批准业务结果，也不自动触发收费模型请求。

## 打包给另一台电脑

开发者先完成以上构建和 OCR 检查，再执行：

```bash
bash docker/package.sh
```

会生成 `dist/valvue3-docker/`，包含两个镜像归档、预下载的模型缓存、启动文件、
配置模板和校验清单。不会包含你的 `.env`、登录信息、客户资料、安装包或运行日志。
把这个文件夹作为整体交给接收方。此操作不会自动公开发布或推送镜像仓库。

接收方安装 Docker Engine 和 Compose，解压到一个全新目录，执行：

```bash
bash docker/import.sh
bash docker/run.sh login
bash docker/run.sh doctor --stage diagnosis
bash docker/run.sh ocr-smoke
```

导入复用镜像和已下载模型，无需安装 Python 或再次构建；接收方自行登录和放资料。
必要时按前面说明启用隔离配置。以后使用 `run.sh`，无需保留原仓库。
更新时用新的分发目录，迁移自己的外部数据；不要覆盖已有模型目录或认证。
`SHA256SUMS` 检查传输一致性，不是发布者签名，应通过可信渠道取得文件和校验值。

当前仓库没有明确许可证。对外分发前必须明确项目代码的授权，并核对依赖、模型和
WPS 的许可；持有安装包不等于有权把 WPS 镜像分发给任何人。尤其是含 WPS 的镜像，
应先确认分发权限；也可让每位使用者自行取得安装包并构建。

## 验收层次

GitHub Actions 自动验证入口/协议测试、原 Skill 完整性、两个默认镜像构建、CLI
兼容参数，并用匿名文字/表格图片执行真实 CPU OCR。CI 不持有 Codex 登录或 WPS
安装包，所以不会声称完成真实模型诊断或正式 WPS PDF 验收。

发布完整可用版本还须在目标 Linux 主机验证：证据只读且排除文件不可见、真实模型
诊断及审核、WPS RPC 导出、PDF 页数与逐页视觉检查，并记录输入、模型/镜像身份、
结果和失败。原有 [验收记录](ACCEPTANCE.md) 不因增加 Docker 文件自动更新为通过。
