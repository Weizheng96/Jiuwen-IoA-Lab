# 接入设置

触发：首次接入、修改配置或诊断。已有支持公开Process接口的原版九问时，首次接入先读[账号引导](account-setup.md)，不要求用户预先注册或手写凭据配置。已有配置沿用下列受控入口。

1. 账号页创建或复用~/.ioalab/settings.local.json或指定外置文件；通过受控配置入口补齐既有九问命令/数据目录。账号、身份和宿主检查只使用SDK脱敏结果；不得读取该文件正文，即使只是检查文件存在或角色配置。
2. data_dir使用专用持久目录；同账号至多一个assistant，可另有多个service。assistant身份承接本技能的委托发起和助手通信；委托方是其业务职责，配置中没有client角色。需要辅助修改时读取公共流程，用runtime.configuration/configure按字段更新，不读取账号配置正文。已有实例时使用原配置、身份及路径，不另建身份库。max_model_runs限制同时执行模型轮次，不是累计调用数。
3. 首次账号按account-setup.md，账号认证完成后按[宿主接入](host-setup.md)，最终状态按[状态核对](status.md)逐项报告。已有配置只查状态时直接读status.md；安装、启停和错误恢复按[公共SDK恢复](shared-runtime.md)。不以导入或进程存在报告业务成功。

个人助手后台收件由常驻SDK维护，不由模型轮询连接。收信规则和外部工具按本机明确配置装配；安装官方可选包不会自动启用邮箱、日程或其他权限。配置变更不扩大旧交互的冻结定义。

结构化需求表单使用原版九问 Web 的可选 A2UI 0.8。锁定版本默认关闭，可由用户在其原版公开配置中设置 a2ui.enabled=true，或在启动九问前设置 JIUWENSWARM_A2UI_ENABLED=true；其他版本先核公开支持。保留用户现有配置，不自动改全局设置或重启其九问。项目独立试用实例由部署显式启用。仅输出a2ui-json标签或schema校验通过不等于网页渲染成功，须实际看到表单、提交并核对事件与后续原SDK回执；A2UI不承担签名授权。

如需主人网页收信，host.business_tools配置jiuwen-web：profile为本机身份键，options声明实际/ws地址、user_id、secret_env及专用credentials_file。用{"profile":"原配置键"}参数调用runtime.web-extension，只准备原生用户应用目录内的扩展；prepared不等于已加载。需要时由用户在九问原生入口重启，再查实际通知能力。不得修改安装、全局配置或权限。
