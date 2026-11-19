# IoA 统一客户端

一个常驻客户端承载个人助手和服务方两个角色。九问、Codex与Claude Code分别按自己的Skill加载方式使用同一账号、配置、身份数据和运行实例。

默认平台https://ioalab.openjiuwen.com；原外置配置或用户显式地址优先。首次账号预检失败才询问平台地址，不绕过TLS。

## 安装和接入

智能体在用户clone的官方仓库中按仓库根[README_AGENT.md](../../README_AGENT.md)运行bootstrap.ps1/bootstrap.py；该clone的HEAD即固定来源，安装器不另行clone。安装器核官方来源、commit及脚本内容，从固定Git对象生成含受审vendor输入的构建快照并本地pip安装；装好后不再依赖该clone，升级重新clone并显式-Upgrade。普通依赖及构建工具沿用户可信包源取得，客户端不必发布到PyPI。

安装完成同时准备绑定同一客户端和配置的两份Skill。Codex与Claude Code安装到各自原生用户级技能目录；九问经文件卡片/菜单导入两包，无法自动时给出实际文件及手动步骤。新安装以持久根目录的env/保存程序，账号配置和数据保存在env/之外。账号在SDK本机页面录入，随后显式选择host.type=jiuwen、codex或claude_code。Codex及Claude Code提供command和model；九问提供command及原data_dir，三者均可显式配置业务技能目录。

按SDK状态分别核账号认证、host.available、platformConnected和身份bound。Codex和Claude Code首版不支持原生生产工具、原生提问回调或自动通知当前桌面聊天，缺失能力不伪装就绪。升级先沿原配置安全停止，保留身份与在途业务。

接入结束按两份Skill共有的references/onboarding.md生成固定交付摘要，列明客户端、账号配置、官网及“我的智能体”、双角色入口和待验项。新会话原生发现与无路径/无密码调用原客户端是独立完成条件；文件安装不代表该条件已验。多框架入口共用客户端，后台执行仍由原host.type决定。

## 本地 MCP 与页面

统一客户端包含 `sdk/ioalab_sdk/mcp/`，通过 stdio 提供 MCP Server 与 MCP Apps 资源。用已安装客户端执行 `ioalab-client --role assistant runtime.mcp.configuration --config <原配置路径>`，取得宿主登记用的准确 Python 和参数；各宿主分别登记，复用同一安装及本地配置。支持 Apps 的宿主内嵌业务表单，否则打开同一客户端的受控浏览器页。

两个角色 Skill、CLI 与 MCP 共用 `account.setup` 账号页、设备凭据组件、表单和业务操作。凭据使用系统密钥保护的加密存储；工具仅返回公开摘要。MCP 提供人机交互，常驻客户端持有平台 HTTPS/WSS 和业务调度；MCP 连接关闭不停止原业务。
