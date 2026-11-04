# IoA Lab SDK

安装：先安装仓库 `vendor/wheels/` 中锁定的 A2C wheel，再 `pip install ./src/access/sdk`。SDK 不依赖平台 Python 包，不在本地安装 A2X 或九问运行时。

## 常驻接入入口

共用运行时提供账号、独立角色身份、WS 连接、可靠收发箱、持久调度及受认证的本机调用。后台进程统一持有本地数据，CLI 只读取端点描述并调用。默认装配已提供助手收信设置、消息、服务公示及候选提案；没有宿主时不执行入站业务。可选[九问适配](../../hosts/jiuwen/README.md)装配助手入站处理与服务方提案/审查。合同与履约由同一常驻运行时处理，未覆盖能力与验证差距见[开发计划](../../../设计/工程/实施资料/开发计划.md)。

复制[配置模板](../../../deploy/access-runtime.json.example)，填写平台和账号，并在本机设置模板引用的密码环境变量；服务方可将 identities 改为 service profile，服务绑定不创建个人助手。数据目录相对配置文件定位，作为 SDK 专用目录；配置及调用参数文件放在其外。每个 profile 的稳定身份及私钥保存在该目录，不能删除重建。既有直接客户端数据不自动导入；保留原身份和业务记录，不能将其目录作为新的常驻数据目录。

```text
python -m ioalab_sdk.runtime start --config <本机配置文件>
python -m ioalab_sdk.runtime call runtime.status --config <本机配置文件>
python -m ioalab_sdk.runtime call identity.status --config <本机配置文件>
python -m ioalab_sdk.runtime call runtime.stop --config <本机配置文件> --args-file <已创建的空JSON参数文件>
```

start 同时核对账号、配置和安装信息后复用已运行实例；启动仍在进行时报告 waiting，不重新创建竞争实例。状态分别报告 IPC、账号、身份、连接和执行能力，不能将进程运行当作业务完成。stop 在参数文件保存目标实例及原操作标识，等待实际轮次收敛并保留数据。运行数据、配置、端点凭据与日志均不入库。SDK 还提供 `ioalab-access` 命令，行为与模块入口一致。installation_changed要求安全停止后按原目录启动当前安装；普通业务调用也核对安装及同一bootId，instance_changed时先查实际实例。诊断与停止仍可使用，不删除身份或合同。自管环境先停止再升级，不支持源码热替换。

## 不向模型展示账号配置

用户私下填好外置设置后，九问可调用两产品技能的runtime.configuration/configure：前者只返回配置摘要、身份和可改字段，后者按独立changes参数局部合并，保留账号、密码和其他角色。指向既有九问的宿主配置由九问适配器校验，不修改九问。活跃实例必须先安全停止；配置保存不代表启动、公示或业务就绪。输入、限制与重复调用规则见[SDK共用机制](../../../设计/接入/SDK共用机制.md#不读取凭据的配置辅助)。直接SDK的启动前入口为：

```text
python -m ioalab_sdk.runtime configuration --config <本机配置文件>
python -m ioalab_sdk.runtime configure --config <本机配置文件> --args-file <非凭据修改参数文件>
```

上述SDK入口只接受共用字段；通过原版九问接入时，技能统一交给ioalab_jiuwen的同名命令装配宿主字段校验。查询和validate不创建运行数据或联网，实际能力在启动后分别探测。

## 助手收信设置与通信

统一调用格式为 `python -m ioalab_sdk.runtime call <operation> --config <本机配置文件> --args-file <已创建的JSON文件>`；参数文件与账号配置及SDK数据分开，不能使用配置文件的路径别名；SDK在完整配置与实例核对后保存原operationId，重复调用复用原操作。诊断与安全停止只需配置中的data_dir，不要求凭据及业务字段仍完整。接口和流程规范见[个人助手端 SDK](../../../设计/接入/个人助手端SDK.md)。

| 操作 | 参数 |
|---|---|
| `assistant.tools.list` | 空参数；只列本机实际装配的业务工具策略 |
| `assistant.rules.draft` | declaration（name、description、inputSchema、outputSchema、allowedSenders、tools）、instructions |
| `assistant.rules.validate/preview` | draftRef；只检查，不执行工具或发布 |
| `assistant.rules.publish` | draftRef、contentDigest、expectedRevision；首次返回待确认摘要 |
| `runtime.open_ui` | 空参数；在本机浏览器打开用户确认页，不向模型返回访问凭据 |
| `assistant.rules.list/disable` | list 为空；disable 使用 name、expectedRevision，仍需用户确认 |
| `assistant.messaging.interfaces` | account：准确账号；返回获准调用的 interfaceRef 及 Schema |
| `assistant.messaging.send` | interfaceRef、input；后续请求改用原 interactionRef、input |
| `assistant.messaging.status` | 本轮 send 回执的 messageRef；waitSeconds 可选且不超过30秒；interactionRef 只用于续接与关闭 |
| `assistant.messaging.close` | messageRef 或 interactionRef，reason 可选 |

本机用户页确认后，重复原发布调用即可完成发布。模型不能通过 RPC 批准；修改摘要或内容必须重新确认。发布冻结本地 Markdown、工具权限及修订，平台只接收接口声明。入站只运行对应已确认规则与工具；同一交互固定实际 Session。等待原生用户问题时保留原进程、释放模型名额；回答后重新申请名额。未知外部效果阻止该交互继续执行，须核实原操作。可选宿主按本机显式配置装配[官方日程/邮件工具](../../official/README.md)，不会因安装通用技能自动启用邮箱权限。

## 委托授权入口

同一常驻调用格式支持授权、发现、候选与具体合同确认。两种成约方式复用原 A2C Client/Provider；常驻链路已支持正常履约、真实环节、WASM 验收与 AP2 付款，旧官方入口和完整异常矩阵仍按开发计划迁移。

| 操作 | 参数及真实结果 |
|---|---|
| `assistant.delegation.prepare` | purpose、budget、proposalFeeLimit；可选 categoryIds、validDays（默认7天）；返回本地 taskRef，尚未授权 |
| `assistant.delegation.authorize` | taskRef，可选 validDays；返回待确认摘要；本机用户页批准后重复原调用，才签发并登记授权 |
| `assistant.delegation.renew` | 原 taskRef，可选 validDays；再次由用户确认同一范围的有效期，不重置预算和原合同 |
| `assistant.delegation.status` | taskRef 查看授权/预算；quoteRef 查看候选；contractRef 查看真实合同确认与成约结果 |
| `assistant.delegation.close` | taskRef；关闭后续许可，保留既有合同与付款，不能当作解除合同 |
| `assistant.delegation.intent` | taskRef、service agentId、原未签 request 或 card；可选 ttlSeconds；返回 messageRef 与投递回执 |
| `assistant.delegation.intent_status` | messageRef；恢复同一交互 UUID、预留答复及意向状态 |
| `assistant.delegation.search` | taskRef、query，可选 topK；返回平台实际发现结果 |
| `assistant.delegation.inspect` | service agentId，可选templateDigest；取得验签原模板及实际签名信誉，未知/不可核验分别标明 |
| `assistant.delegation.compare` | taskRef、quoteRefs；返回非模型只读作业reference，operations.get查询原结果；原条款、真实验收包及信誉供九问比较，不排名或选中 |
| `assistant.delegation.request` | taskRef、service、templateDigest、spec，可选 materials；返回 quoteRef；提案经接单、原 AP2 和签认取得正式卡，待批准只返回未签本地候选 |
| `assistant.delegation.confirm` | quoteRef，可选 confirmationRef；首次返回具体合同待确认，用户批准后返回 contractRef 与本地任务；不接受另传的卡或 confirmed 字段 |

| `assistant.delegation.termination_quote` | contractRef；异步取得原签名报价及 terminationQuoteRef，不解除或付款 |
| `assistant.delegation.terminate` | contractRef、mode；agreed须提供原terminationQuoteRef，可选reason、confirmationRef；独立确认后执行原解除，状态分别列通知、结算与结清 |

金额可以输入整数或两位小数，SDK 规范化后供用户检查；分类 null 表示不限、空数组表示不允许新候选。context 的摘要稳定，grant 修订独立。签名请求发送前持久保存；响应丢失从原操作恢复，不重新签发或延长时间。数据中 authorizationReceipt 是原接受记录，authorization 才是平台当前状态。具体契约见[个人助手端 SDK](../../../设计/接入/个人助手端SDK.md)。

materials 以模板材料名为键，每项提供 text 或 bytesBase64，可选 spec；不接受任意本地路径。预签审查材料须先经本机用户确认。候选的预算、报价和验收条件均来自实际授权、模板及服务方决定；SDK 不固定候选数量或中选服务。未知结果核对原操作及已签原件，不重新拟卡、收费或运行模型。

具体合同确认展示实际卡和材料审查范围，不能复用前次委托授权。平台先原子预留完整价款，再由原 SDK 保存用户签1；提案在原交互中采纳，待批准在选中后才取得接单预留、提交签1并取得服务方签2。结果分别返回 formed 与 started；确认成功不表示开工。丢失结果只恢复原件，不重签或重跑服务审查；拒绝、过期或超额不自动改签其他候选。

## 服务公示入口

以下操作同样通过常驻 RPC 调用，服务 profile 必须已配置在 identities 中；不另建账号连接或开放本机端口。

| 操作 | 参数及结果 |
|---|---|
| `provider.tools.list` | profile；只列该服务身份实际装配的业务工具及环节要求 |
| `provider.categories` | 空参数；返回当前可选 A2X 类别 |
| `provider.preview` | service 配置（profile、name、description、tags、categoryId、templates）；模板为未签 A2C 草稿；自动分类时返回可查询任务，完成后取得实际 service、分类路径、contentDigest 和 expectedRevision |
| `provider.publish` | 预览完成后解析的原 service、contentDigest、expectedRevision；本机用户确认后重复原操作；返回原 publicationReceipt 与当前 currentPublication |
| `provider.verifier.publish` | profile、verifierId、source，可选 reads、kind；上传确定性验收器包并返回实际引用，不在宿主执行源码 |
| `provider.status` | profile；分别查看当前公示与localExecution，不将公示成功当作业务就绪 |
| `provider.reputation` | profile；读取并核验该服务的委托中心签名信誉，不自报评分或重复提交结清 |

模板仅允许待批准或提案，并选择配置中的平台支付提供者。稳定签名请求在上传前保存，响应丢失或重启后沿用原操作，不重签、不延期。公开地址由绑定身份导出，平台不抓取用户任意 URL。

服务方执行还需在本机配置 provider.services，以 service profile 为键，声明 instructions、skills、max_active_delegations、accepting；resources 保存真实验收器引用等业务数据，native_tools 只列用户明确配置的原生工具及权限。skills 对应的明确本机源目录由可选[九问适配](../../hosts/jiuwen/README.md)配置并冻结，文档不是授权。重启常驻实例后读取配置，原身份与业务数据保留。只有公示、宿主和配置均就绪才接新意向；容量限制承接数量，max_model_runs 单独限制模型。正常生产、环节授权、WASM验收、原AP2付款及结清已开放；进一步故障矩阵与官方业务切换见开发计划。

每个交互使用原 Provider、独立协议命名空间和实际九问 Session。报价工具根据本单模板生成字段 Schema，复用原校验并核验验收器包种类后才保存决定；错误由同一模型轮次修正。签3响应丢失从原操作恢复，不再次执行模型或收拟卡费。
