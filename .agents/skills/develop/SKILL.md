---
name: develop
description: 按现行设计开发、重构和修复代码，按模块读取专业技能并验证公开闭环与恢复；不承担 Demo 制作。
---
# 按设计开发

先读根 AGENTS.md、[设计索引](../../../设计/README.md)和[开发计划](../../../设计/工程/实施资料/开发计划.md)，按需求读取唯一职责页。旧代码与测试描述现状，不覆盖设计。

1. 核工作区、提交基线及并发文件所有者。内部阶段直接维护简洁现行架构，不增加旧接口、格式回退或双执行链。
2. 规则有缺陷先交 [design](../design/SKILL.md)。实现偏离正确设计时先写修复方案，开发计划精确标“开发中，与当前设计有偏离”，再修所属实现。
3. 实施前按[架构审查](../design/references/architecture.md)核职责与唯一规则/数据/状态 owner、公共契约、调用闭环和依赖方向；边界变化先走设计流程，不跨读写他模块私有实现或增重复权威。迁移同步构建、安装、资源、版本及所有消费者，不预建空目录；替换后退出旧主流程，保留原业务数据及未知结果核对能力。
4. 依 testing 验证公共正常、拒绝、恢复与依赖方影响，核实际实现无跨层耦合或重复状态。修改模块时核其 VERSION 的实际符合范围，不高于设计；差异实报，不批量虚升。产物版本或安装要求变化时，先按[交付](references/delivery.md#发行输入与锁一致性)同步固定版本消费者与目标锁元数据，并验证真实发行构建，不只修改VERSION后发布。完成后更新设计最新完整方案、删除开发计划已完成进展项，再按交付参考提交推送。

| 工作 | 专业入口 |
|---|---|
| 模块、协议、资源及迁移边界 | [实现边界](references/implementation.md) |
| 检查、覆盖及诊断证据 | [testing](../testing/SKILL.md) |
| 安装、生命周期与恢复 | [deployment](../deployment/SKILL.md) |
| 原版九问、公开扩展与配置排错 | [jiuwen-swarm](../jiuwen-swarm/SKILL.md) |
| 用户已授权的多智能体分工与交接 | [parallel-development](../parallel-development/SKILL.md) |
| 开发模型及显式初始化 | [llm](../llm/SKILL.md) |
| 媒体制作 | [demo-video](../demo-video/SKILL.md) |
| 提交、版本、并发与同步 | [交付](references/delivery.md)、[gitcode-sync](../gitcode-sync/SKILL.md) |

复用 `scripts/check.py` 总入口与[工具索引](../../../tools/README.md)，不复制专业步骤或把场景值写入通用 SDK/技能。
