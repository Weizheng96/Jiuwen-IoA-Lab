# 账号完成后的宿主接入

原 account.setup.status 为 completed 后，继续使用同一脚本及外置配置，不读取配置正文。

1. 调用 runtime.configuration 取得脱敏配置和 configurationDigest。复用已有 host.default 及 host.registrations，不因当前聊天宿主不同而修改默认执行宿主。
2. 当前宿主的安装回执提供真实原生入口、登记引用及公开字段采集步骤；只完成该宿主尚未完成的步骤。来源或命令缺失时按回执报告具体缺项，不猜会话、工作目录、模型或安装目录。
3. 需要登记时，用新建独立 UTF-8 文件向 runtime.configure 提交 expectedDigest 和 changes.host.registrations。既有引用保留类型；新增引用复用账号及实例，首次登记选为默认，之后新增不切换默认。旧单宿主格式须经该公开入口显式迁移。
4. 配置完成后调用 runtime.start，再按 status.md 分别核认证、身份 bound、平台连接与宿主能力。waiting 只观察原实例，不另起进程。文件安装、原生发现、可信来源采集和原会话回执分别报告；未实现或未实测的能力保持 not_ready/待验。

普通业务调用自动复用已运行且账号、配置及安装身份匹配的实例，停止后沿原配置启动。控制、安装和账号准备不递归唤起业务。原生来源由宿主适配采集并验证，模型不提交 host/session/workspace 自报字段。
