# 部署方个人助手

这是独立客户端；平台核心不启动模型或个人助手。管理员账号默认ISC_001，浏览器具管理权限，SDK令牌仅有普通账号权限。

1. 复制`deploy/personal-assistant.env.example`到忽略的`data/personal-assistant.env`，配置平台账号、九问WebChannel和部署方通知邮箱。
2. 在Lab的SDK环境执行`python personal-assistant/assistant.py check --env data/personal-assistant.env`。
3. 在Microsoft Entra注册支持个人Microsoft账号的公共客户端应用，启用Allow public client flows，委托权限`Calendars.Read`、`User.Read`。将应用ID写入`IOALAB_GRAPH_CLIENT_ID`，无需客户端密钥。
4. 执行`python personal-assistant/assistant.py calendar-login --env data/personal-assistant.env`，按终端设备码以配置的收件账号登录。令牌仅在`IOALAB_HOME`中。
5. 启动已锁定的九问，再执行`python personal-assistant/assistant.py run --env data/personal-assistant.env`。`once`只收取处理一轮，适合诊断。

独立九问安装：`python personal-assistant/install_swarm.py --venv .swarm-venv --data data/admin-swarm`。需Python 3.11、Git、Node/npm；源码、版本和依赖在同目录锁文件中。安装器只在新建归档副本中构建，不改传入的参考仓。模型配置写入九问自己的config/.env，不上传核心。当前锁在Windows实测；Linux实机安装另行验收。

先生成技能安装包，再执行`deploy/configure_assistant.py`：会将完整技能安装到九问的`agent/workspace/skills`，首次写入ISC_001账号与本地SDK目录；升级保留已有技能账号配置。技能包排除`settings.local.json`，账号仅写在部署机器中。

将九问EXTENSION_DIRS指向本仓personal-assistant/extensions，JIUWENSWARM_DATA_DIR指向该九问数据目录，IOALAB_SWARM_USER_ID为ioa-admin。九问与收件器设置相同IOALAB_SWARM_BRIDGE_SECRET。浏览器以`?user_id=ioa-admin`打开九问；扩展经宿主通道写通知，不执行外部正文。生产通过受保护网络或TLS反代访问。Linux install.sh默认附带安装并生成独立单元，部署配置由deploy/configure_assistant.py生成。

`message.forward`写入真实九问Web会话；`meeting.request`执行本地预约规则，读取日历并通过通知邮箱发送标准邀请邮件。邮件已受理与用户已接受分别记录；不直接创建管理员日历事件。当前不自动处理接受回执，记录保持等待接受；与已有待接受预约冲突时拒绝。

本机配置不等于外部验收：没有Microsoft授权或SMTP配置时，不会宣称日历空闲或邀请已送达。完整验收和录屏见`demo/README.md`。
