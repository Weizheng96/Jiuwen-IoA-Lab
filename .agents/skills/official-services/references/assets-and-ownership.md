# 资产与账号归属

新增类别、候选、业务工具或生产说明时读本页。先核[架构](../../../../设计/架构.md)、[服务方接入](../../../../设计/流程/服务方接入.md)、[服务方SDK](../../../../设计/组件/客户端/服务方端/接口.md)；编程业务另读[编程服务](../../../../设计/流程/场景/编程服务.md)，其他类别读其真实职责页，不套美甲规格。

## 先填决策表

| 决策 | 本次填写内容 |
|---|---|
| 能力与候选 | 用户目标、载体、必要功能、明确不支持项；现候选中谁合格，新增差异是什么 |
| 所属账号 | 既有领域 slug/category/account，或确需新领域账号的原因；service ID/profile 与账号映射 |
| 输入与交付 | 必需规格、材料及签前审核条件；文件名/mediaType/内容结构，哪些与合同逐项对应 |
| 生产资源 | instructions/业务 Skill、所需工具及最小权限、原版宿主/模型配置；缺项如何显示未就绪 |
| 验收与证据 | 判据、读取字段、反例、未覆盖范围；源码、WASM行为、客户端、构建、部署分别如何取证 |
| 影响范围 | 唯一资产源、生成清单、资源打包、消费者、产物 VERSION、测试与是否需修改设计 |

## 从这些代码入口核实，避免复制权威

| 当前入口 | 所有者与用途 |
|---|---|
| `src/official/services/ioalab_services/assets/domains/*.json` | 领域及服务定义唯一源：slug/label/account/category/services；同一领域集中维护同类候选 |
| `ioalab_services/catalog.py` | `load_domains`校验领域/账号/分类及服务ID唯一；`definitions/account_of/category_of`生成映射；`materialize`装配验收资源；`seed_manifest`生成公开摘要及规范化源文件hash |
| `assets/verifiers/*.py` | 有业务含义的确定性WASM验收源码，或复用`common.py`与资产中的checks；不写一套宿主验收旁路 |
| `ioalab_services/profiles.py:prepare` | `ioalab_services.prepare(service_id, profile=..., verifier_ref=..., payment_provider=...)`纯构造未签`service`及`execution`；复用SDK模板构造，不注册或生产 |
| `ioalab_services/preparation.py`、`deployment.py` | 依目录构造账号下多个profile，上传真实包、核预览、持久化准备；每账号一个SDK运行时，原版宿主随生产装配 |
| `tools/build_seed.py` → `deploy/default-seed.json` | 由官方资产生成核心部署初始化输入；不手改第二份摘要/SQL或在核心反向import官方包 |
| `src/platform/ioalab/seed/{domains,accounts,__init__}.py` | 只消费显式初始化清单，幂等准备账号与分类；不创建服务Agent、运行时或模型 |

创建本技能时目录实读为8领域、8个ISP账号、52项服务，其中dev的10项均归`ISP_008`；这是待复核快照，不是新增上限。后续从`load_domains/account_of`和生成清单重新核对。新增编程品牌仍复用dev账号，不按每条服务建账号；新类型先找既有领域，确无归属才新增领域和唯一账号并验证种子。已有身份、私钥、合同及付款不能因改目录被迁移归属或重新生成。

## 最小交付清单

- **每项能力**：修改所属领域JSON中一份定义，给出稳定id、name/description/tags、formation、deliverable及specSchema、inputs、deadlines、pricing、verifier、notCovered、generator与可验证的sample/sample_spec（需要时sample_inputs）。先看同类现定义和`template_from_definition`，不凭此提纲发明协议字段。
- **有新判据时**：复用已有checks或增加所属验收源码，`reads`声明真实合同规格/材料字段而非交付文件名；判据文字、实现和反例一致。不要只检查`allowCancel=true`就声称取消行为通过。
- **需要新能力时**：专业生产说明在官方服务资产；较大业务Skill/资源须有实际消费者与包资源读取入口。外部效果工具归`src/official/tools/`，模型及权限由显式实例配置注入；无需求不新建工具、Skill或通用生产引擎。
- **随实际影响**：专属正反例测试、生成的初始化清单、资源打包声明和消费者、所属产物VERSION；修改后不得高于设计，发行输入依develop交付参考处理。

`specSchema`使用锁定A2C允许的标量值域，必需字段有真实来源；`inputs`声明必需与签前审查。能力声明必须能让助手对应载体/功能/材料/证明要求。对于声明不支持的目标，返回缺材料或不合格理由，不能删需求、造材料、拿无关候选凑数量。同载体基础与可靠覆盖可以不同，名称或最低价格不决定合格性与推荐。

报价复用原A2C表达式白名单和SDK算价，`priceFormula`不走eval；提案拟卡费与交付总价分列。按现协议/SDK起草期限与authorizableSteps；模型文字、A2UI选择和自测报告不能代替用户签约、环节或验收批准。

交付文件清单（manifest）须与合同交付件、generator输出和验收器读取的文件一致，核实际文件名、mediaType、内容及摘要。通过SDK的`provider.task.selftest/deliver`提交同一份本单`files`，采用text、bytesBase64或本次artifacts内的artifactPath；原协议交付与存证由SDK生成和核验，不另造manifest协议。多文件/新格式超出现资源构造能力时先明确缺口，不把任意二进制交付伪装成JSON或另开旁路。

## 依赖与安装约束

官方包可依赖SDK公开构造/运行契约与AgentHost装配；SDK、平台核心及宿主适配不反向依赖官方行业模块。核心不运行服务生产、不保管参与方私钥。规则、合同/付款状态归原所有者，不复制状态机、连接或履约循环；工具只保自己的业务效果与幂等记录。

资源使用`importlib.resources`或受控显式配置；`src/official/pyproject.toml`当前打包`assets/domains/*.json`、`assets/verifiers/*.py`。新增别种资源须同步package-data及消费者，并验证安装后读取，不能靠源码相邻路径。Demo仅消费官方能力再填场景值，不被官方包反向import。

现官方`Deployment`绑定整个目录及配置摘要，`prepare`保留完整已准备实例和人工修改；不是向旧目录随意追加服务的热更新入口。目录变更遇到既有绑定失配时，按官方实例与安装升级职责保留现场、原身份及未知作业，明确处理迁移，不能删state、换operationId或覆盖配置使检查通过。
