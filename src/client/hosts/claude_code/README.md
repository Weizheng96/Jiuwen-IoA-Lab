# Claude Code宿主适配

通过官方claude-agent-sdk==0.2.181复用用户已安装Claude CLI及其认证。配置command为只含一个真实可执行文件绝对路径的数组，model必须显式选择，skill_directories为可选业务技能目录映射。不使用PowerShell/CMD包装脚本，不复制认证或读取已有聊天。

统一产品类型为claude_code；原生生产工具与原生提问回调首版明确不支持。probe仅核CLI版本、SDK握手与公开模型目录，实际推理和认证须由真实轮次证明。配置、启动及关闭复用SDK原账号和数据目录，不增加业务状态权威。
