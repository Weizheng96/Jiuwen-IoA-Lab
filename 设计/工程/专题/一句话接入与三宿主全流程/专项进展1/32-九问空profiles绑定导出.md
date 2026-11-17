# 32 专项进展：九问空profiles绑定导出（2026-11-17）

负责：32子智能体；主智能体负责调度与Git。现行规则以设计索引与[票据32](../../../../组件/客户端/进展/32-Windows九问原生会话与上下文投递.md)为准；来源为[28交接](28-原生入口绑定迁移.md)“越界需求（32）”一行。

## 状态

工作区改动，**未git add、未提交、未推送**。

| 项 | 结果 |
|---|---|
| 规范核对 | [接入交付](../../../../组件/客户端/共用运行时/接口/接入交付.md)P07与[宿主提交](../../../../组件/客户端/共用运行时/接口/宿主提交.md)只要求导出两角色公共内容与非秘密绑定，不要求profiles非空；未选角色保持未就绪 |
| 改动 | `src/client/hosts/jiuwen/ioalab_jiuwen/native_skills.py`：删`not binding['profiles']`；拒绝sourceOperations重复及setupOperation混入（同另两适配） |
| 新测试 | `tests/test_jiuwen_native.py`末尾3例：空profiles接受、重复／混入拒绝 |
| 检查 | `temp/checks/t32-199-fix02`真实exit1：4light全0；onboarding+skill_location+jiuwen_native 65passed／1failed |

## 剩余失败（不归32）

`test_same_role_markdown_and_references_are_identical_across_three_host_exports`：比较全部`.md`，含宿主专属`native/README.md`，Codex与Claude之间也不同；九问异常解除后才暴露。归28公共导出或测试口径，32未改测试。

## 未验

未跑fast层；未在原生九问实例导入空profiles资源；账号后重装被判“Existing skill differs”的风险仍归28。
