# 官方服务

领域定义在 `domains/`，验收器在 `verifiers/`，服务技能在 `skills/`。默认 8 个 ISP 领域、每领域 6 个服务。独立进程只依赖 `ioalab-sdk` 与九问，不导入平台核心。

核心启动后执行 `python official-services/serve.py`。默认监听 8751；配置见 `设计/部署.md`。无任务时只保持协议端点，浏览模板不启动九问。真实模式需已安装并配置兼容的 `jiuwenswarm-process`，支持机器协议 `--run-json` / schema `0.1`；适配依据上游 b465f45（workswarm 0.2.5b1），完整真机验收状态见变更记录。缺少入口时 health 报错，不自动改成桩。

显式 `IOALAB_STUB=1` 用固定报价与样件验证协议，不能作为真实 Swarm 验收结论。`GET /health` 返回可接入状态与执行计数；任务错误保存在独立 service.sqlite 的 jobs 表。重启恢复排队任务，已有签4的合同不重复交付。
