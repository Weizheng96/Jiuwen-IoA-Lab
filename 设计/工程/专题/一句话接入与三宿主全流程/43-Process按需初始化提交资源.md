# 43：Process 按需初始化提交资源

状态：claimed；修复已发布并部署到内部测试服务器，真实 SQL 委托等待用户预算授权。依据为[宿主提交与资源规则](../../../组件/客户端/共用运行时/接口/宿主提交.md#文件排队与等待)和[九问宿主适配](../../../组件/客户端/宿主适配/接口.md#agenthost-实现)。

## 现状

修复前，测试服务器的 Process 适配器在两组 `HostResource` 列表均为空时也调用装配层绑定的资源工厂。服务方复签使用 `HostInput(prompt)` 和默认冻结定义，也未注入 `resource_io`。Registry 按原授权规则拒绝，原 S01 回执为 `not_ready / resources / not_submitted`，未进入九问原生轮次。本机无模型对照已复现该拦截。

报价与复签共用 `HostPolicy.decide`，生产也走同一 `ProcessHost.submit`。这些路径经 SDK 准备的业务材料可以已在本轮工作区落盘，仍可能没有显式 `HostResource`。真实故障证据、上层错误传播和旧作业恢复归[专题42](42-委托复签未知结果与诊断.md)。

## 修复方案

| 场景 | Process 适配器处理 |
|---|---|
| 无显式输入或定义资源 | 跳过工厂、资源文件布局和清单生成，直接走现有原生提交。普通 SDK 工具回调仍沿真实会话绑定及授权执行。 |
| 有显式输入或定义资源 | 原生提交前，使用原 operation 与完整 attempt 取得本轮 `ProcessFiles`，经原 reader 读字节、核摘要并落盘，将真实映射清单附到本轮输入。授权、摘要或布局失败仍返回 `phase=resources / dispatch=not_submitted`。 |
| 工具回调返回显式资源 | 原回调完成后才按需获取同一个 `ProcessFiles`。首次资源来自回调时也走原工厂。映射失败保留原回调值，以 `callback_resources / accepted` 报告，并保存原生终态及原业务回执。 |

在 `Submission.submit` 内让两处资源分支共用按需获取逻辑，成功创建的控制器只在本次 attempt 内复用。同轮如可并发进入资源分支，保护首次创建和同一资源的落盘，复用现有工厂、`ProcessFiles.materialize` 和 Registry 的 I/O 检查。保持改动局限于九问 Process 适配器及定向测试，沿用现行接口、权限、文件布局与回执格式。

## 验收目标

- [x] 常规提交：使用绑定真实 Registry 的工厂，无 `resource_io` 且两组显式资源为空。新建及续接均到达 Process 协议边界，工厂调用为 0，无资源清单和资源布局。原会话与请求绑定后，各工具回调执行一次。
- [x] 提交资源：输入资源和定义资源均映射为可读的原字节；摘要与冻结定义不变。同轮提交和回调均有资源时，控制器初始化一次。拒绝读取及摘要不符均未提交，另有 Registry 作用域拒绝用例。
- [x] 回调资源：首次资源由回调带入时才初始化，并发映射同一引用得到同一可读文件。缺工厂、缺授权或读取拒绝时，保留原回调值、`callback_resources / accepted` 及实际轮次终态。原资源隔离和关闭用例通过。
- [x] 本机定向首轮：`test_jiuwen_resources.py`、`test_jiuwen_submission.py`、`test_submission_resource_scopes.py` 和 `test_business_skills.py` 合计 44 通过、1 真实模型用例跳过，Process 协议使用边界替身。报告为本地 `temp/checks/ticket43-targeted/report.json`。
- [x] 常规提交夹具细化后的定向回归仍通过。先前快速回归中断时观察到的 A2C 旧期次付款恢复失败已独立复跑通过，发布检查结果另记。
- [x] 服务器安装验收：固定 GitCode 来源与实际安装文件字节匹配，六个受管单元就绪，官方 SQL 已开放接单。来源、发行及实例证据见[部署发行](../../实施资料/部署发行.md#process-资源与接单窗口内测)。
- [ ] 服务器实单：通过可信确认发起一笔获授权的新官方 SQL 委托，核到 `decision` 的实际原生请求、会话、终态，以及平台的服务方复签和成约状态。若本次实单进入生产，单独记录真实轮次及交付回执。
