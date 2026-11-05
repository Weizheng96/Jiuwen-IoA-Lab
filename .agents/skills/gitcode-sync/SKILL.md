---
name: gitcode-sync
description: 将本项目已发布到GitHub的main及其可达设计标签同步到GitCode主仓；用于用户要求同步本仓更新，不用于仅查看GitCode或同步其他仓库。
---

# 同步 GitCode 主仓

先读[项目约定](../../../AGENTS.md)。复用[同步脚本](../../../tools/sync_gitcode.py)，不复制其检查或实现另一条推送链。

## 工作流程

1. 核对用户当前请求是否授权同步；已有明确授权直接继续，只有查询请求时不推送。与并行写者协调 Git 窗口，不暂存、stash、覆盖或替他人提交在途工作。
2. 核对 `main` 的提交快照与已发布 GitHub 源，按项目约定提交并推送本次获准文件。跟踪文件有未提交修改时先协调原写者完成检查点；不绕过脚本，不承诺同步工作区草稿、数据或秘密。
3. 在仓库根目录使用项目 Python，先核计划，再执行同一脚本：

   ```text
   python tools/sync_gitcode.py --dry-run
   python tools/sync_gitcode.py
   ```

   GitCode 始终直连；GitHub 需要命令级代理时，两条命令均加 `--github-proxy http://127.0.0.1:7897`，实际地址按当前环境选择。网络和凭据边界遵守 AGENTS；不把凭据放入参数或日志。`--dry-run` 只核计划，不证明推送权限。
4. 保存真实退出码及 JSON 结果。脚本成功返回 `status=synchronized` 时已核对目标 `main` 与全部选定设计标签的对象；报告 `sourceCommit`、同步引用及双远端核验范围，明确未提交工作不在此次快照内。

分叉、标签冲突、认证失败或结果未知时，保留原报告并停止该次同步，核对原远端后再决定下一步；不使用 force、mirror、删除远端引用或改写历史来绕过检查。不为技能说明验证重复执行真实推送测试；同步脚本未变时复用已有回归结果。
