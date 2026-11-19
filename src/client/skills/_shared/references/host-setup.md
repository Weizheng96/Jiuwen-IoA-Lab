# 账号完成后的宿主接入

触发：原ioa_setup_status或account.setup.status已completed，继续首次接入。nextAction=use_existing_configuration（包括phase=reused）时改用回执existingConfiguration只读核原实例，不执行本页配置/启动；MCP配置绑定交接按mcp-setup.md。复用账号阶段已定位的脚本、外置配置和原非秘密参数，不打开配置文件。SDK负责保留设备凭据、data_dir与身份；宿主接入按下文公共登记流程完成。

1. 调用 runtime.configuration 取得脱敏配置和 configurationDigest。复用已有 host.default 及 host.registrations，不因当前聊天宿主不同而修改默认执行宿主。
2. 当前宿主的安装回执提供真实原生入口及登记引用。登记只选择适配类型，不采集命令、模型、会话或工作目录。读取回执的 `onboarding.pending`，智能体执行 `actor=agent` 的步骤，只将 `actor=user` 的实际待办原样转告用户；缺原生入口或实际宿主要求启用/重载时准确报告，不推测信任设置。
3. 需要登记时，用新建独立 UTF-8 文件向 runtime.configure 提交 expectedDigest 和 changes.host.registrations。既有引用保留类型；新增引用复用账号及实例，首次登记选为默认，之后新增不切换默认。仅旧统一产品格式经该公开入口显式迁移；已经核对的显式应用保持原装配，不因字段形状迁移。迁移冲突时停止并保留原件，不猜引用、改名或轮试参数。changes.host.migrate_from是runtime.configure的字段，不是独立operation。
4. 配置完成后调用 runtime.start，再按 status.md 分别核认证、身份 bound、平台连接与宿主能力。waiting 只观察原实例，不另起进程。文件安装、原生发现、可信来源采集和原会话回执分别报告；未实现或未实测的能力保持 not_ready/待验。

例如本次安装回执为 `host=codex`、`hostRef=codex_clean`，则配置参数为 `{"expectedDigest":"<刚取得的configurationDigest>","changes":{"host":{"registrations":{"codex_clean":{"type":"codex"}}}}}`。必须用本次回执的实际宿主和引用替换示例；首次默认由SDK自动选择，新增不传default便保留已有默认。不要读取配置正文，也不要自行填写command、model或cwd。

普通业务调用自动复用已运行且账号、配置及安装身份匹配的实例，停止后沿原配置启动。普通控制、安装和账号准备不递归唤起业务；下面的明确入口绑定是唯一setup特例。原生来源由宿主适配采集并验证，模型不提交 host/session/workspace 自报字段。

## 明确入口绑定

账号、宿主登记、原客户端启动和当前宿主必要入口资源完成后，在**本次真实原生入口**执行一次`runtime.entry.bind`，`arguments={}`。此步骤不通过普通终端脚本代调；原生包装自动取得实际来源并完成SDK意图/绑定，模型不提供SID、cwd、CLI、profile、model、目录指针、grant或批准布尔。初次账号准备和SDK启动不以此步骤或宿主探针为前置。

原生入口需要用户信任、授权或重载时，准确展示该宿主安装回执中的动作并保持未完成，不代用户点击，不把安装或后台日志当成已绑定。无此入口时不猜最近聊天、SDK数据目录或其他宿主项目。

只按实际回执报告：`completed`只确认此次入口绑定状态，不证明模型可用、已公示/可接单或四流程通过；`waiting`保留原接入operationId及实际待办；`rejected/failed`按真实缺项修复，不切账号/默认宿主；`unknown`保留原操作及已保存回执，沿原接入查询，不换号从当前聊天重新采集。

已绑定时重复接入复用原项目，新增宿主不隐式切默认；变更项目须显式迁移。后续新业务不要求原接入聊天继续存活，已有业务SID不被重配，当前model正常更新不因旧接入字符串被拒绝。最后按onboarding.md生成只读摘要，仍分别报告原生发现、模型能力、真实执行与未验范围。
