# IoA Lab SDK

安装：先安装仓库 `vendor/wheels/` 中锁定的 A2C wheel，再 `pip install ./src/access/sdk`。SDK 不依赖平台 Python 包，不在本地安装 A2X 或九问运行时。

## 常驻接入入口

共用运行时提供账号、独立角色身份、WS 连接、可靠收发箱、持久调度及受认证的本机调用。后台进程统一持有本地数据，CLI 只读取端点描述并调用。默认装配已提供助手收信设置及消息操作；没有宿主时可配置、发送和接收，不调用模型或执行业务工具。可选[九问适配](../../hosts/jiuwen/README.md)装配入站执行。委托与服务方角色尚未切换，差距见[开发计划](../../../docs/开发计划.md)。

复制[配置模板](../../../deploy/access-runtime.json.example)，填写平台和账号，并在本机设置模板引用的密码环境变量；服务方可将 identities 改为 service profile，服务绑定不创建个人助手。数据目录相对配置文件定位，作为 SDK 专用目录；配置及调用参数文件放在其外。每个 profile 的稳定身份及私钥保存在该目录，不能删除重建。旧直接客户端目录须先迁移，不与常驻进程争用。

```text
python -m ioalab_sdk.runtime start --config <本机配置文件>
python -m ioalab_sdk.runtime call runtime.status --home <配置中的数据目录>
python -m ioalab_sdk.runtime call identity.status --home <配置中的数据目录>
python -m ioalab_sdk.runtime call runtime.stop --home <配置中的数据目录> --args-file <已创建的空JSON参数文件>
```

start 复用已运行实例；启动仍在进行时报告 waiting，不重新创建竞争实例。状态分别报告 IPC、账号、身份、连接和执行能力，不能将进程运行当作业务完成。stop 在参数文件保存目标实例及原操作标识，等待实际轮次收敛并保留数据。运行数据、配置、端点凭据与日志均不入库。SDK 还提供 `ioalab-access` 命令，行为与模块入口一致。

## 助手收信设置与通信

统一调用格式为 `python -m ioalab_sdk.runtime call <operation> --home <数据目录> --args-file <已创建的JSON文件>`；参数文件自动保存原 operationId，重复调用复用原操作。接口和流程规范见[个人助手端 SDK](../../../设计/接入/个人助手端SDK.md)。

| 操作 | 参数 |
|---|---|
| `assistant.rules.draft` | declaration（name、description、inputSchema、outputSchema、allowedSenders、tools）、instructions |
| `assistant.rules.validate/preview` | draftRef；只检查，不执行工具或发布 |
| `assistant.rules.publish` | draftRef、contentDigest、expectedRevision；首次返回待确认摘要 |
| `runtime.open_ui` | 空参数；在本机浏览器打开用户确认页，不向模型返回访问凭据 |
| `assistant.rules.list/disable` | list 为空；disable 使用 name、expectedRevision，仍需用户确认 |
| `assistant.messaging.interfaces` | account：准确账号；返回获准调用的 interfaceRef 及 Schema |
| `assistant.messaging.send` | interfaceRef、input；后续请求改用原 interactionRef、input |
| `assistant.messaging.status` | messageRef 或 interactionRef；waitSeconds 可选且不超过30秒 |
| `assistant.messaging.close` | messageRef 或 interactionRef，reason 可选 |

本机用户页确认后，重复原发布调用即可完成发布。模型不能通过 RPC 批准；修改摘要或内容必须重新确认。发布冻结本地 Markdown、工具权限及修订，平台只接收接口声明。入站只运行对应已确认规则与工具；同一交互固定实际 Session。等待原生用户问题时保留原进程、释放模型名额；回答后重新申请名额。未知外部效果阻止该交互继续执行，须核实原操作。当前可选宿主入口不自动装配日程、邮件等业务工具。

## 迁移中的原业务入口

个人助手使用 `ioalab-client <command> --args '<JSON>'`；命令清单见 `python -m ioalab_sdk.cli --help`。配置 `IOALAB_BASE_URL`、`IOALAB_USERNAME`、`IOALAB_PASSWORD`；身份与合同存入本地 `IOALAB_HOME`。需确认的命令显式传 `--confirmed`，技能必须先取得用户同意。

`LabClient` 封装发现、身份绑定、观察上报，并直接使用 A2C Client 执行合同。验收在调用进程的 WASM 沙箱中完成。付款发送结果不明时报告 requires-reconciliation，不自动再次付款。未发送观察在本地 outbox 保存，下次调用重发。

服务方使用 `ProviderAgent` 管理 Provider SDK 和本地持久状态；独立Swarm经四个结构化host工具进入本单`ProviderJob`能力，模型不接触宿主令牌；人工诊断保留`python -m ioalab_sdk.provider_cli`入口。详见[接入设计](../../../设计/接入/SDK共用机制.md)与[官方服务](../../../official-services/README.md)。

主skill按需读取委托交易/私人交互通用流程，再逐项调用SDK原语。`delegate`只保留给旧受管理任务恢复和显式外部计划；受管理合同仍从原taskId继续，不绕过既有确认保护。任务、授权与卡分别持久化；重复取件核已签清单并恢复缓存，不重复签收。完整边界见[接入设计](../../../设计/接入/SDK共用机制.md)，美甲计划单独放在[示例](../../../examples/nail-booking/README.md)，不随通用skill分发。

服务发现回执的hits[].id是注册中心标识，templates/reputation的provider参数须使用hits[].card.url；SDK会在向服务端点发请求前给出明确的URL参数诊断。可选[官方工具包](../../official/README.md)中的 AssistantHandlers 要求调用者显式配置会议收件人，缺少收件人不会发送邀请；该业务处理器已移出 SDK。它仍使用旧 MailboxClient 收件，不代表新宿主已自动配置会议工具。

可选`IOALAB_MIN_QUOTES`是账号的最低比选数量（默认1，范围1—8），授权scope固化本次策略。`request/draft`返回`quoteProgress`，`quote-progress {scope_id}`可恢复进度；未达下限或拟卡费结果不明时不允许选定合同。同一服务端点多张卡只计一次，旧scope保持原策略；这不是A2C协议字段，也不会代替模型做业务推荐。技能配置可加入该项，示例账号的三份偏好独立放在examples中。
