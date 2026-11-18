# 29：一句话安装配置启动与Skill接入

**阻塞于：** 27、28

2026-11-17交接：本票适配/安装切片已纳入develop提交`53d95e9`；下文“未提交”描述为原检查时点，不代表当前Git状态。原生、资源及完整验收仍按未勾选项保留。

**状态：** claimed，开发中（207已正式发布，GitCode普通/ManagedPython隔离安装实测通过；干净新用户、原生加载与新聊天验收未完成）。
**负责范围：** P01/P02安装、P03复用与安全升级、P07/P08接入交付、P37脱敏摘要；消费公共配置和唯一启动入口。
**依赖：** 27公共／适配契约、28公共Skill及原生包装；30—32交付对应宿主的静态登记、实际入口及必要扩展资源。
**规范入口：** [共用运行时](../共用运行时/接口.md)、[安装与实例](../共用运行时/接口/安装与实例.md)、[接入交付](../共用运行时/接口/接入交付.md)。

## 九问原生扩展随接入安装（2026-11-18）

开发中，与当前设计有偏离。九问已有公开扩展接口，原接入只导出 Rail 和说明，导致用户要离开聊天手动导入并启用，之后智能体才能调用 `runtime.entry.bind`。本次由既有 `skills-install` 把最终双角色包的共享 Rail 交给九问适配，以当前本机 WebChannel 的扩展管理接口安装并返回真实回执。已有同名或同类扩展先核归属。九问通过既有客户端控制先完成账号和宿主登记，启动后由当前真实入口绑定，观察真实绑定回执后再交付手动导入 Skill 的待办。启用、实际加载和原生绑定分别验收。

- [x] 隔离 WorkSwarm 0.2.8 已启动。网页返回 HTTP 200，公开模型列表和空扩展列表可查询。独立程序、数据、监听端口和 worker IPC 可与原实例并存。
- [x] 最终定向检查退出 0，四项轻量和 207 项测试通过（`temp/checks/jwo-final-1118`）。覆盖账号与宿主前置控制、扩展导入与恢复、有效 SDK 绑定回执后才提示补装、Codex/Claude 顺序及 Windows 启动器。阿里云短文本直连成功，隔离九问显示验收模型为默认。
- 快速层 2822 项通过、40 项跳过、1 项失败（`temp/checks/jwo-fast-1118`）。失败用例发现导出态回执缺少 `instructions`。已恢复这个字段并说明绑定后的处理顺序，最终定向复跑包含原失败用例。
- [ ] 从固定 develop 候选在隔离九问新会话提交一句话，智能体完成扩展导入、启用并在提示用户手工导入两份 Skill 之前取得原生绑定回执。用户无需进入扩展管理或再发一条接入指令。两份 Skill 的真实发现和新聊天复用同一客户端继续独立记录。

## Windows 安装解释器选择（2026-11-18）

首装及重建环境按 v0.0.217 的选择范围探测本机 CPython 3.11／3.12，没有合适版本时在开始构建前调用原 uv 路径准备私有 3.12。核验和补装宿主继续使用既有环境。

- [x] Windows 启动器默认过高用例修复前选中 3.13。最终定向 113 项通过（`temp/checks/bootstrap-python-policy-final-targeted`），覆盖高低版本、预发行版、并存版本、已有环境复用及重建。探测期间关闭宿主 Python 启动器自动安装，并在探测后恢复其进程设置。已实测 uv 0.9.21 能按 CPython 3.12 请求定位缓存的私有解释器。
- 快速层 2819 项通过、40 项跳过、1 项失败（`temp/checks/bootstrap-python-policy-final-fast`）。失败项是平台控制分发用例读取回调时的 SQLite 连接事务状态。平台相关源码和用例不在本次变更内，模块完整复跑 16 项通过（`temp/checks/bootstrap-python-policy-agent-delivery-rerun`）。
- [ ] 发布后在原失败环境复验实际下载、启动器安装和新聊天原生 Skill 加载。本轮证据限本机安装测试及隔离验证。

## 最终目标

### 本轮原生验收发现（2026-11-17）

**用户最新阶段确认：一句话接入已验收成功。** 下述权限/回执问题归已配置客户端的新会话调用与后续业务测试，不重新打开下载注册接入验收。后续会话直接交付本地已配置客户端、沿原账号测试；修复先最简定向检查及本地复验，通过后再合main，不再先合main再一句话接入。公共修复须同时适配Codex/九问/Claude Code；当前九问/Claude配置由用户接手，未因无模型测试而恢复它们。

本地候选复验：公共修复已提交并推develop `e73c635`（v0.0.208.1），未合main。经唯一bootstrap --upgrade在原 `C:/Users/ROG/.ioalab/client` 原位更新完成、exit0，实际wheel SHA256为 `1bcbb40a868b78d1df2a3b4fae614f799e4469e65c493737d5d005786ec0c47a`；原账号、两角色agentId、配置摘要及runtimeId保持不变。普通沙箱准确返回权限拒绝与恢复分类，获准访问后沿原两角色native状态参数/原operationId均completed且平台在线。原Codex测试聊天已恢复后续原生绑定/业务验证，不重新下载注册；真实九问/Claude模型业务未验。脱敏更新前后基线保存在temp/client-retest-2081。

v208新会话后续阻断：两角色runtime.binding/install.status正常，普通沙箱runtime.status因配置访问权限不足被误报invalid_input／UTF-8 JSON不可读；同一命令获准提升后completed，原账号认证及平台连接正常，排除配置损坏。onboarding捕获该失败后误给未注册账号待办。共用修复已实现：配置权限拒绝单独给明确原因及inspect_configuration_permissions恢复分类；摘要保accountName=null并给agent授权访问步骤，不生成注册待办，不改账号/权限。两轮最简定向8通过及4通过（摘要重验，11个不同case），均exit0；三真实宿主包装共同验证原回执保留、错/缺ID仍unknown。报告为temp/checks/20261117T141449Z-0c0f9888及20261117T141906Z-4461000c。现待原本地客户端候选更新和真实业务复验，不提前合main。

本轮修复已实现：skills-install回执附现有onboarding/1摘要与actor/verify步骤，区分当前智能体动作和用户动作，支持Markdown展示；登记给实际host/ref、type-only参数及configurationDigest来源，保已有默认；原生安装/setup给当前入口说明。Codex信任要求改未知null，移除固定“去设置审阅信任”指引。首轮118通过/2资源装配失败（exit1），仅修测试资源装配后两新增用例重跑2通过（exit0，74.43秒），合计覆盖120项；失败报告保留。本轮未解决账号页无响应观察差异，也未完成修复版正式来源接入。用户明确要求跳过剩余发布前检查直接同步：未执行本轮full、自检及实际打包验证，不以定向结果代替。

**已复现接入阻断：** 同一干净安装注册成功后，runtime.configuration为hostType=null、hostFields=[]，runtime.start返回not_ready／`Register a default host before starting`／recovery=configure_host。安装回执未给host-setup.md所要求的公开字段采集步骤，测试智能体未猜填模型/会话，也未使用旧配置绕过。Hook安装回执nativeTrustRequired=true、nativeLoaded=false，但用户确认设置中已出现Hook且未要求信任；不得把该字段当成实际用户待办。原始脱敏证据在新聊天工作目录 `outputs/acceptance-record.json`、`outputs/startup-receipt.json`、`outputs/native-hook-receipt.json`；开发与当前设计存在偏离，先核公共安装与Codex适配登记职责，补齐原定接入链及回执，拒绝增加第二启动链。修复和正式来源复验均未完成。

已核引导来源：Codex native_install.install无条件返回nativeTrustRequired=true，native_skills.manual_steps与随包native/README.md固定要求审阅信任，未依据实际宿主提示。该不实待办须修正；重载和实际加载分别留证，不能从文件存在推断。

Codex干净新聊天从GitCode `2d2de21`接入，用户完成本机账号页后反馈页面无响应；沿原请求查询已得到completed、新账号注册及在线认证成功、两角色身份刷新。未重复注册。当前只能确认页面反馈与后台结果存在观察差异，根因待查，不宣称注册失败或已修复。原生证据归[客户端36](36-Windows三宿主四流程统一验收.md#干净环境原生验收2026-11-17本轮进行中)，新聊天 `01a1e997-13e1-7781-9c12-0713e035f35b`。修复子智能体先核真实页面行为和客户端账号页响应/查询逻辑，确认根因后按正确规范修复；不得接触测试账号秘密或改本轮测试客户端。

Windows用户向Claude Code CLI、Codex桌面版或九问Swarm提供同一句README_AGENT接入指令，智能体自行取得客户端、完成公共配置与SDK账号交互，并启动或复用原实例。
宿主配置只选择静态已提供的适配器；命令、模型、认证、原生目录及连接机制由适配内部定位并复用原设置，不要求用户重新填写。
两份角色Skill及配套原生资源可自动或手动配置；用户得到当前宿主的真实安装结果、必要启用动作与未完成项。
接入后新聊天原生发现、真实ID自动注入及原实例复用由P09另行取证，不用文件存在或脚本执行替代。

## 保留成果与证据边界

- 保留192固定来源、vendor／依赖／RECORD校验、程序与账号数据分离、Windows私有MinGit／uv引导及Python≥3.11路径。
- 保留原固定commit补extra、原账号／默认登记复用、同home OS锁、失败现场保留、ACP936子Python显式UTF8及现有双Skill绑定成果。
- 保留SDK账号本机页、两角色准备、统一start／ensure-running和固定脱敏交付摘要，不另造安装器、账号或常驻启动链。
- `576b9b61a846feefb67e17f90a94613ca72b962f`（本地v0.0.192.1）：受影响定向89通过、离仓wheel1通过、fast1865通过／41跳过／518未选／14子测试通过，退出均0。
- 上述仅证明192对应输入；离仓模块路径及源码指纹已核对，不能用于证明新契约迁移、干净Windows或三宿主原生接入完成。
- 初次定向74通过、1次原生Skill冲突用例失败：第二角色未安装，原因未确立；补诊断后单点、扩大定向和最终fast通过，不将其记作根因已修复。

## 待迁移与实施切片

1. **P01/P02最小引导：** 消费适配包静态登记和资源引用，只校验宿主选项已提供；保留必要来源、依赖、文件及配置身份校验。
   Skill安装前即可通过公共入口完成配置与启动，不依赖尚未安装的Skill或可选安装方法才能取得必要资源。
2. **公共配置与启动：** 接入P05/P06及P11，不再让公共层收集command、model或宿主目录。
   清除接入、配置保存、start路径中的prepare／probe／S02 check前置；必要定位仍由适配内部完成，正常调用失败返回真实错误。
3. **P03原安装复用与安全升级：** 保留已有可用客户端的账号、身份、数据、默认登记和业务引用；重复接入、补宿主与明确升级分别处理。
   修改环境前经原生命周期停止活跃写入并取得原OS锁；未实际停止不得覆盖程序。安全升级仍是待实现／验证项，192的补extra不等于升级已完成。
4. **P07资源交付：** 每角色正文跨宿主相同，随包取得当前宿主原生入口、非秘密绑定、必要扩展及启用说明。
   静态登记及资源由P01/P07/P08消费，不新增运行期发现方法；必要资源不能只藏在S03的副作用中。
5. **P08自动／手动协调：** S03存在时优先自动安装并保留冲突；S03缺失时仍交付完整两Skill、入口、扩展和可直接使用的手动提示。
   提示必须代入真实资源及原客户端非秘密绑定；只说明当前宿主需要的重载、信任、授权或人工启用动作，准确保留未完项。
6. **P37真实摘要：** 分开报告环境／安装、账号、认证、平台连接、角色、Skill发现和反向能力。
   安装、启动或S03成功不合并称业务就绪；失败、取消和待人工操作也交付固定摘要及可执行恢复步骤，不含秘密或手抄会话ID。

## 实现边界与接棒

公共层不解析三套原生协议，不新增配置权威、宿主发现探针或第二启动器；必要安装完整性、账号、配置身份、原实例定位及OS锁继续保留。
适配内部复用既有宿主设置；无法定位时返回真实原因及当前宿主的准确恢复动作，不能要求用户默认重填原设置或猜最近会话。
P03复用原生命周期与业务数据；原生提交／反向执行由27、30—32及相关执行票负责，29不重写S01或业务引擎。
28交付公共正文及包装消费；29协调其资源安装。P09与28、30—32、36共同取证，四业务流程整体通过仍由36维护。
S02仅归运行期健康策略；本票不以检查成功作为配置或启动门禁，也不为P09强制付费推理。

## 正常／拒绝／恢复验收

- [ ] 正常：三宿主分别从一句话完成公共安装、账号交互、宿主选择及原实例启动；有效配置不重问，不重新注册或生成第二写入者。
- [ ] 正常：已有安装后装宿主复用原环境、账号及默认；配置仅选适配器，实际宿主设置由其内部复用。
- [ ] 正常：有S03可自动安装；无S03也取得同等完整资源及代入真实资源的手动提示，当前宿主重载／信任动作准确。
- [ ] 正常：配置保存与start不调用prepare／probe／check；宿主能力未知时按真实调用结果报告，不删除必要定位或完整性校验。
- [ ] 拒绝：来源／摘要／依赖不符、未知安装归属、配置身份冲突、OS锁被占时拒绝修改；原账号、数据、默认及在途引用不变。
- [ ] 拒绝：同名Skill、权限或组织策略阻断时保留原件并交付准确步骤；缺S03本身不是接入失败，缺必要资源不得假报完成。
- [ ] 恢复：断网、安装／补extra失败及明确Python不兼容保留日志和现场；按原引用继续，不对任意网络错误自动换解释器或来源。
- [ ] 恢复：明确升级在真实停止后替换程序，原账号／数据与未决业务可恢复；未完成的升级或资源启用保持待办，不换账号绕过。
- [ ] 摘要：成功、失败、取消、人工步骤均分别呈现真实状态与未完项，不泄露凭据；P09缺证据时保持原生待验。
- [ ] P09另验：新聊天原生发现两Skill，自动注入真实宿主／会话ID，复用原客户端／账号／runtimeId且不手填路径；反向能力独立留证。

## 当前状态与完成条件

192已实现并本地提交的成果继续保留；本轮票据重写不运行测试，不增加新的实现、发布或原生通过结论。
待27／28新契约及30—32随包资源接缝明确后，再继续上述迁移；不先按旧command／model、来源预检或启动探针契约扩代码。
干净Windows完整首次／追加接入、新旧账号、组织策略拒绝及三宿主原生新聊天仍未验；旧客户端安全升级也未因192测试转为已完成。
正式GitCode发布来源与开发候选分别记录，192历史结果不作为本轮复测；后续只经现有runner串行验证受影响接缝。
实现、提交、发布及原生验收分别更新；所有验收条件满足后才收票，并同步工程进展及36矩阵。

## 198统一打包资源修复（2026-11-17）

统一setup已复用组件pyproject的package-data及packages.find（where/include/exclude/namespaces）权威；不再复制宿主资源名单，通用资源模式限本组件，越界／重复包拒绝。隐藏Claude插件manifest与必要native资源进入wheel和RECORD。
首次定向r1失败：去掉旧硬编码后暴露统一发现未启用namespace，漏A2C guest两脚本；失败wheel／日志保留，未放宽独立A2C等价断言，也未补文件名单。
修正组件发现语义后r2：原离仓安装节点1通过，4项light全0（真实测试退出0，34.36秒）；521项build/test输入前后hash相同。22项声明资源逐字节、离仓importlib.resources真实安装位置及RECORD核验通过；A2C41文件与独立wheel集合及字节相等，SDK web/trust保留，Claude native6及九问native3齐全。
最终wheel SHA256为7870f186207a70dd871f5b9066095f2317d4f2bf7d99a01a7d518b0c25721577，产物版本仍186；这是当前冻结工作区的构建／安装证明，不是发布或原生业务验收。
本次未启动模型、平台、账号或native CLI；30原失败环境不动，其后须在全新私有root重装验native。29其余接入／安全升级／新聊天发现仍按上述未验范围保留，192历史证据不替代本轮输入。

## 设计v0.0.200／v0.0.203实施（D36、D41）

规则见[安装与发布](../共用运行时/接口/安装与实例.md#安装与发布)与[固定交付摘要](../共用运行时/接口/接入交付.md#固定交付摘要)。2026-11-17工作区已实现下列各项，**未提交、未发布、未实机**；只有定向检查（`temp/checks/t29-201-r*`）证明对应输入。一句话按用户定稿写入根README：部分克隆（`--depth 1 --filter=blob:none --sparse`）后读根README_AGENT.md。

- [x] 账号后重装（已实现，定向通过）：skills-install仅替换按自身清单逐字节核验未改且binding与sourceBinding相同的自有目录；暂存→旧目录移入暂存内层→新目录就位，失败回滚，回滚也失败时不删除并报告保留位置；导出ZIP同规则替换；回执含replaced／reloadRequired。解28待续“空profiles绑定重装判differs”
- [x] 来源（已实现，定向通过）：bootstrap.ps1在其所在官方clone内运行、不另clone；origin按通道核对（容忍无.git／尾斜杠），HEAD即固定来源并回报resolvedCommit；bootstrap.py `--commit --channel`核`refs/remotes/origin/<通道>`谱系（--depth 1下tip即自身祖先），tag改为ls-remote须指向clone HEAD；marker记录channel及对应design标签；源码快照改为ls-tree列出所需路径blob、在稀疏clone中以no-cone稀疏检出一次批量取得（保留根文件）、cat-file按对象写出，不再用git archive（部分克隆中它会懒取整仓）：本地file://实测克隆0.3MB、检出src/client后0.8MB、快照后14.7MB（git archive基线345—360MB），272文件与提交blob逐一相同，路径清单只在bootstrap.py一处；结果含sourceClone可删提示
- [x] 显式`-Upgrade`（已实现，定向通过）：原客户端CATALOG缺runtime.stop返回explicit_upgrade_required；经原客户端runtime.stop后从固定源码加载SDK自身Lease取得原data_dir锁，未取得返回not_ready且不改程序；env改名保留后新建并核pip check／RECORD来源，失败恢复原env与marker；账号、配置、数据不动；回执标入口绑定migration_pending及其他宿主Skill待重装。未给-Upgrade时来源不同返回explicit_upgrade_required
- [x] 中断恢复（已实现，定向通过）：仅marker installing、同一来源、无配置文件且无`.data`目录时原env改名保留后原位重装，其余保留拒绝
- [x] 镜像与失败摘要（已实现，定向通过；镜像URL未联网实测）：MinGit／uv官方失败后试登记的npmmirror地址，任何来源都核同一SHA256，不符即拒并保留文件；uv私有Python依次官方、npmmirror python-build-standalone（显式-PythonMirror时只用它）；ps1失败输出ASCII转义的六项handoff、stage与resolvedCommit
- [x] 结构化步骤（已实现，定向通过）：onboarding的pending为{id, actor, instruction, verify}，verify为只读`onboarding --verify <id>`，new_session永不自报完成；根README_AGENT.md按部分克隆重写（先`sparse-checkout set src/client`再运行bootstrap.ps1），要求原样转告user步骤、verify通过才报完成；`/connect`改307到GitCode仓库首页
- [x] 207已提交并正式GitCode发布；本机真实稀疏clone与两种隔离安装通过（范围见下节）。
- [ ] 干净Windows三宿主实机、缺Git分支、原生加载、审批次数与新聊天发现。

## 207正式来源安装实测（2026-11-17）

GitCode main固定`2d2de21d10f71c4a8ed5dc31edd838965efb1662`，按根README_AGENT真实部分克隆并检出src/client，不另clone。普通路径安装到`temp/i207/client-ordinary`，实际exit0/status=completed；pyvenv.cfg证明复用本机CPython3.11.4，PS5.1探针不再误判并下载备用解释器。ManagedPython路径安装到`temp/i207/client-managed`同样exit0/status=completed，使用已校SHA256的官方PyPI uv0.9.21 Windows wheel ZIP，独立exe真实版本0.9.21，venv解释器3.12.12；仅该子进程PATH排除现有uv以验证新资产链，未改全局PATH。

uv官方PyPI与清华完整同文件下载均核SHA256 `64a7bb0e4e6a4c2d98c2d55f42aead7c2df0ceb17d5911d1a42b76228cab4525`。这是受审固定资产替换，不是修改旧GitHub ZIP哈希绕过校验；依然经原Get-PortableTool下载/校验/解压，不增加pip或第二安装链。ManagedPython复用了此前已下载的受管解释器缓存，不称本轮重新下载Python包。

两安装均仅导出两角色ZIP、返回manual_required/connected=false，未配置账号、未启动实例、未导入用户已有宿主或改其Skill。账号页、宿主登记/start、原生导入/entry.bind、新聊天发现与四流程继续待验。此为现有Windows用户、已有Git/Python的正式来源安装证明，不代替干净新用户、缺Git场景或三宿主原生验收。证据：`temp/handoff-deploy/bootstrap-207-{ordinary,managed}.log`、`managed-207-asset-proof.json`及`uv-pypi-evidence.json`。
