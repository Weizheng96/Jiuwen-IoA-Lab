# 本单执行

## 本机管理中的任务核对

本机用户要求查看或继续自己的任务时，用provider.tasks.get，参数profile，可选原taskRef；未指定时返回该服务最近至多100项。只有canResume=true且用户明确要继续时，才用provider.task.resume，参数profile、taskRef。SDK自动关联原任务，不让用户抄UUID。accepted只表示原作业已入队；用provider.tasks.get查询当前结果。相同恢复参数文件重传只返回原resumeReceipt，不再次入队。若新一轮再次unknown，先核对；确有新的恢复要求时使用新的管理操作，业务taskRef保持原值。

恢复仅限已明确结束的模型轮次在验收器准备上中断，其他unknown保持待核对。新轮次开始前旧证明失效；不使用operations.resume强制重跑模型。待批准、生产或已返回决定不能通过此入口重拟。以下本单工具属于后台受限执行入口，不授予来信任务查询所有任务或管理恢复的能力。

## 后台本单执行

入站委托只由常驻 SDK 以真实阶段触发，运行在与该交互绑定的原 Session。用户前台配置对话不伪造入站任务或自己创建 UUID。

| 阶段 | SDK 本单工具 | 边界 |
|---|---|---|
| 提案准备 | provider.task.verifier.publish | 仅quote且未提交决定时；传verifierId、source及可选reads/kind，取得真实引用。可复用业务资源或preparedVerifiers；不改服务配置、公示或已签判据 |
| 提案 quote | provider.task.quote | 按模板交付件名给 acceptance、acceptWithin、lineItems；验收器映射仅列实际 script/skill/service 完整引用。拒绝只提交 reason |
| 待批准 decision | provider.task.decision | approved 及可选 reason；只批准/拒绝当前已确认合同，不能改成报价。SDK 核容量后才签2 |
| 生产 production | provider.task.request_step | 需要模板声明的用户环节时提交 step，等待真实请求/答复，结束本轮，不自行批准 |
| 生产自测 | provider.task.selftest | files 按文件名映射，每项含 mediaType，以及 text、bytesBase64、artifactPath 三选一；artifactPath 只指本单 artifacts 下相对文件 |
| 交付 | provider.task.deliver | 按原合同提交真实成果；只有取得原签4/5回执才称交付。随后结束本轮，等待本期真实验收及付款 |

报价、复签和生产使用同一固定 Agent/技能/工具定义。原生工具名自首轮固定，但报价/复签 deny，生产才按本机冻结权限启用；工作目录不是另一个安全沙箱。

验收器源码及其边界见[合同模板与验收器](contracts.md)。准备上传unknown时结束本轮并报告待核对；在本机管理入口依下方流程核对，不重建委托或另收拟卡费。

收到等待或已交付结果后不继续制造业务效果。后续环节由 SDK 核验原签名和阶段，续接同一 Session。拒绝授权不能当作同意；unknown 保留原操作和证据，不重跑模型、生产、付款或发信。文本“完成”不代替工具结果，也不替委托方验收。
