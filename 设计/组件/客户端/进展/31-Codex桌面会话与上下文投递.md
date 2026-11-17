# 31：Codex桌面原生会话与统一提交

**阻塞于：** 27、28、33公共基础切片

**状态：** claimed（2026-11-17：D31固定入口hook、显式setup与最小native_adapter（S01准确not_submitted、check=unknown）已合并并定向通过；桌面hook已真实命中（r3：精确入口allow并附一次性handoff，入口停在ensure_client；近似命令不改），验证hook已经产品uninstall卸载；原生审批桥已由用户裁定走路线A（D32，设计v0.0.200），路线A切片A1—A8均未实施；桌面业务S01/工具/审批/P26未完成，开发中，与当前设计有偏离）。

## 最终目标与边界

以S01统一原聊天和外部每业务新聊天；原生包装自动取得真实ID。原生busy默认queue，不steer，不另开writer；实际桌面列表可见并能继续。执行定义/工具/文件与事件使用27/33中性类型，原生协议私有。

共同S01—S05、来源、模型名额及文件/回调契约只在[宿主提交](../共用运行时/接口/宿主提交.md)维护；[接口映射](../../../工程/专题/一句话接入与三宿主全流程/接口覆盖映射.md)列已有/待改/缺失。公共/适配解耦，仅公共目录面向Skill，原生机制和传输细节在适配内部，不新增公共准备方法或第二提交链。

## 保留成果与实证边界

保留公开CLI queue与实际桌面原聊天排队消费的局部证据，及旧纯SDK生产/交付记录（17）。现有stdio后台、动态SDK工具或取消能力不自动证明实际桌面原生新会话/可继续/真实终态。

## 依赖与实施安排

27契约、28包装及33公共基础；原生完成后返回33完整集成。本票由独立owner分切片实施，检查与真实原生窗口由root串行授权；已完成的SDK检查及原生探针见下文，不能将旧阶段“只改规范/未运行”当当前事实。

## 拒绝与恢复

原request/queue关联、实际turn及终态逐项证明；queue消失/interrupted/null completedAt/取消受理均不猜结束。原生权限请求仍原用户通道或SDK受控路径，不自动拒绝全部，也不绕确认。

S02仅运行期；S03/S04/S05按真实支持分别声明。断线unknown沿原请求查，文件失败不重产；四流程与跨宿主由36。

## 验收与交接

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

## 设计v0.0.200路线A待实施（D32/D33）

规则见[路线A公开能力条件](../Codex适配/接口.md#路线a公开能力条件)及[只读观察](../共用运行时/接口/宿主提交.md#只读观察)；切片草案及定向集合在`temp/ticket31/199/route-a/SLICES.md`。以下均未实施，现有最小native_adapter的`desktop_transport/not_submitted`与`check=unknown`即本票与当前设计的偏离。

- [ ] A1 桌面定位与S02：本CODEX_HOME桌面server在即available（聊天未加载不算unavailable），持续确认不到unavailable，枚举失败unknown
- [ ] A2 外部新业务零模型建持久聊天（ephemeral=false、命名、不发turn），自有进程退出、P28绑定真实SID后再排队
- [ ] A3 排队提交：原SID或新SID经公开队列带原clientUserMessageId，受理只算accepted，unknown只查不重投
- [ ] A4 关联与终态：queue→clientId→唯一turn，仅completed且completedAt非空为机器终态
- [ ] A5 冻结定义、工具声明与受控引用渲染进排队正文，不含秘密；SDK工具只经固定入口+窄hook
- [ ] A6 静态登记不声明`native_tool_restriction`（本版三适配均不提供，D37）、readonly_context=business_session；报价、复签审查、观察照常排入并记录`nativeToolsRestricted=false`（登记字段由27提供）
- [ ] A7 S04、S05不出现在静态登记，公共层保unknown／等自然结束
- [ ] A8 收口：实现页、接口页测试列及本票、工程进展追平
