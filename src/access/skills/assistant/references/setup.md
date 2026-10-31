# 接入设置

触发：首次使用、修改账号/配置、连接异常。先确认用户已在平台注册，并安装支持公开 Process 接口的原版九问。

1. 让用户把settings.example.json复制到~/.ioalab/settings.local.json，或用户指定的外置配置文件，填写平台、账号、密码环境变量和九问命令/数据目录。密码也可由用户直接填写 password，二者选一；模型不读取或输出实际密码。data_dir 是持久身份目录，不得放配置或临时参数，不删除重建。
2. 已有 IoA Lab 运行实例时复用原配置文件，在调用后加 `--config <原文件>`；需要另一角色时由本机用户增加身份和相应配置，不改原稳定身份。
3. 配置完成后，用终端依次执行下方三条命令；runtime.start自身已包含隔离依赖安装、配置校验和启动，不需要先另找安装工具。实际skill_directory取原生加载结果，不猜路径。
4. 根据真实JSON回执核对身份、平台连接及宿主能力。缺模型、原版能力或配置时说明缺项；start返回completed或进程存在不等于业务就绪。
5. configuration_changed / installation_changed 表示旧实例尚未加载当前配置或安装版本。先创建空 JSON 参数文件，调用 runtime.stop 等待实际安全停止，再用原配置路径启动。保留 data_dir；不杀进程、不并行启动、不改私钥。

```text
python "<skill_directory>/scripts/ioalab.py" runtime.start
python "<skill_directory>/scripts/ioalab.py" runtime.status
python "<skill_directory>/scripts/ioalab.py" identity.status
```

两种Skill默认共用~/.ioalab/settings.local.json，配置不可放在Skill目录内；原生覆盖导入会替换该目录。使用其他外置配置时，三条命令均追加 `--config "<原配置文件>"`。runtime.install仅用于单独预装，runtime.validate仅用于单独检查，不是普通接入必做的两步。依赖安装使用隔离环境及国内镜像，不拉GitHub、不改九问或全局Python。读取脚本/清单不是运行命令，不能根据代码推演成功；只汇报真实stdout回执，无需写安装报告或长期记忆。

| 配置项 | 正确含义 |
|---|---|
| password / password_env | 用户二选一；环境变量须已设置且非空；模型不读取配置正文来检查密码 |
| data_dir | SDK身份、原会话关联和业务状态的持久目录，与依赖安装缓存分开 |
| identities | 同一账号至多一个assistant，可另配置多个service；key是本机profile键 |
| max_model_runs | 同时运行的模型轮次上限，不是累计调用次数；不会改变委托接单容量 |
| host.command / host.data_dir | 用户原版九问的实际命令及数据目录，不是IoA平台路径 |

普通操作格式见主技能；runtime.install/validate/start 不需要 args-file。修改业务状态的操作先创建参数文件，重复提交复用原文件。需要用户确认时调用 runtime.open_ui（空 JSON 参数），等用户在受控页面实际完成，再重发原操作并查真实结果。不得自行写批准记录。

离线时报告离线，连接由常驻组件恢复；不让模型高频轮询。unknown 保留原引用，按原操作查询，不自动重新登录赠币或创建新交易。更新技能保留本机配置，两个角色使用兼容清单相同的包。

共享环境包含官方可选业务工具，安装不启用日程、邮箱或其他权限。只有本机用户显式配置host.business_tools，相关profile才获得工具；后台消息不能选择工厂或修改配置。instance_changed时先重新查看实际实例，不把原操作改成新业务。安装缓存损坏时保留运行数据并报告，不原地重装正在使用的环境。

如需主人九问网页收信，本机配置host.business_tools的jiuwen-web项：profile为本角色配置键，options含url（/ws及匹配user_id）、user_id、secret_env及专用credentials_file。未有密钥时，准备命令可在IoA配置目录内生成新的专用凭据文件，不覆盖已有文件。用参数文件{"profile":"原配置键"}调用runtime.web-extension；它只准备原生用户应用目录内的IoA扩展，返回prepared不等于已加载。需要时请用户用九问原生入口重启，再查runtime.status的实际通知能力；不修改安装、全局配置或权限。
