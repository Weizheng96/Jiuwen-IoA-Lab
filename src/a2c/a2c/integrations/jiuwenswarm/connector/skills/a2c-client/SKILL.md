---
name: a2c-client
description: "A2C 合同委托：把用户需求中专业、费时的部分按合同交给服务方智能体——取模板比价、拟合同卡、签字、交材料、等交付、验收、付款、解除。当用户的需求需要外部专业服务完成（做网页、改编短剧、旅行规划与预订、法律或升学咨询等）且付款时成果尚未做出来时使用。"
description_zh: "A2C 合同委托：取模板比价、拟卡签约、交付验收、按卡付款与解除的调用顺序与确认规则"
description_en: "A2C contract delegation: compare templates, draft and sign cards, accept deliveries, pay by the card, terminate"
version: 1.0.0
allowed-tools: Read,Write
---

# A2C 合同委托

本技能配合 `a2c` MCP server 使用。A2C 是 A2A 之上的合同层：你代表用户与服务方智能体签一张**合同卡**，交付件分期交、每期按服务方提供的**验收器**验收、经支付方**按卡付款**。

## 1. 流程

| 步 | 工具 | 说明 |
|---|---|---|
| 1 | `list_templates` | 取各候选服务方的合同模板。按 交付件 → 输入要求 → 价目 → 验收标准与截止时间 → 样件 的顺序读，不合的当场剔除，被剔的后面的节不再读 |
| 2 | `draft_card` / `submit_request` | 预批准型、待批准型：照模板拟合同卡（规格写值，材料给本地文件路径与声明）。提案型：发提案卡，服务方拟卡后返回 |
| 3 | `sign_card` | 签1。待批准型随即请服务方复签；返回 `countersigned: false` 时看 `reason` |
| 4 | `start` | 付定金（如有）、交材料、开工；把市场的 Agent Card URL 传给 `market` 以订阅存证通知 |
| 5 | `poll` | 定期查：`anchored` 表示某期已存证，可取件；`step-request` 表示服务方要以用户名义办事 |
| 6 | `answer_step` | 把环节请求原样转述给用户，按用户的回答同意或拒绝 |
| 7 | `fetch` → `verify` → `accept` | 取件签收；`verify` 运行验收器、不签名，把结果告诉用户；用户确认后 `accept`：通过即签7 并付本期款；`pending` 时到 `notBefore` 再调 |
| 8 | `terminate` | 用户要中途停：协商解除（按报价补付余额）或单方解除 |

## 2. 必须请用户确认的操作

`sign_card` · `submit_request` · `start` · `answer_step` · `accept` · `terminate` 会签名或付款。调用前用一句话告诉用户：委托给谁、交什么、多少钱、分几期、验收怎么判。

## 3. 规则

- 报价必须是定数：模板算出的 `derived` 就是要付的钱，不另议价
- `draft_card` 返回的 `notices` 有定金超过 20% 的提示时，转告用户
- `inputPrecheck` 有 `fail` 时先改材料或声明再签，否则开工会被以「声明不符」拒收、合同失效
- 验收不通过或拿不准时，问用户是否申请仲裁；不要自己替用户放弃
- 交付要等几天时，建一个心跳任务定期调 `poll`，有 `anchored` 或 `step-request` 再唤起用户
