# 委托交易

用户希望取得专业成果时走本流程，不由本机助手自行生产。先读[调用说明](calling.md)与必要的[需求交互](requirements.md)，保留成果、载体、预算、时限、材料及验收要求。

业务唯一入口 `assistant.delegation.flow`。参数 action、可选input对象、续接时的flowRef。operationId放独立参数文件；新业务决定使用新号，waiting/unknown用原调用或原flowRef恢复，不换号重做未知效果。

| action | input | 实际判断与等待 |
|---|---|---|
| begin | purpose、budget、proposalFeeLimit；可选categoryIds、validDays、query | 真实任务授权，批准后可检索；拟卡费属于总预算 |
| search / inspect | search用query、可选topK；inspect用service | 审阅完整模板、材料、收费、验收源码和信誉；不能以介绍或摘要代替 |
| request | service、spec，可选materials、templateIndex（从1开始） | 先用inspect审阅同流程的真实模板，再映射规格并征集；多个模板须明确选择templateIndex，客户端引用原摘要，自动比较本流程候选；不适用时澄清，不删需求/空规格凑报价 |
| compare / select | compare为空；select为quoteRef | 只比较原候选，Agent提供证据，用户选择；具体合同独立确认后自动成约开工 |
| answer_step | 空对象 | 客户端确定原合同当前环节，真实用户独立确认，不传granted |
| accept / pay | period | 本期独立验收及价款确认；pay只恢复已验收期付款 |
| renew / close | renew可选validDays；close为空 | 续期原范围或关闭任务许可；关闭不解除合同 |
| termination_quote / terminate | 报价为空；解除用mode及相应terminationQuoteRef、reason | 原报价与真实解除确认 |
| dispute | ground，可选period、materials | 原合同仲裁理由、真实材料与受理回执 |
| observe / resume | 不传input | 原flowRef读取/恢复，不搬运子操作和协议字段 |

## 后台跟进

客户端自动跟踪报价与候选比选、具体合同批准后的复签与开工、环节答复、交付取件及验收预览，并记录拒绝、失败和未知结果。原生入口已绑定且有原会话通知通道时，客户端主动把迟到的确认事项、阶段结果和可验收交付发送到原会话。通知只给阶段和原引用，收到通知后用原flowRef执行一次observe读取完整候选、协议回执或交付证据。queued表示已排队，只有实际宿主回执才能说明投递或处理。

收到waiting先读reason、required和evidence。confirmation有真实待办时调用runtime.open_ui展示可信确认页；用户决定后由客户端继续。external_processing且已有后台续接和原会话通道时，告诉用户当前实际阶段和后续通知，结束本轮等待，不高频反复observe或operations.get。没有可核的通知通道时保留原flowRef并说明回告未就绪。用户主动追问或收到新进展时只读观察原flowRef。unknown_result按原引用核实，不换号重签、付款或重做生产。

contractRef只标识原合同，签署待办也可能已有这个引用。以实际成约回执报告已签署，以原select结果中的开工receipt报告已开工；确认成约且开工作业仍排队时报告已成约、待开工。

交付推送触发后台取件、本机保存和验收预览。有原生来源时通知原会话，无原生来源时可用已配置的owner.notify。暂时无法提醒时仍保留本机待办。展示真实文件路径、报告和下一步；取件含签6，签7验收及付款仍须真实用户决定。任务授权、具体合同、合同要求的环节和每期验收分别确认，任何通知、自动等待或聊天回答均不能代替许可。
