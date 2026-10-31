# 委托交易

触发：用户需要专业服务完成尚未生产的成果。先理解成果、预算上限、期限及必须条件；只澄清影响合同的缺项，合理默认值在合同确认时列明。后续流程保持通用，不预选行业或服务。

| 顺序 | operation 与参数 | 真实结果与推进条件 |
|---|---|---|
| 委托授权 | assistant.delegation.prepare：purpose、budget、proposalFeeLimit，可选 categoryIds、validDays；再 authorize：taskRef | 保存 taskRef；展示授权范围和费用上限，取得本机真实确认后重发原 authorize |
| 发现 | assistant.delegation.search：taskRef、query，可选 topK；inspect：service，可选 templateDigest | 读取实际服务列表及签名模板；候选应符合需求，不能只按价格或文字相似度决定 |
| 征集与比较 | assistant.delegation.request：taskRef、service、templateDigest、spec，可选 materials | 每家各自保存 quoteRef；提案型取得实际签2/3的报价，待批准只生成未签算价卡，不能为比价向多家复签 |
| 候选进展 | assistant.delegation.status：quoteRef | accepted/running 等待原结果；必要材料审查另按真实许可处理。比较合格候选的交付、验收覆盖/未覆盖、费用、期限、材料和责任，不固化最低数量 |
| 具体合同 | assistant.delegation.confirm：quoteRef | 展示实际合同及材料发送阶段，取得独立用户确认后重发同一操作；保存 contractRef，status 查询 formed 后才开工 |
| 开工 | assistant.delegation.start：contractRef | SDK 复用原合同及实际定金/开工回执。新候选失效不等于旧合同失效，仍遵守原合同窗口 |
| 合同环节 | assistant.delegation.answer_step：contractRef、stepRequestDigest | 仅在 status 有真实 stepRequest 时展示对象与动作；用户实际选择同意/拒绝后重发原操作，不传 granted/confirmed |
| 交付与取件 | assistant.delegation.status：contractRef；fetch：contractRef、period | availablePeriods 及实际签名证明交付，取件保存原文件与签收；不是模型文字“完成” |
| 验收与付款 | verify：contractRef、period；accept：contractRef、period | 异步作业先返回引用；accept取得通过报告后进入waiting，展示真实预览和应付款额。取得本期独立确认后用同一operationId重发accept，原Client.accept复验、通知及AP2，分别查实际验收与付款结果 |
| 结束核对 | assistant.delegation.status：contractRef；pay：contractRef、period 只核原付款恢复 | 核实际付款和 closed；unknown 先查询原操作/原凭证，不重签验收或重付，不把“已验收”当“已付款” |

多期合同按模板顺序逐期取件、验收及付款；每期使用独立的验收确认和操作文件，不把首期同意当作全单同意。状态中的部分付款不表示结清。取件若明确返回failed且error.recovery=retry_read，SDK已核本期尚无签6和缓存；可在原请求内以新的本地读取操作重试一次，再失败则报告网络未就绪；原失败操作重传仍返回原结果，不额外要求用户批准读取。unknown或已有签收时先核原凭证，不换操作强制重签。

spec 是按模板交付件名组织的需求对象；materials 按模板材料名组织，提供 text 或 bytesBase64 及可选 spec。材料不得超出用户已授权范围。服务、模板、报价及合同引用从实际结果获取，不能编造 ID。

status 也可附原 operationId 查询履约动作。报价/合同/动作引用用途不同；脚本参数文件中的 operationId 是同一操作的恢复键，不是新交易请求。readonly 业务查询也按脚本参数格式创建文件。

验收不通过或拿不准时向用户说明报告和选项，不擅自付款或发起仲裁。当前 SDK 若对某异常给出未支持/未就绪，保留原证据并说明，不能自行调用私有接口绕过。用户拒绝和未知结果均停止对应新效果。
