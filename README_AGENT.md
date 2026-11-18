# 帮用户接入 IoA Lab（给智能体）

你正在用户刚下载的官方仓库根目录阅读本文：它由 `git clone --depth 1 --filter=blob:none --sparse` 取得，目前只检出了根目录文件。本地文件就是原文，不要再从网页取。这个下载目录（下称 `<clone>`）的 HEAD 就是本次固定来源；安装器在其中运行，不会另行 clone，安装所需的其他源码由安装器自己按最小集合补取。

一个持久客户端（默认 `~/.ioalab/client`）同时服务两份角色 Skill：个人助手 `ioalab-delegate` 与服务方 `ioalab-provider`；九问 Swarm、Codex 与 Claude Code 共用同一客户端、账号与实例。官网默认 https://ioalab.openjiuwen.com ，已有平台配置优先。

## 1. 先复用已有客户端

- 当前宿主已能加载 IoA Lab Skill 时，先用它调用 `runtime.binding` 与 `runtime.status` 取得实际客户端、配置与状态，不读取配置正文。
- `~/.ioalab/client/ioalab-bootstrap.json` 已存在：给其他宿主补接入时在第 2 步命令加 `-ReuseInstalled`；用户要求升级时加 `-Upgrade`（经原客户端安全停止后只替换程序，账号、配置与数据不变）。两者都不加时已有客户端只做核验；回执 `explicit_upgrade_required` 表示来源不同，询问用户是否升级，不要换目录另装或重新注册账号。
- 不完整或未知安装保留现场并报告，不另建账号掩盖。

## 2. 取得安装器并运行（Windows）

在 `<clone>` 中检出客户端目录：

~~~text
git -C "<clone>" sparse-checkout set src/client
~~~

再查 `Get-ExecutionPolicy -List`：MachinePolicy/UserPolicy 强制禁止脚本时报告 policy_blocked，不改注册表、不 Unblock、不用 Bypass。然后执行：

~~~text
powershell -NoProfile -ExecutionPolicy RemoteSigned -File "<clone>/src/client/bootstrap.ps1" -HostType <claude_code|codex|jiuwen> -HostRef <稳定登记引用>
~~~

- `-Channel` 默认 main，对应 GitCode 官方仓库；仅开发测试时在 GitHub develop 的下载中加 `-Channel develop`，结果不能称正式发行通过。
- 首装或重建时，安装器先查找本机正式版 CPython 3.11 或 3.12。如果默认 Python 版本过低或过高，也会检查并存版本，缺少合适版本时自动用 uv 准备私有 3.12。工具、依赖下载及构建可能耗时数分钟，用宿主的后台运行或最长超时等待同一进程结束。联网或写 `~/.ioalab`、宿主 Skill 目录需要审批时按宿主方式申请。
- 下载先走官方源，失败再试随源码登记的国内镜像，两者都核同一固定 SHA256；仍失败时问用户要代理并以 `-Proxy <url>` 重试，不关闭 TLS、不换非官方包。
- 失败回执为 `status=rejected`，`data.handoff` 是固定六项摘要，`data.stage` 是失败阶段：原样报告，保留现场。`unsupported_python` 按回执的 `recoveryCommand` 执行；网络错误不当作 Python 版本问题。
- 成功回执给出 `configurationFile`、`programDirectory`、两份 Skill 的安装结果 `nativeSkills`（`manual_required` 时含当前宿主的手动步骤，`reloadRequired` 表示需重载或新开会话）以及 `sourceClone`。

## 3. 账号、登记与启动

1. 用任一已安装 Skill 的 `scripts/ioalab.py` 按其 `references/account-setup.md` 调用 `account.setup` 打开本机账号页，再轮询 `account.setup.status`。账号密码只在本机页面录入或生成，不经模型或聊天；已有账号不重复注册。
2. 账号完成后重装两份 Skill，取得带角色身份的入口绑定（只替换本客户端自己且未被修改的副本）：

   ~~~text
   <客户端Python> -I -m ioalab_client skills-install --host <宿主类型> --host-ref <登记引用> --config <configurationFile>
   ~~~

   `<客户端Python>` 为 `<programDirectory>/Scripts/python.exe`。
3. 按 Skill 的 `references/host-setup.md` 完成 `runtime.configuration`/`runtime.configure` 宿主登记、`runtime.start` 唯一启动，以及在真实原生入口执行一次 `runtime.entry.bind`。

## 4. 待完成步骤与交付

运行只读摘要：

~~~text
<客户端Python> -I -m ioalab_client onboarding --host <宿主类型> --host-ref <登记引用> --config <configurationFile>
~~~

`data.pending` 是结构化步骤，每条含 `actor`、`instruction` 与 `verify`（只读、不调用模型的命令）：

- `actor=agent`：由你执行，再运行该条 `verify`，退出码 0 才算完成。
- `actor=user`：把 `instruction` 原样转告用户，不改写、不省略；等用户告知完成后运行该条 `verify`，退出码 0 才报告完成，否则如实说明仍未完成。
- `new_session` 的 `verify` 不会自报通过：按用户在新会话得到的两次结果记录，并保持“原生发现待验”。

全部步骤处理后，用同一命令加 `--format markdown` 输出固定六项摘要。安装、账号、连接、Skill 发现与业务就绪分别报告，不把局部完成称为全部接入完成。最后告诉用户：客户端已不再依赖 `sourceClone.path` 这个下载目录，可以删除；以后升级时重新说接入那句话，并让智能体运行引导时加 `-Upgrade`。
