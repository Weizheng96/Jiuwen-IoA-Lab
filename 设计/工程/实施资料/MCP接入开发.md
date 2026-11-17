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

## v0.0.177：录制前的 Schema 与宿主等待修复

缺陷有两个独立来源：模型经常为字段生成 title 注解，但原客户端与平台关键字集合不接受；内嵌 App 将包含宿主人工授权时间的工具请求统一限时35秒，并在 waiting 状态自动轮询需要授权的工具。前者导致预览拒绝，后者可能把尚未决定的请求标为未知并丢弃迟到回执。

客户端与平台现统一校验、保留字符串 title；JSON Schema 的关键字、格式、深度和大小约束继续存在。角色表单从本地规则集合生成公开格式说明。App 的工具请求超过35秒只更新等待提示，明确需查看宿主授权；收到迟到回执仍完成原请求。统一禁用在途按钮，不产生并发工具请求；App 不后台轮询，受控浏览器继续静默轮询，常驻实例依旧自动消费已决业务确认。没有跳过九问原生授权或 IoA 发布确认。

回归：demo-title-red复现 title 拒绝，demo-title-green真实发布通过；demo-app-red复现缺少授权等待状态，demo-fixes-green共45项通过，覆盖超过35秒的迟到保存、拒绝后按钮恢复、等待阶段无背景工具、原发布与恢复及 stdio。快速层 demo-stability-fast：1780 passed、4 failed、16 skipped、516 deselected、14 subtests passed，273.31秒；失败仍为既有3项 deployment_tls 与1项 trial_ingress，不声明整仓全绿。

本机候选由唯一构建入口产出：客户端 wheel SHA-256 e7e98e6917853a6a6dd9be6487644bf96c83d05c2737169adef4bd3b8a77b36c；平台 wheel SHA-256 70b71e722944e4aca089eda4ab1e96267f454604dda2154779588f67c10eac68。组件版本仍为169/153，本次不是完整部署发行；不把候选wheel称作原标签的未修改安装。本机8750先停写并备份数据，再更新重启；两客户端沿原配置启动，配置SHA、runtimeId、身份、消息计数逐项一致。独立九问后端正常重启，前端保留，openJiuwen参数保真局部补丁保留。证据归本机temp/demo-stability，不入库。

新原生会话已创建独立会议接口草稿，记录真实内嵌页面及原操作引用。自动化内置浏览器嵌套按钮定位失败后，转Chrome原生UI继续同一新会话，不使用后端调用代替按钮。


### 本机新会话彩排结果

从ioalab-experience的“使用”进入新聊天，自然语言创建独立meeting_demo_rehearsal接口：只允许既有隔离测试账号，启用，不授权本地工具，仅回复“已收到，等待本人确认”。九问实际生成带title的Schema，未出现关键字拒绝；旧表单未改动。本次会话web_1a1d4927ba4_f3181d33b265，原表单4710bfb9-c437-5baf-9bcd-9007b481345b。

Chrome原生UI完成编辑、保存、预览、提交申请、打开可信确认页、允许此操作、返回刷新。保存故意等待超过35秒后才点九问“允许”，仍收到原结果且未出现未知错误。内置浏览器自动化无法点击嵌套按钮、原生键盘中文输入只留下标点；前者转Chrome继续同一会话，后者用AX字段输入并核对后重存，属于本次自动化操作问题，不能描述为首次录制全程无中断。实际业务未用后台补提或重新建表单。

确认按钮一次打开/user，确认对象和白名单均核实。Codex按用户授权的隔离彩排范围操作本机批准入口，确认4670985a-be88-5c07-afed-c008c0a1fef4已允许；没有冒称真人批准。常驻实例自动发布，返回内嵌表单手动刷新后显示“已完成”。独立只读核验最终revision2、phase=completed、接口revision1、tools=[]，摘要sha256:14002cbd3c00ee7fc7f4e2f1366b5db59f1a018b3ea4598b8a6349649d2cbd84。

本次只通过“定义会议预约接口”的完整UI闭环。没有发送双方预约消息、调用日历、生成邀请或证明实际预约成功；其他业务及宿主验收继续开放。

### 用户录制的最短路径

1. 使用当前本机候选环境，在九问“扩展→我的扩展→连接器→ioalab-experience→使用”新开会话。
2. 要求创建一个新接口标识的会议预约草稿，明确发送账号、所需字段、固定回复与不操作日历，先不提交。等待新表单生成。
3. 在表单改一处用途说明，保存；九问出现工具授权时点击“允许”，等待按钮恢复。
4. 生成预览并完成本次宿主授权，看到有效操作预览后提交并申请确认。
5. 点击“打开确认页面”，完成宿主工具授权；在本机确认页核对对象后点击“允许此操作”。
6. 返回九问点击“刷新状态”并允许只读工具，显示“已完成”即可收尾。业务确认与宿主工具授权是两种不同决定，不跳过也不把等待称为失败。

录制前保持8750与独立九问、两个客户端现状，不在录制过程中升级或重启；只用新会话和独立接口名，避免陈旧内嵌卡片与旧定义草稿。模型生成时间和网络波动不能承诺固定时长；本轮没有通过脚本造成功结果。

### 原生授权按钮可见性补测（2026-11-13）

用户手工保存时再次受阻：九问宿主的 McpAppView 授权层横向排列，长 JSON 参数撑开文本最小宽度，把授权按钮挤到卡片裁剪区域外。请求仍等待 ioa_update_form 授权；不是平台保存失败。前轮 AX 点击能访问离屏按钮，未覆盖人眼可见性，不能据此前轮彩排宣称录制不会受阻。

独立九问源码的 McpAppView.css 改为参数区与按钮区纵向排列，文本允许收缩，按钮可换行；不改授权逻辑。隔离 Chromium 在320/816像素宽度均复现旧版按钮出界，修复后所有按钮落在卡片内；截图和结果归 temp/mcp-layout。Vite 构建成功，静态产物已更新至本机15173服务，新载入页面生效；保留旧哈希资源供在用页面读取，没有重启后端或刷新用户页面。当前用户页通过键盘焦点滚动显露原授权按钮，未代点允许；用户随后自行切换聊天，原保存是否完成未据此认定。

这是独立九问本机源码补丁，未提交或发布到九问上游；IoA平台和客户端业务代码未改。完整手工录制以及新构建在真实授权提示中的视觉复核仍待完成。

## 内嵌优先与误弹窗修复（2026-11-14，本地 v0.0.178，未提交）

实机日志确认模型在读取原草稿后主动调用 ioa_open_browser，外部业务表单与内嵌页面重复出现；此前用提示词避开不构成产品修复。现按宿主协商能力给浏览器工具声明 `_meta.ui.visibility=[app]`，普通表单工具继续关联内嵌资源。规范依据为 [MCP Apps visibility](https://github.com/modelcontextprotocol/ext-apps/blob/main/specification/2026-01-26/apps.mdx)。

标准 tools/call 无统一可信点击来源标记，不能把模型可填的 fromApp/confirmed 当作来源证明。服务端增加进程随机密钥派生、绑定原 reference 的导航校验信息，仅经 CallToolResult._meta 下发给 App，模型 content/structuredContent 不含该值；不写入账号、业务库或凭据存储。App 只在用户点击浏览器/确认按钮时携带，服务端先校验再查原表单、选择原浏览器入口；缺少、伪造、跨表单或旧连接信息均拒绝。此机制防误调用，不证明真人批准，也不防持有完整原始协议权限的恶意宿主；发布仍须原可信确认。

本机锁定九问的 openjiuwen stdio list_tools 将原工具转成 McpToolCard，未处理 visibility；故不能声称该版本已把工具从模型目录隐藏。服务端拒绝是实际兜底，不修改用户宿主源码；合规宿主可直接隐藏。真实九问网关已验证 App-only 元数据、原始结果_meta保真、模型普通结果不含导航值、无导航值调用拒绝，以及新版 App 资源已加载。新聊天用户点击的完整视觉复测仍待验，不能以后台探针冒称已完成。

定向验证沿原真实 stdio/IPC 与隔离 Chromium 桥接：mcp-inline-red 复现未声明 App-only，mcp-navigation-ui-red 复现按钮无界面导航信息；mcp-inline-green 3项通过，mcp-inline-focused 20项通过（66.61秒），包含保存/预览不弹窗、模型拒绝、用户按钮携带信息、确认页直达、重连拒绝旧信息并读回恢复、无 Apps 降级及私人交互/委托原流程。证据保留在 temp/checks，不进仓库。

由 tools/build_client.py 和 tools/build_access_skills.py 构建本机候选，wheel SHA-256 29047aad6dd5fbdf83c5a281cae3b3a38259c4fd7a008f1210509a3581110c34；组件版本未作为正式发行递增。已安装到原统一客户端，并只同步独立九问已导入的两份 Skill references/forms.md；两个空闲客户端正常停止后通过 ioalab_jiuwen start 恢复，九问独立后端正常重启加载新 MCP。首次误用 ClientOperations 调用 runtime.start 被拒绝，随后改用原公开装配入口；首次状态断言未通过，独立复查两个实例的配置摘要、runtimeId、身份、delivery均与更新前一致，平台/IPC/宿主均就绪，无在途任务。未修改8750平台程序、账号、原草稿或原接口。证据 temp/mcp-inline-update。

本地设计版本按177递增到178；已知远程fork另有178分叉，尚未合并，不能将本地版本描述为远程发布。遵照用户要求不提交、不推送、不打标签。

快速层 mcp-inline-fast：1781 passed、4 failed、16 skipped、516 deselected、14 subtests passed，256.03秒；失败与前轮相同，3项 deployment_tls 与1项 trial_ingress 退出超时，本轮无新增失败。隔离浏览器20项专项已显式开启；快速层跳过不冒称通过。安装后的 MCP/JS 文件逐字摘要与测试源码一致，两份导入 Skill 与新ZIP的 forms.md 一致。

## 2026-11-14 已有账号重复接入

修复运行锁被误报为网络未知：existing_client / existing 阶段引导受控选择原配置；核对平台、账号、角色、配置摘要及在线状态后返回 reused。原配置不写入，原进程不停止，不复制凭据或新增身份。尚不自动搜索用户配置文件；原配置路径可由已有接入上下文提供。离线或无法核验时明确要求恢复原实例，不自动启动第二实例。

28 项 onboarding/account_setup 定向测试通过；独立 Chromium 实际点击复用路径 1 项通过，覆盖错误账号拒绝、原配置字节与运行实例保留。记录位于 temp/checks/account-reuse-pass 与 account-reuse-browser-pass。首次沙箱端口受限、测试导入及断言修正、浏览器路径缺失均保留原失败记录，不列为通过。

本机仅重开本次临时账号页，以绝对 PYTHONPATH 加载候选源码；安装包未覆盖、两个常驻实例未重启。原个人助手配置摘要、bootId、pid、runtimeId 与在线状态前后相同，证据 temp/account-reuse/preserved.json。原未知请求已重新归类 existing，用户点击复用及九问后续原生验收仍待完成。代码、设计与文档未提交或推送。

## 2026-11-14 自动发现接入（替代上节手填路径）

SDK 在本次配置目录及默认 IoA 目录中限定数量和文件大小、非递归发现配置，过滤账号/平台/角色后核对原实例摘要、宿主装配与在线状态；页面展示身份和在线状态，仅接收不透明引用。“继续使用”重新核验引用绑定的实例与配置，拒绝伪造、失效引用，成功幂等。原配置路径仅用于 SDK 向接入助手交接，用户无需填写。已同步产品账号接入指引。

自动识别本机原个人助手1项成功；临时账号页已重开并加载源码候选，常驻实例和已安装包均未替换。两个原实例的配置摘要、pid、bootId、runtimeId和在线状态前后相同，记录 temp/account-reuse/auto-preserved.json。安装包未覆盖，完整原生后续流程待验，不以后台查询替代用户点击。未提交、未推送。

定向与独立 Chromium 检查通过；本机复核曾发现宿主装配标识未传入导致过滤原实例，已修正为使用真实装配标识并新增测试，未放宽安装一致性验证。最终32项通过（含个人助手/服务方、默认/自定义目录、重开、伪造引用、配置变化和独立Chromium），文档与差异检查通过；证据见 temp/checks/account-auto-complete。

## 2026-11-14 委托 Skill 优先 MCP Apps

实机普通请求“我想找人做一个展示公司简介和联系方式的网页，给我一个界面填写需求”走了旧 requirements.md 的 A2UI 生成器，最终输出 text 代码块；未调用 MCP 表单工具，不能记为展示成功。根因是委托/需求指引与共用 forms.md 路由冲突。已统一 SKILL.md、delegation.md、requirements.md、setup.md：委托默认使用 delegation.trade，需求和候选沿同一原表单，当前会话缺连接器先引导从已启用连接器进入，无 MCP 使用同一 SDK 本机表单；文本办理仅在用户明确选择时使用同一业务入口。未修改宿主源码、配置或扩大批准权限。

两款 Skill ZIP 已重建；已逐字核对助手包与源文件，并同步独立九问导入的4份指引，记录 temp/trade-apps-fix/skill-sync.json。已登记 ioalab-experience 连接器实际列出表单工具；这只证明服务可调用，不证明当前聊天已注入或页面渲染成功。两个原客户端的配置摘要、pid、bootId、runtimeId及连接状态与修复前一致，记录 temp/trade-apps-fix/preserved.json。安装包和常驻进程未替换，未创建真实委托、签约或付款。

定向30项通过（51.03秒），记录 temp/checks/trade-apps-routing；包含技能打包、原生 stdio MCP、委托表单真实平台闭环。新会话自然语言路由和九问内嵌展示仍待用户复验，不能用程序测试替代。设计0.0.181及代码文档本地未提交、未推送。

## 2026-11-14 共用表单保存反馈

forms.html/css/js 在操作按钮区新增独立可访问状态提示：保存中、已保存、无新修改；新编辑清除旧提示，失败不冒称成功，自动保存已成功而预览失败时分开说明。内嵌与浏览器共用实现，MCP等待授权提示独立保留。测试覆盖真实浏览器成功/无修改/冲突、预览失败保留已存结果，以及沙箱App等待授权和延迟成功回执；2项真实Chromium测试通过（10.75秒），temp/checks/save-feedback-final。

候选统一客户端包已构建，静态资源与测试源码逐字一致；SHA256 9d001bb44857caa518f447be62621df6911314412393653481fcc1d84a342f16，位置 temp/save-feedback/packages。原安装、配置、身份、业务数据与运行实例均未替换。ioalab_client/installation.py 核验所有RECORD资源；runtime/installation.py 将RECORD摘要纳入实例发行一致性。因此不能只热替换资源规避校验，当前实机生效需协调安全排空、更新包、重启原客户端及重载原表单。用户要求保留运行实例，尚未授权本轮短暂重启，故暂未安装，不把源码或隔离测试计作原生实机通过。设计与开发文档已同步；未提交、未推送。

## 2026-11-14 账号接入内嵌优先（未提交）

账号接入新增独立 Apps 资源，复用现有 Onboarding、账号恢复与 CredentialVault；普通工具只接受非秘密平台和角色。支持 Apps 时不自动弹窗，无 Apps 使用原浏览器；页面主动降级使用独立导航校验，直连入口能力仅进入 _meta，密码和邀请码走本机受限 HTTP，不进入工具参数/模型正文。账号通道不允许停止客户端、配置变更、设备撤销或业务批准。共享 Skill 优先 MCP 入口，无 MCP 才走原脚本。

协议和账号定向15项通过（account-inline-protocol，11.00秒）。真实 Chromium 首轮因浏览器路径缺失未执行；指定已有浏览器后4项原页面回归通过，新内嵌测试暴露宿主沙箱禁止 HTML 原生提交，已改按钮直接调用受控请求。account-inline-sandbox 两项通过（4.41秒），覆盖无配置、服务方角色、内嵌注册/取回密码、无自动浏览器和主动降级。最终 account-inline-final 的63项全部通过（30.29秒），覆盖协议、账号双页面、凭据恢复和可信批准隔离；四项文档检查通过。统一候选 wheel 和两个 Skill ZIP 在 temp/account-inline/packages，账号资源与共享技能逐字比对一致，摘要见 package.json。只读核验两个原配置摘要、pid、bootId、runtimeId 与先前记录相同且均在线，证据 preserved.json。原安装、配置与常驻实例未改，未做九问原生新版本验收；当前用户页面不会因源码改动自行更新，应用新包需另行协调安装和安全重启，不冒称已生效。

## 2026-11-14 原生报价参数与等待恢复（开发中）

本次原生服务方把 acceptance 的交付件键重复嵌套，宿主在回调前返回通用 Schema 错误。IoA 没有记录有效报价；宿主等待至截止后转 unknown，原网页空闲不构成原 Process 轮次终局证明。原请求签署于04:05:21Z，模板 respondWithin=PT10M，截止04:15:21Z（北京时间12:15:21），不能改签名期限或虚构恢复。旧引用与业务数据保留。

补报价字段路径和宿主工具输入结构说明，回调提供准确错误路径；原轮已明确结束但无决定时可按原任务显式恢复。ProcessHost 加外层有界等待并区分 host_timeout，页面说明 running/unknown 的含义。隔离第一轮2项通过（28.82秒），包括原任务/Session保留并纠正报价、SDK不返回时超时保留未知。最终回归与应用待核对，未提交。

## 2026-11-14 报价恢复验证补充

报价恢复回归61通过、1失败、2显式实机跳过（quote-recovery-final）；失败是共享搜索候选排位排除本用例服务，独立复跑通过（quote-recovery-isolated）。真实九问GLM无副作用探针生成合法参数并完成，仅校验不上传、不报价、不成约（quote-recovery/native-format.json）。

零费用请求过期回收新增复现及修复：平台37通过、1失败（unquoted-final，125.65秒）。失败的旧接入重启用例复用已被认证更新的配置快照，独立复跑仍 configuration_changed，保留失败不改验证断言。新原表单重报价闭环通过（quote-retry-form），覆盖原授权、旧引用保留、平台关闭通知门禁、刷新不重报价与用户选择后新报价。新旧提案、付费保护、原件缺失、付款证据不一致、截止边界和事务回滚均通过。

用户已明确授权沿用原委托重新申请报价；原报价过期且未成约，不能伪造成功或改变原期限。新设计v185补齐零费用未报价的原子关闭与容量回收，仅使用公开流程恢复，保留旧未知记录。更新结果及实机后续另记。未提交、未推送。

本轮更新与恢复完成：平台完整备份后更新正常重启；两个空闲客户端完整wheel更新并正常启动，等待身份初始化后核对两个配置摘要、runtimeId、全部身份一致且在线（首次立即读取曾碰到初始化未就绪，后续完整核验通过）。本机客户端下载包已同步；wheel SHA256 ed91a53976a2a66109bc397640a2e1db06ae9016f620dfbef6573c410896c840，关键源码与包逐字一致。九问原连接器以原配置公开RPC重载成功。

平台正常维护释放旧请求，容量回到0且原政策revision4/maxActiveDelegations1不变。按用户授权通过原表单重新报价，旧请求仍unknown，新请求真实九问完成并取得服务方/市场签2/3、总价1；同一委托taskRef、原需求和规格保留。原表单revision8为“比较报价与选择合同”，未签1、未成约、未付款；原生用户点击后续待验。最终quote-retry-final 7项通过（41.72秒）；所有文档基础检查通过。运行证据保存在temp/quote-recovery，私人对象与配置不入库。设计、代码和文档均未提交或推送。

## 2026-11-14 已返回报价过期的原表单恢复（v186，未提交）

已返回签2/3报价超过选择窗口后，原表单revision9以expired拒绝，旧UI未突出错误。本轮增加WorkflowDefinition只读恢复回调、runtime.forms.recover与ioa_recover_form；原失败修订归history，准备新选择不执行业务。个人助手核候选原签名/期限/同任务及无合同确认签署记录，沿原requestValues恢复，不复用旧批准。新请求由用户另行预览提交；旧proposal-expired可靠通知前只显示等待安全结束，保留原容量和协议窗口。旧过期报价不再进入可选/比较列表，保留历史依据。

共用页面直接显示过期原因和恢复按钮，浏览器与Apps保留结构化错误码；恢复成功清除旧错误提示。后端首轮10项通过。双模式初轮因测试宿主缺protocolVersion导致1项失败，补齐测试握手后returned-expiry-complete 12项通过（23.91秒），包含浏览器/不透明沙箱、真实协议和原授权重报价；候选筛选及全业务工作流returned-expiry-choices 10项通过（55秒）。过期保护其他6项在前轮通过，输入未变复用结果。早期错误测试路径导致零收集及红测均保留，不计通过。

最终候选wheel SHA256 2d6d2cd8b22416ee37de808d72c5e086097547a39fec70d79f4b4b6e7cd56999。两个空闲本机客户端正常停启更新，平台8750无需重启；原配置/身份/runtimeId及业务记录保留检查、连接器重载与原表单恢复证据归temp/quote-expired。原生实机点击首次揭示九问旧连接残留：公开重连成功但旧MCP客户端未加载新增工具。正常重启项目独立九问后端后配置摘要与聊天历史保留，原连接器工具可用。再次在原内嵌页面点击“沿用原需求重新报价”并允许本次操作，页面显示同名恢复标题及原需求，公开MCP读回原引用revision10、draft、result为空；没有发起新报价、签1或付款。等待用户预览提交继续验收。代码和设计文档未提交、未推送。

## 2026-11-16 验收确认过期恢复（v187，未提交）

原验收确认过期返回data.reason=expired，旧共用页面只识别error.code，因而仅显示已拒绝且没有恢复入口。本轮统一补齐履约直接返回和后台终局返回的reason；原表单recover核对真实确认状态、actor、operationId、合同与期次，再读取最新履约状态。已关闭、已验收或未交付的期次不能重验；恢复只产生新草稿，用户再次预览提交才重做核验并申请独立新确认。原失败历史、合同、交付及旧批准保留，不复用批准，不改合同期限。

正常与过期恢复的真实隔离平台流程、表单接口15项通过（accept-expiry-complete）；浏览器与不透明Apps沙箱4项通过（accept-expiry-browser，4.28秒）。验证包含新确认引用不同、恢复不改变原付款状态、重复原修订幂等及批准后的正常验收结算。早期运行误选无pytest的运行环境、测试错误假设夹具无定金、浏览器未指定现有缓存路径的失败如实保留；已纠正测试环境及基线，未放宽产品断言。

候选wheel SHA256 9575aefdae011c76aca967a39a9c121f82a984050fa6d0ac8f53b3dcc8629f51；运行更新与原表单核验记录在temp/accept-expired。尚未代用户批准新验收或付款，整条实机业务验收未完成。

本机更新后核对两个客户端配置摘要、身份及runtimeId不变、在线，原消息数量未减少。平台和九问后端无需重启。九问原聊天重载资源并刷新，实际显示“确认已过期”；点击“重新申请验收确认”并允许仅本次恢复工具，原表单回到第1期accept草稿，已交付1期、已验收0期、未付款。没有提交新的确认请求，停在用户操作前。

## 2026-11-16 原委托授权过期的付款提示

平台已保存原ap2.pay的明确expired续期拒绝，适配层白名单漏收导致签7已完成而本地作业unknown。本轮只补该明确语义，仍核原冻结请求、账号、实际付款凭证及其他未决尝试，不泛化所有过期错误、不自动重付。共用双模式页面显示“已验收，付款待续期”并引导先续期原授权，再恢复已验收期付款。属于实现追平既有已知拒绝规范，不升设计版本。

红测1失败16通过；修复后付款适配、保护和真实隔离平台恢复35项通过（33.40秒），包含付款一次性、原签7保留和未知尝试阻断。候选wheel SHA256 76d431d0174ea0793c8bb43d36f5f4d9d03a747c979361a6daa5f9f86631e59c。实际续期及后续付款需用户确认，不能以测试完成替代本单付款。未提交、未推送。

本机实际核验：两个客户端正常停启更新，配置摘要、身份、runtimeId保留且在线。后台从原付款拒绝回执收敛原作业为finished/completed，acceptance=pass、payment=unpaid；读取该缓存结果后原表单进入revision21。未重新验收、未生成新付款尝试、未续期用户授权。

## 2026-11-16 MCP原委托续期入口（v188，未提交）

新增delegation.renew表单：原taskRef及validDays预填，预览真实原授权范围和预算，复用现有renew独立确认；原履约monitor增加续期入口，恢复付款前查询真实授权，未active则先进入续期草稿。原付款waiting可独立打开续期表单，保留旧请求。续期成功返回业务选择，不自动付款，不改合同期限。产品Skill说明续期和付款批准分离，模型文字不得替代平台授权生效。

红测证明旧目录缺少表单；11项工作流回归通过（63.18秒），增强后的付款转续期/确认分离与MCP协议6项通过（31.82秒）。测试核原scopeDigest、预算不变、授权修订只增一次、重复提交幂等及续期后仍停在草稿。候选wheel SHA256 bedc115c43f48ff938f6db61d8ad733c5616478dd46f4757adca90f5ec38c5b0。原生完整自然语言续期及付款仍待用户继续，不能把协议调用当模型行为验收。

运行核验：两个空闲客户端完整更新，原配置摘要、身份、runtimeId保留且在线，客户端下载wheel同步。原生九问mcp_app.call_tool经ioa_forms发现delegation.renew，经ioa_open_form成功创建原任务续期一天草稿，未提交/批准；证据在temp/renew-form。只读核原授权仍revision1/expired，原合同、已验收结果、付款等待保留。

原生自然语言复验未通过：旧九问会话误选delegation.case并宣称无续期表单；公开查询确认争议仅草稿、arbitration为空，原合同已验收未付款。原连接器目录仍含delegation.renew。核独立九问实际安装的forms/delegation说明为旧内容，已备份后同步本轮产品技能对应文件；新聊天自然语言复验待用户继续，不能把MCP目录可用记成完整宿主验收。证据temp/renew-form/skill-sync。

## 2026-11-16 付款确认过期恢复（v189，未提交）

故障为原pay确认过期后恢复分支只认识accept，错误返回“Recover the original selected quote”，模型因此扫描无关历史表单与脚本。现在核原确认绑定后查询原合同、已验收期与实际付款记录；明确未付款且没有未知尝试才恢复原pay草稿，不重新报价、验收或复用旧批准。monitor携带实时授权，页面区分付款/验收确认过期，已经续期时不再显示历史“付款待续期”。

红测在原恢复节点失败；修复后业务工作流、付款恢复、浏览器/opaque MCP Apps共35项通过（84.72秒），设计与文档四项检查通过。隔离平台测试核原签7不变、恢复幂等、重新独立批准后仅一条分期付款记录。证据temp/checks/pay-confirm-red与pay-confirm-green。未跑整仓回归，不以隔离测试代替本单真实付款。

候选wheel SHA256 1cb138f15c287f0534e25e343d2b2253fd5bf4e3f9870ae0e9aca8711dbc6f3e，安装releaseId 9f4116486d2e25c1e660c81d8d2a27dc47d8ddfb88e4283e7df2d0efe17ae80d。两个空闲客户端经公开stop/start更新，配置摘要、身份、runtimeId不变且在线，原消息未减少；dist/client下载包同步，平台8750及九问后端无需重启。

实机只读证实用户原授权revision2/active、有效期至2026-11-17 02:52:56 UTC。公开SDK恢复原付款表单revision22→23（draft，pay/period1），原验收签7逐项一致、已验收1期、未付款。未代用户创建或批准新付款确认。原生模型续接和最终付款仍待用户继续；证据temp/pay-confirm。

2026-11-16付款实机续验：只读公开接口确认原合同第1期已验收且payment=paid、paymentNotifiedPeriods=[1]、closed=true，存在1元CNY分期凭证，授权contractPaid=1.00、reservedRemaining=0.00。原表单revision25/previewed对应refresh状态查询，非待付款；本笔支付与合同结算已完成。其他MCP业务和故障矩阵仍待验，整票保持开放，未提交/推送。

2026-11-16最终交付核对：公开九问历史确认最新回合已发送note.md（send_file_to_user成功1个文件）；读取本地1689字节UTF-8原件，其SHA256与原合同manifest一致。原验收报告pass、已验收1期；付款凭证为第1期1.00 CNY、payment=paid、服务方已获付款通知、closed=true。本笔隔离文本委托的交付—验收—付款闭环验收完成；交付物为公司网页验收说明，不是实际网页。授权仍active、预留归零，不等于授权余额清零或授权关闭；其他业务及故障矩阵仍待验，整票保持开放。历史证据temp/pay-confirm/final-web-history.json，未提交/推送。

## 2026-11-16 委托确认内嵌优先（v190，未提交）

复用原Confirmations和表单推进，新增仅可信呈现适配调用的ClientOperations.confirmation_entry及认证/v1/ui/confirmation路径；无approve/answer普通工具。原委托等待确认时_meta携带限原表单修订、确认引用及摘要的短期能力，App直连本机/user/confirmation-app，决定不经过tools/call。授权/续期、具体合同、合同环节答复、验收/付款及原委托争议复用原确认。CSP仅许可实际回环origin；账号/导航/普通IPC令牌不能在用户决定端点批准。能力隔离依赖宿主正确处理_meta，不对恶意宿主或任意本机私有文件访问作安全承诺。

真实stdio红测缺ioalab/confirmation；首轮端口沙箱受限另记环境失败。协议与真实不透明iframe允许/拒绝7项通过，扩展到工作流、原浏览器确认及账号隔离46项通过（138.27秒），其中新增内嵌通道完成授权、签约、合同环节、续期、验收、付款恢复并核仅一次分期付款。额外丢决定回执只读恢复和连接阻断浏览器降级4项通过（7.03秒）。证据temp/checks/confirm-inline-*，未跑整仓完整回归。

候选wheel SHA256 8172b9ad544d715da244bf1be8b13e8a84790ed82824a6d876fa7545a0a27f76，安装releaseId ab4d8e1b71d19a77a29be33f37485f5e1437afd5f9e002e517e4a9838c7d2469。两个空闲客户端正常停启更新，配置摘要、身份、runtimeId保留且在线，消息数量未减少；两技能ZIP与下载wheel已构建更新，独立九问三份导入reference备份后同步。连接器沿原配置公开重新登记/连接，实际资源包含新确认卡片；原平台和九问后端未重启，原已结清合同保留。原生视觉检查与用户批准另记。

原生检查补充：新聊天可创建/预览测试表单，但宿主独立读取资源的连接未经历原工具调用，首次CSP缺本机origin。已改资源读取独立核验当前实例地址，缺实例时仍可显示浏览器降级；红测复现后9项MCP/页面回归通过（12.84秒）。此处不依赖跨连接内存，也不开放通配网络地址。

独立资源修复后的最终候选wheel SHA256 c9f2205528bae01836ea60952ffc596edc4a2d90a7b9cce207af1f2f8648fbf2，安装releaseId abb01ca0fc8565d1b5a3891b18468e32ee5312a7b6ac17862610eaff7e95d2fb。两个原实例再次正常更新并核保留；九问公开重连仍命中旧Apps进程，已在确认空闲后正常重启独立后端，保留配置与聊天历史。

原生九问验证：从ioalab-experience进入新聊天，用自然语言准备新测试文案委托（预算1元、拟卡费0），模型实际创建和预览新表单。页面点击提交并仅允许宿主运行申请工具后，独立卡片真实读到delegation.authorize原预览、预算、身份、期限，出现“允许此操作/拒绝此操作”和“等待您的独立批准”；未打开外部浏览器、未批准、未签约或付款。原已结清合同只读仍paid/closed且仅一张分期凭证。视觉检查发现首次停在上方旧字段，另补待确认时收起重复字段、首次定位卡片及紧凑样式，600px高度可见性红测已复现。

最终可见性回归：15项浏览器/App页面检查通过（24.72秒），包含600px iframe首次看到确认标题、允许/拒绝、回执丢失和浏览器降级。最终应用wheel SHA256 8088d1be20d487b4fb5151386eedfea93746b19f586bb963610ab92418f17828，releaseId 54e9bbc8ead436e6effc6150210c629b12fb6def989d3fdd84b5b7c83412c453，下载wheel同步。正常更新两个空闲客户端及独立九问缓存后，再核原配置摘要、身份和runtimeId不变且在线。未提交、未推送；真实用户内嵌批准留待继续验收。

真人拒绝复验：用户在上述新测试卡片点击“拒绝此操作”并刷新后，公开SDK只读核runtime.forms.get和confirmations.get均为rejected，原委托任务authorized=false、authorization=null、nextActions为空；表单readOnly=true、canRecover=false、completedSteps=0。独立拒绝已实际生效，不恢复终态拒绝申请；下一步以另一个测试申请验证允许分支。未改变原配置及既有业务，未提交/推送。

## 2026-11-16 候选报价显示（v191，未提交）

修正选项和报价摘要直接显示服务ID。工作流只读presentation根据原发现记录和原报价生成名称、金额、期数、报价序号；共用表单仅在返回副本中投影enumNames，不改原Schema约束、quoteRef、修订、预览或批准。名称按文本节点显示，完整原卡可展开；不为展示重征报价或重建表单。

公开工作流红测复现svc编号，修正后通过并核读回不写存储。定向回归33项通过；新增两项浏览器测试最初误把业务ID当HTML option值而超时，改为按可见标签选择后2项通过（2.75秒），仍断言提交原quoteRef与原修订。版本检查首轮因VERSION漏v前缀失败，纠正后轻量检查通过。报告temp/checks/quote-label-*；未跑整仓完整回归。运行更新与原表单核验继续记录。

运行更新：wheel SHA256 a554bad7824c358b27b4d993828a17788d5992abc5ecb54cc459af733761120f，releaseId 8e162277de9ac46fc563eb9b7c1b9bc4a0c4be00886b5d24d401f2011de0d9d2。两个空闲本机客户端正常停启，原配置摘要、身份、runtimeId与消息保留且在线；下载wheel同步。原候选报价读回显示真实服务名称、0.50虚拟币及单期交付，剔除展示标签后整个表单与更新前逐项一致，仍revision8/draft。原生资源探针首次使用错误URI被拒，改用声明的v1资源后确认新展示代码已返回；九问和平台后端无需重启。当前已打开旧App含用户未保存的选择，保留不动，页面重载后的真人确认继续验收。私有证据temp/quote-label。未提交、未推送。

## 2026-11-16 修订冲突保存刷新循环修复

原生旧工具结果可能重放历史表单；旧revision保存被拒后dirty仍为true，原刷新要求先保存，形成提示循环。共享页面新增冲突恢复状态：中文说明、禁止旧修订写入，显式加载同一原表单最新状态；未保存字段按可见名称保留在仅本页的核对副本中，不自动覆盖后台。

两种传输红测复现后，19项页面回归全部通过（24.88秒），包括冲突后只读get、不重发update/submit、本页副本保留、原浏览器确认及内嵌允许/拒绝/丢回执/降级。证据temp/checks/form-conflict-*。未变业务代码复用上一轮工作流结果，未跑整仓。原任务读回仍revision8、draft，报价及合同选择尚未改变。

应用与原生恢复：wheel SHA256 3118e32e9467158ce35b9984ef4048ac19b7767107b7616fa78f3c3015b360ca，releaseId 87b0f86162d2bf87cee1736f6d505109369a141dfbe6e88195e72162891aad5e。两个空闲本机客户端正常更新，原配置摘要/身份/runtimeId及消息保留且在线，下载包同步。原表单完整读回与更新前逐项相等（revision8/draft）。真实九问旧卡仍重放初始授权且有未保存输入，保留旧页，在同一聊天新标签页对原两张卡执行只读刷新和单次宿主工具许可，均回到最新报价选择，候选真实名称/金额显示正常；未作业务批准、签约或付款。私有运行证据temp/form-conflict，未提交/推送。

内嵌合同确认网络失败定位：受限本机status端点HTTP200且pending；原生旧iframe CSP仍许可更新前端口，与当前运行地址不同。重载同一聊天获取当前origin后，原表单只读刷新恢复delegation.contract卡片与允许/拒绝按钮；原确认引用不变，新报价合同总额0.01虚拟币，未批准、未签约或付款。此为运行恢复，尚未实现端口变更自动识别及重载指引，不将旧卡片内刷新视为CSP更新。

## 2026-11-16 原确认能力对齐与呈现故障修复

按用户收窄范围，本轮只修复内嵌呈现，不新增合同确认续期、报价续期或业务恢复规则。核原Contracts.confirm及Confirmations与HEAD无差异：原绑定重传返回原记录，业务过期不能批准；先前新增的原表单重报价、验收/付款恢复属于本次开发过程的流程编排，不将其全部记作原平台页面搬迁，也不在本轮继续扩展。

UserUi将15分钟呈现能力与原业务截止分开，能力有效时仍可GET读出expired；POST继续由原Confirmations校验期限。终态通过呈现元数据返回只读摘要、无token。页面提供“重新连接原确认”，仅调用原表单get并核对原引用；提交回执未知不重发决定。捕获宿主connect-src阻止时提示重载原表单资源或用户主动浏览器降级，不能通过业务刷新修改宿主CSP。业务过期明确说明刷新和换浏览器不续期。

红测复现原业务过期被HTTP403入口错误掩盖、页面缺少重连按钮。33项定向检查通过；最后提示与终态元数据补充后受影响29项再通过（35.63秒），未变的4项用户确认owner检查沿用本轮通过结果。初次测试因沙箱禁止本机临时监听退出，提权后使用隔离临时数据运行；另一次报告目录重名未启动检查，改用唯一目录。证据temp/checks/confirm-parity-*；未跑整仓完整回归。

候选wheel SHA256 ef4019a96d91f39280b2acc61a27c516225a1bd31fe4a3155f0f17995486c27b，releaseId fdbda9d00d240488501a89574347eaee1c322561fcdb6db741de98cb0036adf3。两个空闲本机客户端正常更新，配置摘要/身份/runtimeId不变、消息未减少且在线；原表单更新前后完整读回相等（revision12/rejected）。九问旧MCP缓存未下发终态摘要，正常重启空闲独立后端后原连接器已返回expired及原截止时间，不含token；配置及聊天历史保留，下载wheel同步，8750后端未改。原业务确认已实际过期，本轮没有新建、续期、报价、批准、签约或付款。私有证据temp/confirm-parity。未提交、未推送。

原生呈现复验：重载同一九问聊天资源，对原表单点击只读“刷新状态”，仅允许宿主运行ioa_get_form；实际内嵌显示“确认已过期”、原截止2026-11-16 16:17:10及“刷新或换浏览器不会续期”，无允许/拒绝业务按钮。未发送模型新请求、未创建或批准业务；真实入口失效重连与CSP提示分支本轮由隔离浏览器验证，未将此终态展示记为真人内嵌允许成功。

## 2026-11-16 普通聊天接入先启用MCP（v192，未提交）

实机旧路径：普通新会话mcp=[]，模型据“已安装/有配置文件”转入status/start，失败后account.setup打开浏览器，没有尝试当前会话MCP启用。此次统一两角色Skill、setup/account-setup/forms/shared-runtime/host-setup及README入口，新增共用mcp-setup.md：先核安装、生成登记资料、原生登记或选择、核实际工具，再展示账号页。未挂载不等于宿主不支持；无公开登记工具时停在用户原生启用步骤，不擅改宿主私有配置。现有接入reused后沿SDK给出的existingConfiguration交接，保留原实例，不重启第二实例或复制凭据。

commands.py的runtime.mcp.configuration补充stage=mcp、nextAction=enable_in_current_chat、accountRequired=false、setupArguments及明确下一步；原stdio参数不变，不读取/写入账号文件，不启动账号页或业务实例。无配置与残留不完整文件、两角色的4项红测复现缺阶段回执，修正后29项定向检查通过（15.79秒），包含真实stdio账号内嵌/降级与离仓wheel安装。最后仅加强已有安装优先说明，双角色导出复测1项通过（0.30秒）；文档/模块/阅读预算与diff空白检查通过。未跑整仓完整回归。

最终候选wheel SHA256 107143056ddb88d9fb77ac70ec0a3e608f4cbbaf922a152976a4eab9c1e3e318，releaseId 7e40b9cdf5938c181cf190985f6b91675556506ceaa5acd5e826b817789a08d4。两个空闲客户端正常更新，配置摘要、身份、runtimeId不变且在线、原消息保留；原业务表单完整内容仍revision12/rejected。两角色技能经九问skills.import_local原生覆盖导入，旧内容保存在本机备份，逐项核新引导与导出包一致；安装、原生技能及本机下载产物同步。平台8750与九问后端未重启，无注册、登录、批准、报价或付款。

原生短验证说明：用GitHub原URL的新聊天仍会读取未发布的旧README，实测先尝试bootstrap并因已有安装安全退出，没有覆盖原客户端；此分支不记通过。已补充README与角色主入口的“已有安装先核验”保护。另用本机最新版README在无MCP普通新聊天验证：最终公开历史仅调用runtime.install.status和runtime.mcp.configuration两项SDK操作，无bootstrap重装、account.setup或runtime.start。真实网页明确展示客户端已核验、登记资料已生成、等待用户原生启用连接器，并说明启用后内嵌登录；该起步分支通过。用户启用及登录仍待真人续验，不把登记完成等同于账号认证成功。未提交/推送，远程README仍旧；本机重验可直接自然语言请求接入以使用已更新角色Skill，或读取本机README。证据temp/mcp-first、temp/mcp-first-final与temp/checks/mcp-first-*。

## 履约查询交互简化（2026-11-17，v193，未提交）

原共用表单把查询也要求用户手动保存、预览和提交，同时get刷新没有成功反馈。本轮角色presentation.directActions声明履约refresh、已有交付的fetch/verify；共用页面将原update/preview/submit串成一次点击，校验同引用、同修订、同动作，不另建业务链路。等待/未知或回执丢失时刷新只get，不重发查询；修订/阶段变化停止自动串联。当前动作不需要批准才显示一键按钮，签约、accept、pay不在声明内。下载与验收预览可取件、签6、运行原验收器，不等同接受交付或付款批准。查询结果默认展开，刷新成功/无变化和失败都有明确反馈。浏览器与MCP Apps使用同一页面逻辑；宿主原生工具许可仍归宿主。

验证：最初两次浏览器测试因未指定浏览器缓存路径及沙箱不能启动Chromium失败；指定现有temp/mcp-browser并获准隔离执行后，两个红测均在旧页面仍展示“生成预览”处失败。首次绿测遇测试select使用业务值而实际选项为索引，改为按可见标签选择后2项通过。随后fulfillment-query-regression：49 passed，141.72秒；最终去重刷新按钮、清除已保存的临时核对副本后，fulfillment-query-final-ui：4 passed，4.44秒。设计/链接/模块/阅读预算门禁通过；没有跑整仓全量，不把模拟宿主桥接等同九问真人验收。

构建：tools/build_client.py生成本机验收wheel，SHA256 acadec62761e9b94056461d23e1aaa8adb34a563983b79e28311330c0708f2df，产品版本不作为正式发布递增。更新前两个原客户端均报告stale_generation且平台8750 HTTP200，账号与业务数据保持；正常启停及原数据核对结果另记。证据temp/fulfillment-query与temp/checks/fulfillment-query-*留本机，不入库。

本机应用结果：新版releaseId为22f8fc5c60a02ac036a6782098260201c26b0a2dafd95a23b96f36b395fe61b7。原两个实例沿原配置正常启停后均在线、executionReady=true，runtimeId/账号身份/配置摘要保留，消息数量未减少。更新脚本的“完整身份相等”断言因executionReady从false恢复true失败；独立复核稳定身份字段一致。原表单更新前已是revision20/accepted、verify第1期排队；恢复连接后按原队列完成到revision21/draft，原合同、规格、金额及授权范围不变，签收证据随原取件更新，验收报告pass；acceptedPeriods仍0、payment仍unpaid、凭证未增加。不能将该正常续接记为原表单完全不变。

两角色Skill经skills.import_local原生导入，旧技能留本机备份；dist下载包/wheel已同步。九问原ioalab-experience连接器经公开mcp_app.read_resource返回新版directActions及刷新反馈标记，无需重启8750或九问后端。完整真人一键交互继续待验；没有创建新合同、代批准验收或付款。

九问可见核验：重载用户原聊天后，宿主先重放原open_form历史草稿；只点击get原表单并允许本次ioa_get_form只读请求，已显示最新“合同履约、验收与结算”及唯一“刷新合同状态”按钮，查询态隐藏手动保存/预览/提交，明确显示“表单状态已更新”，本期原验收报告已展开。没有从真实页面触发新取件、验收批准或付款；新一键查询的实际点击覆盖在隔离browser/App测试，真人继续操作仍待验。

## 履约操作结果与表单阶段分离（2026-11-17，状态显示修正）

用户重复刷新后显示“草稿”的原因是UI把工作流下一输入阶段draft直接当作业务操作结果。修复限定共享表单显示：合同阶段的页签显示“等待选择下一步”，本次操作按完成回执与原报告显示检查通过、未通过或待核对；合同进度单列实际接受交付期数及付款状态，下一步提示独立确认。accepted/running/waiting/unknown/rejected/failed保持真实语义，不因先前报告pass改成成功；无报告的refresh只显示合同状态已更新。不修改协议、有效期、批准入口、业务修订或合同记录，不新增接口、不递增设计版本。

既有browser/App公开接缝增加状态迁移回归，红测2项均在“草稿”错误展示处失败；修复后contract-progress-browser共27 passed，32.52秒，覆盖进行中→完成、旧报告不覆盖当前未知、未通过、无报告不伪造通过、未接受/未付款及已付清。设计门禁通过，未运行整仓全量；此前未变业务输入的定向结果继续复用。构建与应用证据留temp/contract-progress，未提交/推送。

本机应用：wheel SHA256 b2f63457eaffd426777b5b626d085169de2e0421634a76d77574875cdc679761，releaseId a33fdb0b83a4f114b8c6ef67e37130dc0cab0ba7ff5a3eee53dd110e51d6541e。更新前核两个实例无活动/排队/待确认作业及原表单空闲，正常启停后配置摘要、runtimeId、身份、消息记录保留且在线；原表单完整回读仍revision27/draft，与更新前完全相同。dist接入包已同步，九问公开资源读取已核新版三段状态展示标记；未执行验收批准或付款。

九问可见复核：原聊天重载新版资源后，仅允许一次ioa_get_form只读刷新；原表单明确显示“等待选择下一步”“第1期验收检查已完成，全部通过。”“合同进度：尚未确认接受交付；尚未付款。”及独立确认说明。原始聊天中的历史草稿文字不修改。未触发新验收或付款。


## 双仓整合与原生提交契约衔接（2026-11-17，未提交）

本轮在fork `32f0b0d`（含origin `f6fc489`）上整合本地MCP Apps及设备会话改动，完整基线、产物与实测结果见[客户端同步整合](客户端同步整合.md#pr1二次整合2026-11-17)。保留远程本仓A2C、S01—S05宿主契约、HostRegistry、原生入口与材料恢复，不恢复旧HostTurn/HostResult/run公开执行链。CLI和MCP复用ClientOperations，并将contextRef送入原受控IPC；原生Skill仍由宿主包装核验来源。

MCP/浏览器表单不能假称知道宿主聊天SID。公共表单owner在原持久表单回调期间建立内部来源；新委托冻结该表单引用和默认宿主，私人交互记录应用来源，后续沿原业务和宿主恢复。该来源不接受工具参数，不给模型增加批准能力；独立用户确认及原生来源拒绝测试均保留。规则唯一归属[交互表单](../../组件/客户端/共用运行时/接口/交互表单.md#与原生会话来源的衔接)。

账号继续复用现有设备凭据和接入识别；远程双角色新账号准备沿同一Onboarding，服务方仍需显式发布/开启接单。远程原对象硬有效期、软响应截止及nullable确认期限保留，MCP测试按实际业务期限验证。九问报价参数提示与已知终态无报价恢复迁入S01；显式截止保留有界传输等待，未指定业务截止不额外缩短，未知结果保留原requestId，不把超时伪造为未执行。

本轮回归覆盖接入、私人交互和委托交易的公共协议与隔离页面；此前真人单期文本交付/付款结清属于整合前版本证据。没有新建实际业务、代用户批准或付款，也没有覆盖原配置、升级活动安装或重启实例。新候选原生三宿主及最新账号内嵌完整链路仍需单独验收，四张MCP票继续开放。
