# 帮用户接入 IoA Lab

本文件是唯一安装与接入说明。目标平台地址使用用户本次指令明确提供的地址；已有配置保持原平台，未提供地址时沿用所选 Skill 的默认平台探测规则，不从仓库网址推断平台地址。

复用用户已有九问Swarm。IoA只有一个客户端，个人助手和服务方由两个角色Skill区分；不按角色重复安装客户端。

1. 使用当前 GitHub 仓库中已发布、包含`src/client/pyproject.toml`的客户端tag；统一 GitHub 安装来源从 `design-v0.0.175` 开始，选择该版本或更高版本。标签形如`design-v0.0.N`，按数字N核对版本。下载同一tag的[bootstrap.py](https://raw.githubusercontent.com/ZhangRan-Zora/Jiuwen-IoA-Lab/TAG/src/client/bootstrap.py)到本机独立工作目录，将URL中的TAG替换为实际tag。原文不可读、tag不存在或版本尚未发布时报告实际原因，不猜其他地址，不使用平台网站下载客户端，不关闭TLS校验。
2. 核本机Git和CPython3.11，在终端执行下载的脚本（Windows使用已核的Python3.11；Linux用python3.11）：

   ```text
   python bootstrap.py --tag <实际已发布tag>
   ```

   默认使用持久客户端目录`~/.ioalab/client`和Skill导出目录`~/.ioalab/role-skills`。需要其他持久目录时才指定`--install-dir`和`--output`。宿主终端有短时限时，用其原生后台任务能力执行并保留任务引用；观察超时只查询原任务，不重复安装。

   脚本通过pip从GitHub tag解析出的固定commit安装`src/client`，检查依赖、安装文件摘要及来源，导出个人助手和服务方两个轻量Skill。普通第三方依赖与构建工具使用本机配置的可信包源；客户端无需发布到PyPI。安装失败时停止本次接入，报告脱敏错误并保留原目录及原任务引用，等待安装程序修复；不自行删除目录、修改脚本或改写安装步骤。成功不代表账号或平台连接已就绪。
3. 按真实回执中的绝对文件路径，用九问原生`send_file_to_user`发送两个Skill ZIP到`web`（`abs_file_path_list`为绝对路径列表的JSON字符串，`target_channels`为`["web"]`），核交付成功。引导用户在原生“我的技能→创建”上传或保存文件卡片，再在会话中选择“个人助手”或“服务方”Skill；实际用`skill_tool`读取所选Skill及`references/setup.md`，继续其账号、宿主、启动与状态流程。账号秘密只在本机账号页填写，角色切换复用原账号、配置、身份与常驻客户端。

已有客户端也可直接在其独立环境执行等价pip安装：

```text
python -m pip install "ioalab-client @ git+https://github.com/ZhangRan-Zora/Jiuwen-IoA-Lab.git@<实际已发布tag>#subdirectory=src/client"
python -m ioalab_client skills --output <角色Skill导出目录>
```

首次接入推荐bootstrap，以便自动核验来源并保留可恢复的安装归属。升级前按Skill的安全停止流程处理原实例，不在活动环境中直接覆盖安装。

客户端已包含本地 MCP Server 与 MCP Apps 资源。加载角色 Skill 后调用 `runtime.mcp.configuration`，使用回执中的同一客户端 Python 和配置路径登记到宿主 MCP Client；无需安装另一套 SDK。MCP Apps 能力由宿主协商，支持时内嵌业务表单，不支持时使用同一客户端的受控浏览器页面。账号页只在本机输入秘密，设备会话加密保存在本地；MCP 工具只返回公开进度。不同宿主分别登记 MCP，后台执行者是否就绪仍以 `runtime.status` 为准。
