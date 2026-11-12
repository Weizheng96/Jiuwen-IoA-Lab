# 接入与业务能力

触发：本机用户配置或诊断服务。首次接入先读[账号引导](account-setup.md)，使用service角色；已有账号和配置直接复用。确认支持公开Process接口的原版九问及实际业务能力。

1. 账号页创建或复用外置配置，之后经受控配置入口补齐既有九问命令/数据目录。账号、身份和宿主检查只使用SDK脱敏结果；不得读取该文件正文，即使只是检查文件存在或角色配置。
2. 同账号已有个人助手时复用同一个--config与data_dir。读取公共流程，用runtime.configuration/configure按稳定key增加service身份及provider.services；不读取或整份改写账号配置，不改变既有角色或另建竞争运行时。
3. 用受限配置入口填写instructions、skills、max_active_delegations及accepting，可按用户明确授权填写精确native_tools；resources和外部工具选项留在用户私下配置中，通用配置操作不读取或修改。业务技能名通过host.skill_directories映射到实际绝对目录，使用原生加载结果；SDK冻结读取SKILL.md和references中的Markdown，不凭名称宣称能力就绪。
4. 首次账号按account-setup.md，账号认证完成后按[宿主接入](host-setup.md)，最终状态按[状态核对](status.md)逐项报告。已有配置只查状态时直接读status.md；安装、启停和错误恢复按[公共SDK恢复](shared-runtime.md)。未发布模板时executionReady=false是实际未就绪，不能宣称上线接单。

用户要暂不接单时用provider.flow（profile、action=pause）并核currentPolicy.accepting=false；开放接单用provider.flow（action=resume_admission，原profile及flowRef）并核真实状态。可先暂停再公示，原参数文件重传不重开开关；重启或公示不等于恢复。provider.flow（action=status）中的admissionPolicy是平台实际策略，executionReady只是执行能力。原有效预留及合同继续处理，不能杀进程冒充暂停。

接单上限限制未结束委托，等待授权/验收仍占名额；max_model_runs限制正在执行的模型轮次，二者独立。业务技能不自动授予工具权限。核unavailableSkills、unavailableNativeTools及uncheckedNativeTools，实际工具错误如实处理。

模板及验收器按[发布服务](publication.md)与[合同模板与验收器](contracts.md)处理。resources提供本服务真实业务数据，不授予新权限。已有交互使用原冻结技能、工具和Session，不能换配置或新会话绕过未知结果。后台收件由常驻SDK维护。

官方业务工具只有本机明确配置host.business_tools后才加载，来信不能选择工厂、读凭据或扩大权限。需要主人网页通知时可显式配置jiuwen-web，调用runtime.web-extension准备原生用户扩展，再核实际能力；不修改九问安装或全局设置。
