# 部署方个人助手

## 本地可编辑时间安排

当前演示选择本地安排作为预约依据。配置`IOALAB_CALENDAR_BACKEND=local`，可选`IOALAB_SCHEDULE_FILE`（默认`IOALAB_HOME/schedule.json`），执行`python personal-assistant/assistant.py schedule-init --env data/personal-assistant.env`显式创建。`schedule-show`查看规则、已有安排和本地邀请状态。该文件可直接在IDE编辑；手工修改时暂停收件器，保存后恢复。

九问在线修改使用IoA技能的`schedule show`与`schedule replace`：先读取revision，再提交完整修改后document及该revision；冲突需重读，原内容自动备份到schedule.json.bak。timeZone为IANA时区，rules维护工作日/时段/时长/提前量，events包含id、title、带时区start/end和busy/cancelled状态。文件缺失或非法会阻止预约，不自动当成空闲。

日程与备份属于本机私有运行数据，不推送仓库。待接受与结果不明的邀请仍由客户端SQLite防止重叠；修改手工日程不会清除这些预约。本地安排不自动同步私人Outlook；通知邮箱发邀请，收件人接受后才入历。下文的Microsoft读取授权仅用于显式选择graph后端。

这是独立客户端；平台核心不启动模型或个人助手。管理员账号默认ISC_001，浏览器具管理权限，SDK令牌仅有普通账号权限。

1. 复制`deploy/personal-assistant.env.example`到忽略的`data/personal-assistant.env`，配置平台账号、九问WebChannel和部署方通知邮箱。
2. 在Lab的SDK环境执行`python personal-assistant/assistant.py check --env data/personal-assistant.env`。
3. 选择下表的通知邮箱。AgentMail通过官方Calendar API发送会议请求，不需要Microsoft发信应用；需要先完成所有权验证。
4. 如需自动检查Outlook日历冲突，再配置独立的日历读取授权，见下文；仅收到邀请邮件不代表此项已验收。
5. 启动已锁定的九问，再执行`python personal-assistant/assistant.py run --env data/personal-assistant.env`。`once`只收取处理一轮，适合诊断。

独立九问安装：`python personal-assistant/install_swarm.py --venv .swarm-venv --data data/admin-swarm`。需Python 3.11和可达的PyPI包镜像；Git源码依赖、本地wheel、预构建原网页及许可证已随仓保存，见[vendor](../vendor/README.md)。服务器不需要Git或Node/npm安装九问，安装器不拉取或修改上游仓。可用--verify-only只检查本地输入。模型配置写入九问自己的config/.env，不上传核心。当前锁在Windows实测，已补齐并审查Linux条件依赖；Linux实机安装另行验收。

先生成技能安装包，再执行`deploy/configure_assistant.py`：会将完整技能安装到九问的`agent/workspace/skills`，首次写入ISC_001账号与本地SDK目录；升级保留已有技能账号配置。技能包排除`settings.local.json`，账号仅写在部署机器中。

将九问EXTENSION_DIRS指向本仓personal-assistant/extensions，JIUWENSWARM_DATA_DIR指向该九问数据目录，IOALAB_SWARM_USER_ID为ioa-admin。九问与收件器设置相同IOALAB_SWARM_BRIDGE_SECRET。浏览器以`?user_id=ioa-admin`打开九问；扩展经宿主通道写通知，不执行外部正文。生产通过受保护网络或TLS反代访问。Linux install.sh默认附带安装并生成独立单元，部署配置由deploy/configure_assistant.py生成。

`message.forward`写入真实九问Web会话；`meeting.request`执行本地预约规则，读取日历并通过通知邮箱发送标准邀请邮件。邮件已受理与用户已接受分别记录；不直接创建管理员日历事件。当前不自动处理接受回执，记录保持等待接受；与已有待接受预约冲突时拒绝。

本机配置不等于外部验收：没有Microsoft授权或SMTP配置时，不会宣称日历空闲或邀请已送达。完整验收和录屏见`demo/README.md`。

| AgentMail操作 | 命令/结果 |
|---|---|
| 首次创建 | `python personal-assistant/agentmail.py signup --human-email change-me-owner@example.com --username change-me-inbox`；替换占位值，验证码发到所有者邮箱 |
| 验证所有权 | `python personal-assistant/agentmail.py verify`隐藏输入验证码，或`verify --code-file data/agentmail-otp.txt`读取本机文件；不把验证码放在命令行参数 |
| 写本机配置 | `python personal-assistant/agentmail.py configure --env data/personal-assistant.env`；仅验证后可用，设置notification transport为agentmail，保留平台账号、SDK身份和日历配置 |
| 状态 | `python personal-assistant/agentmail.py status`；不输出API key；重复signup复用本地登记，网络结果不明不自动重试 |
| 存储与传输 | 登记在忽略的data/agentmail/account.json；会议走官方Calendar API，复用SMTP_FROM/SMTP_PASSWORD中的实际邮箱与API key；smtp.agentmail.to:465保留作SMTP诊断 |

AgentMail提供收信箱，但本轮只接入通知发信，不额外授权九问任意读取邮件或执行邮件正文。接口、配额与新账号限制遵循实际服务结果；不能用未验证账号向外部收件人测试。会议使用官方Calendar API（US区Beta）及send_invites，避免通用SMTP转发丢失MIME参数导致Outlook只有附件。组织者事件保存在通知邮箱，收件人仍通过邮件接受；API成功不代表邮件已送达或对方已接受。固定请求id映射为合法client_id，实际UID/Event ID保存在回执；未知结果不自动重发。参考[注册与验证](https://docs.agentmail.to/quickstart)、[会议邀请](https://docs.agentmail.to/calendar-invitations)。

日历读取：在可管理的Microsoft Entra目录注册支持个人账号的公共客户端应用，启用Allow public client flows及Graph委托权限`Calendars.Read`、`User.Read`，应用ID写入`IOALAB_GRAPH_CLIENT_ID`，无需客户端密钥。执行`calendar-login --env data/personal-assistant.env`并以被预约邮箱登录；calendar-token.json仅在客户端。若通知方也选择Outlook，另配置oauth2模式与SMTP.Send权限，通过`smtp-login`登录通知邮箱；资源和令牌分开，不复用日历访问令牌发信。

| 诊断项 | 处理 |
|---|---|
| `notification-check --env data/personal-assistant.env` | 按所选传输验证API或SMTP身份，不发邮件、不创建会议；通过不代表邀请已送达 |
| `smtp-check --env data/personal-assistant.env` | 仅诊断TLS/SMTP，不论会议选择哪种传输；不能用SMTP通过代替原生会议请求验收 |
| `live.com`不属于`Microsoft Services`/AADSTS50020 | 个人Outlook邮箱不等于Entra目录；改用自己有权限的目录注册应用，不申请加入微软目录 |
| 没有可管理目录 | 按微软当前注册要求建立可用Azure/Entra账号；涉及身份、银行卡或订阅的步骤由部署者本人完成，不自动购买或升级 |
| 错误账号/授权失效 | 登录指定邮箱；校验失败不会覆盖已有有效授权；收件账号和通知账号无需相同 |
| 升级后修改账号或应用ID | 对应资源重新登录；不复用另一账号、应用或资源的缓存令牌 |

参考：[Outlook SMTP OAuth](https://learn.microsoft.com/en-us/exchange/client-developer/legacy-protocols/how-to-authenticate-an-imap-pop-smtp-application-by-using-oauth)、[Microsoft Services登录错误](https://learn.microsoft.com/en-us/troubleshoot/entra/entra-id/app-integration/error-code-aadsts50020-user-account-identity-provider-does-not-exist)、[应用注册前提](https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-register-app)。

纯skill接入：用户导入技能、填写平台地址和账号，在九问原生菜单选择IoA个人助手即可。主skill按需求读取委托交易或私人交互的通用流程，逐项调用SDK；不写宿主AGENT.md，不改技能可见性或工具开关，不改上游源码。未预选的自动识别仍受模型影响。

旧版配置覆写的撤回入口与技能使用说明见[connector](../connector/README.md)。管理员通知扩展与常驻收件器是部署方显式选择的独立能力；普通用户安装skill不自动获得后台轮询或邮箱授权。本人日程/通知设置与消息协议分离。
