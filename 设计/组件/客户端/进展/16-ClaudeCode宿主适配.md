# 16：Claude Code宿主适配

**目标行为：** Claude Code经独立适配器执行两角色工具轮次，沿原Session续接、可信用户问题和取消，不导入九问适配包。
**阻塞于：** 无
**状态：** ready
**范围：** [ClaudeCode适配](../ClaudeCode适配/接口.md)、共用运行时AgentHost；官方Python Agent SDK与进程内MCP工具。
**定向测试：** 待实现时登记claude_code_host；现有共用契约以business_skills、user_confirmations为基线

方案见[专题](../../../工程/专题/多宿主适配.md)。当前仅设计，未实现、未安装依赖、未调用真实Claude。

- [ ] 版本/模型/会话/工具能力探测与本机显式配置
- [ ] 固定工具、冻结技能、原Session绑定及恢复
- [ ] 权限规则与PreToolUse联合约束，不能只依赖can_use_tool
- [ ] 问题转换、取消排空原终态和未知结果不重试
- [ ] 定向、实际宿主及双角色验证
