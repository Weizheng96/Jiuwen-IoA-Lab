# 官方服务

领域定义在 `domains/`，验收器在 `verifiers/`，服务技能在 `skills/`。默认8个ISP领域、50项服务；软件开发8项，其余各6项。独立进程只依赖 `ioalab-sdk` 与九问，不导入平台核心。

核心启动后执行 `python official-services/serve.py`。默认监听8751；配置见[部署设计](../设计/部署.md)。无任务时只保持协议端点，浏览模板不启动九问。真实模式使用`jiuwenswarm-process --run-jsonl`机器协议；适配依据上游b465f45（workswarm 0.2.5b1），验收结果见[验证记录](../demo/verification/README.md)。启动先通过`model.list`校验模型`selection_key`，不能填网页模型资源ID；入口或模型无效时不登记服务，不自动改成桩。

显式 `IOALAB_STUB=1` 用固定报价与样件验证协议，不能作为真实 Swarm 验收结论。`GET /health` 返回可接入状态与执行计数；任务错误保存在独立 service.sqlite 的 jobs 表。重启恢复排队任务，已有签4的合同不重复交付。

原美甲剧本使用`python official-services/serve.py --scenario nail-booking`，经同一公开注册/签认流程提供七家原名候选，三家提案由真实Swarm按需拟卡。该场景要求部署方明确配置可执行的仲裁模型注册表以签认灵犀的L2验收器；默认领域内容保持独立。预约交付包含配置与源码，实名注册只演练合同环节授权，不向微信提交真实身份资料。

模板不再使用固定截止年份；启动检查和运行中的自动续期、索引更新与旧合同保留规则见[委托中心](../设计/委托中心.md#内部服务模板自动续期)。

四个本单工具按任务类型提供子集：quote仅inspect/quote，deliver仅inspect/selftest/deliver。模型正常结束而SDK未提交时，在同一会话有界继续，最多三轮且合计不超过300秒；写入结果未知停止自动重发。源码按text_fields行数组组装，自测通过可复用同份成果提交。
