# 改动路由表

按改动所在目录找到要读的职责页、要跑的定向测试和所属产物，只读所列页面。测试名对应 `tests/test_<名>.py`，`*` 为前缀匹配；`tools/check_scope.py` 持有更细的文件级映射，`check.py --changed-from <已提交基线>` 可据此自动选测试。

| 改动目录 | 先读职责页 | 定向测试 | 产物 VERSION |
|---|---|---|---|
| `src/platform/ioalab/identity/` | [身份中心](../../../../设计/组件/身份中心.md) | auth, auth_failures, authentication_recovery, account_enrollment | `src/platform/VERSION` |
| `src/platform/ioalab/registry/` | [注册中心](../../../../设计/组件/注册中心.md) | category_preview, search_availability, service_publication, publication_authorizations | 同上 |
| `src/platform/ioalab/delegation/` | [委托中心](../../../../设计/组件/委托中心.md)、[A2C适配](../../../../设计/接入/A2C适配.md) | delegation_*, protocol_relay, provider_admission, arbitration*, payment_rejection, rule_boundaries | 同上 |
| `src/platform/ioalab/payments/` | [支付中心](../../../../设计/组件/支付中心.md) | payment_*, account_rewards, delegation_scopes, rule_boundaries | 同上 |
| `src/platform/ioalab/messaging/` | [消息中心](../../../../设计/组件/消息中心.md) | agent_delivery, message_boundaries, protocol_relay, runtime_delivery | 同上 |
| `src/platform/ioalab/observation/`、`web/` | [通用规范](../../../../设计/界面/通用规范.md)、对应界面页、[平台接口](../../../../设计/接入/平台接口.md) | admin_api, observation_views, workbench_*, frontend, account_views | 同上 |
| `src/platform/ioalab/configuration*.py`、`web/configuration.py` | [平台配置](../../../../设计/工程/平台配置.md) | platform_configuration, configuration_web, configuration_migration | 同上 |
| `src/platform/ioalab/migrations/`、各组件 `migrations.py`、`storage.py` | [架构](../../../../设计/架构.md) 数据归属 | domain_migrations, protocol_operations, core_ownership | 同上 |
| `src/platform/ioalab/app.py`、`operations.py`、`readiness.py` | [架构](../../../../设计/架构.md) 平台模块与依赖 | core_*, protocol_operations | 同上 |
| `src/access/sdk/ioalab_sdk/runtime/` | [SDK共用机制](../../../../设计/接入/SDK共用机制.md)、[本地接入组件](../../../../设计/组件/本地接入组件.md) | runtime_*, resident_runtime, lease, installation_identity, configuration_boundary, user_confirmations | `src/access/sdk/VERSION` |
| `src/access/sdk/ioalab_sdk/assistant/` | [个人助手端SDK](../../../../设计/接入/个人助手端SDK.md)、[委托交易](../../../../设计/流程/委托交易.md)、[助手通信](../../../../设计/流程/助手通信.md) | assistant_role, resident_delegation, resident_contracts, resident_fulfillment, inbox_rules, candidate_evidence | 同上 |
| `src/access/sdk/ioalab_sdk/provider/` | [服务方端SDK](../../../../设计/接入/服务方端SDK.md)、[服务方接入](../../../../设计/流程/服务方接入.md) | provider_*, service_publication, publication_authorizations, task_*, template_renewal, rule_boundaries | 同上 |
| `src/access/sdk/ioalab_sdk/adapters/` | [A2C适配](../../../../设计/接入/A2C适配.md)、[平台接口](../../../../设计/接入/平台接口.md) | runtime_delivery, payment_transport_recovery, payment_atomicity, protocol_relay, arbitration_review, rule_boundaries | 同上 |
| `src/access/skills/` | [个人助手技能](../../../../设计/接入/个人助手技能.md)、[服务方技能](../../../../设计/接入/服务方技能.md)、[个人助手接入](../../../../设计/流程/个人助手接入.md) | access_products, business_skills, skill_*, cli_encoding, private_files | `src/access/skills/VERSION` |
| `src/access/examples/` | [SDK共用机制](../../../../设计/接入/SDK共用机制.md) | external_provider, external_product_config | `src/access/examples/VERSION` |
| `src/hosts/jiuwen/` | [九问网页通知](../../../../设计/接入/九问网页通知.md)、[本地接入组件](../../../../设计/组件/本地接入组件.md) 宿主接口 | jiuwen_host, host_tool_names, native_*, web_*, jiuwen_a2ui_forms | `src/hosts/jiuwen/VERSION` |
| `src/official/services/`、`admin-assistant/` | [官方实例](../../../../设计/工程/官方实例.md)、[编程服务](../../../../设计/示例/编程服务.md) | official_*, programming_services, admin_assistant_preparation, task_verifiers, seed_manifest | `src/official/VERSION` |
| `src/official/tools/` | [会议预约](../../../../设计/示例/会议预约.md)、[官方实例](../../../../设计/工程/官方实例.md) | business_tools, appointment_source, local_schedule, agentmail_*, calendar_outputs | 同上 |
| `src/trial/` | [在线试用](../../../../设计/工程/在线试用.md) | trial_*, trusted_user_port | `src/trial/VERSION` |
| `deploy/` | [安装与升级](../../../../设计/工程/安装与升级.md)、[部署教程](../../../../设计/工程/部署.md) | deployment*, linux_service_management, windows_local*, owned_workspace, production_release | `deploy/VERSION` |
| `vendor/` | [架构](../../../../设计/架构.md) 接入与参与方依赖 | vendor_deployment, container_images, sync_a2c | 无 |
| `tools/verification/` | 所属专项技能 | installed_deployment_probe, trial_acceptance, trial_resources | `tools/verification/VERSION` |
| `tools/media/` | [演示制作](../../../../设计/示例/演示制作.md) | demo_artifacts, review_story, approved_voice, booking_demo, nail_scene | `tools/media/VERSION` |
| `tools/*.py` 检查与构建、`tests/chain_layer.json` | 无职责页，读[工具索引](../../../../tools/README.md) | check_scope, check_workflow, test_layers, check_docs, sync_gitcode, llm, package_installation | 无 |
| `.agents/skills/`、`AGENTS.md` | 无职责页，读所改技能本身 | 轻量检查 A；被脚本读取的技能另跑 check_workflow | 无 |
| `设计/` 职责页 | 该页及其链接的唯一归属页 | 轻量检查 A；影响代码时按对应源码行 | 设计 VERSION，仅不变量变化 |
| `设计/工程/实施资料/`、`设计/工程/部署.md`、`设计/变更/` | 无 | 轻量检查 A | 无 |

模块已迁入 `设计/组件/<模块>/` 的行，职责页列指向其 接口.md，定向测试以接口页测试列为准；迁移票完成时在同一提交更新本行。一次改动跨多行时合并所列页面与测试。职责页带“子页”表时，先读父页的短索引，再只读相关子页。路由表缺项或所列测试已改名时，在本次提交里修正本表，并同步 `check_scope.py` 的文件级规则。
