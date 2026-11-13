> 2026-11-13 整合说明：本页先前的独立接入包构建/安装记录仅是历史证据。现行分发遵循 README_AGENT.md：统一 ioalab-client 从已发布 GitCode tag 安装，两个轻量角色 Skill 和 MCP 共用该环境；本轮隔离目录验证不更新原运行实例。

# 本地 MCP 接入 · 开发记录

## 状态与范围

用户已授权完成本地 MCP 与三类业务的开发，并要求先不提交。本次在 develop 工作区继续补齐接入、私人交互、委托交易和服务方管理，接口依据为同一未提交批次的设计 v0.0.151。随后按用户要求更新本机 8750 测试平台并准备两款接入包，保留原配置和数据。未提交、推送、正式发布或更新远程服务器，也未修改用户正在运行的宿主或客户端配置。

当前实现覆盖下表；代码/隔离测试完成与真实宿主验收分开记录。新服务器上线、九问原生导入及三宿主实际加载仍须验收，不能由本机测试推出完成。整体方案见[专题](../专题/接入易用性与Agent调用效率.md)。

| 业务 | 本次实现 |
|---|---|
| 首次接入 | 网站的一句话引导、动态 readme、两款 ZIP 的下载校验清单；公共安装/连接入口；无配置时打开受控登录/注册页，随机账号密码由程序生成并先保存；丢回执恢复原注册 |
| 本地凭据与配置 | 普通工具只读公开摘要；用户在可信浏览器输入/按需查看凭据；原配置不覆盖；服务说明、技能、容量经原 configure 校验；按所见 bootId 安全停止，持锁时拒绝改配置 |
| 私人交互 | 接口定义、更新、停用、具体版本确认；发现对方实际接口，按 Schema 填写输入、多轮发信、查询回复及结束；没有会议专用协议 |
| 委托交易 | 需求/材料/预算、任务授权、服务检索/模板、报价比较与选择、具体合同确认、开工、合同环节答复、取件、验收付款、原解除报价/终止/仲裁 |
| 争议恢复 | 主交易等待期间可按原 quoteRef/contractRef 打开案件表单；已付拟卡费后逾期未报价也能申请原请求仲裁；不伪造合同或绕过独立确认 |
| 服务方 | 通用资料、分类、A2C 条款、原发布预览/确认、暂停/恢复接单、真实任务查询/续接与仲裁；专业生产及交付继续由原 AgentHost 与执行模块承担 |
| MCP / 无 Apps | 本地标准 stdio MCP 工具与 Apps 资源；能力协商后内嵌，未声明 Apps 时打开本机浏览器；Skill/CLI 调用同一公共能力 |

## 架构与代码位置

宿主 MCP Client → 本地 `ioalab_sdk.mcp` → 公共客户端/受认证 IPC → 原常驻 SDK → 平台 HTTPS/WSS。账号建立之前，短期接入控制服务只打开本机受控账号页，不启动另一个业务执行者。平台不新增云端 MCP Server。

| 位置 | 职责 |
|---|---|
| `src/client/sdk/ioalab_sdk/mcp/` | 官方 Python MCP SDK 的 stdio 生命周期、工具、资源、Apps 协商；没有业务数据库或批准工具 |
| `runtime/client.py` | 原配置、安装身份及 bootId 核对，固定所属实例调用 |
| `runtime/onboarding.py`、`account_setup.py` | 注册/登录的私有持久化、恢复、临时页面控制、原配置/停止入口；独立于 MCP |
| `runtime/forms.py`、`workflow.py` | 草稿、乐观修订、冻结预览、阶段 context、原回执与有限自动推进；业务含义归角色 |
| `assistant/forms.py`、`interaction.py`、`trade_workflow.py` | 收信接口、私人交互、委托与案件流程，只组合原角色操作 |
| `provider/forms.py` | 服务发布、接单、任务与案件流程；实际生产不在页面内运行 |
| `runtime/user_ui.py` | 一次性入口、Cookie、Origin、CSRF，账号和业务确认分离 |
| `runtime/web/{identity,registry,messaging,delegation,payments,provider}/` | 按目标中心/服务方分目录；字段和业务证据展示 |
| `runtime/web/shared/` | 共用页面、导航及浏览器/MCP App 两种通信适配 |
| `src/client/ioalab_client/commands.py`、`references/forms.md` | 安装、接入、MCP 配置生成与公共表单说明，两角色共用 |
| `src/platform/ioalab/web/agent_setup.py`、`static/connect.*` | 一句话接入提示、平台实际地址的 readme、固定 ZIP 清单与 SHA-256 |

具体契约见[MCP接入](../../组件/客户端/MCP接入/接口.md)、[交互表单](../../组件/客户端/共用运行时/接口/交互表单.md)、[个人助手](../../组件/客户端/个人助手端/接口.md#图形化流程)和[服务方](../../组件/客户端/服务方端/接口.md#图形化服务管理)。

## 接入和使用

1. 平台部署包含本改动后，用户让 Agent 阅读 `<平台地址>/readme_agent.md`。入口指向正式 `README_AGENT.md`，按已发布 GitCode tag 安装统一客户端，再从该环境导出两个轻量角色 Skill。网站本身不操作用户机器。
2. 按宿主公开入口加载技能。九问仍需原生导入；需要用户批准/重载时遵守宿主规则。运行角色脚本的 `account.setup`；未安装客户端则回到原 bootstrap 记录，不由 Skill 另装 SDK。
3. 未有配置时打开本机账号页。用户自行输入密码，或留空注册账号/密码由程序生成。Agent 只查询 `account.setup.status`，不读取私有配置正文。未知注册沿原操作恢复。
4. `runtime.mcp.configuration` 返回安装环境 Python 的准确 stdio 配置，以及九问条目、Codex/Claude Code 命令参数。由 Agent 通过宿主公开入口登记；此操作的“配置已生成”不等于“宿主已加载”。
5. 配置既有九问的 Process 命令、工作目录及模型选择，继续 `runtime.connect`。账号已认证、身份登记、WSS 已连接和 AgentHost 执行能力分别核实；不要把裸 SDK 或 MCP 成功当成业务已就绪。
6. 调用 `ioa_forms`，用 `ioa_open_form(kind, values)` 打开对应流程。values 仅放普通业务初值。用户补充、预览、提交；需要批准时转原受控确认页。表单提交本身不授权签约、付款或发布。

标准 MCP 启动契约：

```text
command: <已安装环境的 Python 绝对路径>
args: ["-m", "ioalab_sdk.mcp", "--config", "<外置配置绝对路径>"]
```

不把密码写入 MCP 配置。无 MCP 时用产品脚本的 `runtime.forms.*` 和 `account.setup/account.setup.status`，或 SDK `ioalab-access` 公共命令。共享安装和配置不代表后台执行宿主可自动替换：同一身份先排空原实例，释放原绑定，保留身份、合同、原会话和未知操作，再核验新宿主能力。

## 持久化、批准与恢复

- 接入状态在配置旁 `.ioa-setup-<摘要>/pending.json`，秘密及原操作在同目录 `credentials.enc` 加密保存（主密钥独立）。先保存原注册 operationId、随机凭据及输入摘要，再请求平台；响应未知不换账号。真实认证后才写入配置，既有配置不能被新接入覆盖。账号锁、客户端锁沿原所有权规则工作。
- 表单在原运行库的 `forms` 检查点中，按原 reference/revision 派生稳定业务标识。草稿可不完整，预览必须通过完整 Schema。提交先保存 unknown，再访问角色；已提交内容冻结。
- 每阶段完成保存原回执，再进入下一修订；旧阶段重复提交返回当前阶段，不能重复发信、签约或付款。内部 context/history 不暴露为可编辑参数。
- 常驻线程只推进已受理/运行中的作业，或已有实际决定的确认等待。新输入、语义选择和新的确认都停下；unknown 不自动重试，临时 not_ready 可按原操作续接。关页/MCP 退出不停止业务。
- 审批仍绑定原对象、版本/摘要及有效期。MCP 没有任意操作/批准工具，App 不能靠页面可见性获得可信批准；生成密码仅在同源可信账号页一次性取回，默认隐藏，不进入普通 RPC。
- 任务恢复只进入原恢复操作。服务方任务已重新入队与任务已完成分开展示；拒绝、未知和不可续接的原因保留。

## 扩展方法

在业务所属角色新增 `FormDefinition` 或 `WorkflowDefinition`，由 `bind(runtime)` 的 `forms` 登记。Schema 不能引用外部资源；预览使用真实原件，提交使用传入的稳定标识。多阶段返回 `next` 的 Schema、context、title 与 evidence；context 的业务含义由角色维护，公共运行时不判断合同或支付。

新增展示资源放到所属业务目录，同步 `form_view.py` 和 SDK package-data。协议效果仍调用原 SDK，不在 JavaScript 或 MCP Server 重写。测试优先通过公开 forms API、真实 IPC/HTTP/WSS、原确认通道，系统边界的 AgentHost 可用受控替身；同时核验离开源码目录后的 wheel/ZIP 资源加载。

## 宿主能力证据

| 宿主 | 本轮已核对 | 本轮未证明 |
|---|---|---|
| 九问 `workswarm 0.2.5b1` | 本仓固定 wheel 中的 stdio 配置、`mcp.register_custom`/`mcp.connect` 公开入口；沿原 ProcessHost 能力检查与装配；首期浏览器路径 | 用户原生前端导入、注册 MCP、浏览器续接；标准 Apps 原生嵌入 |
| Codex `0.166.0-alpha.5.1` | 本机 `codex mcp add --help` 与生成参数形状匹配 | 本轮未登记用户配置或启动交互会话；原生 Apps/确认；Codex 后台 AgentHost |
| Claude Code `2.1.310` | 本机 `claude mcp add --help` 核对 stdio、`--scope user` 与 `--` | 本轮未登记用户配置或启动交互会话；原生 Apps/确认；Claude 后台 AgentHost |

同一 MCP Server 不依赖三个宿主的私有源码。四项能力（MCP、Apps、可信确认、后台执行）须分别验收，不能互相推导。本次没有新增 Codex/Claude 后台 AgentHost 适配器，不承诺它们代替九问执行无人值守收信/生产。标准配置依据：[Codex MCP](https://developers.openai.com/codex/mcp)、[Claude Code MCP](https://code.claude.com/docs/en/mcp)；协议依据：[官方 Python SDK](https://github.com/modelcontextprotocol/python-sdk)、[Apps 稳定规范](https://github.com/modelcontextprotocol/ext-apps/blob/main/specification/2026-01-26/apps.mdx)。

## 验证记录

开发测试使用独立 `temp/mcp-dev-venv` 和 `temp/mcp-browser`，不修改原 `.venv` 或真实业务数据；后续本机平台更新另见下节。统一 runner 为 `.agents/skills/develop/scripts/check.py`，报告保存真实退出码、日志、XML 与耗时。显式浏览器测试设置 `IOA_BROWSER_TESTS=1` 与 `PLAYWRIGHT_BROWSERS_PATH`；它证明 Chromium 页面及模拟标准宿主桥接，不等于第三方前端验收。

| 检查 | 实际结果 | 报告目录 |
|---|---|---|
| 三类流程、账户保护、服务配置、浏览器、产品技能/前端回归 | 35 passed、1 skipped；跳过未启用的真实宿主专项 | `temp/checks/mcp-ui-and-controls/` |
| 完整流程与 MCP/浏览器/注册/争议/离库安装定向 | 41 passed；退出 0；62.371 秒 | `temp/checks/mcp-complete-focused/` |
| 本轮 fast | 1678 passed、26 failed、6 skipped、500 deselected；退出 1；369.425 秒 | `temp/checks/mcp-complete-fast/` |
| 修复后定向复测 | 50 passed、1 failed（接收方未配测试宿主）；仲裁与协议扫描问题已通过 | `temp/checks/mcp-complete-recheck/` |
| 私人交互最终复测及原助手回归 | 7 passed、2 skipped；退出 0；16.993 秒；两轮实际回复与原会话复用通过 | `temp/checks/mcp-private-final/` |
| 账号与浏览器最终复测 | 8 passed；退出 0；8.140 秒；注册后登录拒绝保留原账号 | `temp/checks/mcp-account-final/` |
| 最终 Linux 目标开发发行构建 | 退出 0；8.244 秒；含两款完整技能 ZIP，与发行 participant wheel 一致 | `temp/checks/mcp-complete-build/` |
| 最终目标依赖锁核对 | 退出 0；0.150 秒；使用本次发行中的真实 wheels | `temp/checks/mcp-complete-lock-check/` |
| 前阶段 fast 基线 | 1668 passed、23 failed、6 skipped、500 deselected；退出 1；335.001 秒，非全绿 | `temp/checks/mcp-fast/` |
| 原 HEAD 对照 | 三类代表失败均重现；3 failed；已核对导入的是 HEAD 快照 | `temp/checks/mcp-head-repro/` |

本轮真实协议测试完成注册及丢回执恢复、接口公示/停用、发现与结构化发送、服务发布/暂停恢复、合同环节确认、真实文件及 WASM 验收、AP2 付款、原案件独立批准。AgentHost 和用户决定为显式测试边界，平台/SDK 协议和签名不是模拟页面结果。

既有环境失败分布：部署激活 16 项在 macOS arm64 触发 Linux x86_64 目标限制；TLS 错误 IP 6 项连接 `127.0.0.2` 超时，未到预期证书校验；试用网关 1 项在阻塞写入后 SIGTERM 退出超时。原 HEAD 代表用例也失败，本轮不放宽这些断言。本轮 fast 中新增的三项失败分别为异步交互测试等待条件/接收方宿主、仲裁摘要的字段断言，以及静态检查将本地表单类型误认作线上消息；已在上述定向复测收口。协议检查仅区分 FormDefinition/WorkflowDefinition 的登记参数，所有真实控制调用、帧和信封种类仍严格检查。按最后结果合并三份复测报告，共 58 个相关用例通过、2 个真实宿主用例跳过；其余 fast 输入未变，不把定向修复报告写成整仓全绿。

## 依赖、构建与交付界限

SDK 使用 `mcp>=1.30,<2`，接入技能和 Linux participant 锁固定 `mcp==1.30.0`；只新增 MCP 必需依赖，原锁中既有版本保持。`core`、`swarm` 锁未改。macOS 临时目录 `/var` 与 `/private/var` 的真实路径差异在 wheel 构建入口统一解析，保留包根越界校验。

两款完整 ZIP、SDK wheel 和 Linux 目标发行已由原构建工具生成，产物 VERSION 仍保持未发布状态。开发构建位于 `temp/mcp-complete-release/31fc95edc873a5b84c02d85f46745dc65f27653abb89d1145aa88d1f72c321ff/`，不会替换用户现有安装；构建成功不代表 Linux 实机安装或新服务器业务验收。

未验证范围：真实三宿主加载/切换、九问从零原生导入、公开 GitCode 下载入口、新服务器更新及业务、真实模型业务质量和效率对比。客户端12、13、15—18的实机验收项继续保留；本机已经实现的能力不再标为待开发。

## 本机 8750 验收准备（2026-11-11）

按用户要求，使用当前 develop 未提交工作区代码更新已有本机开发实例。继续使用原 `.venv/bin/python`、`temp/local-platform/platform.json`、`temp/local-platform/data` 和 `http://127.0.0.1:8750`；未重新初始化、重置数据库或改动平台注册策略。原 PID 20628 正常退出后完成备份，新 PID 79650 已通过公开 readiness 身份检查。原 `server.log` 继续追加，`server.pid` 已更新。

本次操作证据保存在本机忽略目录 `temp/local-platform/updates/mcp-20261111-215206/`。其中 `backup/platform.json` 保存原配置，`backup/ioalab-data-20261111-215341.tar.gz` 由原 `ioalab backup` 命令生成并逐文件校验，备份目录权限为 0700、配置及归档权限为 0600。备份包含私有数据，不对外提供下载。

停机后的基线与重启后的只读快照一致：配置 SHA-256 不变，数据库 62 张表、共 47 行的内容摘要全部不变，密钥及其他持久化配置文件摘要不变；运行日志及运行批次按正常启动追加。详情见本机 `preservation-summary.json`。

使用原 `tools/build_access_skills.py` 重新构建两款完整包，构建退出 0；校验 wheel 清单、新 MCP/账号/业务资源及两包 SDK 内容一致后，放入原配置指定的 `dist/`。本次为未正式发布的验收构建，产物 VERSION 未递增。

| 下载包 | 字节数 | SHA-256 |
|---|---:|---|
| `ioalab-assistant-skill.zip` | 14206433 | `eae187cb06876354bdd600e33bd1fb12e9fb076bfcefb5deeeab0f2db8d7682d` |
| `ioalab-provider-skill.zip` | 14202686 | `42de328345156eeb198074cd163ab0dee39f4238f5e1a51282f68c9d86075177` |

实际访问 `/`、`/connect`、`/api/config`、`/readme_agent.md`、`/api/access/bundles`、新版 `connect.js` 和两款 ZIP 均返回 200；下载字节数和 SHA-256 与清单及本次构建一致。独立 Chromium 检查接入页的个人助手/服务方切换、指令中的本机地址和下载链接，未出现页面脚本错误或 HTTP 失败。只读检查未注册账号、导入宿主技能或发起业务。

用户可从 `http://127.0.0.1:8750/connect` 复制接入指令，在同机九问中开始验收；此地址仅供本机访问。原生导入、MCP 实际加载、账号界面、私人交互与委托交易仍由后续实机操作验证，以上准备不代替业务验收。

## 本机原生验收（2026-11-12）

复用用户已安装的 `workswarm 0.2.8`，建立独立数据目录与端口（前端 15173、WebChannel 19190、AgentServer 18192），模型沿用用户指定的 GLM-5.3-Flash。原九问安装源码、5173 前端及用户原配置保留；测试账号与证据在忽略目录 `temp/mcp-real-20261112/`，不入库。8750 已正常重启加载新版 agent readme，重启前后配置、62 张表内容和稳定文件摘要一致；之后新增的数据属于本次测试账号。

已发现并处理的问题：

- 账号页控制服务与常驻客户端随启动会话退出。两个 POSIX 后台入口改为独立进程会话；经实际 SIGHUP 重现失败再验证修复。`mcp-native-lifecycle-green` 为 25 passed、1 Windows 专项 skipped；真实九问调用新版包后，系统浏览器账号页提交成功，公开 setup 状态为 ready、authenticated/verifiedOnline 均为 true，配置文件权限 0600。
- agent readme 没有明确九问本地 ZIP 导入方式，首轮模型搜索商店并探测安装位置。补齐原生菜单和缺上传工具时的停点后，新会话完成下载与摘要校验，正确等待用户导入；验收通过原生“技能→我的技能→创建→上传技能”完成，技能启用，后续实际调用 skill_tool 读取流程页。首轮失败证据保留，不将人工补救记作首轮通过。
- 新账号缺宿主字段时直接启动，外层只报告 bootstrap 退出。产品 `runtime.connect` 改为读取公开配置摘要，先返回 configuration 阶段、configurationDigest 与所缺字段，再由原 configure 接口补齐。流程页明确 Process 命令与 Web/App 启动命令的区别。`mcp-native-host-red` 先复现错误，`mcp-native-host-green` 20 passed。更新包已通过原生覆盖导入，逐文件核对 21 个资源与 ZIP 相同；后续由真实九问调用公开 configure 补齐宿主，连接回执 authenticated、platformConnected、host.available 与助手 executionReady 全为 true。

独立九问初始 worker 启动还遇到 macOS Unix socket 路径过长；仅对本次实例使用原版支持的 `JIUWENSWARM_RUNTIME_SOCKET` 短路径，模型列表和实际聊天工具调用随后可用。这是验收环境问题，不修改九问源码。

最新 8750 实际下载包（覆盖前一节的旧验收包，仍未正式发布）：

| 下载包 | 字节数 | SHA-256 |
|---|---:|---|
| `ioalab-assistant-skill.zip` | 14208837 | `d73842d244e9b0fb6238c7cc71174157687f93ff2aac5f17e19ddd7c71042309` |
| `ioalab-provider-skill.zip` | 14205090 | `bc6865c2f23cc8d576b4401549cf3260796a418f3b623e0aa9f2a09cea057022` |

实机已完成：

- 九问原生连接器登记本地 stdio MCP，界面实际列出 11 个 IoA 工具；聊天实际调用 status/forms/open_form 创建接口草稿。
- 原生 Apps 页面失败，错误为 `unknown method: mcp_app.read_resource`。当前宿主前端有 Apps 渲染代码，运行中的网关不接受资源桥接方法；不能以工具可调用推断 Apps 可用。本次未改第三方宿主源码。用户式后续指令成功调用 open_browser 打开同一 reference，保留草稿。
- 浏览器完整完成接口填写、预览、受控确认、原操作发布；权限仅允许另一个新建测试账号。另一测试身份发现接口、填写消息、发送，经平台长连接与真实 GLM-5.3-Flash 返回“已收到验收会议预约请求：MCP双通道隔离验收会议”，随后关闭原交互。没有操作真实日历或外部联系人。
- 服务方受控注册、专业说明保存、公开 configure 绑定独立九问及增加测试 peer 身份均完成；服务发布、平台索引就绪、恢复接单与 executionReady=true 已核实。全部合同关闭后，经同一浏览器表单暂停测试服务，页面显示暂停接单、在途委托 0。
- 实机发现 A2C specSchema 字段映射被错误当作 JSON Schema 根对象。服务页改为按字段编辑协议约束，委托页投影非 const 字段并保留类型、枚举和范围。`mcp-spec-red` 复现，`mcp-spec-green` 15 passed（52.13 秒），含原完整履约、验收付款及争议回归。真实页面编辑 maxLength、发布非空规格、委托方按模板填写 topic 均通过。
- 原生 MCP 登记回执补充 jiuwenJson 和准确 UI 菜单，缺原生登记工具时明确交接，不搜索/写入宿主私有配置。`mcp-native-registration-green` 20 passed（7.70 秒）。
- 零费用文本委托完整完成发现服务、填写非空规格、任务授权、实际九问报价、候选查看、具体合同确认、模型生产与文件交付、真实 WASM 校验、独立验收及结清。公开状态为 formed/started/closed=true、availablePeriods=acceptedPeriods=1、payment=paid，服务方报价、控制与生产任务均 completed。本单金额为 0，没有付款凭证或真实资金划转；不把它当作实机有偿付款验收。测试服务与接口均为通用业务能力，验收文字只是测试输入。

新版共享 SDK 发行摘要为 `64a67b2f2ca60e34b052c7cf51154eba25d54d3d036a07dcdda3b517892b839f`。两个测试实例按原 bootId 排空后由产品安装器升级，账号配置、runtimeId 和既有记录保留。8750 再次正常重启至 PID 6193，readiness 通过；重启前后配置、表与稳定文件摘要一致，实际下载字节与上述新包摘要一致。独立九问通过原生“扩展→我的扩展→连接器→ioalab→编辑”将 command 更新到新安装，重新加载 11 个工具；新聊天实际调用 mcp_ioalab_ioa_status，连接、认证、宿主和 executionReady 全为 true。公开历史核对无模型直接读写账号配置的工具调用，此结论限于已观察的会话元数据。原九问私有 .env/config.yaml 与平台配置的 SHA-256 核对不变。

本单合同摘要为 `sha256:e7aa5d57a544898ce9c1232b04d47a0f5a2c30d0ed81533a921faf6e82e70df1`。交付 note.txt 的 SHA-256 为 `f04e4e0591b4256f0cb691e17d7f93c78be8e73654f9b884e3f217697b5c9bea`；mcp_note_verifier@1.0.0 实际报告 pass、errors=[]，UTF-8、内容标记和非空检查通过。模型生产会话为 `process_cli_1a1ce09e57e_7b7fc55a09ef`；新版原生 MCP 查询会话为 `web_1a1ce12bb6a_4148744aacc2`。

证据位于 `temp/mcp-real-20261112/`：`private-message-replied.png`、`interface-published.png`、`service-published-ready.png`、`service-paused.png`、`trade-paid.png`、`native-mcp-upgraded.jpg`、`native-mcp-status-upgraded.jpg`、`native-app-bridge-error.jpg`。业务回执为 `contract-final-result.json`、`trade-verified-result.json`、`provider-tasks-final-result.json`；公开历史为 `native-upgraded-mcp-history.json`、`native-app-fallback-history.json`；安装与保留证据为 `platform-reload-spec.json`、`spec-fix-bundles-summary.json`、`original-config-preservation.json`。这些本机证据含测试私有运行信息，不进入下载包或版本库。

结论：浏览器替代路径的接入、私人交互和单期零费用委托已通过实机主流程；原生 Apps 内嵌明确未通过。Codex/Claude 实际加载与切换、新服务器、多期合同、有偿支付及异常恢复的完整实机矩阵仍未验证。页面在合同已结清后仍显示下一步操作的“草稿”，历史确认过期后显示“已过期”，容易与业务状态混淆，作为后续体验改进项记录。没有提交或推送。

### 九问 Apps unknown method 定位

2026-11-12 按用户要求进一步只读排查。受测 workSwarm 0.2.8 为本地 editable 安装，源码提交 5c052b521；agent-gateway 0.0.1 固定提交 43a1d502d0bd6ea4b6665644be50d674167003e9，openjiuwen-protocol 0.1.0 固定提交 cbbadde9101db6e3d75e4ef2b9bdb6029fd92088，与该仓 pyproject 的依赖 pin 相同。

确定根因是独立 Gateway 的协议方法登记缺漏，不是整个九问版本没有 Apps 实现：

1. 前端 mcpAppHost.ts 使用 mcp_app.read_resource；AgentServer 已有 _handle_mcp_app，转成标准 MCP resources/read。
2. Gateway 的 app_web_handlers.py 已将 mcp_app.list_tools、mcp_app.read_resource、mcp_app.call_tool 放入转发表及免本地 ack 表，但 app_gateway.py 在转发前还必须找到同值 ReqMethod。
3. 固定协议包没有这三个枚举，Gateway 的 common/protocol_compat.py 也遗漏它们；AgentServer 通过 jiuwenswarm.common.schema.message 动态补齐，却不能影响另一个 Gateway 进程。独立诊断进程中，导入 Gateway 后三项仍不存在，导入 Swarm schema 后三项才存在。
4. 当前隔离实例 WebSocket 实测 mcp.list 成功，两个只读 Apps 请求均返回 unknown method。使用标准 MCP Client 直接读取已安装 IoA 的 ui://ioalab/forms/v1.html 成功，MIME 为 text/html;profile=mcp-app，返回 26966 字节 HTML，包含 ui/initialize。故该报错发生在到达 IoA MCP Server 之前，不能通过改 IoA 资源 URI 或改成 HTTP 连接解决。

修复应归九问的 Gateway/协议包：统一发布三个方法的协议枚举，或沿 Gateway 现有 protocol_compat 机制补齐登记，再发布匹配依赖；覆盖独立进程启动后的资源读取、工具列表和工具调用转发回归。仅在 AgentServer 里补登记或重启当前同版进程不能修复缺项。本次未改第三方源码、site-packages 或用户配置，也未尝试旁路转发。这里只确认本次 unknown method 的根因；修复后的沙箱加载、页面握手和交互仍须实测，未宣称完整嵌入兼容。

### 独立九问升级复验

2026-11-12 经用户授权，将 15173 独立验收实例切换到官方 develop 提交 `e1e73174208e4fe1593d1ed8a395c319e3a4d9fd`，使用单独源码副本和虚拟环境；不更新原九问仓库、原虚拟环境或 8750 平台。固定 Gateway 为 `54e5330d3ea6be972aa38de4c42a898ea73fed37`、协议包为 `4c5f3e9eb41bd33d34d012b06a3e364e8497ac11`、agent-core 为 `f3f8f576164cc405c046e43079c8ad00ee17f0c4`。应用元数据仍为 0.2.8，不能仅据发行版本号判断方法注册是否完整。官方源码未打补丁，前端按 package-lock 安装并构建成功。

安装边界：完整 `uv sync --frozen --no-dev` 因本机缺完整 Xcode，无法编译新引入的 `openjiuwen-computeruse` 原生 macOS 组件。随后使用 `--no-install-package openjiuwen-computeruse` 安装其余锁定依赖成功；`uv pip check` 明确报告且仅报告该组件缺失。因此本次是 MCP Apps 范围的可运行升级，不是完整桌面自动化依赖验收。源码中的演示视频 LFS 对象返回 404，跳过该文档视频下载；应用源码完整，Git 工作树无修改。

实际验证：新版 Gateway 的 `mcp_app.list_tools` 返回 11 个工具；`mcp_app.read_resource` 返回原 IoA HTML 资源及正确 MIME；`mcp_app.call_tool` 调用只读 `ioa_status` 返回 completed、isError=false。原聊天内两张历史表单均通过真实双层 iframe 显示“定义消息接口”，原草稿引用及初值保留，原生页面不再出现加载失败卡。上游 `test:mcp-app-document`、`test:mcp-app-context`、`test:mcp-app-view` 三组测试退出码均为 0。自动化工具能读取嵌套 iframe 控件，但点击/键盘激活返回目标或焦点失效，未成功触发“刷新状态”；页面按钮回调、编辑保存及发布仍待手动确认，不能用直接 Gateway 调用替代这一层证据。本次未重新执行模型生成表单或业务发布，也未验证新设备会话包。

保留核验：原九问 `.env`、`config.yaml` 及验收实例 `.env` 摘要不变。新版导入时按原生迁移机制补齐验收实例 `config.yaml` 模板字段，故该文件与升级前摘要不同；此后停止旧实例、备份数据再启动新版，备份后的配置文件无丢失或内容变化。保留原聊天与业务数据，不取消或重做业务。证据位于 `temp/mcp-real-20261112/`：`upstream-upgrade-report.json`、`upstream-mcp-app-probe.json`、`upstream-frontend-tests.json`、`upstream-native-app-embedded.jpg`、安装/构建日志和 `swarm-before-upstream-upgrade/` 数据备份。备份发生在模板迁移之后，不代表迁移前 `config.yaml` 的逐字节副本；这些本机资料不入库。进程入口与源码提交记录在 `native-processes.json`。没有提交或推送。

本机诊断证据：temp/mcp-real-20261112/mcp-app-routing-diagnosis.json、mcp-app-process-enum-diagnosis.json、mcp-app-live-diagnosis.json；只读探针为同目录 probe_app_bridge.py。

设备会话、凭据迁移与安装环境要求已改为[设备会话开发](设备会话开发.md)所述实现；上文历史实机验收不代表新版设备认证已经在原生九问或全部系统凭据库中验收。


## 2026-11-13：九问 MCP 参数保真修复与原草稿预览

用户明确授权仅修改 MCP 工具参数路径，故本次对独立验收实例的 openJiuwen 安装作局部补丁；不沿用前文“官方源码未打补丁”的历史结论。上游基线仍为 agent-core `f3f8f576164cc405c046e43079c8ad00ee17f0c4`，本机实际运行代码已不同于该原版，不代表上游修复或正式依赖发布。IoA 产品代码、安装来源与部署锁未改；重新安装该原版依赖会覆盖补丁，推广前仍需上游正式修复并更新锁定来源。

故障：模型原始 MCP 参数包含 `allowedSenders: []` 和 `tools: []`，但 `MCPTool.invoke` 使用 `remove_none_values` 递归删除空数组、空对象与 null；原草稿缺字段，预览报 required。不得由 IoA 将缺字段默认为任何权限，也不得把 null 和空列表混用。仅在 MCP 路径改为校验而不格式化、清理参数；原 skip_inputs_validate 行为不变，未修改全局 SchemaUtils。

作用文件为独立九问环境内 `openjiuwen/core/foundation/tool/mcp/base.py`。补丁前 SHA-256 `c9811048a03061c470ed7f898cd260bfa3fd2d691d3429bb90d209b1923378fa`，补丁后 `ada331ecef5ee4b359527758c0f8fa765e9ff525439c7dcef78f95e10bf5b138`。下列差异只对应上述固定基线，不自动应用于其他版本：

```diff
--- a/openjiuwen/core/foundation/tool/mcp/base.py
+++ b/openjiuwen/core/foundation/tool/mcp/base.py
@@ -204,12 +204,10 @@
                     ToolCallEvents.TOOL_PARSE_STARTED,
                     tool_name=self.card.name, tool_id=self.card.id,
                     raw_inputs=inputs, schema=self._card.input_params)
-                skip_none_value = kwargs.get("skip_none_value", True)
-                arguments = SchemaUtils.format_with_schema(inputs, self._card.input_params,
-                                                           skip_none_value=False,
-                                                           skip_validate=kwargs.get("skip_inputs_validate", False))
-                if skip_none_value:
-                    arguments = SchemaUtils.remove_none_values(arguments) or {}
+                # MCP arguments are JSON: explicit null/empty containers and omitted
+                # fields have distinct meanings. Validate without rewriting the payload.
+                if not kwargs.get("skip_inputs_validate", False):
+                    SchemaUtils.validate_with_schema(arguments, self._card.input_params)
                 await trigger(
                     ToolCallEvents.TOOL_PARSE_FINISHED,
                     tool_name=self.card.name, tool_id=self.card.id,

```

验证分层：

- 通过唯一 check.py 入口测试已安装 MCPTool，边界替身仅替换 MCP 传输；修复前 4 failed / 4 passed，修复后 8 passed。覆盖显式空数组、空对象、null、嵌套值、false/0/空字符串、未填写字段，以及缺必填/类型错误/额外字段拒绝。报告 `temp/checks/mcp-arguments-red/` 与 `mcp-arguments-green/` 含真实退出码、耗时与 XML。初次宿主 Python 缺 pytest，未安装额外宿主依赖，改用既有测试环境加载原宿主库运行。
- 通过修复后的真实 MCPTool → stdio MCP → 常驻 IPC 保存原草稿并读回，两个空列表保留。首次预览暴露草稿 Schema 含不受支持的展示关键字 title；仅从业务草稿移除 title，保留字段、描述、必填与其他约束，不扩展平台 Schema 校验。再次预览 completed / previewed，原表单修订到 6；未提交、未发布。
- 仅重启归属已核实的独立九问后端与 MCP 子进程，保留独立账号设置进程及前端；配置文件摘要不变。通过重启后的 ioalab-experience 公开 Apps 网关读取同一 revision 6 / previewed 和空列表，platformConnected、ipcReady 均为 true。8750 和 IoA 常驻客户端未重启。
- 这次真实 MCPTool 调用不经过新模型轮次，不能充当修复后原生聊天/用户点击证据。用户刷新聊天后的读取、页面预览及可信发布继续待验；同名旧连接缓存问题此前通过新连接器规避，未修缓存。其他宿主与完整业务闭环仍开放。

局部补丁、探针、运行证据保存在本机 `temp/mcp-argument-fix/`；不包含账号凭据，不入库。仓库保存上述可审阅差异与结论，IoA 已发布实现和本机上游依赖修复分别记录。


### 隔离发送端恢复与手动验收交接

用户提供修复后九问新模型轮次回执：revision 8，空列表保留、预览 valid=true、读回成功；期间模型重新加入不支持的 title 后自行移除，最终内容与 revision 6 一致。这证明新模型轮次的参数路径恢复，不代表发布和双方收发完成。

随后按用户明确授权恢复原隔离测试发送端：旧实例为 SDK 0.0.148，公开状态 installation_changed、connectionError=stale_generation，调度 active=0、pendingOperations=0。通过原 bootId 正常 runtime.stop，再用当前统一客户端 ioalab_jiuwen start 沿原配置启动，未新建账号。公开复核 authenticated、verifiedOnline、platformConnected、ipcReady、host.available 和个人助手 executionReady 均为 true，connectionError=null；runtimeId 与身份标识不变，保存消息仍为10条。配置文件摘要发生变化，不能声明配置逐字不变；当前启动入口包含验证在线设备会话后迁移旧密码配置的逻辑，本次没有读取或展示配置正文，未逐字段比较迁移前后内容。原草稿未在本次恢复中修改或发布。

只读状态摘要及启动回执保存在本机 temp/sender-recovery；下一步由用户将原表单限制为指定测试账号、启用并预览，经受控确认后才能进行双方收发。没有因客户端恢复而发送测试消息、发布服务或推进合同。


## v0.0.176：确认页直达、静默轮询和批准后自动续接

用户在旧版内嵌页面提交后，等待页面反复重建控件；ioa_open_browser打开原业务表单，第二次点击才到确认页；批准记录已为approved，但普通FormDefinition被后台推进器排除，必须人工续接。原接口已沿原revision发布完成，没有重建或重复发布。

修复仅改变三处生产代码：MCP server按原表单的waiting/confirmationRef选择既有runtime.open_ui；forms推进器消费所有表单的已决确认，只保留WorkflowDefinition的accepted/running自动推进；共享JS以独立polling执行只读查询，未变数据不渲染，迟到响应不覆盖新状态/编辑。没有增加批准工具、自动批准或unknown自动重试。确认入口仍使用原一次性令牌和受控用户浏览器。

验证：form-confirm-red复现2项失败；form-poll-red通过真实Chromium复现等待时DOM节点被替换。首轮修复回归29 passed/1 failed，进一步发现MCP测试子进程加载了测试环境旧安装；测试显式设置当前SDK源码根，保留对真实stdio传输的覆盖。最终form-confirm-final 10 passed（含批准自动发布、拒绝不发布、失响应恢复、确认页直达、两次轮询后DOM/焦点保持及页面确认按钮）。未变的多阶段和user_browser回归沿用前轮通过结果。报告均在temp/checks，保留实际退出码与耗时。

本机候选由tools/build_client.py构建：ioalab_client-0.0.169-py3-none-any.whl，SHA-256 9ca9954c5f1609ac593fd8df80bb78034edebbdd4bf48203bd9e47e8b16712d7。组件VERSION未作为正式发行递增，当前安装来源为本机构建wheel，不再声称是原design-v0.0.175的未修改安装；pip RECORD核验通过。按原bootId正常停止两个无在途调度的验收实例，安装后沿原配置启动；公开快照验证配置SHA、runtimeId、身份标识、保存消息数量不变，平台/IPC/宿主/执行能力均就绪。8750无需更新；原meeting_request表单仍revision10/completed，发布版本1。重启独立九问后端加载新MCP资源，网关返回静默轮询资源摘要1c4ba4644acfb338ab7906cff0e5d84f8e12e943e58f850c3997b3a66fda5ff6，原结果可读。证据temp/form-confirm-update与temp/form-confirm-release不入库。

本轮没有代用户批准真实业务或发送测试消息。新代码的协议及隔离浏览器验证与九问网关资源加载已完成，用户在九问新表单上的按钮/等待/批准后自动完成仍待继续；现有已完成接口无需再次发布。openJiuwen参数保真仍为此前本机局部补丁，上游发布状态未变。

提交前快速层 form-confirm-fast：1778 passed、4 failed、16 skipped、516 deselected、14 subtests passed，271.33秒。失败仍为此前3项deployment_tls及1项trial_ingress阻塞写退出测试，本次未修改相关实现，未宣称整仓全绿；浏览器专项已在显式IOA_BROWSER_TESTS环境完成，快速层跳过项不作为通过。
