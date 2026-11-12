# 改动路由表

按改动所在目录找到模块文件夹，只读该模块六件与 依赖.md 所指的其他模块接口分组（[阅读预算](../../design/references/module-format.md#阅读预算)）。定向测试只取模块接口页（含子页）的测试列：`check.py --module <模块名>` 跑该列并集，`check.py --changed-from <已提交基线>` 对模块代码按同一列选择；产物 VERSION 取模块 实现.md 的产物行。

| 代码目录 | 模块文件夹 |
|---|---|
| `src/platform/ioalab/identity/` | [身份中心](../../../../设计/组件/平台/身份中心/接口.md) |
| `src/platform/ioalab/registry/` | [注册中心](../../../../设计/组件/平台/注册中心/接口.md) |
| `src/platform/ioalab/delegation/` | [委托中心](../../../../设计/组件/平台/委托中心/接口.md) |
| `src/platform/ioalab/payments/` | [支付中心](../../../../设计/组件/平台/支付中心/接口.md) |
| `src/platform/ioalab/messaging/` | [消息中心](../../../../设计/组件/平台/消息中心/接口.md) |
| `src/platform/ioalab/observation/` | [观测](../../../../设计/组件/平台/观测/接口.md) |
| `src/platform/ioalab/web/*.py`、`app.py`、`operations.py`、`cli.py`、`bootstrap.py`、`seed/`、`readiness.py` | [web服务](../../../../设计/组件/平台/web服务/接口.md) |
| `src/platform/ioalab/web/static/` | [网页UI](../../../../设计/组件/平台/网页UI/接口.md) |
| `src/platform/ioalab/storage.py`、`migrations/`、`errors.py`、`files.py`、`keys.py`、`events.py`、`config*.py` | [平台基础](../../../../设计/组件/平台/平台基础/接口.md) |
| `src/access/sdk/ioalab_sdk/runtime/`、`adapters/`、`templates.py`、`src/access/skills/_shared/` | [共用运行时](../../../../设计/组件/客户端/共用运行时/接口.md) |
| `src/access/sdk/ioalab_sdk/assistant/`、`src/access/skills/assistant/` | [个人助手端](../../../../设计/组件/客户端/个人助手端/接口.md) |
| `src/access/sdk/ioalab_sdk/provider/`、`src/access/skills/provider/` | [服务方端](../../../../设计/组件/客户端/服务方端/接口.md) |
| `src/access/sdk/ioalab_sdk/access.py` | [SDK装配](../../../../设计/组件/客户端/SDK装配/接口.md) |
| `src/access/examples/` | [共用运行时](../../../../设计/组件/客户端/共用运行时/接口.md)（调用方示例） |
| `src/hosts/codex/` | [Codex适配](../../../../设计/组件/客户端/Codex适配/接口.md) |
| `src/hosts/jiuwen/` | [宿主适配](../../../../设计/组件/客户端/宿主适配/接口.md) |
| `src/official/services/` | [官方服务](../../../../设计/组件/官方服务/接口.md) |
| `src/official/admin-assistant/`、`src/official/tools/` | [管理员助手](../../../../设计/组件/管理员助手/接口.md) |
| `src/trial/`、`deploy/trial/` | [在线试用](../../../../设计/组件/在线试用/接口.md) |
| `deploy/` | [部署](../../../../设计/组件/部署/接口.md) |
| `vendor/` | [发行与依赖](../../../../设计/组件/部署/接口/发行与依赖.md) |
| `tools/media/` | [演示制作](../../../../设计/工程/演示.md) |
| `tools/verification/` | 所属专项技能 |
| `tools/*.py`、`tests/chain_layer.json` | [工具索引](../../../../tools/README.md) |
| `.agents/skills/`、`AGENTS.md` | 所改技能本身，跑轻量检查 A；被脚本读取的技能另跑 check_workflow |
| `设计/` | 该页及其链接的唯一归属页，跑轻量检查 A |

一次改动跨多行时合并所列模块。接口页带子页时，先读父页的短索引，再只读相关子页。路由表缺项时，在本次提交里修正本表与 `tools/check_modules.py` 的 MODULES；选测缺项时补模块接口页的测试列，`tools/check_scope.py` 的文件规则只管模块之外的文件。
