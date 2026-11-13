# Codex宿主本机验证

> 实施资料，2026-11-12。只记录本机宿主层证据，不作为Linux部署、平台业务或原生产品接入验收。

## 实现与归属

新增独立`ioalab_codex.CodexHost`，实现共用运行时AgentHost的capabilities/run/cancel；两角色业务与连接继续归SDK。冻结业务技能文档实现迁到共用运行时，九问消费者同步切换，不保留旧实现或兼容别名。Claude Code仅完成同级适配设计，仍由客户端16实施。

本机使用用户已有Codex `0.167.0-alpha.3.2`、显式模型`gpt-6.1-sol`及原宿主认证，不读取/复制登录凭据，不修改全局配置或用户已有聊天。验证显式复用已监听的本机HTTP代理，只作用于验证进程；适配器未固化代理地址。

## 真实宿主

| 步骤 | 实际终态 | 秒 |
|---|---|---|
| bound-tool | completed | 11.375 |
| resume-original-session | completed | 11.391 |
| cancel-original-turn | cancelled | 0.672 |

首轮在真实Session持久绑定后收到SDK工具回调；第二轮重开App Server进程，沿同一Session，要求模型从上一轮工具结果取得真实随机回执，测试端独立核对，未在第二轮提示中提供该答案。第三轮在真实turn/started后取消原操作，收到cancelled且无额外业务工具调用。模型completed本身不算工具成功，验收另核真实回调和回执。

原始回执在本机忽略目录`temp/codex-adapter/acceptance-stream-safe/`；其中有Session、轮次与测试回执，不入Git。本轮未连接IoA平台、签合同、付款、发邮件或调用原生生产工具。

## 回归与构建

| 检查 | 实测结果 | 记录 |
|---|---|---|
| 定向及分层 | 47 passed、1 skipped；包含阻塞stdin期限、未知结果、Session/定义拒绝及九问消费者 | `temp/checks/codex-transport-final/` |
| 快速层基线 | 1705 passed、30 skipped、515 deselected；在最后写线程修复之前，最终源码以完整门禁为准 | `temp/checks/codex-fast/` |
| 最终完整门禁 | 2202 passed, 49 skipped, 15 warnings, 14 subtests passed in 2822.43s (0:47:02)；设计、文档、模块、阅读预算及自检均通过 | `temp/checks/codex-full/` |

新Codex wheel已实际本机构建；仅证明该可选包可打包，不代表依赖完整安装、产品分发或服务器发行已完成。现有SDK/九问产物VERSION与发布锁本轮不升级，首次正式发行前须按客户端17更新同仓SDK新入口的依据与全部消费者。

## 已修正的实测问题

- 本机动态工具同样依赖code-mode host；保留该协议承载，同时禁用代码执行及无关宿主能力。
- MCP覆盖需要保留原名称、忽略config/read的可空项后禁用；空表与原配置合并，不能当作清除了原MCP。
- code mode裸await可能未向模型展示工具回执；显式捕获/输出约定现已纳入会话定义摘要。
- 同步stdin写入会让大请求越过期限；已改为有界独立写线程，超时仅终止自有子进程，结果保持unknown且不重发。

早期失败回执保留在本机temp，不以重试的成功覆盖失败或反推没有业务副作用。

## 后续边界

原生交互回调及原生生产工具明确不支持；本轮没有当前桌面聊天投递、产品Skill、账号/确认页、常驻双角色连接或平台交易验收。后续见[客户端16](../../组件/客户端/进展/16-ClaudeCode宿主适配.md)、[客户端17](../../组件/客户端/进展/17-多宿主产品接入与业务验收.md)及[专题](../专题/多宿主适配.md)。客户端15按上述宿主层验收范围收票，不影响既有服务器统一验收基线。
