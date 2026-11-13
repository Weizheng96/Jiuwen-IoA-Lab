# IoA 统一客户端

一个常驻客户端承载个人助手和服务方两个角色，源码与适配器统一放在本目录。用户在九问中选择角色Skill，复用同一账号、配置、身份数据与运行实例。

默认平台地址为https://ioalab.openjiuwen.com，可通过外置配置的platform_url或首次账号设置的非秘密参数指定。原配置优先；首次接入先尝试连接，失败才询问用户地址。

## 从GitCode tag安装

需要Git和CPython3.11。在独立环境执行：

```text
python -m pip install "ioalab-client @ git+https://gitcode.com/WeiZheng96/jiuwen-IoA-lab.git@<已发布tag>#subdirectory=src/client"
python -m ioalab_client status
python -m ioalab_client skills --output <角色Skill导出目录>
```

客户端本身不发布到PyPI；普通第三方依赖与构建工具使用配置的包源。A2C与Process SDK使用仓库vendor中锁定的原版内容，构建核摘要并保留许可证，不安装九问本体或平台核心。

首次自动接入使用同一tag的bootstrap.py：

```text
python bootstrap.py --tag <已发布tag> --install-dir <独立客户端目录> --output <角色Skill导出目录>
```

脚本解析tag为固定commit、创建独立venv、pip安装、核依赖与安装文件摘要并导出两个Skill ZIP；成功不代表账号、宿主或平台连接已完成。原生导入并选择Skill后继续账号页和宿主接入。既有目录冲突或安装失败保留现场，不覆盖原环境和数据。

源码分为sdk、hosts、skills、examples与ioalab_client。构建使用tools/build_client.py，两个轻量Skill只包含说明和绑定客户端解释器的薄调用脚本。Linux受管部署的组件wheel沿部署模块管理，使用同一客户端源码。

## 本地 MCP 与页面

统一客户端包含 `sdk/ioalab_sdk/mcp/`，通过 stdio 提供 MCP Server 与 MCP Apps 资源。用已安装客户端执行 `ioalab-client --role assistant runtime.mcp.configuration --config <原配置路径>`，取得宿主登记用的准确 Python 和参数；各宿主分别登记，复用同一安装及本地配置。支持 Apps 的宿主内嵌业务表单，否则打开同一客户端的受控浏览器页。

两个角色 Skill、CLI 与 MCP 共用 `account.setup` 账号页、设备凭据组件、表单和业务操作。凭据使用系统密钥保护的加密存储；工具仅返回公开摘要。MCP 提供人机交互，常驻客户端持有平台 HTTPS/WSS 和业务调度；MCP 连接关闭不停止原业务。
