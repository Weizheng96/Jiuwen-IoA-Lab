# IoA个人助手技能

`python tools/build_connector.py`生成`dist/ioalab-skill.zip`（九问技能页导入）与兼容目录包`dist/ioalab-client.zip`。技能包含锁定SDK/A2C wheel、依赖约束和摘要，首次调用自动准备隔离环境。

导入后复制settings.example.json为settings.local.json，仅填写平台地址、用户名、密码。在九问原生菜单选择IoA技能，再输入业务需求；不修改九问源码、全局指令或技能模式。未预选时自动加载受宿主/模型影响，不保证稳定。既有目录标识ioalab-delegate用于安装兼容，显示标题为IoA个人助手。

主SKILL.md只路由委托交易和私人交互，分别按需读取references/delegation.md和private-interaction.md。九问按通用流程逐项调用SDK；协议与信箱共享本地身份，消息同步不长期占用合同执行锁。美甲计划和台词在[独立示例](../examples/nail-booking/README.md)，不随通用skill分发。

旧部署的发现配置和AGENT标记区可显式执行`python deploy/restore_swarm_overrides.py --swarm-data <九问数据目录>`撤回；仅恢复匹配旧覆写值的字段并移除自己的标记区，保留其他用户配置及备份。变更后重启该实例。新安装器不写这些配置。
