# 08：宿主 notify 无调用方

**目标行为：** 网页通知只有一条可观察的执行路径，并与 `AgentHost` 契约一致。现状：SDK 的 `AgentHost.notify`（`runtime/host.py`）与 `ProcessHost.notify` 均已定义，`ioalab_jiuwen/__main__.py` 的 `serve` 还为其注入 notifier，`capabilities()` 报告 `notify`；但 `src/` 内没有任何代码调用 `host.notify`。通知实际经模型调用 `owner.notify` 业务工具（`ToolBinding` 经 `on_tool` 回调）送出，契约表[本地执行与恢复](../共用运行时/接口/本地执行与恢复.md#模块与依赖)所写的“cancel / notify”中的 notify 是无人走的第二条通知链。

**阻塞于：** 无

**状态：** ready

**范围：** 共用运行时与宿主适配。二选一：若通知只经业务工具，则作为不变量变化先改共用运行时契约表并升版，删除 `AgentHost.notify`、`ProcessHost.notify`、`notifier` 注入与 `capabilities().notify`；若 SDK 确需在非模型路径（如已保存事实触发的业务阶段提示）调用宿主通知，则在共用运行时补上调用点与测试。需读[宿主适配接口](../宿主适配/接口.md#agenthost-实现)与[九问网页通知](../宿主适配/接口/九问网页通知.md#配置与工具)。

**定向测试：** jiuwen_host、tool_output_contract、web_notifications

- [ ] 仓库内 `notify` 契约方法要么有被测试覆盖的调用方，要么不存在
- [ ] 共用运行时契约表、宿主适配接口页与代码一致
