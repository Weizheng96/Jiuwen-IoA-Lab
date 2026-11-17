# A2C：本仓维护的合同协议 SDK

安装名与公开Python子模块保留a2c；唯一维护源码为本仓src/a2c。规范见[本仓A2C协议模块](../../设计/组件/A2C协议/接口.md)，原来源与作者声明见[NOTICE](NOTICE.md)。

Provider.resume_countersign(task_id)仅准备原复签任务的延迟Reply，不执行业务policy、模型、网络或签名；调用方先核旧执行者停止及原效果已知，再由finish续接。原期限与签1不变，已返回决定或签2未知不得重演。

以下保留原SDK使用说明；其中原设计文件名作为历史出处，不是构建或安装依赖。

# A2C Python SDK

> 实际安装版本见[VERSION](VERSION)，现行规则统一见[本仓接口](../../设计/组件/A2C协议/接口.md)。

A2C（Agent 2 Contract）的三方 SDK 与验收器运行器。本目录是一个独立组件，目标是并入
[openJiuwen-ai/agent-protocol](https://github.com/openJiuwen-ai/agent-protocol)，与 MCP、A2A、A4P、registry 并列（目录名 `A2C-python-sdk/`），
由 [jiuwenswarm](https://github.com/openJiuwen-ai/jiuwenswarm) 作为委托方引用。

## 一、快速开始

```bash
python -m venv .venv && .venv/Scripts/pip install -e "src/a2c[a2a,mcp]" pytest   # Linux/macOS: .venv/bin/pip
cd src && ../.venv/Scripts/python -m pytest -q                              # 单元、三种成约方式、A2A、九问接入、恢复与线上细节
```

| 依赖 | 用途 |
|---|---|
| `cryptography` · `rfc8785` | Ed25519 签名（RFC 9864）· JCS 规范化（RFC 8785） |
| `wasmtime` | 验收器沙箱；运行时 `python.wasm` 随包提供（`a2c/verifier/runtime/`，按 sha256 锁定），首次运行解压到 `A2C_CACHE_DIR` |
| `a2a-sdk[http-server]==1.0.0` · `httpx`（可选 `a2a`） | A2A 绑定，与 jiuwenswarm、agent-core 用同一版本 |
| `mcp`（可选 `mcp`） | jiuwenswarm 连接器的 MCP 服务 |

## 二、分层

```
integrations/   jiuwenswarm（委托方：MCP 服务 + 连接器包）· openjiuwen（服务方适配）
binding         A2A 承载：Agent Card 扩展 · DataPart · 任务 · JWKS；客户端传输。参考端点只服务 A2C（扩展 required: true），兼营普通 A2A 须自行分流 executor
client · provider · market · service      三方 SDK 与被认证第三方服务        payment/  付款凭证 · AP2 · 支付方替身
common          密钥目录 · 存储 · Endpoint 分发与任务 · 进程内网络
verifier/       包 · 沙箱（wasmtime + python.wasm）· 请求重放 · 验收与仲裁复算
slots · timing · objects · card · template · pricing · expr · schema      合同的对象与规则（纯函数）
codec · jws · errors                                                      I-JSON · JCS · 摘要 · JWS 头参数 · 错误码
```

依赖单向向上；对象与规则层不做 I/O，时钟由调用方传入。

| 模块 | 落实的设计规则 |
|---|---|
| `codec` · `jws` | 线上编码 §二 §三：I-JSON、JCS、`sha256:` 摘要、扁平分离载荷 JWS、`crit` 恰好列出 `a2c-*`、`kid` 只用于 JWKS 公钥、时刻核对（即发即收 5 分钟 · 取回的签名窗口）|
| `schema` | 线上编码 §七：七个关键字，`format: date` 校验，`const` 不填 |
| `expr` · `pricing` | 线上编码 §八：词法白名单 + AST 白名单，绝不 `eval`；变量解析；银行家舍入；末期取余；定金在 0 与总价之间；超 20% 只提示 |
| `template` · `card` | 合同协议 §二–§四：模板按成约方式校验；不变量 1–3、6；提案卡签名 |
| `objects` | 线上编码 §四：回执、拒收、凭证、信息授权、环节请求与授权、解除报价与通知、清单、报告、认证结论、出处声明 |
| `slots` | 合同协议 §五：槽位签署方、前提与时间顺序；生效时刻；`respondWithin` · `startWithin` 的定约侧核对 |
| `timing` | 合同协议 §九：各期限起算、环节暂停、仲裁依据 |
| `verifier` | 验收器.md：清单核对、只读挂载、无网络、固定时钟与随机数、燃料与内存上限、按调用序号重放、三态结论、仲裁复算 |
| `market` | 交易市场.md：签模板（核认可名单与模型可运行、存快照）、签3、签5 与存证通知、结清结局、出处加签、`recompute` |
| `provider` · `client` | 线上编码 §五 全部操作；材料核对与拒收；环节请求；交付与取件；验收、付款、解除 |
| `payment` | `terms`统一核卡并导出实际金额与双方；AP2 Port保存原授权和回执；`psp.PaymentProvider`为带幂等记录的测试/演示支付方替身，不承担真实支付业务 |

## 三、三方 SDK

| 委托方 `Client` | 服务方 `Provider` | 平台方 `Market` |
|---|---|---|
| `get_templates` 取模板并验签 | `publish` 签模板并请市场背书 | `template.endorse` |
| `draft_card` · `precheck` · `sign` | `template.get` | `card.endorse`（签3） |
| `countersign`（待批准型） | `card.countersign`：核签前审阅材料，`policy.review` | `delivery.anchor`（签5）· `anchor.watch` |
| `draft_request` · `submit_request` · `adopt`（提案型） | `request.submit`：核拟卡费凭证，`policy.draft`，请市场签3 | `card.close`：完成 / 委托方解除 |
| `start`：付定金、交材料 | `work.start`：核凭证、材料摘要与声明，回执或拒收 | `provenance.endorse` |
| `watch` · `poll` · `answer_step` | `request_step` · `step.authorize` | `recompute`：用快照复算（仲裁） |
| `fetch_delivery`（签6）· `verify`（只跑验收器）· `accept`（签7、付款）· `pay`（仅恢复已验收期付款） | `deliver`（签4 → 签5）· `delivery.fetch` · `acceptance.notify` · `payment.notify` | |
| `termination_quote` · `terminate` · `sign_provenance` · `status` | `termination.quote`（上限校验）· `termination.notify` · `close` · `endorse_provenance` | |
| `refund_checkout`：只为原协商解除退款签发AP2收款单 | `bind_contract`：导入生效签1及可选实付定金凭证，不开工；`refund_termination`：按原解除记录退款并保留凭证 | |

服务方的业务判断由 `ProviderPolicy` 回调给出：`review` · `draft` · `termination_fee` · `on_start`。
拟约回调中断后，宿主先核对原执行者已停止及业务副作用，再调用 `Provider.resume_request(task_id)` 取得原任务的延迟应答，交给 `Provider.finish` 续接；该方法本身不调用模型。原请求、已核材料及任务关联由同一 Store 保存，保留原拟卡费凭证与截止时间。拟约决定已经返回、开始签认、版本被取代或已过期时拒绝重拟，按原签名和回执核对；不重复 request.submit，不把重启当作可以重跑的证明。
被认证第三方服务用 `service.CertifiedService`：`issue(verifier, values, files)` 返回认证结论内容。

支付绑定使用`AP2Payment(..., keys=原密钥目录, store=所属Client或Provider的原Store)`，双方共用同一金额及角色校验函数。`PaymentPort.pay`的`deposit_paid`来自调用方已核验的付款记录，不进入合同或AP2线上报文。服务方通过`Provider(payment=...)`配置自己的付款身份；个人助手通过`Client(ap2_key=...)`配置接收退款时签收款单的密钥。真实系统还须由支付方认证这两方的支付身份。

`Client.terminate`先保存原签名与本地解除事实，回执丢失仍阻止继续履约；同一决定重传不重签。AP2 Port在发送前保存准确原授权，参考支付方保留原过账及签名结果；成功后重启或授权到期可返回原件。未知且原授权已过期时保留原请求，不自动换授权重付。`refund_checkout`由宿主在原合同消息控制通道调用，不作为收费的个人助手接口。

`Client.pay(cardDigest, period)`核原签5/7，只恢复已验收期的分期款，不重新验收或签字；零金额返回null，不生成凭证。原成功凭证先保存，再通知服务方，只有实际`ok=true`回执才记为通知完成；通知丢失后只补通知。后续交付追加到同一张卡时，原付款仍使用已保存的准确对象与授权。一个Client的同卡生命周期操作串行；宿主创建多个Client共享存储时仍须按同一业务交互互斥，不以各实例的进程内锁替代宿主所有权。

参考AP2绑定仅把可信适配器产生、与配置支付方及准确原body摘要匹配的`PaymentRejected`记为本次提交明确未过账；通用协议错误、断线或模型文字仍是未知。明确拒绝后的再次付款需要配置`AP2Payment(..., reconcile=可信回调)`：回调接收付款key、支付方、原卡/请求引用、用途、期次和已保存尝试，返回同key的`PaymentReconciliation`。有效原response直接恢复；只有真实付款查询及原尝试/outbox核对均证实无付款、无未决尝试时，才可返回`no_pending=True`并在当前授权内取得新收款单/授权。HTTP404本身不能证明无未决尝试；缺核对能力、错key、未知或冲突均拒绝新授权。回调是宿主装配能力，不接受模型提供的“未付款”布尔值。原拒绝及每次准确body保留，未知尝试继续使用原body。

```python
import httpx

from a2c.binding import A2ATransport
from a2c.client import Client
from a2c.common import Directory
from a2c.jws import Signer

t = A2ATransport()
client = Client(Signer.generate("urn:a2c:client:me"), keys=Directory(fetch=t.jwks), transport=t, fetch=lambda uri: httpx.get(uri).content,
                payment_provider={"id": "https://pay.example", "jwksUri": "https://pay.example/.well-known/jwks.json"})
[template] = client.get_templates("https://provider.example/.well-known/agent-card.json")
```

## 四、接入九问

| 角色 | 方式 | 九问其他仓的改动 |
|---|---|---|
| 委托方 · jiuwenswarm | 连接器包 [`integrations/jiuwenswarm/connector/`](a2c/integrations/jiuwenswarm/connector/)：stdio MCP 服务 `a2c-client-mcp` + 技能 `a2c-client`；合同、密钥、交付件存 `A2C_HOME` | 无。装连接器（放入 `<workspace>/mcp/mcp_hub/a2c/` 或经技能市场），再在 `config.yaml` 打开确认 |
| 服务方 · openJiuwen 智能体 | `integrations.openjiuwen.provider_app` 起一个 a2a-sdk 应用；智能体经 `CallbackPolicy.on_start` 接活，用 `Provider.deliver` 交付 | 无。要走 `Runner` / `AgentAdapter` 时用其公开接口 `register_server_adapter("A2C", factory)` |
| 市场 | `Market` + `binding.build_app` | 无。A2X registry 目前会丢弃 Agent Card 的 `capabilities.extensions`（上游 PR #143 在修），修好前把 A2C 声明另存一份顶层字段 |
| 支付 | 付款方经`payment.ap2.AP2Payment`付款；负解除余额时服务方付款、个人助手签退款收款单 | 无；支付方须是能读卡的持牌机构 |

jiuwenswarm 里让签名与付款逐次经用户确认（纯配置）：

```yaml
permissions:
  enabled: true
  mode: manual
  tools:
    mcp_a2c_sign_card: ask
    mcp_a2c_submit_request: ask
    mcp_a2c_start: ask
    mcp_a2c_answer_step: ask
    mcp_a2c_accept: ask
    mcp_a2c_terminate: ask
```

交付要等几天：智能体按技能说明建心跳任务，定期调 `mcp_a2c_poll`。

## 五、并入 agent-protocol

| 项 | 做法 |
|---|---|
| 目录 | 整个 `src/` 复制为 `A2C-python-sdk/` |
| 文档 | 本 README 改作组件 README；`A2C_PROTOCOL.md` 取自 [材料/IETF草案](../材料/IETF草案/README.md)；`A2C_SDK_DESIGN.md` 取本文第二、三节 |
| 根 README（中英各一份） | 组件介绍、核心能力小节、选型表、目录树、入门表、文档链接各加一处 |
| 第三方声明 | `cryptography` · `rfc8785` · `wasmtime` · `a2a-sdk` · `mcp` · CPython（PSF 许可，随运行时归档） |

## 六、测试

| 文件 | 覆盖 |
|---|---|
| `test_codec_jws` | JCS 数字与键序、I-JSON 拒收、时长与时刻、JWS 头参数绑定、`kid` 规则、`crit`、新鲜度 |
| `test_schema_pricing` | 子集关键字、取值校验、表达式拒收、银行家舍入、设计示例 3660 / 366 / 2928、末期取余、定金上下限与提示 |
| `test_template_card` | 模板十条规则、卡的不变量、提案型不变量 6 |
| `test_verifier` | 时钟与随机数确定、无网络、只读、无主机路径、不能睡眠、燃料上限、按序重放与不一致、吞掉请求仍拦得住、包核对 |
| `test_flow_countersign` · `test_flow_formations` | 三种成约方式全流程；skill 三次观察；出处双签；拟卡费、退回改版、逾期采纳；认证结论还不能判再通过；仲裁重放不调服务；市场拒签不可仲裁的模板 |
| `test_request_resume` | 原Store重启续接同一拟约任务；原请求、材料、费用凭证与期限保留；未完成材料核验、版本替代及进入签认后拒绝重拟 |
| `test_flow_rules` | 材料预校验、声明不符失效、交错文件重发、签前审阅；复签逾期与不新鲜、开工逾期、环节暂停顺延、未答复与逾期验收的仲裁依据；槽位违规；协商与单方解除、解除价上限、余额正负；不通过的报告与仲裁复算 |
| `test_termination_payments` | 两种成约模式的正/零/负余额、未开工实际定金、真实AP2退款与双方密钥、原通知/回执丢失及重启、成功后授权到期和未知未付授权到期、旧期报价和伪装解除对象 |
| `test_binding_a2a` | 同样的流程跑在 a2a-sdk 1.0 JSON-RPC 上：交付任务、环节请求经 `GetTask`、存证通知、扩展必须声明；慢审核不挂住请求、委托方轮询取结果 |
| `test_integrations` · `test_guards` | MCP 工具走完一单、连接器包格式、身份持久化；不 `eval`、不起子进程、运行时归档摘要、服务方回调 |
| `test_review_fixes` | 支付方与市场的信任、解除后不得追加签名、未付定金按 0 计、认证结论的绑定、分期有理数精确拆分、包的路径与条目上限、`reads` 的限制 |
| `test_wire_details` | 表达式细则（一元符号、前导零、空白、节点与层数）、同时只能一个环节请求、存证订阅随结清结束、样件出处声明验签模板的市场；签3 不收提案卡、拟卡时限以签3 判、取回的签名核窗口、出处加签带结清的卡、过期的未开工记录清理 |
