# 最小服务方接入示例

[provider.py](provider.py)演示由外部程序注入自己的 `AgentHost`，使用普通SDK装配服务方常驻实例。它不创建入站HTTP服务、不依赖平台源码或官方业务包，也不自动批准发布、接单或合同。

准备SDK运行配置（包含账号、服务身份及 `provider.services` 业务配置），安装 `ioalab-sdk`，在宿主程序中使用：

```python
from provider import create_runtime

runtime = create_runtime("/absolute/path/runtime.json", my_agent_host)
runtime.run()  # 当前进程持有身份和运行数据；停止前排空原工作
```

`my_agent_host`实现SDK公开的 `AgentHost` 接口；可以使用已安装的九问适配，也可以实现其他宿主。运行配置和密钥留在所属参与方设备。运行后通过 `ioalab-access call ... --config ... --args-file ...` 调用 `provider.preview`、`provider.publish` 和接单控制；发布与后续业务沿用真实本机用户确认流程，不由示例替用户批准。

SDK配置和操作契约见[SDK说明](../sdk/README.md)及[服务方接入](../../../设计/流程/服务方接入.md)。示例的公开接口回归见 `tests/test_external_provider.py`；测试中的宿主与用户动作是明确隔离夹具，不代表真实模型或普通用户界面验收。
