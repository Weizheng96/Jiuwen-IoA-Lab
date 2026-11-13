# 16：Claude Code宿主适配

**目标行为：** 独立适配器经官方Agent SDK执行固定SDK工具、原Session续接与取消；产品沿同一SDK完成账号、配置、双角色启动。
**阻塞于：** 无
**状态：** claimed（开发中，与当前设计有偏离：纯SDK生产阶段标志被误拒绝，已由子智能体准备修复）
**范围：** [ClaudeCode适配](../ClaudeCode适配/接口.md)、共用运行时AgentHost；不复制业务规则或建立另一配置权威。
**定向测试：** claude_code_host、client_hosts、package_installation

- [x] 显式CLI/模型配置、公开模型目录与能力探测
- [x] 固定工具、冻结技能、先绑定实际Session、原会话续接及未知不重试
- [x] PreToolUse与MCP处理器联合核权限、身份、调用及输入
- [x] 实际interrupt后等原终态，取消不冒充无效果
- [x] 干净统一wheel安装、Chrome账号页、双角色真实绑定及正常停止
- [ ] 纯SDK生产阶段沿原Session续接（阶段标志不开放原生工具）
- [ ] 最终统一回归与正式发布

## 当前证据与边界

统一客户端178实际wheel已在独立CPython3.11环境按[claude-code]安装，pip check退出0；ClaudeSDK0.2.181、MCP2.3.0、anyio4.15.1、pydantic2.14.1、Windows pywin32 312。真实既有CLI2.1.322，官方目录default解析claude-opus-5-5。SDK账号认证、host.available、平台WSS、assistant/service绑定均通过，助手执行就绪；服务未配置业务，未称可接单。assembly为ioalab-claude-code，本项目五组件版本178，roleErrors为空。沿产品Skill正常stop后状态not_ready、原PID消失；未读账号配置或认证正文。原始证据在本机忽略目录temp/c178-claude-native（D盘原工作区）。

宿主独立真实工具首轮、沿同一Session复用实际随机回执的第二轮及取消均已通过；对应独立工作树temp/native-final-440729cd/report.json。13项宿主检查、96项主集成检查及统一候选完整门禁2247通过/58跳过、自检全过；这些证据不替代尚待集成的生产阶段修复及最终业务验收。

首版native_tools/interaction_callback均false，SDK可信确认页承担业务批准；原生AskUserQuestion、原生生产工具与当前桌面聊天通知继续不支持。后续能力与全场景原生验收归[客户端17](17-多宿主产品接入与业务验收.md)，不以握手或后台连接宣称产品业务完整通过。
