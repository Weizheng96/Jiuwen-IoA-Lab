---
name: gitcode-sync
description: 将本项目已发布到GitHub的main及其可达设计标签同步到GitCode主仓；仅在用户要求把develop合入main时执行，不用于日常develop提交、仅查看GitCode或同步其他仓库。
---

# 同步 GitCode 主仓

先读[项目约定](../../../AGENTS.md)。复用[同步脚本](../../../tools/sync_gitcode.py)，不复制其检查或实现另一条推送链。

## 工作流程

1. 同步只由用户要求把 develop 合入 main 触发；日常 develop 提交与推送、阶段完成或查询请求都不同步 GitCode。与并行写者协调 Git 窗口，不暂存、stash、覆盖或替他人提交在途工作。
2. 先按[交付](../develop/references/delivery.md#合入-main)完成合入并推送 GitHub main，再在 main 上核对提交快照与已发布 GitHub 源。跟踪文件有未提交修改时先协调原写者完成检查点；不绕过脚本，不承诺同步工作区草稿、数据或秘密。
3. 在仓库根目录使用项目 Python，先核计划，再执行同一脚本：

   ```text
   python tools/sync_gitcode.py --dry-run
   python tools/sync_gitcode.py
   ```

   GitCode 始终直连；GitHub 需要命令级代理时，两条命令均加 `--github-proxy http://127.0.0.1:7897`，实际地址按当前环境选择。GitCode 命令或 remote 显式禁用代理并保持 TLS 验证；服务器连接归部署技能。TUN 或路由变化后重核实际路径，NO_PROXY 本身不是直连证据。不修改系统或全局代理，不因直连失败自动改走代理；不把凭据放入参数或日志。`--dry-run` 只核计划，不证明推送权限。
4. 保存真实退出码及 JSON 结果。脚本成功返回 `status=synchronized` 时已核对目标 `main` 与全部选定设计标签的对象；报告 `sourceCommit`、同步引用及双远端核验范围，明确未提交工作不在此次快照内。

分叉、标签冲突、认证失败或结果未知时，保留原报告并停止该次同步，核对原远端后再决定下一步；不使用 force、mirror、删除远端引用或改写历史来绕过检查。不为技能说明验证重复执行真实推送测试；同步脚本未变时复用已有回归结果。
