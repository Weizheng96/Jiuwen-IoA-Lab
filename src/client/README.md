# IoA 统一客户端

一个常驻客户端承载个人助手和服务方两个角色。九问、Codex与Claude Code分别按自己的Skill加载方式使用同一账号、配置、身份数据和运行实例。

默认平台https://ioalab.openjiuwen.com；原外置配置或用户显式地址优先。首次账号预检失败才询问平台地址，不绕过TLS。

## 安装和接入

按仓库根README_AGENT.md，用Git浅克隆已发布tag、稀疏检出src/client，再运行其中bootstrap.py。安装器核官方来源、tag/commit及脚本内容，从固定Git对象生成含受审vendor输入的构建快照；pip本地安装，不再次远端克隆客户端。普通依赖及构建工具沿用户可信包源取得，客户端不必发布到PyPI。

安装完成导出个人助手与服务方两个Skill。九问经文件卡片/菜单导入；Codex和Claude Code读取本机实际解压目录中的Skill。账号在SDK本机页面录入，随后显式选择host.type=jiuwen、codex或claude_code。Codex及Claude Code提供command和model；九问提供command及原data_dir，三者均可显式配置业务技能目录。

按SDK状态分别核账号认证、host.available、platformConnected和身份bound。Codex和Claude Code首版不支持原生生产工具、原生提问回调或自动通知当前桌面聊天，缺失能力不伪装就绪。升级先沿原配置安全停止，保留身份与在途业务。
