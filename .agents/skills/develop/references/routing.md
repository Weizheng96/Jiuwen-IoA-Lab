# 改动路由表

按改动所在目录找到要读的职责页、要跑的定向测试和所属产物，只读所列页面。测试名对应 `tests/test_<名>.py`，`*` 为前缀匹配；`tools/check_scope.py` 持有更细的文件级映射，`check.py --changed-from <已提交基线>` 可据此自动选测试。

| 改动目录 | 先读职责页 | 定向测试 | 产物 VERSION |
|---|---|---|---|
| `src/platform/ioalab/identity/` | [身份中心](../../../../设计/组件/平台/身份中心/接口.md) | auth, auth_failures, authentication_recovery, account_enrollment | `src/platform/VERSION` |
| `src/platform/ioalab/registry/` | [注册中心](../../../../设计/组件/平台/注册中心/接口.md) | category_preview, search_availability, service_publication, publication_authorizations | 同上 |
| `src/platform/ioalab/delegation/` | [委托中心](../../../../设计/组件/平台/委托中心/接口.md)、[A2C适配](../../../../设计/组件/客户端/共用运行时/接口/A2C适配.md) | delegation_*, protocol_relay, provider_admission, arbitration*, payment_rejection, rule_boundaries | 同上 |
| `src/platform/ioalab/payments/` | [支付中心](../../../../设计/组件/平台/支付中心/接口.md) | payment_*, account_rewards, delegation_scopes, rule_boundaries | 同上 |
| `src/platform/ioalab/messaging/` | [消息中心](../../../../设计/组件/平台/消息中心/接口.md) | agent_delivery, message_boundaries, protocol_relay, runtime_delivery | 同上 |
| `src/platform/ioalab/observation/` | [观测](../../../../设计/组件/平台/观测/接口.md) | observation_views, observation_transactions, account_views, workbench_network, admin_api | 同上 |
| `src/platform/ioalab/web/*.py`、`app.py`、`operations.py`、`cli.py`、`bootstrap.py`、`seed/`、`readiness.py` | [web服务](../../../../设计/组件/平台/web服务/接口.md) | core_*, boundary, configuration_web, seed*, search_availability, payment_atomicity, resident_delegation, service_publication, frontend | 同上 |
| `src/platform/ioalab/web/static/` | [网页UI](../../../../设计/组件/平台/网页UI/接口.md) | frontend, workbench_*, account_views, admin_api | 同上 |
| `src/platform/ioalab/storage.py`、`migrations/`、`errors.py`、`files.py`、`keys.py`、`events.py`、`config*.py` | [平台基础](../../../../设计/组件/平台/平台基础/接口.md) | core_ownership, domain_migrations, protocol_operations, private_files, platform_configuration, configuration_web, configuration_migration, observation_transactions | 同上 |
| `src/access/sdk/ioalab_sdk/runtime/` | [共用运行时](../../../../设计/组件/客户端/共用运行时/接口.md) | runtime_*, resident_runtime, lease, installation_identity, configuration_boundary, user_confirmations | `src/access/sdk/VERSION` |
| `src/access/sdk/ioalab_sdk/assistant/` | [个人助手端](../../../../设计/组件/客户端/个人助手端/接口.md)、[委托交易](../../../../设计/流程/委托交易.md)、[助手通信](../../../../设计/流程/助手通信.md) | assistant_role, resident_delegation, resident_contracts, resident_fulfillment, inbox_rules, candidate_evidence | 同上 |
| `src/access/sdk/ioalab_sdk/provider/` | [服务方端](../../../../设计/组件/客户端/服务方端/接口.md)、[服务方接入](../../../../设计/流程/服务方接入.md) | provider_*, service_publication, publication_authorizations, task_*, template_renewal, rule_boundaries | 同上 |
| `src/access/sdk/ioalab_sdk/adapters/` | [共用运行时 · A2C适配](../../../../设计/组件/客户端/共用运行时/接口/A2C适配.md)、[web服务](../../../../设计/组件/平台/web服务/接口.md) | runtime_delivery, payment_transport_recovery, payment_atomicity, protocol_relay, arbitration_review, rule_boundaries | 同上 |
| `src/access/skills/` | [个人助手端 · 产品技能](../../../../设计/组件/客户端/个人助手端/接口/产品技能.md)、[服务方端 · 产品技能](../../../../设计/组件/客户端/服务方端/接口/产品技能.md)、[共用运行时](../../../../设计/组件/客户端/共用运行时/接口.md) 的 `_shared` | access_products, business_skills, skill_*, cli_encoding, private_files | `src/access/skills/VERSION` |
| `src/access/examples/` | [共用运行时](../../../../设计/组件/客户端/共用运行时/接口.md) | external_provider, external_product_config | `src/access/examples/VERSION` |
| `src/hosts/jiuwen/` | [宿主适配](../../../../设计/组件/客户端/宿主适配/接口.md) | jiuwen_host, host_tool_names, native_*, web_*, jiuwen_a2ui_forms | `src/hosts/jiuwen/VERSION` |
| `src/official/services/` | [官方服务](../../../../设计/组件/官方服务/接口.md)、[编程服务](../../../../设计/示例/编程服务.md) | official_*, programming_services, admin_assistant_preparation, task_verifiers, seed_manifest | `src/official/VERSION` |
| `src/official/admin-assistant/`、`src/official/tools/` | [管理员助手](../../../../设计/组件/管理员助手/接口.md)、[会议预约](../../../../设计/示例/会议预约.md) | business_tools, appointment_source, local_schedule, agentmail_*, calendar_outputs | 同上 |
| `src/trial/`、`deploy/trial/` | [在线试用](../../../../设计/组件/在线试用/接口.md) | trial_*, trusted_user_port | `src/trial/VERSION` |
| `deploy/` | [部署](../../../../设计/组件/部署/接口.md)、[部署教程](../../../../设计/工程/部署.md) | deployment*, linux_service_management, windows_local*, owned_workspace, production_release | `deploy/VERSION` |
| `vendor/` | [架构](../../../../设计/架构.md) 接入与参与方依赖 | vendor_deployment, container_images, sync_a2c | 无 |
| `tools/verification/` | 所属专项技能 | installed_deployment_probe, trial_acceptance, trial_resources | `tools/verification/VERSION` |
| `tools/media/` | [演示制作](../../../../设计/示例/演示制作.md) | demo_artifacts, review_story, approved_voice, booking_demo, nail_scene | `tools/media/VERSION` |
| `tools/*.py` 检查与构建、`tests/chain_layer.json` | 无职责页，读[工具索引](../../../../tools/README.md) | check_scope, check_workflow, test_layers, check_docs, sync_gitcode, llm, package_installation | 无 |
| `.agents/skills/`、`AGENTS.md` | 无职责页，读所改技能本身 | 轻量检查 A；被脚本读取的技能另跑 check_workflow | 无 |
| `设计/` 职责页 | 该页及其链接的唯一归属页 | 轻量检查 A；影响代码时按对应源码行 | 设计 VERSION，仅不变量变化 |
| `设计/工程/实施资料/`、`设计/工程/部署.md`、`设计/变更/` | 无 | 轻量检查 A | 无 |

模块已迁入 `设计/组件/<模块>/` 的行，职责页列指向其 接口.md，定向测试以接口页测试列为准；迁移票完成时在同一提交更新本行。一次改动跨多行时合并所列页面与测试。职责页带“子页”表时，先读父页的短索引，再只读相关子页。路由表缺项或所列测试已改名时，在本次提交里修正本表，并同步 `check_scope.py` 的文件级规则。
