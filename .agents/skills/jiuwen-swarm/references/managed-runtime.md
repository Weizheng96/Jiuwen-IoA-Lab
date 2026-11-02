# 部署方托管实例

只用于项目新建并明确管理的九问实例；不据此重启、停止或改写用户已有九问。先查当前锁定版的公开入口与本地源码，再选服务管理配置。

| 检查 | 已核实机制与使用边界 |
|---|---|
| 端口 | 已核实原版load_dotenv_runtime会覆盖普通环境；JIUWENSWARM_CLI_PORTS=1保留注入的四端口组并丢弃旧AGENT_SERVER_URL。只使用其支持的公开配置，不修改.env模板或宿主源码 |
| 最终停止 | 已核实app_agentserver在SIGTERM后默认detach，Worker继续运行；Worker本身处理SIGTERM。托管systemd实例可在SDK排空后向原版进程组发SIGTERM，并禁用最终SIGKILL；主PID退出不证明组已空 |
| 服务身份 | root完成首次准备后只移交所属叶目录。后续公开模型目录查询以原服务账号执行；包、安装身份和所有父目录必须可读/遍历，不能靠root导入成功推定可运行 |
| 扩展 | 原生通知扩展在网页/AgentServer启动前准备；配置完成、扩展加载、能力就绪和用户可见分别核验 |

上述行为来自本项目锁定原版源码；其他版本须重查，不推定所有九问版本相同。系统服务的真实停止与读包权限仍须Linux实机证明，模拟管理器或Windows检查不能代替。

本仓固定操作复用deploy/install.sh、upgrade.sh与python -m deploy status；不在技能内复制安装、进程树或重试实现。普通用户接入始终走产品Skill和本地SDK。
