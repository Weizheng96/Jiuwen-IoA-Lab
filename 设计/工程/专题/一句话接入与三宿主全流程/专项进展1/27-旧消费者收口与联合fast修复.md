# 27 旧公开类型消费者收口与联合 fast 修复：会话交接

保存时间：2026-11-17（本机）。保存者：票27实施子代理（Claude）。用户要求换会话继续，本文件是接棒入口；票据现状以[27票](../../../../组件/客户端/进展/27-统一宿主契约与多宿主上下文绑定.md)「199旧公开类型收口事实与待验」一节为准，此处只补交接所需的在途状态。

## 1. 边界（必须延续）

- 27 文件：shared Registry/NativeSources/observations/catalog/Resident/Endpoint/provider执行/tasks/publication/client assembly/runtime、legacy 共享测试与 verification、`runtime/__init__.py`、27票、`tests/chain_layer.json` 与 `tools/test_layers.py`（当前只有27在写）。
- 不碰：hosts/claude_code、codex、jiuwen（30/31/32/16）；28 的 native_entry_bindings、client commands/cli/skills/native_skills 及原生入口测试（native_context_transport、client_onboarding、skill_location、client_hosts，已另派28）；33 的 definition_snapshots/execution_resources/sdk_tool_callbacks/submission_resources/professional_execution、test_business_skills.py，以及 33 当前独占的 `provider/production.py`、`provider/production_recovery.py`、`provider/tasks.py` 的 production.finish 部分；35 的 health/config/configuration/provider/admission。
- 不 reset/clean/stash/checkout/`git add`/提交，Git 归主协调者；只跑定向检查，每次用新 `temp/checks/` 报告目录；`--layer fast`、`--full`、真实模型和真实宿主由主协调者安排。
- 用户最新决定：在线使用入口置灰、API 暂留；依赖在线试用的管理员助手暂不提供。只是暂停，不删代码和测试，不在管理员助手上做新开发；它的失败若属负载时序只记录，若是本轮回归则最小修复保证 fast 绿。

## 2. 已完成（工作区，未提交）

- 旧公开 HostTurn/HostResult 的全部消费者迁到 S01，并原子删除 `runtime/host.py` 两个类型及 `runtime/__init__.py` 导出；无别名、旧格式回退或第二执行链。
- 修掉的生产缺陷：`host_registry.get_submission` 查询失败时降级回执；`scheduling._held` 把嵌套观察计入观察自身 interaction，阻塞同业务协议控制。
- 新增 `tools/verification/s01_driver.py`（opt-in 真实模型只经 Registry/Scheduler/S01/P28/S05）及 `tests/test_verification_s01_driver.py`；codex_host.py、model_rule_authoring、model_messaging 只做静态迁移，未执行真实模型。
- Codex 并行写入核对（脚本在 `temp/ticket27/reconcile199/`）：Codex 的27子代理新增并导出 `host_submission.submission_succeeded`（合规保留，已在 `tests/test_host_submission.py` 补测试）；33子代理放入的 `tests/test_inbound_proposals.py` 属33，其 inbound 实现未落地，目前是预期红灯。
- 联合 fast 清单：`test_resident_contracts` 函数改名，`tests/chain_layer.json` 已改新名，`tools/test_layers.py` 的 RETIRED_CHAIN 登记退役规则与 gap（resident 端到端提交期原生拒绝用例待补）。
- 主协调者已确认：verifier_observations 的 expire 按合同软响应截止改写；共用运行时/接口.md 第60行事实修正保留不升版。

## 3. 联合 fast 修复（定向已完成，主协调者派单，fast2 报告：`temp/checks/joint199-fast2`）

| 用例 | 根因 | 状态 |
|---|---|---|
| test_sdk_contract::test_error_codes_judged_by_the_sdk_are_in_the_error_table | `identity_conflict` 是 SDK 广泛使用且接口页已规定的错误码，错误表漏登记（host_registry 首次比较它而暴露） | 已在 `设计/组件/平台/web服务/接口/幂等与错误.md` 补 SDK 行；r18 该测试已过 |
| test_configuration_edit::test_offline_inspection_and_validation_do_not_read_tools_start_hosts_or_create_state | 测试仍 monkeypatch 已删的 `ProcessHost.probe`，生产不调用 | 已改为守卫 S02 `check` 与 S01 `submit`；r18 已过 |
| test_official_deployment / test_official_lifecycle（cross_domain 等5例） | 第1层：测试替身 `UnusedHost` 仍是旧 run/capabilities（不在原清单），已迁 `ScriptedSubmission` + `bound_runtime(**options)`。第2层：`src/official/services/ioalab_services/coordinator.py` 的 tick 以 `runtime.status` 的 `host.available` 为发布门禁；S01 下 `status_snapshot` 仅在记录了 P34 健康为 available 时才为真，故账号报 not_ready、服务为空 | 两层完成：主协调者裁定第2层为实现偏离[宿主提交](../../../../组件/客户端/共用运行时/接口/宿主提交.md)（check仅P34、不作门禁），授权27最小修改 coordinator：去掉 hostAvailable 门禁，保留 platformConnected 与 stopping，hostAvailable 仍写入状态行作观察；`设计/组件/官方服务/接口/预置服务与启动器.md` 三处“宿主探测/就绪”事实修正不升版；运行期健康暂停接单归35。r22 已过 |
| test_admin_assistant_preparation 三例（两例 Timed out waiting for a participant、一例 probe AttributeError） | r20 单独复跑同样3失败，非负载时序，属本轮回归：替身 `AvailableHost` 仍是旧 run/capabilities，未登记S01，executionReady 恒假；prepare 用例 monkeypatch 已删的 `ProcessHost.probe` | 最小修复测试：替身迁 `ScriptedSubmission` + `bound_runtime`，probe 守卫改为 check/submit；ready 用例 hostAvailable 期望改 false（P12只投影已记录P34健康），无宿主用例改为不配Registry；r21 单独27通过、r22 已过 |

r18（`temp/checks/t27-fast199-r18`，exit 1）：74 通过 / 5 失败，失败均为上表第3行第2层（hostAvailable False、服务列表为空、409、stop 事件未置位等后续连带）。r19 为上一会话残留目录，不作证据。r20（admin 单独，exit 1）3失败；r21（admin 单独，exit 0）27通过；r22（official_deployment、official_lifecycle、admin_assistant_preparation、sdk_contract、configuration_edit 五文件，exit 0）106通过，含版本、链接、模块、阅读预算四轻量通过。

本轮新增修改（在此前清单之外）：`设计/组件/平台/web服务/接口/幂等与错误.md`、`tests/test_configuration_edit.py`、`tests/test_official_deployment.py`、`tests/test_official_lifecycle.py`、`tests/submission_fixture.py`（bound_runtime 透传 `**options`）。诊断时临时插桩的 test_official_deployment 已从 `temp/ticket27/fast199/*.orig` 恢复。接棒会话新增：`src/official/services/ioalab_services/coordinator.py`（仅门禁一处）、`设计/组件/官方服务/接口/预置服务与启动器.md`、`tests/test_admin_assistant_preparation.py`（原件备份 `temp/ticket27/fast199/test_admin_assistant_preparation.py.orig`）。

## 4. 精确改动文件清单（主协调者提交用）

修改：src/client/sdk/ioalab_sdk/runtime/{host.py,__init__.py,host_registry.py,scheduling.py}；tests/{test_host_registry,test_native_event_integration,test_agent_flows,test_arbitration_evidence,test_business_tools,test_candidate_evidence,test_native_web_delivery,test_official_service_runtime,test_resident_multiperiod,test_resident_termination,test_verifier_observations,test_model_messaging,test_configuration_edit,test_official_deployment,test_official_lifecycle,test_admin_assistant_preparation}.py；src/official/services/ioalab_services/coordinator.py；tests/model_rule_authoring.py；tests/chain_layer.json；tools/{README.md,check_scope.py,test_layers.py}；tools/verification/codex_host.py；设计/组件/客户端/共用运行时/{实现.md,接口.md}；设计/组件/客户端/进展/27-统一宿主契约与多宿主上下文绑定.md；设计/组件/平台/web服务/接口/幂等与错误.md；设计/组件/官方服务/接口/预置服务与启动器.md。

未跟踪：src/client/sdk/ioalab_sdk/runtime/host_submission.py；tests/{submission_fixture,test_host_submission,test_verification_s01_driver}.py；tools/verification/s01_driver.py；本交接文件。

不归27：tests/test_inbound_proposals.py（33，红灯）。部分文件含27此前在途改动及31在 test_layers/chain_layer 的条目，提交时一并带上。

## 5. 检查报告索引

- 收口：`temp/checks/t27-legacy199-r1`—`r17`（r14 共享层 296 通过/7 失败/6 跳过：6 例为33 inbound 红灯，1 例 material `[False-countersign]` 负载时序，单独 r12/r15 均通过；r16 resident_contracts 10 通过；r17 fast 已过清单校验后人工中断）。
- 联合 fast 修复：`temp/checks/t27-fast199-dbg1`、`dbg2`（插桩诊断）、`t27-fast199-r18`、`r20`—`r22`（r19 非本票证据）。
- `t27-legacy199-resume1—3`、`overdue1` 为 Codex 27子代理用同前缀跑的，不作本票证据。

## 6. 接棒顺序

1. 已完成：official coordinator 门禁按裁定落地；admin_assistant_preparation 取证并最小修复；联合 fast 定向五文件 r22 通过；27票与本文件已同步。
2. 下一步交主协调者：按第4节清单提交，并安排最终 `--layer fast`。
3. 仍开放：最终 fast（主协调者）、三项 opt-in 真实模型与三宿主原生验收、整轮 error 消费者栅栏（Codex 留有 `temp/ticket27/legacy198/ROUND-ERROR-REVIEW.md` 计划，需复核）、RETIRED_CHAIN gap 的 resident 端到端用例、运行期宿主健康暂停接单（35）。
