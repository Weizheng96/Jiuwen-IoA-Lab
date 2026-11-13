# 发布服务

仅本机用户触发。先读[调用说明](calling.md)及[合同模板与验收器](contracts.md)，业务技能负责生产。唯一管理入口 `provider.flow`，参数profile、action、可选input及原flowRef。新业务决定用新operationId；等待/未知用原调用或原flowRef恢复。

| action | input | 边界 |
|---|---|---|
| template | terms | 起草未签模板，SDK补齐协议和身份字段 |
| verifier | verifierId、source，可选reads、kind | 打包实际验收引用，不使用恒真判据 |
| categories | 空对象 | 真实分类目录 |
| publish | service，含profile、name、description、tags、terms（1—16组业务条款），可选categoryId | 自动生成未签模板、连续预览、分类、独立确认及公示；分类歧义先业务选择 |
| pause / resume_admission | 空对象 | 核currentPolicy，暂停不取消原合同 |
| status / tasks / reputation | tasks可选taskRef，其余为空 | 核实际公示、任务、容量和结清 |
| recover_task | taskRef，可选confirmationRef | 原任务恢复证明及用户要求，不绕过未知生产效果 |
| observe / resume | 不传input | 原flowRef读取/恢复，不选底层接口 |

confirmation表示实际事项已准备并尝试打开可信页，必要时调用runtime.open_ui。真实决定后后台恢复原发布内容、修订及签名。external_processing等待，unknown先核原流程，不换号重发。公示、连接、模型/工具及容量分别核验，不把公示当成生产就绪。
