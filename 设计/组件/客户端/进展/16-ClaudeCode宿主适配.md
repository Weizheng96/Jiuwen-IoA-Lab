# 16：Claude Code宿主适配

**目标行为：** 独立适配器经官方Agent SDK执行固定SDK工具、原Session续接与取消；产品沿同一SDK完成账号、配置、双角色启动。
**阻塞于：** 无
**状态：** claimed（适配与真实产品启动已实现；生产阶段真实终态及最终发布待验）
**范围：** [ClaudeCode适配](../ClaudeCode适配/接口.md)、共用运行时AgentHost；不复制业务规则或建立另一配置权威。
**定向测试：** claude_code_host、client_hosts、package_installation

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
- [ ] 正式发布（按main/GitCode交付程序继续）

## 当前证据与边界

统一客户端178实际wheel已在独立CPython3.11环境按[claude-code]安装，pip check退出0；ClaudeSDK0.2.181、MCP2.3.0、anyio4.15.1、pydantic2.14.1、Windows pywin32 312。真实既有CLI2.1.322，官方目录default解析claude-opus-5-5。SDK账号认证、host.available、平台WSS、assistant/service绑定均通过，助手执行就绪；服务未配置业务，未称可接单。assembly为ioalab-claude-code，本项目五组件版本178，roleErrors为空。沿产品Skill正常stop后状态not_ready、原PID消失；未读账号配置或认证正文。原始证据在本机忽略目录temp/c178-claude-native（D盘原工作区）。

宿主独立真实工具首轮、沿同一Session复用实际随机回执的第二轮及取消均已通过；对应独立工作树temp/native-final-440729cd/report.json。生产阶段修复后的独立追加探针，一例第二轮确实调用原SDK工具但60秒内未取得终态，另一例首轮工具调用后180秒内未取得终态，均保留unknown且未重试；不能记为该阶段实机验收通过。必要报告已保存到主工作树忽略目录temp/claude178-preserved-evidence。

三宿主96项集成检查及先行完整门禁通过；业务修复候选完整门禁另为2257通过、58跳过、14项子测试通过，自检退出0。Skill能力报告补丁在独立候选45项通过、2项Windows文件符号链接权限跳过，目录链接拒绝实测通过；该补丁晚于完整门禁，最终集成验证及正式发布仍待完成。

工具参数错误归一化已先红后绿，Claude最终30项通过、1项Windows文件符号链接权限跳过；严格业务输出Schema、回调自身ValidationError及SDK吞错仍保留unknown。主集成最终快速层1790通过、41跳过、518未选；最终冻结输入的完整门禁c178-release-final-full已真实退出0：2289通过、60跳过、14项子测试通过，pytest2998.20秒、自检39.61秒；1455个跟踪文件摘要在整轮中未变。跳过项为Windows权限/平台限制及显式原生模型专项，不计实机通过。正式发布与生产阶段真实终态继续分别记录。

首版native_tools/interaction_callback均false，SDK可信确认页承担业务批准；原生AskUserQuestion、原生生产工具与当前桌面聊天通知继续不支持。后续能力与全场景原生验收归[客户端17](17-多宿主产品接入与业务验收.md)，不以握手或后台连接宣称产品业务完整通过。
