# 公共调用与确认

触发：调用任一角色业务操作前读取本页；安装和启动另读 shared-runtime.md。以下是本技能脚本参数，不是九问原生工具。

## 调用格式

先读取location.md，使用其实际脚本路径和用户已配置的外置文件；每次命令都保留 --config，不读取配置正文。先用 write_file 在本次工作目录创建独立 UTF-8 JSON 参数文件，再执行：

```text
python "<skill_directory>/scripts/ioalab.py" <operation> --config "<原外置配置文件>" --args-file "<本次参数文件>"
```

`<operation>` 使用流程页中的完整名称，例如 `assistant.delegation.prepare`。其中的点和前缀也是命令的一部分；`prepare`、`authorize` 等末段名称不能单独调用。操作名输入被拒绝时，先按流程页修正命令，再使用原业务参数；账号身份以 `identity.status` 为准。

参数 JSON 只含流程页列明的业务字段，不放 operation、arguments 或整份配置；operation 已是命令位置参数。空输入用 {}。SDK 自动保存 operationId，恢复原动作使用原文件，不覆盖它或删除标识。不同动作或修正明确拒绝的输入，使用新的独立文件。不要用 echo > 写 JSON：Windows 终端可能产生不支持的编码。需要读取时只读本次业务参数文件，保留其原操作标识；始终禁止读取账号配置正文。JSON语法错误按SDK报告的行列修正，用write_file创建新文件；不是编码错误，不用PowerShell、echo或终端字符串重写中文。产品JSON可能以\u转义显示中文，值与原文相同，不据此改写合同或输入。

## 读取异步作业

异步结果中的data.reference是完整对象，例如{"kind":"job","id":"实际UUID"}。调用operations.get时参数为{"reference":{"kind":"job","id":"原UUID"}}，原样复制完整data.reference。若实际履约回执给出data.job.reference的UUID字符串，将其作为上述reference.id并保留kind="job"；不只传字符串、id或外层整份回执。履约也可用流程页的status加fulfillmentOperationId直接查原动作；顶层operationId只用于本次调用幂等，不是被查询动作。

operations.get外层status=completed只表示查询完成。继续检查data.status：queued/running继续查询原引用；completed后从data.data取得原业务结果。比较结果的candidates和verifierPackages位于该原业务结果中；按reviewRef读取已提供的真实源码，不能去本地目录寻找ioalab://资源。查询被拒绝或内容缺失时明确未核对，不能自行概括覆盖、签约或重做征集。

## 依回执继续

| status | 动作 |
|---|---|
| completed | 从 data 取得真实引用或结果，按流程继续；不把本机操作完成泛化为合同或付款完成 |
| accepted / running | 保存原引用，查询原任务；不重新征集、重签或发起重复业务 |
| waiting 且有 confirmationRef | **立即**用独立 {} 文件调用 runtime.open_ui，再展示预览并等待页面决定；不要先索要聊天“同意” |
| 页面已决定 | 重发原业务参数文件，核真实回执；聊天同意、opened=true 都不是批准证据 |
| rejected / failed | 先解析stdout中的结构化JSON；退出码1也可能是原业务已拒绝，不能据此重做。按 error 检查原因和 recovery。明确输入错误才用新文件纠正；不猜字段、删业务条件或绕过校验 |
| unknown | 保留原参数和结果，查询原操作；不以新操作重做可能已发生的效果 |

runtime.open_ui 的命令也沿用同一 --config 和独立 {} 参数文件。授权、具体合同、合同声明的环节与验收分别确认；对象、内容或阶段变化不能复用旧批准。浏览器选择由本机配置的 user_browser 决定，打开失败明确报告；不提取页面令牌或自写批准记录。
