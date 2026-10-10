# ValVue3 离线诊断运行环境

Skill 使用当前已激活的 Python 环境，不依赖任何客户项目的 `.venv`。Python 最低版本为 3.11，当前验证版本为 3.12。

首版独立分发目标为 Linux + Codex，安装、业务边界、匿名验收及版本追溯见[独立安装与分发合同](distribution-and-installation.md)。首次安装或更换环境先运行：

```bash
python .agents/skills/valvue3-offline-diagnostic/scripts/check_environment.py
```

自检只检查本机前置能力，不提交模型请求；按阶段报告缺失项，不能代替实际诊断及交付验收。

## Python 依赖

运行报告引擎前安装锁定的运行依赖：

```bash
python -m pip install -r \
  .agents/skills/valvue3-offline-diagnostic/requirements.txt
```

运行 Skill 和工作区测试时安装测试依赖：

```bash
python -m pip install -r \
  .agents/skills/valvue3-offline-diagnostic/requirements-test.txt
```

`requirements.txt` 只包含共享引擎直接导入的第三方包：

- `PyMuPDF`：PDF 文本、页面和图像处理；
- `Pillow`：曲线、报告页面和联系表图像处理；
- `python-docx`：DOCX 生成、读取、回传比较和排版验证。
- `docxcompose`：在不重建人工逐阀正文的情况下汇编多批次装置分册。
- `pywpsrpc`：在隔离的 WPS 多组件配置中导出正式 PDF；不修改用户全局 WPS 设置。

## 系统工具和外部程序

完整生成和交付链还使用以下系统能力：

- `curl`：调用本机 `http://127.0.0.1:8765` OCR 服务；只有需要 OCR 的页面才调用该服务。
- Codex CLI：必须支持现有计量脚本的 `--ignore-user-config`、`--ignore-rules`、`--ephemeral`、`--output-schema`，接收方自行配置登录及目标模型访问能力；Skill 不携带认证。
- `bwrap`（Bubblewrap）及可用用户命名空间：隔离模型实际可见证据，并实测只读和排除文件。缺少隔离不能用空目录代替。
- `pdftotext`（Poppler）：优先提取 PDF 布局文本。
- `libreoffice` 或 `soffice`：需要生成内部自动检查 PDF 时使用。现有 `--skip-pdf` 或 `convert_pdf=false` 可只生成 DOCX；该模式不因缺少 LibreOffice 停止，但也不代表正式 PDF 已交付。正式 PDF 仍须经过 WPS 导出和完整验收。
- WPS 文字：唯一报告入口的 `--export-wps-docx` 使用 `scripts/export_wps_pdf.py` 导出正式 PDF；WPS 应用本身不由 Skill 自动安装。

本机 OCR 是可选外部服务，不包含在 Skill ZIP 中；Docker 源码提供可选 OCR 镜像。未启用时通过大模型实际读图并按参数复核/确认路径写回事实，脚本保留识别失败和缺口，不保证扫描件自动完整提取。服务协议见安装合同及引擎代码。WPS 运行还需可用显示环境。模型调用保留接收环境显式设置的标准代理变量，不强制本机端口，不修改系统代理。

新项目目录只保存输入、配置、人工反馈来源、中间状态和输出。不得将 Python 环境、共享引擎或通用经验复制到客户项目中。

## Python 隔离兼容性（0.1.1）

证据隔离按当前解释器的 prefix、exec_prefix、base_prefix、base_exec_prefix
只读挂载具体 Python 安装根，兼容非 /usr 安装及虚拟环境基础标准库。
不挂载这些安装目录的公共父目录；若安装根包含用户主目录、认证目录或排除证据，
拒绝运行，避免为兼容性扩大证据可见范围。符号链接安装同时保留原路径和解析后的路径。
环境自检、匿名测试通过仍不代表真实模型、OCR 或 WPS 交付能力已验收。
