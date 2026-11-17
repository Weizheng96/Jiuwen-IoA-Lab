# 32：Windows九问原生会话与统一提交

**阻塞于：** 27、28、33公共基础切片

2026-11-17交接：本票适配/安装切片已纳入develop提交`2892e4b`；下文“未提交”描述为原检查时点，不代表当前Git状态。原生、资源及完整验收仍按未勾选项保留。

**状态：** claimed（2026-11-17；199最小原请求业务切片与202 D35/D37/D38适配侧已过定向检查、未提交，原生实机、文件/只读/网页可见、ASK公共接线与整票验收未完成）。

## 最终目标与边界

按S01内部对接真实user/channel/session和原execution/request归属，外部每业务新会话、后续同ID、原聊天busy队列；随包入口自动身份与必要Rail/ApplicationPlugin资源/重载步骤完整交付。

共同S01—S05、来源、模型名额及文件/回调契约只在[宿主提交](../共用运行时/接口/宿主提交.md)维护；[接口映射](../../../工程/专题/一句话接入与三宿主全流程/接口覆盖映射.md)列已有/待改/缺失。公共/适配解耦，仅公共目录面向Skill，原生机制和传输细节在适配内部，不新增公共准备方法或第二提交链。

## 保留成果与实证边界

保留公开Process/Web、原生应用扩展和私有工具回调基础；temp候选仅证明局部接缝。现有历史追加、runtime.accepted/chat.final/EOF及afterinvoke不证明原模型任务结束。

## 依赖与实施安排

27契约、28包装及33公共基础；原生完成后返回33完整集成。197正式实施已恢复，由本票独立owner持续接棒；检查窗口由根统一协调，不自行启动产品模型/原生服务或部署。

### 197显式SDK首片与请求归属修正

- core8联合检查真实退出0，九问原宿主测试13例及S01测试6例通过；报告 `temp/checks/ticket27-197-core8`。只证明该版显式SDK边界，不代表原生网页或整票验收。
- 根审后修正计划请求ID提前公开：只有原版协议精确回显原request与真实Session后，才公开request_id、建立查询索引并请求P28精确ACK；计划ID仅存私有日志。无echo断线保持unknown，不重投；发送前验证拒绝无公共request ID。新增四项对照测试，尚未运行，core8不作为这些新输入的通过证据。
- 静态核已装Process SDK及原生输入解析：空deadline透传asyncio.timeout(None)，没有默认300秒业务cap；等待退出／关闭回收的独立有限清理窗口不截短运行期。
- 原生入口／完整resource_files、资源映射、提案及真实原生可见会话仍未完成，不在本小片中声明支持。
- 199联合fast分诊修复（工作区，未提交）：九问`native_skills.resource_files`不再要求`profiles`非空（账号前或角色不唯一时公共P07给空profiles，与Codex/Claude一致；空绑定运行时entry明确native_entry_unavailable且not_submitted），并补拒绝sourceOperations重复及setupOperation混入；test_jiuwen_native加空profiles接受与两种拒绝3例。`temp/checks/t32-199-fix02`真实exit1：4light全0，65passed／1failed；jiuwen安装两步与skill_location[jiuwen]已过，余下三宿主同文用例因比较含宿主专属native/README.md而失败（Codex与Claude之间同样不同），归28公共导出/测试，非九问适配。

### 原生来源／静态资源当前检查点（未验证）

core9已验证计划/实际request修正后的两文件23例。后续新增冻结nativeToolsEnabled严格布尔消费及四项phase对照，尚未运行；不按business kind猜quote权限。

新增纯产品native_adapter及NativeJiuwenHost，与显式SDK ProcessHost分开；完整Rail/entry/README由P07 resource_files静态导出并列入package-data，不依赖S03。原生两角色包装仅转公共caller，route/SID/cwd由公开原生工具上下文与Runtime只读会话查询取得，controls不ensure/读grant，业务ensure后重读SDK描述并换entryRef。当前原生执行、setup claim/save/complete、外部会话创建／排队、资源和提案均准确未就绪，未安装/运行/测试原生资源；该检查点不代表接入或业务验收完成。

### Process专业资源桥检查点（待联合检查）

- core17已真实通过九问两文件27例，含冻结nativeToolsEnabled阶段消费；不作为新资源输入已通过证据。
- ProcessHost新增可选resource_factory(op,完整SubmissionAttempt)，显式SDK装配注入唯一SDK SubmissionResourcePort；按原引用调用reader取得bytes并核digest，先布局后提交，不重建专业快照或读latest。
- 专业读取工具及主文档实际读取证据仅由33 ProfessionalExecution/P40维护；Process只映射原声明/回调，不新增第二reader。本轮manifest随input，不改冻结agent；原SDK定义digest原样记录。
- callback文件映射失败保原业务回执及真机器终态并报collection错误；原生自动产出文件协议未落实，不解析普通模型文本路径代替产物。
- 新增test_jiuwen_resources四项接缝，并与33迁移后的整test_business_skills/ProfessionalExecution用例联合验；当前未运行，未启动模型／CLI或原生九问。

## 拒绝与恢复

### 198原生来源／setup／notice Runtime小片（待定向检查）

- 已实现静态bundle的worker资源和同SDK private-control运输，不增监听器/新公开host方法；Rail在原生loop保留真实Runtime，以公开route与describe_session核实际user/channel/SID及绝对cwd。
- 空参数runtime.entry.bind沿原caller建立intent；只有SDK trusted entry-control claim成功后才保存原生context与稳定receipt，再complete。普通来源无intent零绑定保存；保存失败或complete ACK丢失沿原obligation/claim/receipt恢复，不换归属。
- notice S01已接真实外部新Session及同SID后续原native lane；prepare本身会分配/写metadata，严格在原SDK claim之后；实际SID先保存，原request/execution/generation快照回显后才公开request并请求P28精确ACK。机器终态先私有持久，再报SDK事件；原快照缺失或断线unknown，不重建会话或重投。
- 专业定义/SDK工具本轮绑定仍待下一片，带HostExecution或资源的native S01准确pre-dispatch not_submitted；不把notice或文字echo算业务通过。已过显式SDK Process代码与测试保留。
- 新增test_jiuwen_native八项用例包含真实SDK ledger/RuntimeStore/trusted闭包、OS保存与ACK恢复、原请求归属/unknown/terminal，以及公开Runtime边界替身驱动真实worker创建后同SID续投。当前仅代码已落，未运行测试、native API、CLI、模型或部署；真实原生Web可见/可继续及SDKecho验收仍未完成。
- 根静态审后补保存绑定完整性：新写binding/obligation声明严格格式，恢复和旧receipt回用校验完整字段、bounded source/scope、host/account/install一致，并从原target内容重算digest；不补旧字段或兼容回退。新增合法重启、十一类损坏重启和同步改digest仍被实际SDKclaim拒绝的对照，整原生文件现21个case，尚未运行。本片不更改notice无SDK工具的边界；下一工具桥必须在原真实request的P28 bindingAck为真后才可入场。
- 后续统一runner真实退出0：报告`temp/checks/ticket32-198-native-integrity`，4light全0，原生21+显式SDK/资源31+业务Skill非模型10共62passed、原真实模型节点1skipped；857正式输入前后摘要无变。原生Runtime为公开边界替身，SDK ledger/私有控制闭包/OS receipt真实；未运行Swarm/CLI/native用户会话/模型，未据此勾整票原生验收。检查后仅同步本行事实，SDKecho/frozen定义桥继续准备。

只用公开扩展/API，不修改九问源码或私有历史库；原请求snapshot必须绑定实际request，不按parent/root猜测。缺原ID/事件/资源准确失败，不以追加历史冒处理。

双向文件、工具前绑定、SDK回调与原生工具保实际权限；S02及可选S03/04/05各自实证。恢复原快照/ID，未知不重投。

### 199最小原请求业务切片

已完成（定向检查`temp/checks/t32-199-r10`真实退出0，4light全0，98passed/1原模型opt-in skipped；原生Agent/ctx/Runtime为边界替身，SDK ledger/RuntimeStore/OS receipt真实）：

- worker的`await turn.finish()`已有Preparation包装：未派发或原请求终态即卸载自有LoadRecord；派发后未知保留惰性guard，至原invocation结束再清理。
- 严格布尔：execution与本请求守卫均以`nativeToolsEnabled is True`消费，非布尔拒绝，缺字段不开原生工具。
- 非空deadline按原值映射：无时区/已过期提交前not_submitted；运行中在model/tool hook边界结束，SDK回调等待ACK也受此期限，不加固定cap。
- preflight零派发后，SDK新attempt复用已报告创建的SID；journal指纹只含原operation范围不含nonce；已受理/未知/终态换nonce准确id_conflict；创建未报告即报unknown不允许重建。
- 回调带文件时保实际value与原效果回执，报`callback_resources`收集错误至终态，不重跑回调。
- 最小链：IoAEntryRail传prepare_turn，worker声明request_turn；send前公开prepare_session/get_live_session_instance取实际per-SID Agent，仅collision核与预留并挂本请求IoAExecutionRail；原lane before_invoke精确核SID/request/Agent后装冻结section/工具，再核owner；安装失败首个model hook force_finish（accepted/unknown，非未提交）；false时普通工具改到自有拒绝LocalFunction（202按D37已去除），SDK回调须P28 bindingAck为True且带原call ID；tripwire防在无guard Agent上运行。
- 原生扩展加载器（0.2.8 RailManager）实例化rail.py中首个`class *Rail(AgentRail)`：IoAEntryRail须在IoAExecutionRail之前，已加静态对照。
- 新增/改写：turn边界11例、原生S01/传输6例（含原request→P28→SDK echo→机器终态与守卫清理的整链替身）。
- 原生窗口实证：方案及开窗清单见`temp/ticket32/199-native/NATIVE-PROOF-PLAN.md`，用户已批准预算（预计6～7次LLM，硬上限13）。P0证据日志（仅阶段名/ID/时间戳/布尔，标记文件开启，默认关闭）与P1驱动`temp/ticket32/199-native/proof/`已实现，定向检查`temp/checks/t32-199-r8`退出0（98passed/1skip）；SDK侧干跑仅起本机IoA IPC，自有PID已退出。窗口未开，待27收口与联合回归后另定。
- 并入另一会话（Codex，已停止）同期补的修复并经r10复核：准备阶段改用原公开`get_agent_for_request`按实际mode/work_mode选Agent（admit_request为空，不登记请求、不派发），不再拆mode字符串；入口工具token按每个工具ctx保存，并行调用不共享extra；Preparation.failure经snapshot携guardError，真实machine completed保留且同时报`native_guard_failed`（accepted），SDK事件回报失败只追加说明不覆盖；派生known-request索引使重启后get_request只恢复可核原请求，缺失/损坏/身份不符为None，非终态快照恢复为unknown。对应Rail边界5例、晚失败2例、恢复8例、Preparation透传1例。
- 真实库零模型探针（协调者批准，单次）：首跑`temp/ticket32/resume-native/public-sdk-proof-20261117-6c7b2d19`因探针自身ToolCall缺`type`在执行前exit1；补`type`后（探针SHA `6403df66…`，仅此差异）以r10资源原样重跑`public-sdk-proof-r2-20261117T041537Z-e43cd5d5`：已装0.2.8、`-I -B`、隔离环境、0模型/0会话/0服务，PID 36328自然退出，exit 1，无result。按断言顺序已在真实库通过：r10 rail.py加载并以真实PromptSection/ToolCard/LocalFunction/ToolInfo挂守卫；BEFORE_INVOKE安装、BEFORE_MODEL_CALL窗口仅SDK工具；真实AbilityManager.execute经ToolCallInputs改写后原工具body未执行、返回native_tool_not_authorized、无force_finish且守卫仍在；随后SDK工具在ACK前等待、accept后恰一次回调并带原call ID；AFTER_INVOKE清除section与SDK工具。失败于最后一步：守卫清理后以同一route再调原生工具，body未执行（探针期望执行）。按代码推断原因是探针在finally前未卸载守卫Rail，同route工具调用遇已关闭turn即fail-closed（tool_blocked）；真实lane中请求结束后同route不再有工具调用且Preparation会卸载守卫，但"清理后普通工具继续"本次未获真实证据，未复跑。

待验/未完成（不以上述结果收票）：

- rail.py未在本仓导入执行（.venv无jiuwenswarm）；send_session_message实际lane所用Agent与prepare所得per-SID Agent是否同一、权限rail对改写后拒绝工具的交互、真实ToolCallInputs改写零效果与后续工具继续，须原生窗口实证。
- 进行中的模型调用不被deadline打断；SDK回调经15秒native-control HTTP，长受保护确认会超时。
- P26文件、proposals、readonly观察、原生Web可见/用户继续及S05均未接。

建议原生窗口（202版方案`temp/ticket32/202-native/NATIVE-PROOF-PLAN.md`，未执行）：新建明确测试实例与会话，安装本资源，以domain-free echo执行一条HostExecution（false与true各一），记录实际Agent身份、section/工具出现与清理、P28前回调等待、机器终态；按D37本版不验证原生工具限制，false轮只核照常提交、记录nativeToolsRestricted=false、原生工具不被改道或拒绝；不复用或停止用户九问进程。

## 验收与交接

- [ ] 目标契约所需改动已实现，旧入口分叉/错误前置已移除，保原数据/原操作/期限
- [ ] 正常路径经公开入口定向验证；上述拒绝、竞争和恢复用例逐项记录
- [ ] 自动化、真实原生证据和未验项分开；历史结果不冒充本轮复测
- [ ] 本地提交、发布与实机验收分别登记；全部条件满足后才收票

## 设计v0.0.200/v0.0.201（D33/D35/D37）

202工作区已改、未提交；定向检查`temp/checks/t32-202-r06`真实退出0（4light全0，114passed/1原模型opt-in skipped；九问Process/原生Runtime为边界替身，未起九问进程、未调模型）。

- [x] 不声明`native_tool_restriction`（Process与原生两登记均为默认false）；false轮照常提交、不限制：原生turn.py去除模型窗口过滤与ToolCallInputs改到自有拒绝LocalFunction（原偏离已修），ProcessHost不再把false/observation轮的nativeTools改为deny，按原配置级别下发；冻结布尔仍严格解析并写私有记录与证据。nativeToolsRestricted由公共层按登记记录，本适配不写。
- [x] proposal_schema可选（D35）：Process与原生入口在无结构化提案通道时，仅当公共`proposal_tool_declared(execution)`为真（本轮SDK工具input_schema等于proposal_schema）才提交、proposal为空；否则提交前not_submitted。同键只生效一次归27/33公共层，适配不重复实现。依赖27/33工作区未提交的该公共函数。
- [ ] 真实原生窗口：false轮照常提交、原生工具不被改道或拒绝（方案`temp/ticket32/202-native/NATIVE-PROOF-PLAN.md`，未执行）。

## 设计v0.0.202（D38）

- [x] `ProcessHost.check`：不起九问进程、不推理；command可执行、data_dir可列、`config/config.yaml`可打开（不读内容）为available，有依据缺失/无权限为unavailable，本适配有S01执行为busy，5秒自检未完或其他OS错误为unknown（同上检查）。
- [x] 原生check按worker心跳窗口（10秒，主协调者2026-11-17裁定归类）：窗内有已绑定worker为available；传输未开始、启动宽限、已绑定worker超窗、仅有未绑定worker轮询、整窗无心跳（插件未加载、重载后未再调用入口）均为unknown且不累计；unavailable只用于确证九问宿主未运行，当前传输无此依据，故本版原生check不报unavailable。代码依据：worker只在原生会话调用IoA入口后创建，Rail uninit关闭且重载后不自动重建，SDK侧无法区分重载与宿主停止；若报unavailable，九问重启后用户长时间未调用入口会使客户端持续暂停乃至排空退出。
- [ ] 原生窗口实测：插件未加载/重载中保持unknown、绑定后available；确证宿主未运行的无模型依据（如可核的宿主进程标识）待后续另定。

## 原生ASK接线（34准备者发现，202核实）

- 核实：src中无生产代码传入on_interaction；`questions.native_answer`只被`tests/submission_fixture.py`调用。S01五接口无可信用户答复通道，适配拿不到runtime/actor/job，无法自行接线，归公共层（27/33设计S01中性“等待输入”事件或回调并接native_answer）。
- 适配侧已修：Process原生ASK不作答（也不代选reject），记`native_interaction_unanswered`/unknown，原请求不重投；此前同情形只报host_unknown。D37后false轮也保留ask级权限，原生ASK出现面随之扩大。原生常驻入口的ASK在用户自己的九问网页中呈现、由用户原生作答，不经SDK；是否如此待原生窗口观察。
