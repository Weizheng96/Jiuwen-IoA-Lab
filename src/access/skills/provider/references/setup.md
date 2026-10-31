# 接入与业务能力

触发：本机用户配置或诊断服务。先确认已有平台账号、支持公开 Process 接口的原版九问，以及实际业务技能/工具。

1. 用户把settings.example.json复制到~/.ioalab/settings.local.json，或用户指定的外置配置文件，填写平台、账号、密码环境变量和九问命令/数据目录。实际密码不输出到模型或日志；可由用户选择 password 替代 password_env。data_dir 是专用持久目录，配置/参数放在其外，不删除重建身份。
2. 已有个人助手实例时，使用 `--config <原配置文件>`；在同一文件增加 service 身份和 provider.services 对应 profile。保留已有身份与路径，不能再启动一个竞争实例。
3. 业务配置包括 instructions、skills、resources、max_active_delegations、accepting，以及精确 native_tools。skills 填本机用户选择的业务技能名；host.skill_directories 将各名称映射到实际绝对目录，可使用原生技能加载结果的 skill_directory。SDK 只冻结并读取 SKILL.md 和 references 下的 Markdown，设置与脚本不进入文档读取入口。缺少源目录时先完成配置，不凭技能名称宣称可用。
   技能说明不自动授予工具；native_tools 默认空，只有用户明确配置的本地生产工具才可启用，保留原宿主拒绝。permission.get 是命名权限策略，未列出不代表不存在或已验证；核对状态中的 unavailableSkills、unavailableNativeTools 和 uncheckedNativeTools，实际工具错误如实处理。外部业务效果仍需授权和幂等工具。
4. 配置完成后，用终端依次执行下方三条命令。runtime.start已包含共享隔离依赖安装、配置校验和启动；从真实回执核身份、连接及执行能力，不以进程存在冒充可接单。安装不替换九问、不改全局Python。
5. configuration_changed / installation_changed 时，先用空 JSON 参数调用 runtime.stop，等原实例安全停止，再按原配置启动。保留合同、原会话和私钥；停止不取消已签合同，不自动释放名额。

```text
python "<skill_directory>/scripts/ioalab.py" runtime.start
python "<skill_directory>/scripts/ioalab.py" runtime.status
python "<skill_directory>/scripts/ioalab.py" identity.status
```

实际skill_directory取原生加载结果。两种Skill默认共用~/.ioalab/settings.local.json，配置不可放在Skill目录内；原生覆盖导入会替换该目录。使用其他外置配置时，三条命令均追加 `--config "<原配置文件>"`。这些操作是脚本参数，不是原生工具；不去技能商店重装本技能。runtime.install只用于单独预装，runtime.validate只用于单独检查，普通接入不必另做。读取脚本或清单不是执行命令，只汇报真实stdout回执，无需写安装报告或长期记忆。

password/password_env由用户二选一，环境变量须已设置且非空，模型不读取配置正文检查。data_dir存持久身份和业务状态，与依赖安装缓存分开；同账号至多一个assistant，可配置多个service。host.command/data_dir指向用户原版九问，不是平台安装目录。

接单并发限制未完成承诺，max_model_runs限制同时运行的模型轮次，不是累计调用次数；等待用户授权/验收仍占接单容量。具体服务的模板与验收器按发布流程处理；目录公示不等于业务执行已就绪。

普通调用用主文件中的脚本格式。工具配置或权限变动不扩大既有交互的冻结定义；缺原定义时恢复原配置，不另开无历史 Session。后台收件由常驻 SDK 负责，不让模型轮询维持连接。

共享环境包含官方可选业务工具，安装不启用日程、邮箱或其他权限。只有本机用户显式配置host.business_tools，相关profile才获得工具；后台消息不能选择工厂或修改配置。instance_changed时先重新查看实际实例，不把原操作改成新业务。安装缓存损坏时保留运行数据并报告，不原地重装正在使用的环境。

如需主人九问网页收信，本机配置host.business_tools的jiuwen-web项：profile为本角色配置键，options含url（/ws及匹配user_id）、user_id、secret_env及专用credentials_file。未有密钥时，准备命令可在IoA配置目录内生成新的专用凭据文件，不覆盖已有文件。用参数文件{"profile":"原配置键"}调用runtime.web-extension；它只准备原生用户应用目录内的IoA扩展，返回prepared不等于已加载。需要时请用户用九问原生入口重启，再查runtime.status的实际通知能力；不修改安装、全局配置或权限。
