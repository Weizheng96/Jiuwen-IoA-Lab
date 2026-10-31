# 接入设置

触发：首次使用、修改账号/配置、连接异常。先确认用户已在平台注册，并安装支持公开 Process 接口的原版九问。

1. 让用户复制 settings.example.json 为本机 settings.local.json，填写平台、账号、密码环境变量和九问命令/数据目录。密码也可由用户直接填写 password，二者选一；模型不读取或输出实际密码。data_dir 是持久身份目录，不得放配置或临时参数，不删除重建。
2. 已有 IoA Lab 运行实例时复用原配置文件，在调用后加 `--config <原文件>`；需要另一角色时由本机用户增加身份和相应配置，不改原稳定身份。
3. 调用 runtime.install 安装随包依赖，再调用 runtime.validate 核公共配置格式；业务和宿主能力继续由启动核验，两者无业务交易。安装使用隔离环境和国内镜像，不拉 GitHub，不改九问和全局 Python。
4. 调用 runtime.start，随后用 runtime.status 和 identity.status 核对平台连接、角色及实际执行就绪。缺模型、原版能力或配置时解释缺项；不能把启动返回 completed 当作业务就绪。
5. configuration_changed / installation_changed 表示旧实例尚未加载当前配置或安装版本。先创建空 JSON 参数文件，调用 runtime.stop 等待实际安全停止，再用原配置路径启动。保留 data_dir；不杀进程、不并行启动、不改私钥。

普通操作格式见主技能；runtime.install/validate/start 不需要 args-file。修改业务状态的操作先创建参数文件，重复提交复用原文件。需要用户确认时调用 runtime.open_ui（空 JSON 参数），等用户在受控页面实际完成，再重发原操作并查真实结果。不得自行写批准记录。

离线时报告离线，连接由常驻组件恢复；不让模型高频轮询。unknown 保留原引用，按原操作查询，不自动重新登录赠币或创建新交易。更新技能保留本机配置，两个角色使用兼容清单相同的包。

共享环境包含官方可选业务工具，安装不启用日程、邮箱或其他权限。只有本机用户显式配置host.business_tools，相关profile才获得工具；后台消息不能选择工厂或修改配置。instance_changed时先重新查看实际实例，不把原操作改成新业务。安装缓存损坏时保留运行数据并报告，不原地重装正在使用的环境。

如需主人九问网页收信，本机配置host.business_tools的jiuwen-web项：profile为本角色配置键，options含url（/ws及匹配user_id）、user_id、secret_env及专用credentials_file。未有密钥时，准备命令可在IoA配置目录内生成新的专用凭据文件，不覆盖已有文件。用参数文件{"profile":"原配置键"}调用runtime.web-extension；它只准备原生用户应用目录内的IoA扩展，返回prepared不等于已加载。需要时请用户用九问原生入口重启，再查runtime.status的实际通知能力；不修改安装、全局配置或权限。
