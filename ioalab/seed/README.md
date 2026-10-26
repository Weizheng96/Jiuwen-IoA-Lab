# 种子：服务定义 · 需求 · 分类树

## 服务定义（`services/<id>.json` + 同名 `.py` 验收器）

```json
{
  "id": "wechat-posts",                      // 小写字母数字连字符；平台 agent id = svc-<id>
  "name": "朋友圈文案工坊", "description": "…（A2X 按 name + description 做发现，要写清做什么、多少钱、多久）",
  "tags": ["文案"], "runnable": true,
  "formation": "preapproval",               // preapproval | countersign | proposal
  "deliverable": {"name": "posts", "title": "朋友圈文案", "file": "posts.json", "mediaType": "application/json",
                  "description": "…", "specSchema": {"shop": {"type": "string", "maxLength": 40}, "count": {"const": 3}}},
  "inputs": [{"name": "source", "title": "原文", "description": "…", "required": true,
              "specSchema": {"chars": {"type": "integer", "minimum": 1, "maximum": 20000}}, "reviewBeforeSigning": false}],
  "steps": [{"name": "publish", "description": "以用户名义发布"}],        // 可选：可授权环节
  "deadlines": {"respondWithin": "PT10M", "startWithin": "PT30M", "deliverWithin": "PT30M", "acceptWithin": "PT15M", "authorizeWithin": "PT15M"},
  "pricing": {"price": "6"},                 // 预批准 / 待批准：定数或公式（可引用 inputs.<name>.<key>）；提案型：{"proposalFee": 1, "range": [10, 30]}
  "verifier": {"id": "wechat_posts@1.0", "reads": ["spec.posts.shop", "spec.posts.count"]},   // source 取同名 .py；提案型也要（写在卡上）
  "notCovered": ["文采"],
  "generator": {"system": "…交付器系统提示（平台内部概念，不入协议）…", "output": "{\"posts\": [\"…\"]}"},
  "sample": {"posts": ["…"]}                 // 桩交付件：selfcheck / smoke 不调模型时用，必须能过验收器
}
```

规则：
- `deliverable.name`、`inputs[].name`、`specSchema` 的键都是 **ASCII 标识符**（验收器 `reads` 的正则只认 `spec.<name>.<key>` / `inputs.<name>.<key>`）；中文放 `title` / `description`。
- `specSchema` 取值域只许 `const · enum · type · minimum · maximum · maxLength · format`；`const` 的键委托方不填。
- 验收器是 L1 脚本：`def verify(ctx)`，`ctx.read(filename)` 读交付件字节、`ctx.values["spec.<d>.<k>"]` 读卡上声明的标量；
  返回 `{"status": "pass"|"fail", "checks": [...]}`。沙箱里只有 CPython 标准库（WASI，无 sqlite3），确定性、无网络。
- 交付件由服务方智能体按 `generator` 生成（JSON），平台去围栏后 `json.loads` 严格解析，再在沙箱里用同一验收器自测（失败重生成 ≤ 2 次）。
- `runnable: false` 的是牌子：被选中时 `review` 立即返回拒绝；`timeout_reject: true` 的那一块不答复、走超时。

## 需求（`needs.json`）

```json
[{"owner": "奶茶店主", "assistant": "小艺", "service": "wechat-posts",
  "text": "帮我的奶茶店「茶小满」写三条朋友圈…", "scope": {"budget": 30, "category": "文案", "sign1": "client", "sign7": "client"},
  "autoSign": true, "materials": [{"name": "source", "file": "01_source.txt", "declared": {"chars": 420}}]}]
```

材料文件放 `materials/`，seed 时拷到 `data/needs/<序号>/`。

## 分类树（`taxonomy/`）

`python -m ioalab build-taxonomy` 用 `data/a2x` 里的服务离线建 A2X 分类树（需要 `APIkey/a2x/llm_apikey.json`），
产物 `taxonomy.json · class.json · build_config.json` 拷回这里入库；平台启动时拷到 `data/a2x/.../taxonomy/`，克隆者零 LLM。
