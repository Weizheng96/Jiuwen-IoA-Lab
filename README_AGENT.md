# 帮用户接入 IoA Lab

复用当前宿主和已有客户端；支持九问Swarm（jiuwen）、Codex（codex）和Claude Code（claude_code）。各宿主同时安装个人助手与服务方两个Skill，共用一个客户端、账号配置和常驻实例。调用入口与后台执行宿主分开，新增宿主不更换已有host.type。默认官网为https://ioalab.openjiuwen.com，已有平台地址优先。

## 先复用，再安装

已有产品Skill时先原生加载，调用runtime.binding和runtime.status取得真实位置及状态，不读账号配置正文、不重复注册。新增宿主用原客户端解释器执行下文skills-install，保留原配置。没有Skill时，只核默认持久根目录`~/.ioalab/client`的安装归属与客户端；已有未知或不完整目录保留并诊断，不另建账号掩盖故障。

首次安装核Git和CPython3.11，从GitCode公开标签按数字N选择最新已发布design-v0.0.N。智能体自行在本机选择一个尚不存在的源码下载目录，例如`~/.ioalab/sources/design-v0.0.N-本轮唯一标识`，代入以下命令；此目录只保存下载的源码，与安装后运行的客户端不同，不要求普通用户填写。

~~~text
git clone --depth 1 --filter=blob:none --sparse --single-branch --branch <实际发布tag> https://gitcode.com/WeiZheng96/jiuwen-IoA-lab.git "<智能体已选的源码目录>"
git -C "<同一源码目录>" sparse-checkout set src/client
python "<同一源码目录>/src/client/bootstrap.py" --tag <同一tag> --host <当前调用宿主>
~~~

Windows使用已核CPython3.11，Linux用python3.11。默认客户端根目录为`~/.ioalab/client`：程序放`env/`，账号/运行配置放`settings.local.json`，身份与业务数据放`settings.local.data/`，导出包放`role-skills/`；按实际回执报告绝对路径。需要其他持久根目录才用--install-dir；隔离测试须显式目录，不占用用户客户端。安装器核官方来源、tag/commit及自身内容，从固定Git对象取得受审vendor并构建，不再让pip克隆客户端。依赖和文件校验通过仍不等于已连接。

## 同时安装两份原生Skill

bootstrap自动导出绑定同一解释器及配置的两个Skill，并执行所选宿主的安装准备。已有客户端或新增调用宿主执行：

~~~text
<原客户端Python> -I -m ioalab_client skills-install --host <当前调用宿主> --config <原配置绝对路径>
~~~

原配置路径由已有绑定/安装回执确定，不能读取正文或要求用户再次提供密码。Codex安装到用户级`~/.agents/skills`，Claude Code安装到`~/.claude/skills`；两份Skill都必须存在。九问仅在该版本公开安装能力已核实后自动导入；否则用send_file_to_user交付两个真实ZIP（abs_file_path_list为绝对路径列表的JSON字符串，target_channels为["web"]），指导用户在“我的技能→创建”分别导入，两份都保留。不能用文件已导出冒充原生加载成功。

权限不足、同名冲突或不支持自动导入时，展示回执里的两份包、实际目标和手动步骤；保留已有文件。程序绑定客户端及配置，Skill不存密码。Skill调用时不传--config，不手改脚本。实际原生加载两份Skill并读取SKILL.md与references/setup.md后继续。

## 账号、启动和交付

1. 沿Skill的account-setup.md调用account.setup，用户只在SDK本机账号页录入或生成账号。新配置同时准备assistant和service身份，服务方默认不接单、未公示；已有身份和服务政策保持，缺角色按受控配置入口补齐。两份Skill共用账号，无需再次输入密码。
2. 按host-setup.md查询SDK脱敏配置。已有执行宿主原样复用；首次选择实际host.type和公开命令/模型。启动并分别核authenticated、host.available、platformConnected与两身份bound。服务方入口可用与专业能力、模板公示及接单就绪分别报告。
3. 每次接入结束（含失败、取消和待人工操作）都要交付固定摘要。客户端可用时按references/onboarding.md调用runtime.onboarding，生成固定格式交付摘要：接入状态；客户端/程序/数据/配置位置；账号及密码保存位置、本机查看入口；两个Skill的安装位置与使用入口；调用宿主、后台执行宿主和通知限制；官网、同账号登录与“我的智能体”入口。已有账号和一键注册都覆盖，不输出密码。客户端尚不可用时按同一六项顺序列已知实际位置、失败原因及未配置项，不能省略摘要。
4. 完成标准覆盖真正的新会话：分别原生加载两个Skill并调用runtime.binding、runtime.status，核同一客户端、配置、账号与runtimeId，不填路径或密码。保留原生发现和调用证据。没有新会话操作能力时给用户明确步骤，摘要保持待验；当前会话读取或文件安装不能代替这一项。

短时限用宿主原生后台任务并查询原任务，不重复安装。失败保留目录、原引用及脱敏错误，不关闭TLS或现场改发布源码。升级先显式安全停止并保留账号、身份和在途业务。反向来信由配置的后台执行宿主处理，用户通知目标须明确配置；不自动回到最初安装的聊天。Codex/Claude首版原生生产工具、原生问题回调与当前桌面聊天通知仍不支持，按实际能力报告；业务批准只走SDK可信确认页。
