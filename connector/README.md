# 九问本地IoA技能

`python tools/build_connector.py`生成两个包：`dist/ioalab-skill.zip`用于九问「技能」页直接导入，`dist/ioalab-client.zip`保留MCP目录形式供兼容部署，不用于九问插件页导入。

直接技能包包含SDK与A2C wheel、依赖约束和校验摘要。用户导入技能后，将settings.example.json复制为settings.local.json填平台地址、用户名、密码；首次调用自动创建独立SDK环境。不要把密码发到聊天中，也不要把本地配置提交到仓库。

同一技能支持A2C委托与`mail`命令。协议和信箱共享用户本地身份目录；收件同步不会长期占用合同执行锁。完整调用顺序由技能SKILL.md唯一维护。

美甲委托的用户首句只描述需求和预算；九问使用`delegate`阶段入口，按回执的gate分别请求委托授权、合同确认、实名环节授权和验收确认。`references/nail-booking-plan.json`只含声明式查询、默认规格、排除词与拟卡费限额，不含执行代码。底层原语独立放在技能参考页，避免与阶段参数混用。随部署的九问发现设置见[独立助手](../personal-assistant/README.md)。

可靠使用：配置账号后，在九问原生技能菜单预选ioalab-delegate，再输入业务需求和预算；宿主会预载完整技能。当前模型对未预选的自动发现仍不稳定，不能把“已导入”当作“已加载”。无需输入SDK命令或额外流程描述。
