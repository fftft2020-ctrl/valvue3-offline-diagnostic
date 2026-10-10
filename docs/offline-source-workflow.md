# 离线诊断只维护一个 Skill 版本

当前版本为 0.1.3 开发候选。用户于 2026-10-10 选择只分享 Skill，停止以 Docker 作为当前部署和分发入口。

固定流程：**修改本机 Skill → 测试 → 生成 Skill ZIP → 接收方安装依赖并使用自己的 Codex 账号**。GitHub 私有仓库是同一源码的备份和分享入口；同步仍需明确 Git 授权，不能把线上和本机当作两套独立开发版本。

- 唯一维护源码在当前工作区 `.agents/skills/valvue3-offline-diagnostic/`，共用构建器、匿名测试和经验。
- 客户项目只在 `离线诊断/projects/` 保存资料、配置、反馈、批准、冻结和报告，不进入 ZIP 或共享 Git。
- 安装副本与 ZIP 由源码生成，不在副本中修改。保留被旧诊断引用的版本和证据。
- Docker 部署源码、根目录配置和旧工作流已撤出当前工作区入口，留在本机忽略的恢复档案；当前源码导出和 CI 只有 Skill。旧镜像、模型缓存、认证和验收记录继续保留，不清理其他项目服务。

## 修改后的验证与打包

```bash
python -m pytest -q tests .agents/skills/valvue3-offline-diagnostic/scripts/tests
python scripts/validate_offline_regressions.py
python scripts/build_offline_skill_package.py --output .scratch/valvue3-offline-diagnostic-new.zip
python scripts/build_offline_skill_package.py --repository-output .scratch/offline-skill-source-new
```

两个导出命令都要求新路径。ZIP 可直接分享；源码副本供 GitHub 同步，只包含 Skill、维护文档、构建器、匿名测试和 Skill CI，不包含 Docker 部署文件。先核对清单和解包结果，再在接收环境检查前置条件；实际模型和正式 PDF 验收按任务需要执行，自动测试不能代替。

只有用户明确授权提交/推送/合并后，才同步该白名单源码到 GitHub。用户于2026-10-10已明确授权取消Docker配置并将远端改为Skill；同步须核对新导出与旧树差异，仅提交该离线仓库的文件，不提交整个本机业务工作区。

成果为 Skill ZIP、安装说明和源码导出。必须保留维护源码、依赖锁定文件、匿名夹具、构建器及被旧诊断引用的版本和项目证据。无引用的导出/解包副本可在授权后清理，ZIP 和源码副本可用上述命令重建；真实诊断重跑会消耗额度且结果可能不同，冻结证据不能以重跑替代。旧 Docker 大文件清理候选与重建代价继续沿用已有工程记录，当前不删除。
