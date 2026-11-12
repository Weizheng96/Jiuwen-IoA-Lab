# 已接入状态核对

触发：用户已有外置配置，要求查连接、身份、双角色共用或服务是否已发布/接单。先读location.md和calling.md，使用所选技能的实际脚本路径。配置路径只作为--config参数传递，检查依据全部来自以下SDK脱敏回执。

1. 直接查询现有实例，不先安装、配置或启动：

```text
python3.11 "<skill_directory>/scripts/ioalab.py" runtime.status --config "<原外置配置文件>"
python3.11 "<skill_directory>/scripts/ioalab.py" identity.status --config "<同一外置配置文件>"
```

Windows使用已核的python。上述两个命令不带args-file。runtime.status回执的data中account给出账号认证、identities给出实际profile/role/bound/executionReady、host给出宿主及模型、platformConnected给出平台连接，installation给出实际运行环境；以这些字段分别报告，不打开账号配置文件寻找字段。

2. 从runtime.status回执的data.identities选择实际role=service的条目，其profile就是服务管理参数。选中ioalab-provider时，查询公示与接单状态用新的独立UTF-8参数文件：

```json
{"profile":"<上述实际profile>","action":"status"}
```

按calling.md以provider.flow调用。published、templateReady、admissionPolicy和localExecution分别来自真实结果；未发布、未配置业务或未接单如实报告。只选中个人助手技能时先报告其共用实例和身份状态，服务管理由原生菜单选择ioalab-provider后继续。

3. 实例not_ready时按回执stage/nextAction进入shared-runtime.md恢复原实例；要修改配置时才用runtime.configuration取得脱敏可修改字段与摘要，再按setup.md受控配置。配置文件缺失才进入account-setup.md。原账号、身份、参数与未知操作保留；不以读取配置正文、账号日志或内部数据库补齐诊断。
