# 委托交易通用流程

本流程适用于公开服务的成果委托。业务理解与推荐由九问完成，协议动作由SDK执行，不使用预置行业计划、不固定三家、不固定排序或确认次数。

## 用户确认的呈现

**委托授权、具体合同、实际环节及每期验收都用普通聊天提出问题，然后结束当前回复，等待用户下一条自然语言答复。不要调用ask_user或interact生成确认窗口；当前锁定宿主的无选项窗口可能提交空答案。用户提出需求不算同意，工具返回“通过”不算用户验收。** 不固定确认次数，只在本合同确实需要的边界询问。

## 先取得委托授权

**用户只提出需求、尚未确认委托范围时，第一轮只解释目的、总预算、拟卡费上限及有效期，提出授权问题并结束回复等待。此时可以读取本技能流程，但不执行authorize或任何付费/签字命令；不能把用户提出需求当作确认。** 若需建议拟卡费限额，明确为待用户确认的建议，不能假定已获同意。用户确认后才创建授权scope并继续发现。不要用失败的authorize命令探测是否需要确认。

授权说明采用以下通用事实模板，替换真实数值，不增添退款承诺：

> 将为您就【目的】发起委托。总预算【预算】虚拟币，包含拟卡费；拟卡费上限建议【限额】，有效期【天数】天。拟卡费按各模板公示规则计收，不承诺未成交或未采纳时退还；具体合同、合同声明的环节和每期验收仍分别请您确认。是否授权？

**禁止声称“未成交可退还”“未采用免费”或“签约前不产生费用”。** 若模板另有明确规则，后续只按实际已验签内容说明。用户未确认前，额度只是建议，不得执行authorize/request。

## 调用顺序

| 步骤 | SDK命令及参数 | 下一步条件 |
|---|---|---|
| 委托范围 | 从用户原话提取purpose、总预算budget；说明proposal_fee_limit（未授权默认0）、valid_days（最长7天）与拟卡费可能不退。获得确认后`authorize {purpose,budget,proposal_fee_limit,valid_days}`加--confirmed，保存scopeId | 不把委托范围授权当成签署具体合同 |
| 服务发现 | `search {query,top_k}`；逐项`reputation {provider}`、`templates {provider}` | 只使用回执中的真实URL、名称、信誉和完整验签模板 |
| 逐节筛选 | 阅读deliverables、inputs、specSchema、pricing、acceptance、deadlines、signers与样件；先排除能力不符，再比较 | 材料不足先请用户补充；不能将所有需要材料的服务一概排除。没有合适服务如实说明 |
| 准备规格 | 按每份模板的deliverable name分组构造spec；materials用`{名称:{path,declared}}`提供本地文件 | 不猜测字段，不在JSON字符串中隐藏不兼容值；缺少必填信息先补齐；拟卡费超限重新授权 |
| 形成合同 | 提案型`request {template_digest,spec,materials?,budget,scope_id}`加--confirmed；其他类型`draft {template_digest,spec,materials?,budget,scope_id}` | 前者使用已确认的拟卡费范围，取得真实签名提案；后者仅拟卡。不能用公示参考价冒充提案报价 |
| 比选与确认 | `inspect {cd}`读取真实卡及模板；多方比较可`compare {providers,selected,reason,cd?}`（2—8方） | 按用户偏好解释交付/验收覆盖、价格及期限，不强制script优先。只有一方不伪造其他候选。预算包含所有已付拟卡费 |
| 定约 | 明确说明具体成果、待确认规格/默认值、材料、亲签条款、分期金额、拟卡费及合计、期限、验收/未覆盖项；等待用户确认具体合同。提案型`adopt {cd}`，其他`sign {cd}`，均加--confirmed；成功后`start {cd}`加--confirmed | start使用本次已确认合同的开工授权，不另编造确认问题；拒绝时不签字不开工 |
| 等待与环节 | `watch {cd,timeout:60}`或`poll {cd}`；没有人工节点就继续等待 | 仅有真实stepRequest时按合同描述询问；用户同意/拒绝后`answer-step {cd,granted}`加--confirmed。不预设实名；可以有0个或多个环节 |
| 本期交付 | anchored事件给出实际period后，`fetch {cd,period}`→`verify {cd,period}` | SDK在本地WASM验收。pending到notBefore再验；fail/error不签7不付款，讨论修正/仲裁，不能仅凭模型宣称通过 |
| 本期验收 | 告诉用户本期成果、实际检查结果及付款金额；等待验收确认后`accept {cd,period}`加--confirmed | SDK重验后才签7并读卡付款。签约确认不能代替本期验收确认 |
| 多期与恢复 | 从inspect的实际合同期数逐期重复等待/取件/验收/付款；`scope {scope_id}`恢复授权，inspect/poll核对合同 | 全部期完成才报告整单完成；already-accepted不代表付款已确认。unknown或结果不明先核实，不重建任务或重复付款 |

用户本轮已明确同意的对象、用途、金额和动作可传递给对应SDK命令，不要求重复确认；不得扩大授权。scope只负责限额，不等于SDK能独立证明用户确实同意。金额均以真实合同和回执为准。

## 格式与恢复

`spec`以真实deliverable name为第一层，例如`{"document":{"language":"中文","pages":2}}`，字段和值域来自模板。调用参数写入独立UTF-8文件，不能在命令后直接放业务文字。不得读取settings.local.json、密钥或修改SDK来绕过报错。

`compare`的reason只记录个人助手推荐，不是市场认证；没有历史信誉则不编造。签名、卡片和验收原件由SDK本地存储。断线继续原合同watch/poll，保留身份目录。需要停止时`terminate {cd}`加--confirmed，存在解除报价及协议条件；验收争议可`arbitrate {cd,period}`加--confirmed。付款确实失败与结果未知要区分，`pay`只能在已签7且核实允许重试时使用。

兼容旧delegate任务：`delegate {action:status}`列出本机任务；有taskId的受管理合同继续status/poll，并仅在对应用户确认后advance --confirmed。不将新需求强行套入旧任务，不从主skill加载行业计划。完整接口参数表见contract-primitives.md。
