# 可复用工具

可复用脚本按职责查找；一次性脚本与草稿放 `temp/`，所有复用数据和实测记录放 Git 忽略的 `.agents/data/`。存在工具不表示其专项已通过。

| 职责 | 入口 | 使用技能 |
|---|---|---|
| 检查与覆盖 | `.agents/skills/develop/scripts/check.py` 总入口，`check_scope.py`、`test_layers.py`、`selfcheck.py`、`check_docs.py`、`check_modules.py`（模块文件夹对代码：依赖声明、入口导入、接口测试列、进展索引，以及 `CONVERGED` 名单内模块的入口规则），`reading_budget.py`（改一个模块的必读字数，轻量检查按门槛核对，复盘时与上次记录比较） | [testing](../.agents/skills/testing/SKILL.md) |
| 依赖与发布同步 | `sync_a2c.py`、`sync_gitcode.py` | [develop](../.agents/skills/develop/SKILL.md)、[gitcode-sync](../.agents/skills/gitcode-sync/SKILL.md) |
| 随仓基础镜像 | `container_images.py verify/load`；第三方原件归 `vendor/container-images/` | [deployment](../.agents/skills/deployment/SKILL.md) |
| 新服务器连接 | `server_connection.py`（开发环境需 paramiko 4）：读取唯一私有 `.agents/server-connection.json` 及其中已核主机公钥/指纹，严格拒绝未知或变更密钥；`--script` 通过 stdin 执行 Bash，`--upload` 用 SFTP；不另读凭据源或主机密钥缓存 | [deployment](../.agents/skills/deployment/SKILL.md) |
| 本机Codex宿主验收 | `verification/codex_host.py --codex <绝对路径> --model <模型> --report-dir <新目录>`；可显式给进程级`--proxy`，验证真实工具、原Session续接和取消，不触发平台业务 | [testing](../.agents/skills/testing/SKILL.md) |
| 安装与原生专项 | `verification/` 的安装、管理员、资源及网页回执探针 | [deployment](../.agents/skills/deployment/SKILL.md)、[jiuwen-swarm](../.agents/skills/jiuwen-swarm/SKILL.md) |
| 媒体与场景构造 | `media/` 的剧本、配音、剪辑、证据及发布工具 | [demo-video](../.agents/skills/demo-video/SKILL.md) |
| 模型输入与初始化 | `llm.py`、`import_model_source.py` | [llm](../.agents/skills/llm/SKILL.md) |

工具仅按显式参数执行。真实模型、外部消息、业务、部署及录制按原授权运行，不为整理文件触发。
