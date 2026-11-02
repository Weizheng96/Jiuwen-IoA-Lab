# 公共SDK启动与恢复

触发：完成本机角色配置后安装、启动、诊断、停止或打开确认页。先用skill_tool读取location.md并确定唯一脚本路径；其余流程页也使用relative_file_path读取。

配置文件路径直接传给脚本，不用read_file或终端查看正文；SDK会报告文件缺失、格式及配置缺项。普通接入从runtime.start开始；用户只要求预装依赖时必须用runtime.install，不用runtime.start，以免已安装时意外启动SDK。缺依赖时发起独立后台安装并快速返回，不需要延长九问终端时限。安装仅访问随包wheels和配置的国内镜像，不改九问或全局Python。两种技能共用同一依赖任务。

```text
python "<skill_directory>/scripts/ioalab.py" runtime.start --config "<外置配置文件>"
python "<skill_directory>/scripts/ioalab.py" runtime.status --config "<外置配置文件>"
python "<skill_directory>/scripts/ioalab.py" identity.status --config "<外置配置文件>"
```

仅预装：`python "<skill_directory>/scripts/ioalab.py" runtime.install`；查询：`python "<skill_directory>/scripts/ioalab.py" runtime.install.status`。安装操作不需要账号配置或参数文件，也不需要手写releaseId/attemptId。

依照真实回执的stage和nextAction继续：stage=installation时查询runtime.install.status；依赖completed后，普通接入再次runtime.start才会校验配置并启动SDK，随后查询runtime.status；仅预装到此结束。安装完成不代表已上线。runtime.install用于单独预装或明确失败后的显式重试，不能当查询反复调用；runtime.validate只做配置检查，普通接入不必另做。其他诊断/业务操作不触发安装。安装结果只以runtime.install.status回执为准，不反复读日志推断完成；明确失败时才按logPath诊断。上述名称是本脚本参数，不是原生工具或技能商店动作。

| 真实结果 | 下一步 |
|---|---|
| waiting且stage=installation | 沿原releaseId/attemptId查询runtime.install.status；前台结束后后台继续，不再发起安装 |
| 安装completed | 仅预装到此结束；普通接入再runtime.start并查询runtime.status，不直接重做原业务 |
| SDK启动返回waiting | 沿同一配置查询runtime.status；不重复启动 |
| 工具观察超时 | 查询原安装任务或同一SDK实例，核真实进程/锁和终态；不因观察超时另起任务 |
| installation_failed | 查本次logPath并修正原因，显式runtime.install可重试；不改校验或手写完成标记 |
| 安装unknown | 保留原环境、attemptId和日志，核对真实所有者；不自动删除、重装或运行SDK |
| status完成 | 分别核对身份bound、platformConnected、宿主available及角色执行能力；未发布服务仍可executionReady=false |
| configuration_changed / installation_changed | 用空JSON文件调用runtime.stop，等待实际安全停止后按原配置启动；不改data_dir或私钥 |
| authenticate | 核对本机账号配置；原实例存在时先正常runtime.stop并核释放，再调用account.login，成功后runtime.start；不换data_dir或删除账号库 |
| unknown | 保留原参数文件及operationId，按原角色查询/恢复；不重做签约、付款或通知 |

账号恢复：`python "<skill_directory>/scripts/ioalab.py" account.login --config "<外置配置文件>"`。此命令不需要args-file，由SDK持同一实例锁，只恢复账号会话，不启动九问、模型或业务。有效凭据复用不赠币；明确失效时才新登录。原登录未知时恢复同一标识，结果仍未知或输入已变则保留现场，不改密码/操作标识后反复请求。已明确拒绝后可修正本机配置并再次显式登录；newLogin只表示本次是否建立新的登录操作，不能据历史credited推定再次到账。普通重启不暗中重登。

有效凭据可在当前SDK上用独立{}参数文件调用account.refresh；不赠币，失败不自动密码登录。先按实际状态区分认证、连接和宿主能力，不能通过登录重试修复模型或发布错误。

两个角色默认共用~/.ioalab/settings.local.json，也可用同一个--config指定其他外置文件。配置不要放在技能目录或data_dir中；原生覆盖导入会替换技能目录。data_dir保管稳定身份、原Session和业务，依赖缓存是另一个目录，不得删除运行数据来修复安装。

需要九问辅助增加角色、业务能力或调整并发时，先runtime.install并等完成，再调用runtime.configuration --config "<外置配置文件>"取得configurationDigest、身份和可修改字段；不要读取设置正文。生成独立UTF-8 JSON文件，例如：

```json
{"expectedDigest":"<实际configurationDigest>","changes":{"max_model_runs":2,"identities":[{"key":"coding","role":"service","name":"我的编程服务"}],"provider":{"services":{"coding":{"instructions":"用户明确的业务处理规则","skills":[],"max_active_delegations":2}}}}}
```

用runtime.configure --config "<外置配置文件>" --args-file "<修改参数文件>"提交。身份按key新增/更新，服务按字段合并，原角色、其他服务和凭据保留；不提供账号、密码、SDK数据目录、resources或host.business_tools修改。host的command/data_dir/model/skill_directories可由本机用户明确提供后配置，指向其既有九问，不改九问文件、模型设置或权限。返回changed只表示配置已保存；继续runtime.start并核实际能力，不能宣称已发布或接单。若实例持锁，先沿原配置runtime.stop并等实际释放再修改；不自动停服务、换身份或删状态。异摘要重新查询，同值重试只返回当前事实，不当作旧业务回执。runtime.validate仅做结构校验，不运行九问或业务工具。

业务参数、真实确认及原操作恢复统一见[公共调用与确认](calling.md)，开始角色业务前先读取该页。

配置由用户或本机脚本读取；模型不得读取配置正文来检查密码，也不在聊天和日志输出凭据。只汇报实际stdout回执及缺项，不读源码推演成功，不写报告或长期记忆充当安装。业务包安装不等于启用工具，权限只来自用户明确配置。
