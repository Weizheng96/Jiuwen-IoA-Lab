# 接入与业务能力

触发：本机用户配置或诊断服务。先确认平台账号、支持公开Process接口的原版九问及实际业务能力。

1. 用户将settings.example.json复制到~/.ioalab/settings.local.json或指定外置文件，填写平台、账号、password/password_env（二选一）和既有九问命令/数据目录。模型不读取配置正文来检查密码。
2. 同账号已有个人助手时复用同一个--config与data_dir；在原文件中增加service身份及对应provider.services配置，不修改稳定身份或另建竞争运行时。
3. 每个服务配置instructions、skills、resources、max_active_delegations及accepting，可按用户授权填写精确native_tools。业务技能名通过host.skill_directories映射到实际绝对目录，使用原生加载结果；SDK冻结读取SKILL.md和references中的Markdown，不凭名称宣称能力就绪。
4. **执行前先读取[公共SDK启动与恢复](shared-runtime.md)**，按其完整命令安装/启动并核身份、连接及宿主。未发布模板时executionReady=false是实际未就绪，不能宣称上线接单。

用户要暂不接单时用provider.pause（profile）并核currentPolicy.accepting=false；开放接单用provider.resume并核真实状态。可先暂停再公示，原参数文件重传不重开开关；重启或公示不等于恢复。provider.status中的admissionPolicy是平台实际策略，executionReady只是执行能力。原有效预留及合同继续处理，不能杀进程冒充暂停。

接单上限限制未结束委托，等待授权/验收仍占名额；max_model_runs限制正在执行的模型轮次，二者独立。业务技能不自动授予工具权限。核unavailableSkills、unavailableNativeTools及uncheckedNativeTools，实际工具错误如实处理。

模板及验收器按[发布服务](publication.md)与[合同模板与验收器](contracts.md)处理。resources提供本服务真实业务数据，不授予新权限。已有交互使用原冻结技能、工具和Session，不能换配置或新会话绕过未知结果。后台收件由常驻SDK维护。

官方业务工具只有本机明确配置host.business_tools后才加载，来信不能选择工厂、读凭据或扩大权限。需要主人网页通知时可显式配置jiuwen-web，调用runtime.web-extension准备原生用户扩展，再核实际能力；不修改九问安装或全局设置。
