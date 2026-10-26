# 种子：领域 · 服务定义 · 需求池 · 分类树

部署时随平台自带（首次启动自动注入，或 `ioalab init`）。规则见 [设计/交易中心.md](../../设计/交易中心.md) 与 [设计/智能体运行时.md](../../设计/智能体运行时.md)。

```
domains/<nn>-<slug>.json   一个领域 = 一个 ISP 账号 + 分类树一个叶类 + 6 份服务定义（4 预批准 + 1 待批准 + 1 提案型）
verifiers/common.py        声明式验收规则引擎（在 python.wasm 沙箱里跑）；verifiers/<name>.py 脚本验收器
needs/<slug>.json          需求池：每领域 3 条；材料在 materials/
accounts.py                ISP_001…（密码 001…，N = 领域文件数）· ISC_001…（M = IOALAB_ISC_COUNT）
domains.py                 领域文件 loader：把 verifier.checks / verifier.script 统一成运行时要的 {id, reads, source}，补 category · capacity
```

## 领域文件

```json
{"slug": "health", "label": "医疗健康", "account": "ISP_001",
 "category": {"id": "cat_health", "name": "医疗健康", "description": "…（A2X 导航时模型读它）", "decision_rule": "需求与…有关时在此找"},
 "services": [ 服务定义… ]}
```

## 服务定义

```json
{
  "id": "wechat-posts", "name": "朋友圈文案工坊",
  "description": "…（A2X 按 name + description 做发现，要写清做什么、多少钱、多久）",
  "tags": ["文案创作", "朋友圈"], "formation": "preapproval",          // preapproval | countersign | proposal
  "deliverable": {"name": "posts", "title": "朋友圈文案", "file": "posts.json", "mediaType": "application/json",
                  "description": "…", "specSchema": {"shop": {"type": "string", "maxLength": 40}, "count": {"const": 3}}},
  "inputs": [{"name": "source", "title": "原文", "required": true, "specSchema": {"chars": {"type": "integer", "minimum": 1, "maximum": 20000}}}],
  "steps": [{"name": "publish", "description": "以用户名义发布"}],       // 可选：可授权环节
  "deadlines": {"respondWithin": "PT10M", "startWithin": "PT30M", "deliverWithin": "PT30M", "acceptWithin": "PT15M"},
  "pricing": {"price": "6"},                  // 预批准 / 待批准：定数或公式（可引用 inputs.<name>.<key>）；提案型：{"proposalFee": 1, "range": [10, 30]}
  "verifier": {"id": "wechat_posts@1.0", "checks": [ {"kind": "list_len", "path": "posts", "eq": "spec.posts.count"}, … ]},
  "notCovered": ["文采"],
  "generator": {"system": "…交付器系统提示…", "output": "{\"posts\": [\"…\"]}", "quote_system": "…（提案型报价提示）"},
  "sample": {"posts": ["…"]}, "sample_spec": {"shop": "小满美甲", "theme": "春日上新"}, "sample_inputs": {"source": {"chars": 407}},
  "capacity": 1                               // 待批准 / 提案型默认 1（撞单即「已接满」），预批准默认 3
}
```

规则：
- `deliverable.name`、`inputs[].name`、`specSchema` 的键都是 **ASCII 标识符**；中文放 `title` / `description`。`specSchema` 取值域只许 `const · enum · type · minimum · maximum · maxLength · format`；`const` 的键委托方不填（`sample_spec` 里也不写）。
- 验收器两种写法：**声明式** `checks`（下表）由 `verifiers/common.py` 执行，规则里 `"spec.<deliverable>.<key>"` / `"inputs.<name>.<key>"` 取卡上声明的标量，`reads` 自动推出；**脚本** `{"id", "reads", "script": "<name>.py"}`，源码取 `verifiers/<name>.py`（`def verify(ctx)`，`ctx.read(file)`、`ctx.values[...]`，返回 `{"status", "checks"}`）。沙箱里只有 CPython 标准库，确定性、无网络。
- 交付件由服务方按 `generator` 生成（JSON），平台去围栏后严格解析，再在沙箱里用同一验收器自测（失败只把未通过的检查项喂回模型，≤ 3 次）；`sample` 必须能过验收器（`tools/check_services.py` 核）。

| check kind | 字段 | 判什么 |
|---|---|---|
| `keys` | path · required | path 处每个对象都有 required 键 |
| `list_len` | path · eq / min / max | path 处每个列表长度 |
| `each_str_len` | path · min / max | path 展开的每个字符串长度 |
| `each_contains` | path · value | 每个字符串都包含 value |
| `contains_all` · `forbid` | path · words（列表 / spec 字符串按 split 切）| 全部文字包含每个词 / 不含任何词 |
| `sum_le` · `each_num` | path · max / min | 数值之和 ≤ max / 每个数值在区间 |
| `each_in` · `each_regex` · `unique` | path · in / pattern | 枚举 / 正则全匹配 / 互不重复 |
| `str_len` · `total_chars` · `cjk_ratio` · `word_ratio` · `count_true` | path · 区间或 base | 单个字符串长度 / 全部字符串总字数（可相对 base）/ 中文占比 / 英文词数比 / 真值个数 |

## 需求池（`needs/<slug>.json`）

```json
[{"service": "wechat-posts", "text": "帮我的奶茶店「茶小满」写三条朋友圈文案…",
  "scope": {"budget": 30, "category": "文案", "sign1": "client", "sign7": "client"}, "autoSign": true,
  "materials": [{"name": "source", "file": "01_source.txt", "declared": {"chars": 407}}]}]
```

`seed_all` 把材料拷到 `data/needs/pool/<slug>/`，并把每条需求轮转分给 ISC 账号的助手，写 `data/dispatch.json` 供管理员「投放」与流量生成器用。

## 分类树

`market/taxonomy.py` 按领域文件确定性生成（root → 8 个叶类 → 服务），零 LLM；`service_hash` 在注册之后按 A2X 同法回写。运行期注册的服务按声明的 `category` 归入叶类，没有声明的进 `cat_misc`。
