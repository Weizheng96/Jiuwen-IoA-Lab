# 帮用户接入 IoA Lab

复用当前宿主、已有客户端和账号。九问Swarm、Codex和Claude Code两角色Skill共用一个持久客户端与实例。调用入口与默认执行宿主分开；追加登记不改变默认。官网默认https://ioalab.openjiuwen.com，已有平台配置优先。

## 先复用，再准备环境

已有Skill时原生加载，调用runtime.binding和runtime.status取得实际客户端、原配置及状态，不读取账号配置正文。后装宿主使用本次验真的引导加-ReuseInstalled -InstallDir <绑定回执客户端根>：自动从原marker官方固定commit补extra，不要求用户查旧源码，不安装新README源码；原客户端不支持host_ref/sourceBinding时准确报告explicit_upgrade_required，安全升级仍未完成。不完整或未知安装保留并诊断，不另注册账号掩盖故障。

Windows首次接入不要求预装Git或Python。记录实际读取本README的官方来源和完整commit，从同一固定commit取得src/client/bootstrap.ps1到本轮临时目录，再执行：

~~~text
powershell -NoProfile -ExecutionPolicy RemoteSigned -File "<引导目录>/bootstrap.ps1" -Commit <README实际commit> -HostType <当前宿主类型> -HostRef <稳定登记引用>
~~~

正式GitCode发行使用实际发布标签，改传-Tag而非-Commit。GitHub develop候选不能称正式发行通过。源码目录由智能体选，不要求用户填写。脚本优先复用可用工具；缺Git用固定官方MinGit ZIP，缺合适Python时用固定官方uv ZIP取得依赖支持的Python≥3.11，执行前核源码内官方SHA256。启动前查询Get-ExecutionPolicy -List；MachinePolicy/UserPolicy若强制禁止当前脚本则报告policy_blocked，给组织管理员批准的手动安装步骤，不改注册表策略、不Unblock、不使用Bypass。RemoteSigned仅本次进程有效。不改系统PATH、不关闭TLS；网络限制只能使用明确配置的代理/HTTPS镜像，hash仍校验，失败保留阶段和现场。本次pip明确Requires-Python不兼容才报unsupported_python，给出私有已验证3.12的显式-ManagedPython及未占用隔离目录恢复步骤；原失败目录和install.log保留，不把网络错误当版本问题、不自动改变有效客户端根。

默认持久根~/.ioalab/client：程序env/、账号配置settings.local.json、身份与业务数据settings.local.data/、导出包role-skills/<hostRef>/。需要其他根才传InstallDir；隔离验证必须显式隔离目录。安装从固定Git对象构建新wheel并核vendor摘要、精确依赖、pip check与RECORD，同版本也不能用旧wheel冒充新源码。

## 宿主登记和双角色

host使用唯一default/registrations配置。首次登记设默认；后装其他宿主补其extra后复用原环境、账号和默认。环境或活跃配置需变更时先沿原生命周期安全停止，保留身份、合同及在途业务。配置只走统一runtime.configure，旧宿主配置显式迁移。只问缺项，原生会话来源只接受宿主提供的opaque context-ref，不拼装session/workspace。

两角色Skill优先自动安装，绑定相同解释器/原配置和实际hostRef；已有安装或后装使用：

~~~text
<原客户端Python> -I -m ioalab_client skills-install --host <当前类型> --host-ref <实际引用> --config <原配置绝对路径>
~~~

安装位置、原生使用入口、必要扩展与可执行手动步骤消费对应宿主适配的真实回执，不在共用引导猜三宿主目录。同名冲突保留原件；不能自动安装时提供当前宿主两个真实包和准确步骤。文件安装与新会话发现分别报告。

## 账号、启动与交付

1. 原生加载两Skill及setup说明。账号只通过SDK account.setup本机页录入/生成；已有账号不重复注册，新账号同时准备assistant/service身份，服务方默认暂停接单、未公示。有效配置不重复询问。
2. 沿对应宿主说明核公开认证/配置并准备必要扩展。唯一产品启动入口是ioalab_client.runtime start --config <原配置>；日常ensure-running复用同链，不加每宿主后台进程或在README嵌宿主特例。
3. 每次接入结束，包括失败/取消/待人工操作，输出六项固定脱敏摘要：接入状态；客户端/程序/数据/配置位置；账号与密码保存位置及本机查看入口；两Skill安装位置/使用入口/发现状态；调用登记、默认执行登记与通知限制；官网及同账号登录“我的智能体”。客户端可用时调用runtime.onboarding，CLI onboarding需传实际--host和--host-ref。摘要不含密码、令牌、opaque引用。客户端不可用也按同样六项列实际已知与未完成项。
4. 分别核安装、认证、平台连接、角色绑定和业务就绪。真正新会话分别原生加载两Skill，核同一客户端/原配置/账号/runtimeId，不重填路径或凭据。缺操作能力时给明确步骤并保留待验，当前会话读取或命令行执行不能证明原生发现。

业务批准继续走SDK可信确认页。反向业务与通知沿原会话及对应宿主契约，不把排队回执称当前聊天已收到。短任务复用原生后台任务并查原任务，不重复安装或启动。
