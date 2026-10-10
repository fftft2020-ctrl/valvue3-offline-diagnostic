# 离线诊断只维护一个版本

当前统一源码版本：0.1.3（开发候选，未正式发布）。

以后固定按这一条路走：**本机改代码 → 本机测试 → 明确授权后同步 GitHub → 重新打包分发**。客户资料一直留在本机；你无需自己整理试验分支。当前验收结果见 [验收记录](ACCEPTANCE.md)。

- 日常只修改当前工作区的 `.agents/skills/valvue3-offline-diagnostic/`；Docker、构建器与匿名回归文件也在当前工作区维护。
- GitHub 私有仓库 `fftft2020-ctrl/valvue3-offline-diagnostic` 保存从本机导出的同一版本，不把两个地方当作独立开发入口。
- 客户项目仍在 `离线诊断/projects/`。原始资料、参数批准、冻结包、反馈和报告不进入 GitHub、镜像或源码包。
- 安装副本、ZIP 和 Docker 镜像都是生成产物，不在其中直接修源码。旧诊断引用的旧版本必须保留。

## 每次修改后的顺序

1. 在维护源码中修改，保留不相关改动。
2. 运行 `python -m pytest -q` 与 `python scripts/validate_offline_regressions.py`。
3. 若改变模型接入或报告合同，在已授权的目标项目补做相应实际链路验证；自动测试不能代替原件实读、诊断质量或 WPS 交付。
4. 用下面的命令导出到一个新目录，检查副本和安装包。
5. 用户明确授权提交/推送/合并后，只同步离线白名单文件到 GitHub。不要给整个业务工作区直接添加离线仓库远端或上传其历史。
6. GitHub 主分支整合后，停止维护被覆盖的试验分支；关闭 PR、删除分支分别按已获授权处理。

```bash
python scripts/build_offline_skill_package.py --repository-output .scratch/offline-release-0.1.3
python scripts/build_offline_skill_package.py --output .scratch/valvue3-offline-diagnostic-0.1.3.zip
```

两个命令都拒绝覆盖已有目标。导出只复制显式白名单，不包含 Git 历史、在线代码、客户资料或登录。目录结构与维护源码相同，独立副本可以运行相同测试和构建命令；Docker 构建时再生成并核对 Skill 安装清单。

源码测试通过后，完整发布还需要在目标机器实际验证模型读取/诊断、WPS 导出与视觉验收，并在第二台干净 Linux 机器验证安装。未具备相应环境时保留候选状态，不以测试数量代替完整验收。

必须保留维护源码、锁定依赖、匿名夹具、构建器，以及被旧诊断引用的版本与项目证据。无引用的导出副本和镜像可在明确授权后清理；它们分别由上述导出命令和 `bash docker/run.sh build` 重建，镜像重建需下载依赖，耗时和网络成本高于源码导出。
