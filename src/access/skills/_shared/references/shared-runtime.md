# 公共SDK启动与恢复

触发：完成本机角色配置后安装、启动、诊断、停止或打开确认页。流程文档通过skill_tool的relative_file_path读取。脚本路径使用原生结果中的skill_directory；若模型可见结果只含正文和目录树，则通过原生glob在默认九问工作区查找 `**/ioalab-provider/scripts/ioalab.py` 或 `**/ioalab-delegate/scripts/ioalab.py`，仅定位当前所选技能。不要从账号data_dir推断，不扫描整仓、用户目录或依赖缓存；没有唯一实际路径则请用户提供导入目录。

普通接入从runtime.start开始；用户只要求预装依赖时必须用runtime.install，不用runtime.start，以免已安装时意外启动SDK。缺依赖时发起独立后台安装并快速返回，不需要延长九问终端时限。安装仅访问随包wheels和配置的国内镜像，不改九问或全局Python。两种技能共用同一依赖任务。

```text
python "<skill_directory>/scripts/ioalab.py" runtime.start --config "<外置配置文件>"
python "<skill_directory>/scripts/ioalab.py" runtime.status --config "<外置配置文件>"
python "<skill_directory>/scripts/ioalab.py" identity.status --config "<外置配置文件>"
```

仅预装：`python "<skill_directory>/scripts/ioalab.py" runtime.install`；查询：`python "<skill_directory>/scripts/ioalab.py" runtime.install.status`。安装操作不需要账号配置或参数文件，也不需要手写releaseId/attemptId。

依照真实回执的stage和nextAction继续：stage=installation时查询runtime.install.status；依赖completed后，普通接入再次runtime.start才会校验配置并启动SDK，随后查询runtime.status；仅预装到此结束。安装完成不代表已上线。runtime.install用于单独预装或明确失败后的显式重试，不能当查询反复调用；runtime.validate只做配置检查，普通接入不必另做。其他诊断/业务操作不触发安装。上述名称是本脚本参数，不是原生工具或技能商店动作。

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
| unknown | 保留原参数文件及operationId，按原角色查询/恢复；不重做签约、付款或通知 |

两个角色默认共用~/.ioalab/settings.local.json，也可用同一个--config指定其他外置文件。配置不要放在技能目录或data_dir中；原生覆盖导入会替换技能目录。data_dir保管稳定身份、原Session和业务，依赖缓存是另一个目录，不得删除运行数据来修复安装。

角色操作需要先写UTF-8 JSON参数文件，即使输入为空也用{}；调用格式为 `python "<skill_directory>/scripts/ioalab.py" <operation> --config "<外置配置文件>" --args-file "<参数文件>"`。参数文件必须独立于账号配置和SDK数据，不能把settings.local.json当作args-file；由SDK统一读取，不自行改脚本解析编码。SDK在参数文件中保留operationId，恢复必须复用原文件；改变已提交内容使用新文件/新操作。runtime.start/status及identity.status按上方命令执行。

需要确认的角色操作先按流程调用一次；返回waiting和真实confirmationRef后，用独立的{}参数文件调用runtime.open_ui，请用户在本机受控页核对并决定，再重发原角色参数文件。申请确认本身不执行受保护动作，不要在创建待确认对象之前要求用户在聊天中“确认”。opened=true只表示浏览器打开，不证明页面有记录或用户已批准；没有待确认内容时回查原角色操作。拒绝/过期按真实结果停止。不能用模型confirmed、自写批准记录或普通RPC代替真实用户。

确认页默认使用系统浏览器。如需指定，用户可在外置配置中将user_browser设为已安装浏览器可执行文件的绝对路径（不带参数），再安全停止/启动SDK；无需改九问或系统默认设置。打开失败时检查该配置；不改用其他浏览器、不提取页面令牌或自写批准。浏览器控制扩展及权限由用户自行连接，opened=true不证明页面已加载或批准。

配置由用户或本机脚本读取；模型不得读取配置正文来检查密码，也不在聊天和日志输出凭据。只汇报实际stdout回执及缺项，不读源码推演成功，不写报告或长期记忆充当安装。业务包安装不等于启用工具，权限只来自用户明确配置。
