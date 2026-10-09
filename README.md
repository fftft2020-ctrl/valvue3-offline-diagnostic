# ValVue3 离线诊断 Skill

用于 ValVue3 / VV3 离线测试资料、诊断 PDF、devdata、规格书、曲线与报告工作流的开发仓库。当前工作树为 **0.1.2 修复版**及其随包测试，基于 0.1.1 私有仓库基线维护。

**当前状态：部分验收通过，完整端到端未通过。** 0.1.2 新增逐原件读取声明门禁、移除报告中的执行机构型号前缀回填，并修复常见规格标签/单位提取及源有值但解析失败的复核队列。测试结果见验收摘要；本轮没有新增模型调用。此前完整诊断因未实读全部原始文件而被拒绝验收，底层读取问题和端到端验收仍未解决。详见 [验收摘要](docs/ACCEPTANCE.md) 与 [已知问题](docs/KNOWN_ISSUES.md)。

“离线”指阀门离线测试资料，模型诊断仍需要联网。当前目标为 Linux + Codex；Windows、macOS 与其他 Agent 未验证。匿名合成验收不能证明真实设备诊断准确性，也不能用于评级、投用或工程验收决策。

## 目录

- `skill/valvue3-offline-diagnostic/`：0.1.2 分发树，包括源码、测试、匿名回归夹具、未变更的依赖锁定文件与更新后的完整性清单
- `docs/ACCEPTANCE.md`：已有验收证据与当前边界
- `docs/KNOWN_ISSUES.md`：未解决问题与后续核验条件

仓库说明与 Git 元数据位于分发目录之外。完整性检查拒绝分发目录中的额外文件，因此不要将 README、项目数据、虚拟环境或 Git 仓库放入该目录。

## 开发环境

先克隆本私有仓库，再在仓库根目录操作。Python 最低版本为 3.11，此前验证版本为 3.12。使用隔离环境；不要复制已有认证、代理配置或虚拟环境。

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r skill/valvue3-offline-diagnostic/requirements-test.txt
```

仅运行时可改用 `requirements.txt`。依赖按原包锁定，没有增加或替换版本。系统前置条件及按阶段的要求见 [运行环境](skill/valvue3-offline-diagnostic/references/runtime-environment.md)。完整链路涉及 Codex CLI、Bubblewrap、OCR 外部服务，以及正式 PDF 所需的 WPS、RPC 和显示环境；安装 Python 依赖不会安装或验证这些外部能力。

以下是本次修复后和后续开发使用的检查命令：

```bash
python skill/valvue3-offline-diagnostic/scripts/check_environment.py
python -m pytest -q skill/valvue3-offline-diagnostic/scripts/tests
python skill/valvue3-offline-diagnostic/scripts/validate_offline_regressions.py
```

环境自检不提交模型请求。`partial` 的返回码为 1，按 `docx`、`diagnosis`、`ocr`、`wps_pdf` 区分受影响阶段；发现前置条件不代表真实调用或交付已验收。开发修改使分发哈希不再匹配时，应如实保留该状态，完成必要验证后再走版本化发布流程。

## 安装为 Skill

先阅读 [独立安装与分发合同](skill/valvue3-offline-diagnostic/references/distribution-and-installation.md)。在仓库根目录，将完整的分发子目录复制到业务工作区的全新安装位置，不覆盖已有安装：

```bash
WORKSPACE="/absolute/path/to/business-workspace"
TARGET="$WORKSPACE/.agents/skills/valvue3-offline-diagnostic"
test ! -e "$TARGET" || { echo "目标已存在，请选择新安装位置"; exit 1; }
mkdir -p "$WORKSPACE/.agents/skills"
cp -a skill/valvue3-offline-diagnostic "$TARGET"
python "$TARGET/scripts/check_environment.py"
```

上面的 `WORKSPACE` 必须替换为实际绝对路径。使用前面配置好的隔离 Python 环境。复制文件不证明 Agent 已加载 Skill；接收环境未自动发现时，应显式读取 `SKILL.md`。

客户项目放在业务工作区独立的 `离线诊断/projects/`，并在该工作区的 Git 忽略配置中排除，不能放入 Skill 或本仓库。报告流程从 `scripts/run_offline_report.py --project <明确项目目录>` 进入，阶段参数与批准绑定遵循随包 [项目合同](skill/valvue3-offline-diagnostic/references/project-contract.md)。真实模型调用、业务判断与正式交付应另行核验，不能从建库或测试通过推定授权或完成。

## 数据与发布边界

- 不提交认证文件、API 密钥、客户原件、项目配置、现场反馈、模型输出、运行日志、报告或本机环境
- 本仓库保留的是原包内已有的匿名测试夹具；真实链路验收的原始日志、输入和输出未纳入
- 验收时单独使用的 Tesseract OCR 适配器不在原分发包内，本仓库也未附加；随包仅有 OCR 客户端与测试
- WPS 应用未安装验收，适用许可证未核实，正式 PDF 及分页视觉验收未完成
- 分发文档引用的上游 `build_offline_skill_package.py` 未随包提供，本仓库未补造该构建器
- 未添加许可证文件；依赖与第三方组件仍须核实各自授权

0.1.1 原包另行保留，Git 基线为 `45e36d189bc65097f7c5731fcba350e23d7ac43e`。0.1.2 的版本、白名单、逐文件清单及运行指纹按实际内容更新。清单哈希用于一致性核对，不是发布者数字签名；具体记录见验收摘要。
