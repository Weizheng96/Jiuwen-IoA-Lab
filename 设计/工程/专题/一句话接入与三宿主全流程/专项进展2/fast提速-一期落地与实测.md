# fast提速专项进展：一期落地与实测（2026-11-17；一期fast实测exit 0，待提交）

负责：fast提速子智能体（唯一实施者）；主协调者负责调度与Git，用户已授权主协调者全权决定fast精简。本专项分析、计划与补丁源都在`temp/fast-slim/`，其中PLAN.md第5节为定稿依据，`proposal/moves.py`可复核守护。

## 背景与裁定

- fast2 的 Tests 一步 869s，fast3 为 926s，远超“数分钟”。主因是新增的整链路用例没有按`--audit`归层。
- 主协调者裁定：
  - 不做并行，不引入xdist，testing技能的“检查串行执行”不改。
  - 守护从严：凡涉及协议/签名/授权/付款/幂等/恢复/资源生命周期、且在fast中找不到同接缝守护的，一律留fast。
  - `participants()`的配置重启保留。
  - trial两处poll_interval不改。
  - A1（ipc.py、trusted_user.py的`serve_forever` poll_interval）另派票27。
- 暂缓：material 3个函数等38修完再定（38已于v0.0.201.2修复，转入二期核验）；agent_flows那一例待33确认。

## 一期改动（工作区，**未提交**，均保持CRLF）

- `tests/chain_layer.json`：新增14个函数（29例，按fast3实测为152.0s），清单由96个文件、345个函数变为100个文件、359个函数。
  - recovery_matrix：acceptance断线、contract choice重启
  - host_submission_roles：a2c not_submitted重启、accepted不重试、real role rpc
  - native_event_integration：leaf dedup、provider sync/async
  - quote_admission_rejection：late reservation
  - resident_fulfillment：early step
  - web_browser：4个纯UI例，以及ordinary account admin
- `tools/test_layers.py`：
  - 新增`MOVED_TO_CHAIN`，逐函数写明seam、owner和仍在fast的守护。
  - 新增`moved_guard_problems()`：被移函数必须已在chain，守护必须存在且仍在fast。
  - A3：`_parse`一次解析供manifest、plan和audit共用，仓库清单测试由6.1s降到3.1s。
  - `plan()`输出新增`movedToChain`。
  - 票27/31原有在途内容保留。
- `.agents/skills/develop/scripts/check.py`：`--layer`跑完后打印本次XML里unlistedSlow的前10项。只作提示，不改清单；XML缺失或损坏时不打印。
- `tests/test_test_layers.py`：
  - 仓库清单测试校验守护。
  - 新增`test_moved_function_needs_existing_fast_guards`，覆盖守护缺失、守护在chain、被移函数不在chain三种情况。
  - runner用例断言提示会出现，且清单不变。
- `.agents/skills/testing/references/scope.md`第12行补一句：移入chain须在`MOVED_TO_CHAIN`登记守护，由仓库测试校验，找不到守护的留fast；runner会提示。

## 检查

| 目录 | 内容 | 结果 |
|---|---|---|
| temp/checks/slim-r01 | 四项轻量＋`--tests tests/test_test_layers.py` | 全部passed，16例通过，exit 0 |
| temp/checks/slim-fast1 | `--layer fast`，经主协调者授权运行一次 | **作废**：进程随旧会话结束，停在73%，没有exit行。期间2例失败均已查明与一期无关：`test_repository_module_folders_pass`由并发改`设计/`引起；38的新回归例因收集早于endpoint修复写入而失败，38修复后已连续5次全绿 |
| temp/checks/slim-fast2 | 同一命令，但误用系统Python启动 | **作废**：runner沿用`sys.executable`，系统Python缺`a2a`，收集阶段182个错误，exit 2。属启动方式错误，与代码无关；testing技能已规定Windows用`.venv/Scripts/python.exe` |
| temp/checks/slim-fast3 | `.venv/Scripts/python.exe .agents/skills/develop/scripts/check.py --layer fast`，输入为HEAD d0c4c58加本专项5个文件 | **exit 0**（真实退出码，见`slim-fast3.out`末行）。2627通过、34跳过、528未选，14个子测试通过，无失败。总时长733s（12分13秒），Tests 730.1s，逐项合计722.5s |

slim-fast3与基线fast3（Tests 925.9s，逐项合计916.2s，3例material失败）比较：

- Tests减少195.8s（−21%），逐项合计减少193.7s。
- 好于预计的约760s。原因是38修复后material不再有失败和重试开销，fast3那3例失败本身耗25.7s。
- `moves.py`对slim-fast3的XML核验通过：14个移出函数在本轮fast中都是0例，全部已在chain；守护没有报问题。
- 耗时分布（analyze.py）：
  - 2661例，<0.1s的2025例，共39.5s；3–10s的44例，共301.0s；≥10s的6例，共103.6s。
  - 前5个文件占41.0%，前100例占68.8%。
- 主要文件耗时：

| 文件 | 秒 |
|---|---:|
| material_observation_recovery | 123.0 |
| production_proposals | 69.4 |
| package_installation | 37.4 |
| arbitration_processing | 33.5 |
| provider_admission | 33.3 |
| web_browser | 24.6 |
| sdk_tls | 19.5 |
| inbound_proposals | 18.6 |
| recovery_matrix | 18.0 |
| admin_assistant_preparation | 17.5 |
| payment_atomicity | 15.8（其中setup 15.2） |

- runner的Layer audit提示：85个fast函数的实测耗时达到清单阈值。前10项都在PLAN.md第5节“留fast”或“待票”列表里，本轮没有新增需要归层的函数。
- payment_atomicity的`test_expired_and_mismatched_signed_checkout_are_rejected`在setup上耗时15.2s，是新出现的夹具成本，fast3的分析中没有单列。

## 二期下一步

不并行、守护从严时，5分钟目标仍然达不到。下表以slim-fast3为基线，详见PLAN.md第5节。

| 项目 | 归属 | 内容 | 预计节省 |
|---|---|---|---:|
| material 3个函数 | 38（已修，v0.0.201.2） | pre_review重启44.1、late machine end 18.1、accepted unknown 7.6。按PLAN.md第2节B1表重跑`moves.py`，核验守护后再移 | 约70s |
| A1 | 票27 | ipc.py、trusted_user.py 的 poll_interval | 约70–100s |
| A2 | 本专项 | sdk_tls等测试夹具的 poll_interval | 约19s |
| agent_flows那一例 | 33确认后 | rejected spec | 7.5s |
| payment_atomicity夹具 | 待查 | 15.2s的setup | 待定 |
| 单元守护：27/33 | 27/33 | production/inbound 的 proposal 单元例 | 约88s |
| 单元守护：28 | 28 | 宿主包静态导入边界例 | 约37s |
| 单元守护：38 | 38 | 损坏记录34.3、Store锁争用18.8 | 约53s |
| 单元守护：35 | 35 | 准入策略 | 约33s |
| 单元守护：仲裁 | 仲裁 | 释放只一次、require_open接线 | 约33s |

前四项不需要新增守护，做完Tests约535–565s；单元守护全部补完后约290–320s。

## 提交

本专项的5个文件（加本交接页）只做了验证，没有git add。提交由主协调者进行，版本号应为当前设计下的`v0.0.201.y`，不是旧写法`v0.0.199.y`。
