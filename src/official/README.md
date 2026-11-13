# 官方参与方资源与可选工具

本包按项目[架构](../../设计/架构.md)与通用 SDK 分开安装，开发环境执行 `python -m pip install -e ./src/official`。平台核心和 SDK 不反向导入它；两个产品技能的共享环境包含本包，独立SDK用户仍按需安装。安装本包不会自动启用日程或邮箱。产物依据见 VERSION。

## 官方服务资源

`services/ioalab_services/` 与 `tools/ioalab_tools/` 属于同一个发行包。领域JSON与验收源码位于前者的 `assets/`，通过包资源读取；源码仓库、已安装wheel或zipimport均不依赖当前工作目录。

| 入口 | 结果与边界 |
|---|---|
| `ioalab_services.catalog.load_domains()` / `definitions()` | 各领域实际服务定义；默认待批准/提案；验收器只读取为源码，不在宿主执行 |
| `ioalab_services.catalog.seed_manifest()` | 公共目录摘要及来源校验值；不含样件、生成提示和验收源码，核心只消费部署方显式传入的清单 |
| `ioalab_services.prepare(service_id, profile=..., verifier_ref=..., payment_provider=...)` | 返回 `service`（未签公示/模板）和 `execution`（该profile的业务配置）；不登录、不发布、不调用模型 |

先通过常驻SDK的 `provider.verifier.publish` 上传定义中实际验收器，取得完整引用后调用prepare。将execution保存到本机配置的 `provider.services[profile]`，按正常流程重新启动；service经 `provider.preview`、真实用户确认及 `provider.publish` 发布。通用SDK负责身份、签名、容量、长连接和履约，不为官方目录增加一套执行器。生产模型只取得格式、定价、验收引用和业务说明，不取得sample样件。

部署协调器提供批量准备、公示及续期；默认安装与验证范围见[进展](../../设计/工程/进展.md)。源变更后用 `python tools/build_seed.py` 重新生成显式初始化清单。

编程服务通过 `ioalab_services.programming.definition()` 读取同一官方资产，提供小程序基础/可靠、网站与Android源码候选；业务参数支持预约或项目目录。Demo只映射场景值。能力与证明分层见[编程服务](../../设计/流程/场景/编程服务.md)，源码结构和WASM核心行为通过不表示前端、构建或部署通过。

## 官方部署协调器

入口为`ioalab-official`，配置示例见[official.example.json](../../deploy/official.example.json)。`prepare --config <文件>`保存真实验收包引用和完整配置；`authorize --config <文件> --admin-password-env <口令变量名>`执行首次公示授权；`serve --config <文件>`使用已安装九问适配运行并检查续期；`status --config <文件>`只查询。管理员口令只用于首次授权进程；长期进程不继承它。

正常停止通过服务管理器通知协调器，协调器等待原SDK释放执行目录；`stop --config <文件> --wait-seconds 30`用于协调器已停后的独立恢复。返回waiting时保留原实例继续核对，不强杀；CLI退出码0为已释放、2为仍在等待、1为拒绝或失败。它不代表合同已经结清。

已有完整配置、暂停、撤销和人工公示均保留；服务缺宿主或授权时报告原因。默认部署通过包入口编排官方实例，进展及实机验证范围见[进展](../../设计/工程/进展.md)。

## 本地会议工具

通知邮箱的可选准备命令为 `ioalab-agentmail`：`signup --human-email <所有者邮箱> --username <收件箱名称>` 登记，`verify --code-file <本机验证码文件>` 验证所有权，`configure --credentials-file <专用凭据文件>` 保存 `AGENTMAIL_API_KEY` 并返回可用于下述 `notification` 的配置片段。默认登记目录为 `~/.ioalab/agentmail`，可用顶层 `--home` 指定；已有参数不匹配或注册结果未知时停止，不自动再次注册。配置保留凭据文件的其他键，安装或配置不自动启用工具、不发送邀请。

九问装配入口读取 `host.business_tools`，只加载明确选中的已安装工厂。将[日程配置片段](../client/hosts/jiuwen/examples/calendar-tools.json)合入自己的本机运行配置，`profile` 对应已配置的个人助手身份；相对路径以本机运行配置文件所在目录为基准。示例的 smtp-capture 只用于回环收件测试。

| 字段 | 类型与作用 | 生效与敏感性 |
|---|---|---|
| `factory` / `profile` | 登记名 `calendar` / 本实例身份 key | 启动时绑定；默认不加载 |
| `options.calendar` | `backend=local` 与 `schedule_file`，或 `backend=graph`、`client_id`、`token_file` | 显式二选一；日程和令牌留在本地 |
| `options.recipient` | 主人指定的通知收件邮箱 | 固定本机值，不由来信指定 |
| `options.notification` | `transport`、`sender`；SMTP 另含 `host`、`port` | 显式选择 agentmail / smtp / smtp-capture；默认不发信 |
| 通知凭据 | `api_key_env`，或 `username_env` / `password_env` | 填变量名；值从本进程环境或显式 `credentials_file` 读取，不送入模型 |
| `options.rules` | 可选可预约规则，见[会议示例](../../设计/流程/场景/会议预约.md) | 本地安排文件的规则优先；Graph 使用配置规则 |
| `options.requires_steps` | 可选服务方 A2C 环节名数组 | 仅服务身份可用；程序核对本卡本期的真实同意 |

AgentMail 的配置形式为 `{"transport":"agentmail","sender":"自己的通知邮箱","api_key_env":"AGENTMAIL_API_KEY","credentials_file":"notification.env"}`。本包不创建邮箱；先单独配置可用的通知账号。AgentMail 使用 Calendar API，SMTP 用于明确配置的兼容通道；服务受理不代表收件人接受。

先显式初始化自己的安排，再启动运行时：

```text
ioalab-schedule init --file <schedule_file>
ioalab-schedule show --file <schedule_file>
ioalab-schedule replace --file <schedule_file> --args-file <修改文件>
ioalab-schedule reconcile|accept|release --file <schedule_file> ...   # 预约后续状态，由主人执行
ioalab-outlook graph-login|smtp-login --client-id <id> --token-file <文件> ...   # 可选微软日历读取与 Outlook 发信授权
```

修改文件是 `{"document": <完整安排>, "revision": "show返回的摘要"}`；安排内容、规则、备份和冲突检查见[会议示例](../../设计/流程/场景/会议预约.md)。命令可离线运行，不注册账号或创建 SDK 身份。日程文件必须显式指定，只读取该文件对应的新预约记录，不扫描旧邮箱数据库。

工具目录通过 `assistant.tools.list` 或服务方的 `provider.tools.list` 查询。`calendar.book` 按主人安排原子预留，冲突返回建议；成功仅返回 `invite_submitted` 与 `awaiting_acceptance`。`calendar.status` 只读取本交互已有预约。受理接口仍需用户检查并确认后发布，不因安装工具而自动开放收件。

预约及邀请回执位于安排文件旁的 `<文件名>.ioa/appointments.sqlite`。同一文件共用预留与锁；同一消息重复调用恢复原回执，即使模型换了工具调用ID；修改时间须由发起方发送同交互的新消息。未知结果保留预留并停止重发。不要删除该目录来解除占用；缺失的既有库或来源不匹配会阻止自动预约。日程、预约库与通知凭据均不入库。

## 迁移边界

助手收信和服务履约统一经常驻 SDK 与九问适配装配；旧轮询收件器和独立服务执行器已退出当前源码。业务工具不拥有专属执行引擎；已有日程和外部回执仍需保留，实机部署及业务验收范围见[进展](../../设计/工程/进展.md)。

## 默认管理员业务助手

`admin-assistant/ioalab_admin_assistant/`提供安装器专用的`ioalab-admin-assistant prepare|status --config <root私有配方>`。只准备本部署ISC_001的两个包内接口，原草稿、可信确认和发布均经同一SDK；不运行模型或业务工具。缺少通知配置时会议预设停用。重复准备保留人工编辑、原身份及日程，状态明确区分原回执与当前接口。

完整运行仍使用`ioalab-jiuwen serve --config <生成的SDK配置>`，不增加收件服务。输入、权限及恢复规范见[官方服务 · 预置服务与启动器](../../设计/组件/官方服务/接口/预置服务与启动器.md)。该入口不向普通用户或模型提供批准能力；默认安装编排的实机验证仍单独记录。
