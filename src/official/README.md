# 官方参与方资源与可选工具

本包按项目[架构](../../设计/架构.md)与通用 SDK 分开安装，开发环境执行 `python -m pip install -e ./src/official`。平台核心和 SDK 不反向导入它；两个产品技能的共享环境包含本包，独立SDK用户仍按需安装。安装本包不会自动启用日程或邮箱。产物依据见 VERSION。

## 官方服务资源

`services/ioalab_services/` 与 `tools/ioalab_tools/` 属于同一个发行包。领域JSON与验收源码位于前者的 `assets/`，通过包资源读取；源码仓库、已安装wheel或zipimport均不依赖当前工作目录。

| 入口 | 结果与边界 |
|---|---|
| `ioalab_services.catalog.load_domains()` / `definitions()` | 8领域50服务；默认待批准/提案；验收器只读取为源码，不在宿主执行 |
| `ioalab_services.catalog.seed_manifest()` | 公共目录摘要及来源校验值；不含样件、生成提示和验收源码，核心只消费部署方显式传入的清单 |
| `ioalab_services.prepare(service_id, profile=..., verifier_ref=..., payment_provider=...)` | 返回 `service`（未签公示/模板）和 `execution`（该profile的业务配置）；不登录、不发布、不调用模型 |

先通过常驻SDK的 `provider.verifier.publish` 上传定义中实际验收器，取得完整引用后调用prepare。将execution保存到本机配置的 `provider.services[profile]`，按正常流程重新启动；service经 `provider.preview`、真实用户确认及 `provider.publish` 发布。通用SDK负责身份、签名、容量、长连接和履约，不为官方目录增加一套执行器。生产模型只取得格式、定价、验收引用和业务说明，不取得sample样件。

资源独立安装与一项服务的接单验收不代表全部默认服务已自动上线；批量启用、续期及旧运行器替换见[开发计划](../../docs/开发计划.md)。源变更后用 `python tools/build_seed.py` 重新生成显式初始化清单。

## 本地会议工具

九问装配入口读取 `host.business_tools`，只加载明确选中的已安装工厂。将[日程配置片段](../hosts/jiuwen/examples/calendar-tools.json)合入自己的本机运行配置，`profile` 对应已配置的个人助手身份；相对路径以本机运行配置文件所在目录为基准。示例的 smtp-capture 只用于回环收件测试。

| 字段 | 类型与作用 | 生效与敏感性 |
|---|---|---|
| `factory` / `profile` | 登记名 `calendar` / 本实例身份 key | 启动时绑定；默认不加载 |
| `options.calendar` | `backend=local` 与 `schedule_file`，或 `backend=graph`、`client_id`、`token_file` | 显式二选一；日程和令牌留在本地 |
| `options.recipient` | 主人指定的通知收件邮箱 | 固定本机值，不由来信指定 |
| `options.notification` | `transport`、`sender`；SMTP 另含 `host`、`port` | 显式选择 agentmail / smtp / smtp-capture；默认不发信 |
| 通知凭据 | `api_key_env`，或 `username_env` / `password_env` | 填变量名；值从本进程环境或显式 `credentials_file` 读取，不送入模型 |
| `options.rules` | 可选可预约规则，见[会议示例](../../设计/示例/会议预约.md) | 本地安排文件的规则优先；Graph 使用配置规则 |
| `options.requires_steps` | 可选服务方 A2C 环节名数组 | 仅服务身份可用；程序核对本卡本期的真实同意 |

AgentMail 的配置形式为 `{"transport":"agentmail","sender":"自己的通知邮箱","api_key_env":"AGENTMAIL_API_KEY","credentials_file":"notification.env"}`。本包不创建邮箱；先单独配置可用的通知账号。AgentMail 使用 Calendar API，SMTP 用于明确配置的兼容通道；服务受理不代表收件人接受。

先显式初始化自己的安排，再启动运行时：

```text
ioalab-schedule init --file <schedule_file>
ioalab-schedule show --file <schedule_file>
ioalab-schedule replace --file <schedule_file> --args-file <修改文件>
```

修改文件是 `{"document": <完整安排>, "revision": "show返回的摘要"}`；安排内容、规则、备份和冲突检查见[会议示例](../../设计/示例/会议预约.md)。命令可离线运行，不注册账号或创建 SDK 身份。原 `IOALAB_HOME` 操作入口保留为旧数据检查方式，不自动迁移旧预留。

工具目录通过 `assistant.tools.list` 或服务方的 `provider.tools.list` 查询。`calendar.book` 按主人安排原子预留，冲突返回建议；成功仅返回 `invite_submitted` 与 `awaiting_acceptance`。`calendar.status` 只读取本交互已有预约。受理接口仍需用户检查并确认后发布，不因安装工具而自动开放收件。

预约及邀请回执位于安排文件旁的 `<文件名>.ioa/appointments.sqlite`。同一文件共用预留与锁；同一消息重复调用恢复原回执，即使模型换了工具调用ID；修改时间须由发起方发送同交互的新消息。未知结果保留预留并停止重发。不要删除该目录来解除占用；缺失的既有库或来源不匹配会阻止自动预约。日程、预约库与通知凭据均不入库。

## 迁移边界

`ioalab_tools.handlers.AssistantHandlers` 仍供原后台和原业务回归使用。旧轮询收件器、默认官方服务及管理员预设尚未全部切换；具体差距见[开发计划](../../docs/开发计划.md)。新业务工具经统一 SDK 装配，不建立官方专用执行引擎，也不能据局部 SMTP 测试声称原生网页或新影片完成。
