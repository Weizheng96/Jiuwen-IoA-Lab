# 独立实验客户端

`traffic.py`通过本地A2C SDK产生委托；`messages.py`通过相同账号接口产生个人助手消息。两者均须显式启动，核心不自动产生背景委托或消息，未接入的预置账号保持空闲。

```bash
python experiments/traffic.py --base http://127.0.0.1:8750 --account <账号> --password <演示口令> --count 3
python experiments/messages.py --base http://127.0.0.1:8750 --accounts data/experiment-accounts.json --count 5
```

消息账号文件结构见`accounts.example.json`。至少两个账号；只为该实验名单开放experiment.ping，不转发、不预约、不调用模型。身份登记为experiment，输出实验编号。全局展示可同时启动两类客户端；不要将实验账号配置提交到仓库。

全景录制由`network.py`统一准备独立账号与六类实验接口，消息收发和委托使用各自的持续工作线程。首次发送铺开到完整周期，服务搜索/交付等待不阻塞收件，委托不以固定笔数提前结束。按服务控制并发容量，结束时等待已有合同处理退出，不删除历史。

活跃模式中所有实验账号都可发起委托，已发现的服务可复用并周期重新发现。每个服务的锁保持到上一合同在平台上真实结清，避免“验收付款完成”与“提供方容量释放”之间的异步间隔导致下一客户端过早进入。录制同时保存多发起方并发与传输密度，不能用单一发起方的高频活动冒充开放网络。

录制入口和逐时间窗混合验收见[演示说明](../demo/README.md#全局视角)。所有背景流量由这里的客户端经公开SDK产生，核心不运行个人助手；实验消息不触发真实邮件或外部通知。

## 原版网页通知验证

`native_web.py`复用已安装的原版程序，在新的私有目录和独立端口建立实验实例。显式指定本任务可复用的模型配置来源；不覆盖已有工作区，不安装依赖到九问环境。原配置摘要不变才报告就绪；输出保存本次PID和日志位置，不自动重启另一实例。

```powershell
.venv/Scripts/python.exe experiments/native_web.py --python .swarm-venv/Scripts/python.exe --source-workspace .swarm-runtime --output data/native-web-check
$env:IOALAB_NATIVE_WEB_CONFIG="D:/project/Jiuwen-IoA-Lab/data/native-web-check/settings.local.json"
$env:IOALAB_NATIVE_WEB_REPORT="D:/project/Jiuwen-IoA-Lab/data/native-web-check/delivery.json"
.venv/Scripts/python.exe -m pytest -q tests/test_native_web_delivery.py
.venv/Scripts/python.exe experiments/verify_web_receipt.py --config data/native-web-check/settings.local.json --report data/native-web-check/delivery.json
```

示例绝对路径须替换为自己的输出目录；端口占用时可选--port-offset。原生扩展及历史是真实调用，集成测试的模型轮次和确认驱动是明确替身；实际网页另行检查，证明范围见[回执](../deploy/verification/web-notifications.json)和[截图](../docs/progress/native-web-notification.jpg)。重放核验只使用已确认的原ID，核对同件原回执、异内容/库标识冲突及唯一历史记录。私人配置、数据库和日志仍留data，不提交。

实验进程仅由本次processes.json登记的PID标识；结束前核对命令行确属该输出目录，再停止本实验进程，不按python等通用名字批量关闭。原有九问保持运行，原生菜单/完整用户配置及影片仍需另外验收。
