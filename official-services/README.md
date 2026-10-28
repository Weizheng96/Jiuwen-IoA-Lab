# 官方服务

领域定义在 `domains/`，验收器在 `verifiers/`，服务技能在 `skills/`。默认8个ISP领域、50项服务；软件开发8项，其余各6项。独立进程只依赖 `ioalab-sdk` 与九问，不导入平台核心。

核心启动后执行 `python official-services/serve.py`。默认监听8751；配置见[部署设计](../设计/部署.md)。无任务时只保持协议端点，浏览模板不启动九问。真实模式使用`jiuwenswarm-process --run-jsonl`机器协议；适配依据上游b465f45（workswarm 0.2.5b1），验收结果见[验证记录](../demo/verification/README.md)。启动先通过`model.list`校验模型`selection_key`，不能填网页模型资源ID；入口或模型无效时不登记服务，不自动改成桩。

显式 `IOALAB_STUB=1` 用固定报价与样件验证协议，不能作为真实 Swarm 验收结论。`GET /health` 返回可接入状态与执行计数；任务错误保存在独立 service.sqlite 的 jobs 表。重启恢复排队任务，已有签4的合同不重复交付。
