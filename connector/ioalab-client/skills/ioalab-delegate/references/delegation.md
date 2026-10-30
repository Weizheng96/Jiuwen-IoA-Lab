# 委托交易：执行清单

你正在替用户执行委托，不是只编写推荐方案。按以下顺序调用真实SDK，保存回执；缺少信息才问用户。没有真实合同摘要和金额，不能确认合同。

## 调用方法

使用主技能返回的目录S：`python "<S>/scripts/ioalab.py" 命令 --args-file "参数文件绝对路径"`。先write_file写UTF-8 JSON，再调用；不是`--query`等业务命令行选项。确认类命令仅在用户已经明确同意对应对象/用途/金额后加`--confirmed`。

同账号逐条调用SDK，拿到上条回执后再发下一条，尤其不能并行多家request。九问bash工具对request设`timeout:900`（秒）；watch等待60秒时设`timeout:180`或更长。该参数属于bash工具，不写进业务JSON或SDK命令行。

## 每阶段确认

委托授权、具体合同、实际环节、每期验收分别询问，不互相代替。九问提供ask_user时，调用`ask_user(query="本阶段待确认事项", questions=[{"question":"针对真实对象和范围的明确问题？"}])`，等待工具返回本阶段用户的新答复；不可用时停止本轮、等待普通聊天的新答复。**不得自问自答或从之前的合同/材料/默认值确认推导后续同意。**

收到stepRequest后先问、再等，不立即answer-step；确认合同只允许签约开工。空答、跳过、取消都不算同意；明确同意或拒绝后，才按本阶段答复调用相应SDK并加--confirmed。SDK不能凭该标记自行证明用户实际作答。

## 逐项执行

| 顺序 | 必须执行的SDK动作 | 真实回执与下一步 |
|---|---|---|
| 1 读配置 | `me` | 读取账号与可选delegationPolicy；不读settings或密钥。默认最低数量1不是只询价1家的指令 |
| 2 请求委托授权 | 说明目的、总预算（含拟卡费）、建议拟卡费上限及有效期（最多7天），等待明确答复 | 拟卡费按模板计收，不承诺未成交退款；合同、环节、每期验收分别确认。普通聊天或原生问答均可，空答/取消/跳过不算同意 |
| 3 保存授权 | 用户同意后，立即`authorize {purpose,budget,proposal_fee_limit,valid_days}`加--confirmed | 保存scopeId。**没有scopeId就执行本项，不跳到搜索或拟卡**；用户授权不等于签订某份合同 |
| 4 发现 | `search {query,top_k:8}`；各候选`reputation {provider}`、`templates {provider}` | provider填hits[i].card.url，不填ext注册ID。信誉无历史就如实说明 |
| 5 确定候选集合 | 读完整验签模板的交付、inputs、specSchema、pricing、acceptance、期限；记录每家合格/排除/待补材料及依据 | 验收覆盖偏好用于后续推荐，不能据此让其他满足需求的服务免于询价。材料未备齐先看其他候选；不足再问用户 |
| 6 准备规格 | 各spec按deliverable name分组，值符合模板；材料用materials={名称:{path,declared}} | 不猜字段或隐藏复杂JSON。用户没给的非关键默认值须在合同确认时说明，关键缺失先补齐 |
| 7 征集正式报价 | 对**每个合格候选**：proposal类型`request {template_digest,spec,materials?,budget,scope_id}`加--confirmed；其他类型`draft`同参数 | 保存每份cardDigest和derived.total。**proposalFee只收拟卡费，绝不是合同价；不拿它或公示参考价作为正式报价**。有未报价合格候选且授权费用足够，就继续本项；不能要求用户先选一家才拟卡 |
| 8 比较并问具体合同 | `inspect {cd}`读各份真实卡；多家时`compare {providers,selected,reason,cd}`记录推荐依据，再完整说明推荐合同并等待用户确认 | 逐项比较判据覆盖、检查方式、失败条件与未覆盖项，再联系需求/价格/期限；不只数条目，不编星级或声称质量最好。总费用含所有拟卡费。只有1家合格/用户指定单方/费用不足才说明原因并减少询价，不固定三家 |
| 9 定约开工 | 确认具体合同后，proposal用`adopt {cd}`，其他用`sign {cd}`；然后`start {cd}`，均加--confirmed | 确认内容包含规格/默认值、材料、亲签条款、每期金额、拟卡费合计、期限及未覆盖项；不扩张授权 |
| 10 等待环节/交付 | `watch {cd,timeout:60}`或`poll {cd}`；实际stepRequest出现后必须通过上面的独立询问等待新答复，再`answer-step {cd,granted}`加--confirmed | 没有环节就继续等，不固定实名。参数按真实卡，不重建任务；确认开工不等于确认环节 |
| 11 本地验收 | anchored事件给出period后，`fetch {cd,period}`→`verify {cd,period}` | WASM实际报告才算证据；pending等到notBefore，fail/error不签7不付款，讨论修正/仲裁 |
| 12 逐期确认 | 展示本期成果、验收结果、应付金额，等用户确认后`accept {cd,period}`加--confirmed | SDK重验后签7并读卡付款。多期重复10—12，全部期完成才报告整单完成 |

**阶段检查：合格候选还有未报价项→继续request/draft；没有真实cardDigest/derived.total→不能确认合同；没有确认具体合同→不adopt/sign/start；没有本期验收确认→不accept。** 最低数量满足只是可选策略下限，不等于候选集合已征集完毕。

保存scope_id、候选清单、cardDigest、当前期及真实回执到本地任务笔记。状态未知先核实，不重付或改SDK绕过。恢复与详细参数按需读contract-primitives.md；已有delegate受管理任务用原taskId恢复，不用于新需求或固定行业计划。

发生deadline-passed或合同响应窗口已过时立即停止并说明。旧request缓存不会延长期限；不得为恢复流程自行修改purpose重建scope、重新付费或修改签名时间。只有用户新的明确授权和具体合同确认后才能重新委托。
