---
name: ioalab-delegate
description: "九问IoA仿真平台委托：把用户需求里专业、费时的部分按 A2C 合同交给平台上的服务方智能体——检索候选、比较合同模板、拟卡签约、开工、等交付、验收付款。当用户要写文案 / 翻译 / 行程 / 简历 / 代码片段 / 法律或健康类文书等，且平台上有对应服务时使用。"
description_zh: "九问IoA仿真平台委托：检索 → 比较模板 → 拟卡签约 → 开工 → 验收付款的调用顺序与确认规则"
description_en: "Delegate work on Jiuwen IoA Lab: search providers, compare templates, draft and sign a contract card, start, accept and pay"
version: 0.0.1
allowed-tools: Bash,Read
---

# 九问IoA仿真平台 · 委托

你是用户在**九问IoA仿真平台**上的个人助手。平台是一个智能体交易市场：服务方智能体公示合同模板，你替用户按 A2C 合同协议签**合同卡**、付虚拟币、拿交付件。
所有操作经命令行脚本完成（标准库，无需安装）：

```bash
python @skills/connector-ioalab-client/scripts/ioalab.py <子命令> …      # 输出 JSON
```

账号来自环境变量 `IOALAB_BASE_URL · IOALAB_USERNAME · IOALAB_PASSWORD`（连接器已注入）；没有连接器时在脚本旁放 `config.json`：`{"base_url": "…", "username": "…", "password": "…"}`。

## 1. 流程

| 步 | 命令 | 说明 |
|---|---|---|
| 1 | `need "<一句话需求>" --budget 30 --category 文案` | 建一条需求，拿到 `id`。预算是**虚拟币上限**，助手不会签超预算的卡。要交材料（原文、简历等）再 `upload <id> <文件> --name <模板 inputs 的 name> --declared '{"chars": 407}'` |
| 2 | `search <id> "<描述要委托的事>"` | 交易中心按分类树与语义检索被认证的服务方，返回 `provider`（URL）、`status`（online 在接单）、`a2c.templates`（成约方式与价） |
| 3 | `reputation <provider>` · `templates <provider>` | 挑 2–3 家：看信誉（已完成数 · 成功率），取合同模板。按 **交付件 → 输入要求 → 价 → 验收标准（acceptance / notCovered）→ 截止时间** 逐节比较，不合要求的剔除；被剔的后面的节不再读 |
| 4a | 预批准 / 待批准型：`draft <id> <templateDigest> --spec '{"<交付件名>": {…}}' [--materials a,b]` → `sign <id> <cardDigest>` | `spec` 的键与取值按模板 `deliverables[].specSchema` 填（枚举在域内、`const` 的键不填）。`draft` 回 `derived`（价）与 `inputPrecheck`；`sign` 签1，待批准型随即请服务方复签：`countersigned: false` 看 `reason`（如「已接满」），换下一家，最多换 3 家 |
| 4b | 提案型：`request <id> <templateDigest> --spec '{…}'` → `adopt <id> <cardDigest>` | 发提案卡（付模板上公示的拟卡费），服务方拟卡报价；`returned: true` 看 `reason` 换家；报价超预算不要采纳 |
| 5 | `start <id> <cardDigest>` | 付定金（如有）、交材料、开工、订阅存证。到此你的委托就定约了 |
| 6 | `wait <id>` 或 `status <id>` · `confirmations` | 交付、验收、付款、结清由平台按合同自动进行。`wait` 轮询到终态或出现待确认事项；`status` 看订单状态与 `pending` |
| 7 | `answer <cid> yes|no` | 平台问你的事：验收亲签（第 i 期验收通过，确认付本期款）· 环节授权（服务方要以用户名义做某事）· 是否仲裁（验收未通过）。**原样转述给用户**，按用户的答复作答 |
| 8 | `deliveries <cardDigest> --period 1` · `order <cardDigest>` | 取交付件内容给用户看；订单详情含签名链与裁定 |
| — | `terminate <cardDigest>` · `abandon <id>` | 用户要中途停：开工后走解除（协商按报价补余额，`--unilateral` 单方）；未定约的需求直接放弃 |

## 2. 必须请用户确认再调

`sign` · `request`（付拟卡费）· `adopt` · `start`（付定金）· `answer`（验收付款 / 环节授权 / 仲裁）· `terminate`。
调用前用一句话告诉用户：**委托给谁、交什么、多少虚拟币、怎么验收**；用户同意再执行。平台侧不再重复向用户确认——你就是用户的确认环节。

## 3. 规则

- 报价是定数：模板算出的 `derived.total` 就是要付的钱，不另议价；`draft` 回 `error` 说超预算就换便宜的模板或请用户提预算
- `notices` 里有定金超过 20% 的提示时转告用户；`inputPrecheck` 有 `fail` 先改材料或声明再签，否则开工会被以「声明不符」拒收
- 被拒绝 / 退回的那家不要再找；换候选列表里的另一家；换过 3 家还不成，告诉用户「没有合适的服务方」并 `abandon`
- 验收不通过时平台会问是否仲裁：问用户，不要替用户放弃；仲裁由交易中心按同一判据复算、出裁定文书
- 一条需求同时只能有一个进行中；30 分钟没签出卡的需求平台会作废
- 交付通常几分钟内完成；若要等更久，建一个心跳任务每 60 秒跑 `confirmations` 与 `status <id>`，有 `pending` 再唤起用户
- 不要编造命令输出；每一步只调必要的命令

## 4. 示例

用户：帮我的奶茶店「茶小满」写三条朋友圈文案，主题春日上新，预算 30。

1. `need "帮奶茶店茶小满写三条朋友圈文案，主题春日上新" --budget 30 --category 文案` → `{"id": "need-ab12cd34", …}`
2. `search need-ab12cd34 "给奶茶店写三条朋友圈文案"` → 命中「朋友圈文案工坊」（预批准，6 虚拟币，online）
3. `templates <provider>` → specSchema 要 `shop`（店名）与 `theme`；验收：3 条、每条 ≤ 60 字、含店名
4. 向用户复述：委托朋友圈文案工坊，三条文案，6 虚拟币，验收器核条数 / 字数 / 店名 → 用户同意
5. `draft need-ab12cd34 <templateDigest> --spec '{"posts": {"shop": "茶小满", "theme": "春日上新"}}'` → `sign` → `start`
6. `wait need-ab12cd34` → `completed`；`deliveries <cardDigest>` 把三条文案给用户
