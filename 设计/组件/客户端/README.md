# 客户端

参与方本机常驻接入：一个 SDK 包承载共用运行时、两角色模块与 A2C 协议适配，宿主适配实现 `AgentHost` 契约并装配进程。SDK 不导入平台核心与具体宿主；运行于个人电脑或服务环境，保管本账号参与方私钥与协议数据。子模块各有接口、依赖、实现三页；本组共用[术语](术语.md)与[进展](进展/)。

| 子模块 | 代码 | 文件夹 |
|---|---|---|
| 共用运行时 | `src/access/sdk/ioalab_sdk/runtime/`、`adapters/`、`templates.py`、`src/access/skills/_shared/` | [接口](共用运行时/接口.md) · [依赖](共用运行时/依赖.md) · [实现](共用运行时/实现.md) |
| 个人助手端 | `assistant/`、`src/access/skills/assistant/` | [接口](个人助手端/接口.md) · [依赖](个人助手端/依赖.md) · [实现](个人助手端/实现.md) |
| 服务方端 | `provider/`、`src/access/skills/provider/` | [接口](服务方端/接口.md) · [依赖](服务方端/依赖.md) · [实现](服务方端/实现.md) |
| 宿主适配 | `src/hosts/jiuwen/ioalab_jiuwen/` | [接口](宿主适配/接口.md) · [依赖](宿主适配/依赖.md) · [实现](宿主适配/实现.md) |
| SDK装配 | `src/access/sdk/ioalab_sdk/access.py` | [接口](SDK装配/接口.md) · [依赖](SDK装配/依赖.md) · [实现](SDK装配/实现.md) |

组内依赖方向为 宿主适配、SDK装配 → 两角色端 → 共用运行时。为保持 `python -m ioalab_sdk.runtime` 不变，共用运行时 `__main__.main` 仍延迟委托 SDK装配，属已知偏离，见[票据](进展/12-SDK命令入口迁出共用运行时.md)。迁入顺序见[设计文档按模块重组](../../工程/专题/设计文档按模块重组.md)。
