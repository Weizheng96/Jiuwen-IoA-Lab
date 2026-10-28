# 独立实验客户端

`traffic.py`通过本地A2C SDK产生委托；`messages.py`通过相同账号接口产生个人助手消息。两者均须显式启动，核心不自动产生背景委托或消息，未接入的预置账号保持空闲。

```bash
python experiments/traffic.py --base http://127.0.0.1:8750 --account <账号> --password <演示口令> --count 3
python experiments/messages.py --base http://127.0.0.1:8750 --accounts data/experiment-accounts.json --count 5
```

消息账号文件结构见`accounts.example.json`。至少两个账号；只为该实验名单开放experiment.ping，不转发、不预约、不调用模型。身份登记为experiment，输出实验编号。全局展示可同时启动两类客户端；不要将实验账号配置提交到仓库。

全景录制由`network.py`统一准备独立账号与六类实验接口，消息收发和委托使用各自的持续工作线程。首次发送铺开到完整周期，服务搜索/交付等待不阻塞收件，委托不以固定笔数提前结束。按服务控制并发容量，结束时等待已有合同处理退出，不删除历史。

录制入口和逐时间窗混合验收见[演示说明](../demo/README.md#全局视角)。所有背景流量由这里的客户端经公开SDK产生，核心不运行个人助手；实验消息不触发真实邮件或外部通知。
