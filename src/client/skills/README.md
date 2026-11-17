# IoA Lab 两个角色Skill

普通用户按根README的一句话让智能体clone官方仓库，并按[接入引导](../../../README_AGENT.md)安装一个ioalab-client，再导出个人助手与服务方两个轻量Skill。原生导入并选择后通过本机账号页接入，密码不经模型或聊天。

assistant/和provider/提供角色说明；_shared/references提供公共流程，_shared/ioalab.py为绑定统一客户端解释器及账号配置路径的薄调用模板。两个Skill不包含wheel或独立安装器，也不自行创建运行环境。实际客户端命令归ioalab_client。

在已安装客户端的环境执行：

```text
python -m ioalab_client skills-install --host <jiuwen或codex或claude_code> --config <原账号配置绝对路径>
```

开发构建入口为tools/build_client.py与tools/build_access_skills.py。依赖固定版本唯一来自_shared/requirements.lock；更新锁后需重新验证完整安装。普通用户默认平台为https://ioalab.openjiuwen.com，原配置或明确地址优先，SDK先尝试连接，失败才要求补地址。

Codex和Claude Code自动准备各自原生目录的两份Skill；九问返回两个导入包和原生导入步骤。导出/安装程序只处理配置路径，不读取账号正文。用户配置、参数、私钥和运行数据不分发。两个角色共用原账号、身份数据和常驻客户端；安装成功不等于账号、连接、宿主或业务已就绪。独立调用tools/check_bootstrap.py --tag <已发布tag> --host <当前宿主>使用同一安装引导，不启动业务。接入结束按两份Skill共有的references/onboarding.md生成固定摘要，并保留新会话的独立验收状态。

安装与调用契约见[公共接入](../../../设计/组件/客户端/共用运行时/接口.md)，未验范围见[进展](../../../设计/工程/进展.md)。
