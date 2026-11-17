# 16：Claude Code宿主适配

## 本轮接棒

本票原生产阶段真实终态继续保留，是客户端33／36的前置；新增原生会话回投与可见性由30承接，不以新专题拟定冒充本票通过。 见[票据顺序](../../../工程/专题/一句话接入与三宿主全流程/票据与实现.md)。

**目标行为：** 显式SDK应用经官方Agent SDK及统一S01执行固定SDK工具、原Session续接与取消；统一产品原生CLI路径由30承接，不以SDK作原生失败回退。
**阻塞于：** 无
**状态：** claimed（197显式SDK S01提案／隔离观察切片已落，core8中本票53项及共同4light通过；完整集成、资源映射及生产阶段真实终态待验；历史181已发布不代表197已验收）
**范围：** [ClaudeCode适配](../ClaudeCode适配/接口.md)、共用运行时AgentHost；不复制业务规则或建立另一配置权威。
**定向测试：** claude_code_host、client_hosts、package_installation

## 196 实施与未验范围

- [x] 197静态readonly_context=isolated_session、原冻结proposal_schema经公开SDK output_format进入，真实structured_output严格校验后才成为中性proposal
- [x] 提案缺失／无效／未请求输出与原机器终态分开；schema冲突拒绝重投；原生allow/ask约束在显式SDK无法落实时明确not_submitted，deny沿受限SDK落实
- [x] 显式SDK应用静态registration、S01 submit、运行期check及原请求get_request/cancel；不保留公共run/probe/capabilities平行链
- [x] SDK签发actor/profile scope、实际UserMessage.uuid回显及P28持久ACK后才执行工具；缺回显不伪造request ID
- [x] 原query机器终态、生成内容与SDK效果分开；背景origin、缺终态字段、断流保unknown，不重放原请求
- [x] 脱敏有界诊断，deadline沿原期限，取消本地请求/实际control ACK/真实结束分开，活跃callback不假作已排空
- [ ] SDK稳定资源引用映射、统一角色/装配集成与完整门禁（当前缺资源映射明确not_submitted；本轮不变更设计版本）
- [ ] 新S01下纯SDK生产阶段沿原Session的真实模型终态；原unknown业务不重放

196定向A4真实exit0：34项通过（pytest4.79秒，runner测试步5.515秒）及4light通过；1036文件前后SHA不变，含实际安装SDK公开types/client/query。外部ClaudeSDKClient与版本进程为边界替身，未启动真实ClaudeCLI、模型、native或服务；未运行client_hosts/package_installation、fast、完整或真实模型专项。A1模块入口检查失败未执行pytest，A2元组直接交canonical摘要失败、A3测试版本替身误截OS身份进程，各24失败/7通过；修复后A4通过，原报告保留。上述结果不是整票完成或新版已发布。

197共同core8真实exit0：本票全53项通过（原34加19项提案／权限），共同146项通过、pytest64.07秒及4light通过。实际安装SDK公开types/client、output_format运输源码及相关资产前后hash无变化。该轮仍是实际SDK消息／选项对象与外部SDKClient边界替身，没有真实Claude CLI、模型或生产业务；隔离观察声明只适用显式SDK应用，不证明产品原生工具／模型／设置继承。P26仍明确拒绝未映射资源，原生产真实终态勾选不变。

以下勾选与数字为178—181历史版本证据，不作为196／197源码的原生或生产验收。

- [x] 显式CLI/模型配置、公开模型目录与能力探测
- [x] 固定工具、冻结技能、先绑定实际Session、原会话续接及未知不重试
- [x] PreToolUse与MCP处理器联合核权限、身份、调用及输入
- [x] 实际interrupt后等原终态，取消不冒充无效果
- [x] 干净统一wheel安装、Chrome账号页、双角色真实绑定及正常停止
- [x] 修复纯SDK生产阶段的错误拒绝，原Session绑定与实际原生权限拒绝回归通过
- [x] 按SDK既有契约报告实际可读的业务Skill源，失效或链接源保持不可用
- [x] 已绑定调用的入参Schema错误在业务回调前明确拒绝，允许同轮新callId修正；回调及输出异常仍保持unknown
- [ ] 纯SDK生产阶段沿原Session续接的真实模型终态
- [x] 最终统一回归（2289通过、60跳过、14项子测试通过，pytest及selfcheck退出0）
- [x] 正式发布（main181及design-v0.0.181已核GitHub/GitCode一致；真实生产阶段终态仍独立待验）

## 当前证据与边界

统一客户端178实际wheel已在独立CPython3.11环境按[claude-code]安装，pip check退出0；ClaudeSDK0.2.181、MCP2.3.0、anyio4.15.1、pydantic2.14.1、Windows pywin32 312。真实既有CLI2.1.322，官方目录default解析claude-opus-5-5。SDK账号认证、host.available、平台WSS、assistant/service绑定均通过，助手执行就绪；服务未配置业务，未称可接单。assembly为ioalab-claude-code，本项目五组件版本178，roleErrors为空。沿产品Skill正常stop后状态not_ready、原PID消失；未读账号配置或认证正文。原始证据在本机忽略目录temp/c178-claude-native（D盘原工作区）。

宿主独立真实工具首轮、沿同一Session复用实际随机回执的第二轮及取消均已通过；对应独立工作树temp/native-final-440729cd/report.json。生产阶段修复后的独立追加探针，一例第二轮确实调用原SDK工具但60秒内未取得终态，另一例首轮工具调用后180秒内未取得终态，均保留unknown且未重试；不能记为该阶段实机验收通过。必要报告已保存到主工作树忽略目录temp/claude178-preserved-evidence。

三宿主96项集成检查及先行完整门禁通过；业务修复候选完整门禁另为2257通过、58跳过、14项子测试通过，自检退出0。Skill能力报告补丁在独立候选45项通过、2项Windows文件符号链接权限跳过，目录链接拒绝实测通过；该补丁晚于完整门禁，最终集成验证及正式发布仍待完成。

工具参数错误归一化已先红后绿，Claude最终30项通过、1项Windows文件符号链接权限跳过；严格业务输出Schema、回调自身ValidationError及SDK吞错仍保留unknown。主集成最终快速层1790通过、41跳过、518未选；最终冻结输入的完整门禁c178-release-final-full已真实退出0：2289通过、60跳过、14项子测试通过，pytest2998.20秒、自检39.61秒；1455个跟踪文件摘要在整轮中未变。跳过项为Windows权限/平台限制及显式原生模型专项，不计实机通过。正式main181已核双仓一致；生产阶段真实终态继续独立待验。

首版native_tools/interaction_callback均false，SDK可信确认页承担业务批准；原生AskUserQuestion、原生生产工具与当前桌面聊天通知继续不支持。后续能力与全场景原生验收归[客户端17](17-多宿主产品接入与业务验收.md)，不以握手或后台连接宣称产品业务完整通过。
