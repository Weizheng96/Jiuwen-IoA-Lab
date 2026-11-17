# 委托交易

用户希望取得专业成果时走本流程，不由本机助手自行生产。先读[本地交互表单](forms.md)与[需求交互](requirements.md)，优先使用 delegation.trade 的 MCP Apps 界面收集需求并推进原流程；保留成果、载体、预算、时限、材料及验收要求。当前会话无连接器时按 forms.md 引导启用，不输出 A2UI。

图形化入口 delegation.trade 复用同一个业务执行入口 `assistant.delegation.flow`，不要求模型另行调用本表动作。以下仅用于用户明确选择普通文字办理时，先读[调用说明](calling.md)。参数 action、可选input对象、续接时的flowRef。operationId放独立参数文件；新业务决定使用新号，waiting/unknown用原调用或原flowRef恢复，不换号重做未知效果。

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

waiting读取reason、required及evidence。confirmation表示SDK已准备真实事项并尝试打开可信页面，必要时调用runtime.open_ui；用户真实决定后后台继续。external_processing等待推送或指定时机观察，不高频轮询。unknown_result先核原流程，不自动签约、付款或生产。

本流程成约开工后，交付推送触发后台取件、本机保存、验收预览及已配置owner.notify通知。展示真实文件路径、报告和下一步；queued仅排队，不表示网页已显示或已读。缺宿主/通知资源时保留待办。取件含签6，不含用户签7或付款；多期逐期独立确认。原九问用户会话关联仍待真实验收，不能据通知排队声称原会话已续接。

用户要求续期时优先打开 `delegation.renew` MCP Apps 表单；taskRef从原委托回执读取，不要求用户手填。原履约表单也可选“续期原委托授权”。续期和恢复付款是独立确认，聊天表达不证明续期已执行；必须核授权回执生效后再申请付款。已有付款等待不得冒称包含续期，也不能重复验收。

委托表单waiting且有确认引用时，支持MCP Apps的宿主优先显示独立确认卡片，让用户在聊天里核对并决定；不要主动调用runtime.open_ui或另开浏览器。卡片不可用时由用户点击“在浏览器确认”，无Apps才使用原浏览器入口。宿主允许工具运行不是业务批准；模型不读取_meta中的入口能力、不代点允许。用户决定后读取原表单核实结果，不把卡片“已允许”说成已签约或已付款。

履约页的“刷新合同状态”“下载本期交付”“查看本期验收结果”可直接点击执行，期次按实际交付选择，不要求用户先保存或手动预览。运行中、等待或结果待核实时只刷新原表单，不重复发起；下载/验收结果不代表用户已接受或已付款，签约、验收及付款仍用原独立确认。
