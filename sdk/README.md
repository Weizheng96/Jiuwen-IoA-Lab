# IoA Lab SDK

安装：先安装仓库 `vendor/wheels/` 中锁定的 A2C wheel，再 `pip install ./sdk`。SDK 不依赖平台 Python 包，不在本地安装 A2X 或九问运行时。

个人助手使用 `ioalab-client <command> --args '<JSON>'`；命令清单见 `python -m ioalab_sdk.cli --help`。配置 `IOALAB_BASE_URL`、`IOALAB_USERNAME`、`IOALAB_PASSWORD`；身份与合同存入本地 `IOALAB_HOME`。需确认的命令显式传 `--confirmed`，技能必须先取得用户同意。

`LabClient` 封装发现、身份绑定、观察上报，并直接使用 A2C Client 执行合同。验收在调用进程的 WASM 沙箱中完成。付款发送结果不明时报告 requires-reconciliation，不自动再次付款。未发送观察在本地 outbox 保存，下次调用重发。

服务方使用 `ProviderAgent` 管理 Provider SDK 和本地持久状态；独立Swarm经四个结构化host工具进入本单`ProviderJob`能力，模型不接触宿主令牌；人工诊断保留`python -m ioalab_sdk.provider_cli`入口。详见[接入设计](../设计/接入.md)与[官方服务](../official-services/README.md)。

`delegate`为可恢复的本地阶段入口：自然需求准备授权范围，用户确认后发现和征集真实提案，推荐合同后等待签约确认，再处理环节授权、本地验收和验收确认。原语仍可用于通用合同；受管理合同由阶段入口调用原语，避免混用破坏状态。任务、授权与卡分别持久化；重复取件核已签清单并恢复缓存，不重复签收。完整边界见[接入设计](../设计/接入.md)，可运行示例和参数在产品技能中。
