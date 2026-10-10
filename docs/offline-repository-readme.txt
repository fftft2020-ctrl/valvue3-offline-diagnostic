# ValVue3 离线诊断

统一源码版本 **0.1.3，开发候选**。本版本整合原 0.1.1 Python 隔离修复、PR #2 的原件读取声明门禁/规格解析/执行机构型号修复，以及 PR #3 的 Linux Docker 分发。

本机维护源码是唯一开发入口；GitHub 与 Docker 来自同一源码。维护说明见 [单版本流程](docs/offline-source-workflow.md)。本仓库只包含离线通用实现、通用经验、匿名测试和分发工具，不含客户资料、认证、在线诊断或原业务仓库历史。

## 检查与构建

Python 最低版本 3.11，已验证环境为 3.12。先创建自己的隔离环境，再运行：

```bash
python -m pip install -r .agents/skills/valvue3-offline-diagnostic/requirements-test.txt
python -m pytest -q
python scripts/validate_offline_regressions.py
python .agents/skills/valvue3-offline-diagnostic/scripts/check_environment.py
```

环境自检不调用模型，`partial` 返回码为 1，只限制对应阶段。

```bash
python scripts/build_offline_skill_package.py --output .scratch/valvue3-offline-diagnostic-0.1.3.zip
bash docker/run.sh setup
bash docker/run.sh build
```

安装 ZIP 到业务工作区的新 `.agents/skills/` 目录，不覆盖旧安装。报告入口是 `.agents/skills/valvue3-offline-diagnostic/scripts/run_offline_report.py --project <明确项目目录>`，先读 [Skill](.agents/skills/valvue3-offline-diagnostic/SKILL.md)。客户项目在独立的 `离线诊断/projects/`，不在 Skill 内。

Docker 支持目标为 Linux x86_64，接收方自行登录。详见 [Docker 说明](docs/DOCKER.md)。默认镜像没有 WPS，不能正式交付 PDF。模型诊断仍需联网。

[验收记录](docs/ACCEPTANCE.md) 与 [已知缺口](docs/KNOWN_ISSUES.md) 分开记录自动测试、真实模型、OCR、WPS 与另一台机器安装结果。测试通过不等于真实设备诊断准确或完整交付通过。当前没有明确对外许可证，第三方依赖、模型和 WPS 的分发权限需分别核对。
