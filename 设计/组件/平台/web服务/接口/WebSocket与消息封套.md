# web服务 · WebSocket与消息封套

本页是[web服务](../接口.md#接口)的子页。

## WebSocket 帧

帧是 IoA 平台的接入协议，不宣称为 A2A 标准 WebSocket 绑定。认证后控制帧结构为 `type + operationId + agentId + generation + payload`；认证前只有携带票据和恢复游标的 `connection.auth` 合法。`operationId` 在首次发送前持久化，恢复同一操作必须沿用；只读查询可使用一次性请求标识。

| 帧/控制操作 | 输入 | 输出与持久边界 |
|---|---|---|
| `connection.auth` | ticket、持久 runtimeId、各身份已连续保存的 cursors | connection.ready：connectionId、各身份 generations、服务端已确认游标、heartbeatSeconds、leaseSeconds；历史缺口显式返回 |
| `connection.ping/pong` | 连接标识 | 心跳与租约有效信息，不产生登录赠币 |
| `message.send` | 本端身份、消息封套 | 校验并持久化后返回 `accepted` 与原 messageId |
| `message.deliver` | 平台推送消息及 recipientSeq | 接收端落库后发送 `message.ack` |
| `message.ack` | messageId、连续保存的游标 | 平台确认已送达；不表示已开始或已完成 |
| `request.start` | 原请求 messageId、interactionId、当前代次 | 按原请求核验并保存 startedAt，返回原许可；已成约A2C请求另含contractCard及有实际记录时的depositVoucher，供宿主绑定原有效签1/2/3和实付凭证，不表示开工或新增授权 |
| `interaction.close` | interactionId；未建立时使用 originMessageId；关闭原因 | 原子关闭入口，可靠产生 `interaction.closed`；委托先通过委托中心核验 |
| `admission.configure/reserve/commit/status` | 接单策略（expectedRevision、maxActiveDelegations、accepting）；或交互、绑定摘要、预留及相关证据 | 委托中心原子设置策略、预留、提交或查询，返回原台账记录；释放没有参与方控制操作，只由平台按到期、结清、关闭与提案到期的证据完成 |
| `scope.register/status/close` | context/grant/proof；或 scopeDigest | 用户签名授权、原记录恢复、预算查询及拒绝新许可；关闭不解除合同 |
| `scope.select` | scopeDigest、完整原卡和用户选择证明 | 验证后预留完整价款；只绑定一份 cardDigest，不执行 A2C 签1或付款 |
| `contract.bind` | admissionId、完整原有效 card | 原个人助手连接登记同单生效签1/2/3；核原承诺、范围选择和双方，返回原成约事实，不开工或付款 |
| `a2c.market` | admissionId、op、原 body；card.endorse 另带原 signed request | 当前服务连接申请 card.endorse、delivery.anchor 或 card.close；按阶段核承诺/成约、实际原件与付款证据，返回原 A2C Reply |
| `operation.get` | 原操作标识与类型 | 查询同一操作的已存结果；与 HTTP 恢复查询读同一记录 |

普通业务结果由持久消息返回；控制操作超时先查询原操作，不重新生成标识尝试同一副作用。平台拒绝旧代次发起新的执行，但允许当前执行者恢复已有操作结果。平台自己产生的关闭/到期通知使用平台系统身份，参与方不得伪造。

控制响应统一为 `operation.result`，包含 agentId、operationId、operation、ok，以及 result 或 error；多身份连接按完整三元组关联原操作。推送 `message.deliver` 包含 message、recipientSeq 和独立的 deliveryState；投递状态不修改原封套摘要。认证失败关闭连接；被新代次替代的连接使用关闭码 4409，旧运行时停止自动重连并报告已失去执行权。

`runtimeId` 由本地实例首次建立时生成并持久保存，不是账号或会话标识。另一个 runtimeId 在原租约仍有效时不能自动接管；同一实例网络重连递增代次。ACK 的 through 为该身份连续持久保存的 recipientSeq，不能超过已接受序列；过期消息的元数据占据原序列位置，恢复时仍显式返回，不能跳过缺口。

`POST /api/v1/messaging/interfaces` 的输入为 operationId、name、expectedRevision 与 definition；definition 仅包含公开说明、object 根输入/输出 Schema、allowedSenders、enabled、完整本地规则的 contentDigest。原文处理步骤不上传。发布返回不可变修订和目标 agentId；读取及发送均复核当前权限，操作重试不重复建立修订。

## 持久消息封套

| 字段 | 必填条件与来源 |
|---|---|
| `messageId` | 必填；发送端 UUID，平台全局唯一；相同发送者及内容重传返回原记录，冲突不覆盖 |
| `kind` | 必填；下表中的明确类型，不从自然语言猜测 |
| `recipient` | 必填；目标稳定 agentId |
| `interactionId` | 已建立交互必填；首次业务请求省略；由原接收端生成后登记 |
| `originMessageId` | 建立/关闭及建立前错误必填；指向原始请求，用于恢复关联 |
| `inReplyTo` | 业务结果必填；指向实际请求，限制返回对象与结果格式 |
| `expiresAt` | 必填；UTC RFC 3339 时间，受平台及业务期限约束 |
| `body` | 必填；由 kind 指定结构；不改 A2C 原字段、摘要或签名 |
| `sender`、`acceptedAt`、`recipientSeq` | 平台赋值；客户端不能声明其他主体或入队顺序 |

封套不携带九问 Session ID、私钥或模型凭据。UUID、admission、路由及连接代次属于平台运行数据，不进入 A2C 合同。

| kind | body 的必要内容 | 关联约束 |
|---|---|---|
| `delegation.intent` | formation、templateDigest、scopeDigest、bindingDigest，及未签 request（提案）或 card（待批准） | 首次请求不带 interactionId；原协议摘要不含后续签名，只表达意向，不形成合同；材料仍走有权文件通道 |
| `delegation.result` | outcome；成功时 admissionId、绑定摘要、holdUntil；拒绝时 error | 原意向的 inReplyTo；建立后沿用已登记 UUID |
| `a2c.request` | 原协议方法/请求、任务引用及 admissionId 等外层元数据 | 必须已有委托交互；原协议请求 ID 与签名保持原义 |
| `a2c.result` | 原协议响应/任务进展与原任务标识 | 绑定原请求；进展不冒充终态，不反派为新请求 |
| `message.request` | interfaceName、revision、input | 首次受理时校验当前启用修订并冻结；后续沿用原交互句柄及固定接口 |
| `message.result` | outcome；完成时 output；失败/拒绝/未知时 error | inReplyTo 指定原请求；output 按原修订 outputSchema 校验 |
| `interaction.opened` | interactionType；通信另含 interfaceName、revision、deadline、maxRequests；原始关联在封套中 | 仅原接收端可提交；平台先登记不可变双方及类型，再可靠转发 |
| `contract.ruling` | caseRef、admissionId、subject、原裁定；原交互关联在封套中 | 仅平台系统产生，核原双方；终止合同新履约，不冒充执行已结束 |
| `interaction.closed` | reason、closedAt；原始关联在封套中 | 平台在关闭事务后产生；拒绝新请求但不抹掉迟到结果 |

`delegation.result.outcome` 使用 `reserved / rejected / failed / unknown`；reserved 只表示预留成功。`message.result.outcome` 使用 `completed / rejected / failed / unknown`。`completed` 表示取得结构化业务答复；例如 `output.status=conflict` 是有效结果，不能冒充预约成功。标准错误不要求伪造符合业务 Schema 的成功数据。

同一请求只保存一个不可变业务终局；`unknown` 是可核实推进的非终局报告。结果消息以 inReplyTo 关联，平台不允许迟到的 unknown 降级已确认终局；同终局重传返回原结果，矛盾终局报冲突。A2C 任务进展按原协议分辨进展与终态。

首次请求在建立前被拒绝时，结果可不含 interactionId，但必须引用原消息；发起方据此退出等待。登记失败或原消息已关闭时不能执行业务；同源不同 UUID、同 UUID 不同参与方均报冲突。助手通信的后续请求保持原请求方向和接口；反向调用对方的另一接口必须另开交互。

首请求已受理但尚未建立 UUID 时也属于在途请求；`interaction.opened` 核对原消息冻结的修订，不再次要求等于最新内容修订。仅更新接口内容不能使其失效，当前停用/撤权、消息到期或关闭仍会阻止取得开始许可。

a2c.request 的常规方向为个人助手到服务方；唯一逆向请求为原服务方在同一委托交互发送的 ap2.checkout，body只含purpose=termination及原cardDigest。平台核已接受的原协商解除及实际负余额，固定参与方和admission；个人助手只用Client.refund_checkout产生本单收款单，a2c.result沿原请求返回。此通道不依赖个人助手通信接口，不提供任意反向业务或跨合同收款。
