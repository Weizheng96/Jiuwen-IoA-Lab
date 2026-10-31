# 接入设置

触发：首次接入、修改配置或诊断。先确认已在平台注册，并有支持公开Process接口的原版九问。

1. 用户将settings.example.json复制到~/.ioalab/settings.local.json或指定外置文件，填写平台、账号、password/password_env（二选一）和既有九问命令/数据目录。模型不读取配置正文来检查密码。
2. data_dir使用专用持久目录；同账号至多一个assistant，可另有多个service。已有实例时使用原配置、身份及路径，不另建身份库。max_model_runs限制同时执行模型轮次，不是累计调用数。
3. **执行前先读取[公共SDK启动与恢复](shared-runtime.md)**，按其完整命令安装/启动并核身份、连接、宿主能力和实际就绪；不以导入或进程存在报告业务成功。

个人助手后台收件由常驻SDK维护，不由模型轮询连接。收信规则和外部工具按本机明确配置装配；安装官方可选包不会自动启用邮箱、日程或其他权限。配置变更不扩大旧交互的冻结定义。

如需主人网页收信，host.business_tools配置jiuwen-web：profile为本机身份键，options声明实际/ws地址、user_id、secret_env及专用credentials_file。用{"profile":"原配置键"}参数调用runtime.web-extension，只准备原生用户应用目录内的扩展；prepared不等于已加载。需要时由用户在九问原生入口重启，再查实际通知能力。不得修改安装、全局配置或权限。
