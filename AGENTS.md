# 九问 IoA Lab · 项目约定

本文件只留通用规则与阅读入口。秘密、私人消息和运行数据不入库；只改并提交本任务，保护并发改动、原数据与进程；报告真实验证结果及未验证范围。两个AI默认串行开发，需要并行时由用户明确下令。

## 1. 版本与提交

在develop小步开发、验证、提交并推送GitHub；AI自行完成的提交与推送只到develop，不直接向main提交。提交为`v0.0.x.y：主题`：x是当前[设计VERSION](设计/VERSION)的第三位，y是该设计版本下的开发推送序号，每次提交递增第四位；设计VERSION第三位只在职责页的不变量变化时递增，此后第四位从1重新计起；前两位由用户决定。仅在用户明确要求时将develop合入main并随后同步GitCode。版本、变更登记与合入步骤只在[交付](.agents/skills/develop/references/delivery.md)。

## 2. 设计

[设计索引](设计/README.md)是唯一现行规范，编修遵循[design](.agents/skills/design/SKILL.md)。改动先分类：改变代码应有行为的是不变量变化，先改职责页并升版再实现；只让文档追平代码的是事实性修正，随代码一起改或攒批收口，不单独升版。实现偏离正确规范时，在开发计划标“开发中，与当前设计有偏离”并写修复方案，不为bug改规范。完工更新职责页、删除已完成进展项；根README只讲理念与使用。

## 3. 开发

按[develop](.agents/skills/develop/SKILL.md)的路由表只读改动涉及的职责页与测试，不通读全部规范；保持架构简洁，不增兼容别名、旧格式回退或双执行链。产物VERSION只在发布时更新，不随每次改模块递增，不高于设计。合同、授权、付款及沙箱边界从设计入口读取，不以模型文字代替用户批准。产品Skill与开发技能分别归src/access/skills与.agents/skills。

## 4. 测试

按[testing](.agents/skills/testing/SKILL.md)选择轻量、定向、完整或专项检查；复用唯一runner和未变输入结果，保留覆盖、日志、逐项耗时与真实退出码。

## 5. 部署

常规部署只读[部署教程](设计/工程/部署.md)，选择Windows本地或Linux服务器部分；[deployment](.agents/skills/deployment/SKILL.md)提供异常处理及专项入口。现行安装、进程归属与数据保留规则归[安装与升级](设计/工程/安装与升级.md)。

## 6. 模型

开发与验收工具依[llm](.agents/skills/llm/SKILL.md)统一引用私有.agents/llm_apikey及调用工具。实例经显式初始化取得自己的配置；运行组件不跨读开发源，不增加第二配置权威。

## 7. 工具与数据

可复用工具归tools，由[工具索引](tools/README.md)按所属技能查找；所有复用数据归.agents/data，一次性脚本、草稿及数据归temp，数据均不远端同步。现有目录迁移须同步所有消费者，保留原数据及摘要。

## 8. Demo

按[demo-video](.agents/skills/demo-video/SKILL.md)制作与验收；demo只留最终成片，制作工具归tools/media，录制与验证数据归.agents/data。必要公开结论只在实施资料摘要中维护。

## 9. 技能

SKILL.md不超过40行，写要做什么而不是罗列禁止；专题进references按需读，能脚本化的规则写成检查并从文字删除。完工提炼有价值经验，更新或按需新建技能，合并替换旧内容，不追加流水复盘。
用户明确授权多智能体协作时，按[parallel-development](.agents/skills/parallel-development/SKILL.md)划分职责、协调共享资源并接棒验收。
