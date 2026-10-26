# 独立实验客户端

使用相同SDK、公开接口和模拟支付通路，核心不执行实验。`traffic.py` 明确以experiment身份绑定，管理员可区分实验来源。

```bash
python experiments/traffic.py --base http://127.0.0.1:8750 --account ISC_001 --password 001 --count 3
```

命令行表示操作者授权按定义中的样例规格进行这批模拟交易；不用于替真实用户绕过确认。SDK身份和合同保存到本地实验目录。默认需求与材料在 needs/、materials/，服务定义来自官方目录；生成记录包含实验编号、迭代、合同摘要和结果。
