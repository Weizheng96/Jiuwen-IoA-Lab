# 31：Codex桌面hook重装与重测（会话交接）

2026-11-17（本机）。保存者：票31实施子智能体。现行规则以[31票](../../../../组件/客户端/进展/31-Codex桌面会话与上下文投递.md)为准，这里只补充交接需要的在途状态。工作区改动均**未git add、未提交、未推送**，Git由主协调者负责。

## 在途状态

| 项 | 结果 |
|---|---|
| 安装器 | `native_install.install`会原位替换同一绑定（同hook.json路径）按旧写法安装的组，并备份hooks.json；别的绑定已有IoA组时仍拒绝；`uninstall`同样按绑定移除。新增用例`test_reinstall_replaces_older_rendering_of_same_binding_only` |
| 本机重装 | 走产品`install`（不是手写）：`~/.codex/hooks.json` sha256 由fd06c87b…543c变为0e3facb0…2fec。只有本组这一项，config.toml（c2b7fd67…ec6）、适配器hook.json（18a6b531…692e）和`~/.codex`文件列表都没变。原件备份在`temp/ticket31/199/hooks.json.before-reinstall-r10`；计划与reinstall-plan.json一致 |
| 两种shell | hooks.json里写入的那条命令，用桌面自带pwsh以`-Command`执行exit0（0.80秒），用`cmd /C`执行exit0（0.56秒），两次都写出`hookRun.stage=no_opinion`，handoffs没有变化。这是本地执行，不算桌面证据（`temp/ticket31/199/reinstall-r10/`） |
| 常驻关闭 | `NativeSetup.drain`已补；`test_client_hosts`里的Codex常驻用例已迁到当前契约（只按`{type:codex}`登记，state=unknown） |
| 检查 | t31-199-r10 exit0（135passed+4light）；r11 exit1（迁移前复现）；r12 exit0（137passed+4light）；文档事实修正后的轻量检查r13见回报 |

## 桌面重测与卸载（已完成）

- 第三次重测命中（`temp/ticket31/199/RETEST-RESULT-r3.md`）：logs、audit、thread_items、rollout四处逐次对齐；精确入口allow并附加一次性handoff，停在ensure_client；近似命令没有被改写；handoff只用了一次。第1轮这次正常触发。
- 已用产品`uninstall`卸载验证hook：hooks.json卸载前sha256为0e3facb0…2fec，只含本组，卸载后文件按原状删除；~/.codex其他文件不变。
- 文档轻量检查：t31-199-r14。

## 下一步

1. 等协调者提交本批后，再派路线A：先按`temp/ticket31/199/ROUTE-A-DESIGN-DRAFT.md`交design改接口页并升版，再按`ROUTE-A-PROOF-PLAN.md`用真实客户端绑定做hook→P10→caller闭环。
2. 原生审批桥的A/B/C方案仍等用户决定。

## 未验

没有跑fast、full或真实模型；没有运行真实客户端的hook→P10→caller闭环；桌面第1轮完全不触发hook的原因还没有解释。
