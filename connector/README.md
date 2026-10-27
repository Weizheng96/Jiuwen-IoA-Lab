# 九问本地IoA技能

`python tools/build_connector.py`生成两个包：`dist/ioalab-skill.zip`用于九问「技能」页直接导入，`dist/ioalab-client.zip`保留MCP目录形式供兼容部署，不用于九问插件页导入。

直接技能包包含SDK与A2C wheel、依赖约束和校验摘要。用户导入技能后，将settings.example.json复制为settings.local.json填平台地址、用户名、密码；首次调用自动创建独立SDK环境。不要把密码发到聊天中，也不要把本地配置提交到仓库。

同一技能支持A2C委托与`mail`命令。协议和信箱共享用户本地身份目录；收件同步不会长期占用合同执行锁。完整调用顺序由技能SKILL.md唯一维护。
