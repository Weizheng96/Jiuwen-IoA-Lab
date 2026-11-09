---
name: develop
description: 按现行设计开发、重构和修复代码；按改动目录查路由表只读相关职责页与测试，验证公开闭环与恢复；不承担 Demo 制作。
---
# 按设计开发

1. 核 `git status`，只改本任务文件；不动他人在途改动、运行数据与进程。
2. 按[路由表](references/routing.md)找到改动目录对应的职责页、定向测试和所属产物，只读这些。需要背景再读[架构](../../../设计/架构.md)与[术语](../../../设计/术语.md)；开发计划和验收页按需查，不默认通读。
3. 要改不变量或规则有缺陷，先交 [design](../design/SKILL.md)。实现偏离正确设计时先写修复方案并在开发计划标“开发中，与当前设计有偏离”，再修实现。文档只需追平代码时随本次提交一起改，不升版。
4. 保持简洁现行架构：不增旧接口、格式回退或双执行链；跨模块只走公共接口，依赖方向与唯一 owner 按[架构审查](../design/references/architecture.md)核对。迁移同步构建、安装、资源及所有消费者。
5. 验证：`python .agents/skills/develop/scripts/check.py --tests <路由表所列测试>`，覆盖正常、拒绝、恢复与依赖方影响；提交前加 `--layer fast` 跑快速层。层级与诊断按 [testing](../testing/SKILL.md)。
6. 交付：产物 VERSION 不随本次改动递增，发布时才按[交付](references/delivery.md)同步版本与锁。完成后删除开发计划已完成进展项，按交付参考提交并推送 develop。

| 专题 | 按需入口 |
|---|---|
| 模块、协议、资源及迁移边界 | [实现边界](references/implementation.md) |
| 安装、生命周期与恢复 | [deployment](../deployment/SKILL.md) |
| 原版九问、公开扩展与配置排错 | [jiuwen-swarm](../jiuwen-swarm/SKILL.md) |
| 开发模型及显式初始化 | [llm](../llm/SKILL.md) |
| 官方预置服务方的资产、归属与发布验证 | [official-services](../official-services/SKILL.md) |
| 媒体制作 | [demo-video](../demo-video/SKILL.md) |
| 用户已授权的多智能体分工 | [parallel-development](../parallel-development/SKILL.md) |
| 提交、版本与同步 | [交付](references/delivery.md)、[gitcode-sync](../gitcode-sync/SKILL.md) |

复用 `scripts/check.py` 与[工具索引](../../../tools/README.md)，不复制专业步骤或把场景值写入通用 SDK/技能。
