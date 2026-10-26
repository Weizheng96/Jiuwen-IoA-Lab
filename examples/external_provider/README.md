# 外部服务方样例

把自己开发的服务智能体接到九问IoA仿真平台：服务方跑在**你自己的机器**上，平台出站访问它。只依赖 a2c SDK（平台仓的 wheel）、httpx、uvicorn。

```bash
pip install ./vendor/wheels/a2c-0.0.19-py3-none-any.whl "a2a-sdk[http-server]==1.0.0" httpx uvicorn
python examples/external_provider/provider.py --platform http://10.0.0.2:8750 --username alice --password '***' \
    --public-url http://10.0.0.9:9000 --port 9000 [--definition my_service.json] [--category cat_writing] [--stub]
```

| 步 | 样例做什么 | 对应平台接口 |
|---|---|---|
| 1 | 用账号登录拿 token | `POST /api/v1/token` |
| 2 | 上传 L1 脚本验收器源码（`def verify(ctx)`）→ `ioalab://pkg/<digest>` | `POST /api/v1/provider/packages`；三方取包 `GET /api/v1/packages/<digest>` |
| 3 | 按服务定义组合同模板（验收器引用上一步的 uri，`paymentProviders` 填平台支付方），起 A2A 应用（含 AP2 收款角色） | 平台 `GET /api/config` 给出 `market` · `pay` · `publicUrl` |
| 4 | `Provider.publish`：向平台市场发 `template.endorse`，拿回市场签认的模板 | `<平台>/a2a/market`（公开） |
| 5 | 登记：地址 · 名称 · 领域 · AP2 收款公钥 | `POST /api/v1/provider/register` → 平台取 Agent Card / JWKS、认证公钥、取模板、写 A2X 并归叶类 |
| 6 | 接单：`work.start` 入队 → 生成交付件（有 `IOALAB_LLM_API_KEY` 则调模型，否则用定义里的 `sample`）→ `deliver` | 平台的助手取件、验收、付款 |
| 7 | 收到末期付款凭证后 `close`：向市场结清 | 平台按市场 ledger 把订单标完成，收款进登记账号的钱包 |

服务定义与平台内部服务同一 schema（见 `ioalab/seed/README.md`），验收器只能是脚本（`verifier.source`）。默认内置「外部回声写手」：按主题写一段 60–300 字短文，3 虚拟币。
密钥（Ed25519 签合同 · ES256 收款）生成在 `--data-dir`（默认 `./ext-data/`）下的 `keys.json`，别入库。
