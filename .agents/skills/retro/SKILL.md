---
name: retro
description: 会话或阶段完成后复盘智能体的工作环境：导航、自动检查、规则、AGENTS 与技能体量、工具开销、信息获取；按严重度提出改进并在用户批准后落实到技能、检查脚本或 AGENTS；不复盘业务结论。
---
# 复盘

目标是改进下一次开发的环境，不是总结本次做了什么。先读 [writing-for-agents](references/writing-for-agents.md) 取得判断尺度：context pointer、两种负载、信息层级、leading word、no-op；技能专属机制读 [skill-mechanics](references/skill-mechanics.md)；原始方法见 [retro](references/retro.md)。

1. 取材：本会话的工具调用、读过的页面、`temp/checks/` 报告、提交 diff；用户指定其他会话时读其记录。运行 `tools/reading_budget.py --json <新记录>` 与上次记录比较，必读字数上涨的模块列为导航候选。
2. 按下表找候选，每条附适用信号对应的证据。

| 类别 | 适用信号 | 落点 |
|---|---|---|
| 导航 | 找一条信息花了多轮，或读了无关页面 | 架构模块表、依赖页锚点、路由表、术语地图 |
| 自动检查 | 犯了检查能抓住的错，或已有检查未接线 | `tools/check_*`、`check.py` 层级 |
| 规则 | 自审或评审漏掉的问题 | 机械规则进检查脚本；判断类进[架构审查](../design/references/architecture.md)或[实现边界](../develop/references/implementation.md) |
| AGENTS 与技能 | 常驻文字过长、重复或对行为无影响 | 删 no-op、下沉到 references、合并复述 |
| 工具开销 | 昂贵或重复的工具调用、不必要的整仓测试 | 更窄的检查入口、复用未变输入的报告 |
| 信息获取 | 关键事实不可得 | 日志、只读访问、探针 |
| 票据与 ADR | 同一取舍被重新讨论，或任务超出一次会话 | 模块 决策/、进展/ |

3. 按严重度排序给用户，每条写证据、改法与落点，一两句。
4. 批准后落实：修改所属技能、脚本或 AGENTS，合并替换旧内容；技能文件不超过 40 行，规则能脚本化就写成检查并从文字删除。
