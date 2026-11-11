# 个人助手端 · Agent流程

本页是[个人助手端](../接口.md#接口)的子页。流程只编排既有角色操作，合同、签名、费用、用户确认仍由原所有者检查。

## 输入与回执

| 接口 | 输入 | 结果与恢复 | 测试 |
|---|---|---|---|
| `assistant.delegation.flow` | action、可选 input（业务对象）、flowRef（续接必填） | begin、search、inspect、request、compare、select、answer_step、accept、pay、renew、close、termination_quote、terminate、dispute、dispute_status；observe/resume读取或推进原流程。禁止传入其他流程的任务、合同或候选 | agent_flows、assistant_flow_guidance、resident_fulfillment |
| `assistant.messaging.flow` | action、可选 input、flowRef | interfaces、send、close、configure_inbox、disable_inbox、inbox、tools；observe/resume同上。发信使用准确account和interface名称，多轮只用原flowRef和本轮input | agent_flows、assistant_role、inbox_rules |

request只接受service、spec及可选materials、templateIndex（从1开始）；同流程先inspect实际完整模板，唯一模板可确定性引用，多个模板返回business_choice，不接收模型搬运templateDigest。回执 data 包含稳定 flowRef、当前 action、reason、required、continue、真实 result/evidence 与原业务引用。reason区分 business_input、business_choice、confirmation、external_processing、unknown_result、host_unavailable、user_acceptance。业务判断仍由Agent/用户决定；字段形状不符拒绝并允许以新动作纠正，原调用异参数返回id_conflict。观察不得携带业务输入。每个新业务决定使用新的operationId，同一动作等待或未知时保留原操作；不以新号绕过未知。

## 调用图与推进依据

| 动作 | 确定性调用图 | 实际等待点 |
|---|---|---|
| begin | prepare → authorize → 可选search | 真实任务授权；检索后业务筛选与规格映射 |
| inspect/search | 原检索/验签模板接口 | 必须审阅真实模板、验收器、信誉与必要原件，不能以摘要替代 |
| request | 按已审阅模板的序号确定原摘要 → request → 原报价作业 → compare（本流程全部候选） | 材料批准、服务方报价、Agent比较与用户选择 |
| select | confirm → 原成约作业 → start | 具体合同真实批准、外部成约与开工 |
| answer_step | status → 当前stepRequestDigest → answer_step | 当前合同环节独立确认；不能用模型布尔值 |
| accept/pay等 | 对应原角色操作与原作业 | 原期次验收及付款边界，不因流程授权自动批准 |
| configure_inbox | draft → validate → preview → publish | 完整规则及真实发布确认；不为用户猜allowedSenders |
| send | 接口读取 → 按明确名称选择 → 原send → 原消息结果 | 接口歧义/业务输入、外部回复；不另建交互续轮 |

后台恢复只查询本机原作业和已收消息，不调用模型选择下一接口；网络/协议等待仍由已有作业承载。流程记录冻结每一步参数和稳定子操作键，已完成步骤复用原结果，未知作业交原角色核实，不通用重排未知作业。确认先创建原记录，再打开既有可信页面；用户决定后后台继续原动作。

## 交付续接

经本流程成约并开工的合同启用交付观察。原委托的服务方a2c.result进展只触发核查，不把正文解释成命令；核原交互、参与方、合同及验签卡后，按已验收期次顺序登记稳定待办，复用fetch（签6及本机文件副本）→verify（本地验收预览）→owner.notify。未启用流程的旧合同不静默接管。

通知、SDK收件、领取文件、预览、排队/会话接收与用户验收分别保存。没有签7或付款许可时不调用accept/pay。多期需前期独立验收及付款后继续。重复/乱序进展使用同合同期次键，崩溃后恢复原缓存、签收、作业与通知来源；结果未知不替换操作。解除、裁定或原交互关闭不得恢复新取件。无通知资源或宿主暂不可用时保留本机待办。

九问原用户会话的关联与真实页面展示按[宿主接口](../../宿主适配/接口.md)验收；交互分组通知不等于原用户会话续接，未取得关联证据前保持票据待验。离线补投遵守原消息期限，不承诺唤醒关机设备。
