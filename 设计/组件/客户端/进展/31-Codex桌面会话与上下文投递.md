# 31：Codex桌面原生会话与统一提交

**阻塞于：** 27、28、33公共基础切片

2026-11-17交接：本票适配/安装切片已纳入develop提交`886df55`；下文“未提交”描述为原检查时点，不代表当前Git状态。原生、资源及完整验收仍按未勾选项保留。

**状态：** claimed（2026-11-17：路线A切片A1—A7代码已实现并经假边界定向检查通过（temp/checks/t31-201-r06真实exit0），真实桌面闭环（建聊天可见、排队消费、hook工具回调、原生审批）未验；P26资源映射未接，提交前resource_mapping/not_submitted，开发中，与当前设计有偏离；A8文档已追平实现页与接口页测试列，工程进展由协调者同步）。

## 最终目标与边界

以S01统一原聊天和外部每业务新聊天；原生包装自动取得真实ID。原生busy默认queue，不steer，不另开writer；实际桌面列表可见并能继续。执行定义/工具/文件与事件使用27/33中性类型，原生协议私有。

共同S01—S05、来源、模型名额及文件/回调契约只在[宿主提交](../共用运行时/接口/宿主提交.md)维护；[接口映射](../../../工程/专题/一句话接入与三宿主全流程/接口覆盖映射.md)列已有/待改/缺失。公共/适配解耦，仅公共目录面向Skill，原生机制和传输细节在适配内部，不新增公共准备方法或第二提交链。

## 保留成果与实证边界

2026-11-17路线调整：共享daemon方向静态核本机桌面local-daemon分支排除win32，隔离CLI启动亦因缺完整安装包失败；停止该方向。本轮改经官方桌面create_thread/send_message_to_thread；独立Python按正规继承上下文已创建真实探针SID，40秒后该实际turn机器completed，用户亲自确认桌面看见；同SID续发第二标记9秒后机器completed。此为桌面入口验证，不代IoA业务通过。适配实现、上下文接棒与最简定向检查进行中；客户端原位更新及新admin发信待执行。连接信息不含聊天历史或模型记忆，不改公共submit参数；[高可用40](../../../工程/专题/一句话接入与三宿主全流程/40-Codex桌面连接与投送高可用.md)另行跟踪。

2026-11-17共享daemon修复已由用户授权交给新的独立子智能体实施，优先Windows原生环境。先在隔离环境核实桌面和适配器连接同一执行实例的公开接缝，再按已证能力修改安装与运输；仅启动daemon、共用CODEX_HOME或队列受理均不算桌面执行通过。目标包括skills-install自动检测与复用/启动、明确智能体与用户待办及复验、IoA客户端停止或重启不停止共享daemon。主协调负责规范、版本与集成验收，原admin在途消息及原客户端保留不重投；若桌面连接入口仍不可用，准确保留未就绪和阻断证据，不用独立后台推理冒充成功。该项开发中，尚未实现或实机验收，不合main。

2026-11-17真实admin入站消息发现路线A执行缺口：接收客户端已持久收到并创建交互，适配台账显示新会话created/named、phase=queued、queueObserved=true，但requestId为空；公开read_thread可读取该持久SID，status=notLoaded、turns为空，桌面列表未出现。当前native_transport在自有stdio AppServer中thread/start后退出，再起自有stdio做queue/add；native_host仅轮询turns/list及queue/list，无加载/消费步骤。此前桌面聊天工具的新会话/发送成功、loaded原聊天queue消费和零模型创建三类证据不能互相代替；本轮未重发原消息或用桌面工具手工补发冒充产品成功。

根因属于Codex适配路线的能力缺口，而非Hook、账号或网络未接入。现行接口限定“仅桌面已加载聊天消费、适配不代加载”，实现与该限制一致，但无法兑现外部新业务自动处理目标；修复前需核真实桌面共享服务的公开加载/执行与审批归属机制，并裁定设计边界，不直接加入独立turn/start或第二writer。已核本机CLI0.169.0-alpha.11.1对应上游session_queue_commands.rs要求发现shared server避免separate server；queue --resume为在非embedded server上读取状态后对NotLoaded发thread/resume，不等于保证当前Desktop展示/执行。当前默认daemon查询失败只证明该默认连接不可用，不推断平台永久不支持。相关证据保持本机私有验收目录，真实业务仍待验。

2026-11-17 v208新聊天 `01a1e9c8-d2f5-7ad1-8489-ea024a1876a8`：两份Skill可发现，配置绑定及安装状态查询正常；普通沙箱配置读取拒绝。原生控制入口runtime.status进一步因SDK在校验request-stdin前读配置，早期失败operationId=null，被严格relay转为unknown／操作编号不匹配，遮蔽原错误。主协调沿原两份参数及原operationId，以获准权限只读重查均completed、同runtimeId `b2ae263e-0cb6-40be-ac6f-1f7cb9b34624`、平台已连接；Hook已有真实PreToolUse运行元数据，runtime.status本不采集source，不由此宣称Hook未加载或来源绑定成功。原生entry binding仍intent/unknown，业务未验。修复中：先完整验证native包，再在提交前失败中保原ID与原因；不放宽relay错ID拒绝、不把已提交未知误称未提交。

保留公开CLI queue与实际桌面原聊天排队消费的局部证据，及旧纯SDK生产/交付记录（17）。现有stdio后台、动态SDK工具或取消能力不自动证明实际桌面原生新会话/可继续/真实终态。

## 依赖与实施安排

27契约、28包装及33公共基础；原生完成后返回33完整集成。本票由独立owner分切片实施，检查与真实原生窗口由root串行授权；已完成的SDK检查及原生探针见下文，不能将旧阶段“只改规范/未运行”当当前事实。

## 拒绝与恢复

公共权限/回执修复已完成源实现及最简测试：先完整验证限长native envelope，再读取配置；只对已验证且原业务未提交的本地失败关联原ID及not_submitted，RPC后未知仍unknown。严格relay及三个适配均未放宽，Codex/九问/Claude真实固定wrapper共同验证，不按宿主名称或模型文字识别错误。11个不同case通过，真实原生入口绑定与业务继续待验；在原本地已配置客户端测试通过前不合main。

原request/queue关联、实际turn及终态逐项证明；queue消失/interrupted/null completedAt/取消受理均不猜结束。原生权限请求仍原用户通道或SDK受控路径，不自动拒绝全部，也不绕确认。

S02仅运行期；S03/S04/S05按真实支持分别声明。断线unknown沿原请求查，文件失败不重产；四流程与跨宿主由36。

## 验收与交接

真实候选bfd7e61已原位安装，原账号/两身份/runtimeId/配置摘要及文件SHA256均保持。原setup台账报告migration_required，列高可用40；不手改台账，正常原生inbox查询成功并刷新连接。新admin消息实际创建桌面新业务并完成模型轮次，已跨过notLoaded，但首轮SDK回调在提交前binding文件/交接访问阶段失败；规则亦缺准确所有者转发目标，尚无admin成功回执。发现真实官方机器delegation对input的&/<>做XML文本转义，旧预期未转义导致无法相关；已修精确转义并保严格唯一关联，补提交前权限阶段/正规访问申请指引。收件SDK前缀错误限制全部工具，按既有v205规范改为仅限定SDK业务效果，原生工具权限仍归宿主。修复及规则澄清后另发新验收消息，不重做前两条未知原消息。

v209候选实施完成：官方MCP运输、真实hook上下文刷新、完整机器delegation精确关联与SDK回调已落盘，旧独立AppServer运输及专属测试已删除。最简检查r1四轻量通过、128通过/1新夹具字段错误；r2修字段后抢跑回调通过，但正常新业务用例因2秒墙钟预算在本机负载下过期；只将正常夹具预算改30秒，并补实际发送前原期限重核，r3三相关用例全通过（exit0），未变输入复用r1。不运行fast/full。原客户端升级、新admin完整业务、材料与原生审批仍待现场验证，未合main。

- [ ] 目标契约所需改动已实现，旧入口分叉/错误前置已移除，保原数据/原操作/期限
- [ ] 正常路径经公开入口定向验证；上述拒绝、竞争和恢复用例逐项记录
- [ ] 自动化、真实原生证据和未验项分开；历史结果不冒充本轮复测
- [ ] 本地提交、发布与实机验收分别登记；全部条件满足后才收票

## 2026-11-17 显式SDK切片（已定向检查，非整票验收）

- root已释放本票源码窗口；显式SDK驱动已改S01 submit/中性事件与工具、静态registration、运行期check/get_request/cancel；不再公开run/probe/capabilities或依赖公共HostTurn/HostResult。模型/命令/SDK受限权限保留，真实SID/request先P28确认再允许SDK工具；取消受理与机器终态分开。
- 已迁定向测试到S01原操作、原真实turn、schema修正/回调未知、P28拒绝、取消、期限与资源拒绝接缝。最终显式SDK test_codex_host.py全34例由27统一core17实际通过（总135passed及4light），报告temp/checks/ticket27-198-core17。这不证明随后新增native文件已检查。
- 当前仅无resources SDK切片；P26原冻结专业与材料reader/store_output工厂尚需接入，真实资源暂在原生提交前准确resource_mapping/not_submitted，保留原ref，不读latest技能目录；不能宣称原专业技能完整迁移。原冻结覆盖仍在temp/ticket31/197/baseline保留并待共享接缝恢复。
- 原生product factory及P07完整静态资源、实际来源包装、busy queue/new业务desktop会话、文件回收与受控审批仍待实现；旧prepare/run/deliver候选已作废，不作为新S01验收。修复路线见temp/ticket31/197/HANDOFF.md。

### 首次检查前代码审查修复（已随SDK定向检查通过）

- 业务模型等待仅使用原execution.deadline，无原截止时不再额外5分钟截断；适配私有transport支持None等待，握手/提交受理仍分阶段有界。
- turn/start回包前事件仅有界暂存，回包真实request确立后先核全部early turn引用再P28精确SID/request ACK，之后才处理工具；turn/started不再替换request。增加早到正确/错误/混合turn边界用例。
- 已获机器终态不因on_event异常改写unknown，另存事件回执错误；execution actor/profile、绝对workspace及未支持proposal_schema在提交前核实。静态readonly_context=isolated_session。
- 定向用例已补，新增本票自有wire假进程tests/_codex_submission_process.py；原tests/_codex_process.py未动。上述SDK34用例与以下新增native20用例已通过单独统一窗口，真实原生模型仍另行验收。

## 2026-11-17 原生真实证据与当前私有组件

- 正常ROG HOME的公开CLI/AppServer旧两轮及桌面原列表/历史可读、loaded-idle queue真实消费证据继续保留；外国独立reader曾在实际inProgress时报告interrupted/completedAt=null，不能作终态。队列clientUserMessageId并非服务端幂等，unknown不重投；qid消失/ACK文字均不证明完成。
- 默认公开proxy P0真实连接失败（os error10050），未启动daemon或fallback；仅说明本次默认proxy不可用，不推断Windows永久不支持。
- 新zero原生SID01a1e6b7-d942-7041-854b-c76ec37961f2在正常HOME、不覆盖原生model/工具/权限下thread/start(ephemeral=false)+name/set，ownedPID39792 EOF退出0。root官方App随后读到同标题/cwd、hostId=local、notLoaded、turnCount0，证明退出后的持久原生历史可读；未证明GUI、模型完成或人工接手。证据temp/ticket31/198/new-thread-zero/{result,root-app-read}.json。
- f88两轮探针实际仅提交FIRST：新SID01a1e6db-9dbb-7060-8e2e-d19655822c18，真实RPC turn01a1e6db-9f83-7170-8caf-e6335f1cafd0受理/inProgress，第二次turns/list被脚本client guard拒绝，EOF后root读到interrupted且结束时间非空。queue/add=0；不是模型完成。旧报告缺触发结构，具体缺字段或错client根因未证，保留原产物不重用SID。
- 公开schema允许userMessage.clientId缺失/null。新机器解析器对缺字段继续等；FIRST以实际RPC turn.id+同writer匹配结束事件+持久非空结束时间关联，若client出现必须匹配；SECOND仍严格qid/client→唯一turn，不能以文字/序号/最新会话补因果。v2新增自有turn结构诊断，未运行新模型，原报告不改。
- 正式新增native_transport.py与native_facts.py私有组件：正常原HOME/原权限stdio新writer、一次创建、真实request/有界early包与精确P28 ACK、同ownedSID queue、未知不重发、只关闭自有child；transport仅增加可选env输入，SDK默认行为不变。不新增公共host方法，不注册为已ready的产品native factory。
- 统一runner本次真实exit0：新transport8例+parser12例+原SDK34例，共54passed（pytest6.01秒），原4light均exit0；报告temp/checks/ticket31-198-native1含逐项日志/XML。1005项源码/测试/依赖/设计输入前后哈希一致（check-native1-before/after/summary.json）。仅假协议边界确定性检查，未启动真实CodexCLI/AppServer或模型，不证明桌面工具、GUI/原生业务闭环。当前这次两页事实更新发生在runner退出之后，不冒充原1005输入已覆盖新文档字节。v2新模型探针仍未运行，f88失败事实保留。
- 工具能力分开核：AppServer builtin、原配置MCP/Skill/plugin与桌面frontend注入连接器并非同一证据；无覆盖不等于全部原生工具均可用，nativeToolsEnabled=true不能缩成仅dynamic SDK工具。完整source hook（allow仍待用户选择）、业务S01装配、P26、原生审批、GUI和用户同SID交接仍未完成。已有用户SID禁止第二writer/resume，后续queue-only；产品自动安全交接仍待验。

## 2026-11-17 恢复后的固定入口与setup候选

用户已批准D31固定入口窄授权；`temp/checks/t31-199-r2/report.json`记录当时119确定性用例及4light通过，不能证明真实hook已安装/信任/命中。之后本轮新增`native_adapter`产品身份资源、原SDK显式setup/claim/OS保存/ACK恢复候选，业务S01仍准确返回`desktop_transport/not_submitted`且不启动独立stdio替代。新增候选已冻结，`temp/ticket31/199/setup-candidate-manifest.json`保存123项源码/测试/依赖摘要；root接棒检查5份Codex定向测试，当前尚未运行，不能沿用119通过作为新输入结果。

固定setup仍使用原P07 `setupOperation`及空业务参数，原args-file保存operationId；原SDK意图后才定位实际CLI并保存原claim。官方hook stdin.cwd可能来自模型指定tool workdir，已改由实际hook进程cwd取得原项目并增加拒绝错误项目来源的用例，待验。来源信任范围为已安装/启用的原生hook及适配器OS入口；不宣称抵御同一OS账号任意代码，手工伪造stdin不得用于原生验收。

默认公开proxy和daemon version只读复核均实际exit1/OS10050、0模型、未改配置或启动daemon；不能据此断言Windows不支持或daemon不存在。只读进程事实显示当前Desktop app-server无显式`--listen`，尚无已证公共桌面连接。官方新聊天deep link仅填composer，不自动发送；不能代替外部新业务自动submit。原生桌面审批、工具/文件及真实setup仍待验，详细差异及原链接保存于`temp/ticket31/199/DESKTOP-CONNECTION-DECISION.md`。

## 2026-11-17 固定入口hook（设计199，D31）

- 已实现（适配器私有）：native_hook.py（严格命令解析+PreToolUse应答+一次性handoff+摘要审计）、native_entry.py（领取handoff→ensure→P10登记真实SID/cwd/toolUseId→公共caller contextRef）、native_install.py（只写CODEX_HOME/hooks.json本组与适配器hook.json，不写config.toml/信任）、resources/native两角色入口与README、native_skills.resource_files（P07）、pyproject包数据。不新增公共参数或宿主方法；setup（runtime.entry.bind）在本构建明确not_ready。
- 合同依据为同版上游公开源码与本机二进制schema：Bash工具input仅{command}；allow只是改写的前提，此后仍走原审批/沙箱；Windows hook经cmd.exe /C；未信任用户hook不运行。证据temp/ticket31/199/APPROVAL-BRIDGE-FACTS-199.md、upstream/。
- 定向检查temp/checks/t31-199-r2真实exit0：119 passed（新65：精确匹配、37种扩张/相似拒绝、元数据缺失、进程不阻断、审计无正文、P10真实来源与caller、单次/挪用/过期/未就绪/错适配器/未知回执、安装合并与还原、P07导出；原54），4light通过。r1含共享test_client_onboarding另有4项失败，落在公共skills.entry_summary与九问resource_files，非本包。
- 本机：temp真实cmd.exe/PowerShell干跑通过（dryrun-result.json、dryrun-audit.jsonl）。随后在协调窗口安装验证版：~/.codex/hooks.json（新建，sha256 fd06c87b…543c）与temp验证hook.json（18a6b531…692e），config.toml前后一致；回执install-receipt.json。绑定的是temp合成非秘密binding（本机无已装客户端/技能），只能验证hook段。
- [x] 用户在桌面信任hook并实测（真实SID命中、近似不改、入口停在ensure_client），随后卸载（第三次重测达成，见下节r3）。首轮（线程01a1e79b…）hook已信任并实际运行4次，但第2、3步命令带引用符无效且未留audit，需按面包屑版重测
- [x] 最小native_adapter：客户端可以codex登记装配，S01准确not_submitted、check=unknown不触发600秒退出（确定性用例）
- [ ] 真实客户端以codex登记启动后，经hook→P10→公共caller的真实闭环（未运行）
- [ ] 显式setup在真实Codex中经hook取得原项目/CLI并保存、ACK（未运行）
- [x] 原生审批桥方案已定：独立stdio server的审批不能到桌面UI，用户选定路线A（D32），实施见下节

## 2026-11-17 合并核对（31 owner）

- 并行的Codex桌面会话（rollout 10-39-25-01a1e75f）对本包的改动经抽取apply_patch核对（temp/ticket31/reconcile199/）：与hook/entry/install不冲突，双方成果均在当前文件中。保留：setupOperation同形匹配、hook改用实际进程cwd（上游证实stdin.cwd可为模型workdir）、native_host/native_setup/native_process最小factory与setup接线。修正：native_process读回校验adapterId误写`codex_code`。新增hook运行面包屑。
- 检查：temp/checks/t31-199-r4真实exit0，124 passed（hook/setup/transport/facts/host五文件），4light通过；面包屑改动后hook文件68例另行通过（r5见回报）。

## 2026-11-17 桌面hook shell兼容修正

- 第二次桌面实测：精确入口命令返回native_source/not_ready（hook未绑定）；hook事件只出现在第2、3轮且无运行面包屑。本地复现：PowerShell `-Command`下原命令ParserError退出，与现象一致；桌面实际hook shell的上游源码证据未取得（未证）。第1轮完全不触发hook仍未解释。
- 修正：native_install在Windows渲染`System32\cmd.exe --% /d /s /c "..."`，cmd与PowerShell 5.1/7下带空格与中文路径均实测执行原argv；危险字符拒绝。定向检查temp/checks/t31-199-r8真实exit0（五文件+4light）。
- [x] 重装hooks.json：native_install补同绑定旧写法原位替换（他绑定仍拒绝），经产品`install`重装，sha256 fd06c87b…543c→0e3facb0…2fec，仅本组，config.toml/适配器hook.json/CODEX_HOME列表不变；原件备份temp/ticket31/199/hooks.json.before-reinstall-r10。写入命令行经桌面pwsh `-Command`与`cmd /C`本地各一次exit0并写hookRun（no_opinion，无handoff），不是桌面证据。
- [x] 桌面hook真实命中（temp/ticket31/199/RETEST-RESULT-r3.md，会话01a1e818-c66d…）：用户重新信任后三句共6次工具调用，logs_2 6对hook事件与audit 6条hookRun一一对应；精确入口audit allow且thread_items实际命令附`--native-handoff`，输出waiting/not_ready/ensure_client（合成绑定无客户端，符合预期）；带extra不改写不allow，入口invalid_input/arguments；handoffs为空即一次领取。第1轮本次正常触发，上次第1轮不触发未重现、原因未解释。仅证hook段，真实客户端hook→P10→caller未验
- [x] 卸载验证hook：产品`native_install uninstall`，hooks.json卸载前0e3facb0…2fec，仅含本组，卸载后文件按原状删除（首装时由本安装创建）；~/.codex其他文件列表不变，config.toml未被安装器写入（Codex自身信任项留存，由Codex管理）。回执temp/ticket31/199/uninstall-receipt-r14.json，原件备份hooks.json.before-uninstall-r14
- [x] 常驻关闭：NativeSetup补`drain`（拒新控制、等原调用），resident关闭不再AttributeError；client_hosts的Codex常驻用例迁到仅`{type:codex}`登记，断言state=unknown/available=false且两角色绑定、停止干净（不再用_codex_process.py）
- 检查：temp/checks/t31-199-r10真实exit0（五文件135passed+4light）；r11迁移前该常驻用例exit1（复现）；r12真实exit0（五文件+常驻用例137passed+4light）。设计页事实修正后的轻量见r13。

## 设计v0.0.200路线A实施（D32/D37）

规则见[路线A公开能力条件](../Codex适配/接口.md#路线a公开能力条件)及[只读观察](../共用运行时/接口/宿主提交.md#只读观察)。下列“代码”勾选只表示已实现并经假边界（自有app-server、进程枚举、SDK回调、真实HostRegistry）定向检查；真实桌面项另列，未运行。检查：最终代码temp/checks/t31-201-r06真实exit0（Codex适配接口页测试列全部文件+facts/transport/setup，222 passed，4light通过）；中间r02同模块1例client_onboarding失败落在并行29改动中的文件，单独重跑与r03、r06均未复现。

- [x] A1 代码：native_desktop按setup所存CLI的安装位置只读进程映像，桌面server在即available（不看聊天），间隔内多次采样都不在unavailable，未setup/枚举失败unknown；常驻用例仍unknown（无setup）。本机零模型探针（只读映像，未起Codex、未写~/.codex）：桌面已升级为WindowsApps包OpenAI.Codex_26.1111.2228.0内app
esources\codex.exe（2个进程），旧AppData\Local\OpenAI\Codexin路径已不在，故安装位置按包族去版本匹配，check=available；temp/ticket31/201/desktop-enumeration-probe.json
- [x] A2 代码：session空时自有app-server零模型thread/start(ephemeral=false)+name/set，不发turn，关闭本PID后才P28 session ACK，ACK不符不排队；建聊天不明unknown、不另建
- [x] A3 代码：私有记录先持久clientUserMessageId再thread/queue/add（原SID或新SID）；受理只发accepted；JSON-RPC拒绝not_submitted，传输丢失unknown且同attempt不重投；不resume/turn/start/queue/start
- [x] A4 代码：watcher每轮新起只读reader（turns/list全items、queue/list），client→唯一turn才P28精确(SID,turn)ACK并记delivered；仅completed且completedAt非空发机器终态；interrupted/空completedAt/多turn/仅回复文字不终态；截止或排空即停
- [x] A5 代码：native_render把冻结定义、摘要、工具声明、截止与输入渲染进排队正文（不含grant/descriptor）；SDK工具经固定入口`<角色>.native.tool`+窄hook→native-control→适配核SID/turn（未关联则现场关联）后才on_tool，同callId重放不重复效果
- [x] A6 代码：静态登记readonly_context=business_session，不声明native_tool_restriction；nativeToolsEnabled=false的报价/复签审查/观察照常排队，正文只作提示并注明不受限（nativeToolsRestricted记录归27公共层）
- [x] A7 代码：optional_methods为空，无get_request/cancel方法
- [x] 零模型协议核对（未调用模型、未写~/.codex，列表前后一致）：桌面包内codex-cli 0.169.0-alpha.11.1可由外部进程启动（--version exit0）；generate-json-schema --experimental仍含thread/queue/add（clientUserMessageId必填）、queue/list、turns/list、name/set，queue方法只在experimental输出中，适配initialize已声明experimentalApi。证据temp/ticket31/201/（version-probe.json、schema-0.169-experimental/）
- [ ] A8 收口：实现页、接口页测试列（submit/check加codex_native_desktop）与本票已追平；工程进展待协调者同步
- [ ] 真实桌面P2：建聊天在列表可见、打开后消费、hook工具回调到P40、原生审批只在桌面UI、报价/观察排入；步骤见交接回报（未运行）
- [ ] 自有app-server的thread/queue/add是否只入持久队列而不自行消费、未加载聊天何时消费（未证，P2判定）
- [ ] P26资源映射与工具产出资源回收（未接，提交前resource_mapping）

已知风险（未验）：business_session观察排在同一聊天当前轮之后，若嵌套在该轮SDK工具回调中可能等到观察截止仍unknown；常驻重启后watcher与工具作用域不恢复，原receipt保持非终态（无S04）。


## v205主路线接棒（D44—D46）

开发中，与当前设计有偏离：专业能力完全复用用户宿主配置，保原skills名称/业务摘要，移除IoA专业文件冻结门禁；输入材料与成果继续真实文件交接。公共所有阶段nativeToolsEnabled=true，三适配原生及显式SDK均不限制宿主工具，去除禁用prompt/强制空工具/只读审批参数；SDK受保护效果仍核原许可。旧false轮次测试及先读专业文件门禁不再作为本期验收目标，必须同步代码和对应测试，不能只改文字。

- [ ] 新专业能力/真实材料边界定向通过，保旧合同摘要及原引用。
- [ ] 公共报价/复签/生产/私人/观察和三宿主两类入口均传允许并继承原工具审批，SDK效果无越权。
- [ ] 最终集成回归与真实宿主验收分别记录。


### v205实施检查点（2026-11-17）

本期实现已落盘：三适配不限制原工具/Skill/权限；公共各阶段允许且nativeTools为空，不下发原定义deny；capabilityReferences保名称；本机材料落盘/原成果出口，九问原生多SDK工具和Process原工具继承。服务skills名称不阻塞ready，uncheckedSkills明确未核实。

资源定向r4四轻量+59通过/1跳过、r5四轻量+5通过/1跳过均exit0（temp/checks/native-route-v205-r4、r5）；原r2的单个scope夹具错误已修但失败报告保留。两显式SDK89项与四轻量通过由主协调保存报告。最终完整回归正在独立运行，本检查点未声称full通过；按用户明确指令，与合main/GitCode及服务器内测并行收集问题。真实宿主四流程、跨机器HostResource映射与原审批桥支持范围仍按未勾选项待验。
