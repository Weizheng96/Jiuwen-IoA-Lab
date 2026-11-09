---
name: testing
description: 选择并执行本仓轻量、定向、完整及环境专项检查，诊断真实失败并保留有效覆盖；复用现有检查入口。
---
# 检查与验证

按 develop 的[路由表](../develop/references/routing.md)确定受影响模块，定向测试取其 接口.md 的测试列；验收条件只查受影响模块接口页的“验收条件”表与所涉流程页的“端到端验收”节，不通读。Windows 使用 `.venv/Scripts/python.exe`，Linux 使用 `.venv/bin/python`。

| 层级 | 触发与唯一入口 |
|---|---|
| A | 文档、技能与模块文件夹：`python .agents/skills/develop/scripts/check.py`，含版本、链接与索引、模块文件夹对代码四项核对 |
| B | 代码迭代：上述入口加 `--tests <路径或nodeid...>`，或加 `--changed-from <已提交基线>` 自动选择；覆盖受影响模块和相邻边界 |
| B+ | 提交前回归：加 `--layer fast`，跑清单外全部用例，数分钟；整链路与真实宿主用例归 `--layer chain` |
| C | 阶段交付和发布：加 `--full`，执行设计、文档、整仓 pytest（两层全部）、selfcheck |
| D | 构建安装、真实模型、原生九问、Linux/systemd、外部业务与视频按所属技能显式触发 |

新增或改写测试先读 [tdd](references/tdd.md)：只在预先约定的接缝测，经接口验证行为，避免耦合实现、同义反复与水平切片；替身只放在系统边界。

修改脚本、配置模板、依赖及安装清单须验证受影响行为；维护工具覆盖成功及失败路径。已通过且输入未变的结果复用，不能用历史报告证明新输入。

检查串行执行，共享状态、固定端口与锁不直接并行。长阶段指定唯一执行者，冻结被读取源码、测试、依赖及模板；等真实退出后再修复。实时输出、日志、阶段耗时、真实退出码及逐项耗时 XML 由现有 runner 保存至 `temp/checks/`。每次使用新短报告目录中的 `pytest-work/`，不扫描清理旧全局 pytest 目录或原运行数据。

按需读[范围与覆盖](references/scope.md)；故障与证据解释读[验证与诊断](references/verification.md)。部署专项读[deployment](../deployment/SKILL.md)，影片读[demo-video](../demo-video/SKILL.md)。不另建 runner，不放宽协议、签名、授权、付款、幂等、恢复及资源生命周期断言。
