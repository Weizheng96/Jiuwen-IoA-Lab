# 客户端

参与方只安装一个ioalab-client客户端，通过GitCode tag的pip VCS安装；统一导出个人助手和服务方两个角色Skill，选择Skill决定操作角色。参与方本机常驻接入：一个 SDK 包承载共用运行时、两角色模块与 A2C 协议适配，宿主适配实现 `AgentHost` 契约并装配进程。SDK 不导入平台核心与具体宿主；运行于个人电脑或服务环境，保管本账号参与方私钥与协议数据。子模块各有接口、依赖、实现三页；本组共用[术语](术语.md)与[进展](进展/)。

| 子模块 | 代码 | 文件夹 |
|---|---|---|
| 共用运行时 | `src/client/sdk/ioalab_sdk/runtime/`、`adapters/`、`templates.py`、`src/client/skills/_shared/` | [接口](共用运行时/接口.md) · [依赖](共用运行时/依赖.md) · [实现](共用运行时/实现.md) |
| MCP接入 | `src/client/sdk/ioalab_sdk/mcp/` | [接口](MCP接入/接口.md) · [依赖](MCP接入/依赖.md) · [实现](MCP接入/实现.md) |
| 个人助手端 | `assistant/`、`src/client/skills/assistant/` | [接口](个人助手端/接口.md) · [依赖](个人助手端/依赖.md) · [实现](个人助手端/实现.md) |
| 服务方端 | `provider/`、`src/client/skills/provider/` | [接口](服务方端/接口.md) · [依赖](服务方端/依赖.md) · [实现](服务方端/实现.md) |
| 宿主适配 | `src/client/hosts/jiuwen/ioalab_jiuwen/` | [接口](宿主适配/接口.md) · [依赖](宿主适配/依赖.md) · [实现](宿主适配/实现.md) |
| Codex适配 | `src/client/hosts/codex/ioalab_codex/` | [接口](Codex适配/接口.md) · [依赖](Codex适配/依赖.md) · [实现](Codex适配/实现.md) |
| ClaudeCode适配 | 计划`src/client/hosts/claude_code/` | [接口](ClaudeCode适配/接口.md) · [依赖](ClaudeCode适配/依赖.md) · [实现](ClaudeCode适配/实现.md) |
| SDK装配 | `src/client/sdk/ioalab_sdk/access.py` | [接口](SDK装配/接口.md) · [依赖](SDK装配/依赖.md) · [实现](SDK装配/实现.md) |

组内装配依赖方向为 各宿主适配、SDK装配 → 两角色端 → 共用运行时；模型适配只依赖共用运行时的AgentHost契约。共用运行时只以模块命令启动 SDK装配 与宿主适配，不导入它们。
