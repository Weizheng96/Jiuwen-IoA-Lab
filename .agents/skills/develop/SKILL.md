---
name: develop
description: 按现行设计开发、重构和修复代码；领票后按路由表只读本模块文件夹与所依赖模块的接口页及定向测试，验证公开闭环与恢复；不承担 Demo 制作。
---
# 按设计开发

1. 核 `git status`，只改本任务文件；不动他人在途改动、运行数据与进程。有票先把 `进展/` 票据状态改为 claimed。
2. 按[路由表](references/routing.md)找到改动目录对应的模块文件夹。只读本模块六件与 依赖.md 所指的其他模块接口分组，定向测试只取接口页的测试列，产物取 实现.md 的产物行，阅读预算见[模块文件夹格式](../design/references/module-format.md#阅读预算)；需要背景再读[架构](../../../设计/架构.md)模块表与[术语](../../../设计/术语.md)。
3. 要改不变量或规则有缺陷，先交 [design](../design/SKILL.md)。实现偏离正确设计时先在票据写修复方案并标“与当前设计有偏离”，再修实现。文档只需追平代码时随本次提交一起改，不升版。
4. 保持简洁现行架构：跨模块只经对方入口文件与接口页所列接口，依赖方向与唯一 owner 按[架构审查](../design/references/architecture.md)核对；宽重构按票据的展开、迁移、收口顺序进行，收口票删除旧形态。迁移同步构建、安装、资源及所有消费者。
5. 验证：`python .agents/skills/develop/scripts/check.py --module <模块名>` 跑接口页测试列，个别用例用 `--tests`；覆盖正常、拒绝、恢复与依赖方影响；新增测试按 [tdd](../testing/references/tdd.md) 在约定接缝写；提交前加 `--layer fast` 跑快速层，并按 [code-review](references/code-review.md) 的坏味道基线自审 diff。层级与诊断按 [testing](../testing/SKILL.md)。
6. 交付：产物 VERSION 不随本次改动递增，发布时才按[交付](references/delivery.md)同步版本与锁。完成后删除票据并更新进展索引，按交付参考提交并推送 develop；阶段结束按 [retro](../retro/SKILL.md) 复盘环境。

| 专题 | 按需入口 |
|---|---|
| 模块、协议、资源及迁移边界 | [实现边界](references/implementation.md) |
| 会话阶段边界：继续、清空、交接、子智能体、压缩 | [phase-boundaries](references/phase-boundaries.md) |
| 安装、生命周期与恢复 | [deployment](../deployment/SKILL.md) |
| 原版九问、公开扩展与配置排错 | [jiuwen-swarm](../jiuwen-swarm/SKILL.md) |
| 开发模型及显式初始化 | [llm](../llm/SKILL.md) |
| 官方预置服务方的资产、归属与发布验证 | [official-services](../official-services/SKILL.md) |
| 媒体制作 | [demo-video](../demo-video/SKILL.md) |
| 用户已授权的多智能体分工 | [parallel-development](../parallel-development/SKILL.md) |
| 提交、版本与同步 | [交付](references/delivery.md)、[gitcode-sync](../gitcode-sync/SKILL.md) |

复用 `scripts/check.py` 与[工具索引](../../../tools/README.md)，不复制专业步骤或把场景值写入通用 SDK/技能。

## 本机开发运行

Windows 开发机只跑单元测试、`tools/smoke.py` 自检，并用 `python -m ioalab --config <私有平台JSON> init` 与 `serve` 只起平台做开发；官方实例、九问与管理员助手不在 Windows 本机运行，部署只走 Linux 受管安装。
