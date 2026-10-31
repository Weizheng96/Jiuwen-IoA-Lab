# 公共SDK启动与恢复

触发：完成本机角色配置后安装、启动、诊断、停止或打开确认页。流程文档通过skill_tool的relative_file_path读取。脚本路径使用原生结果中的skill_directory；若模型可见结果只含正文和目录树，则通过原生glob在默认九问工作区查找 `**/ioalab-provider/scripts/ioalab.py` 或 `**/ioalab-delegate/scripts/ioalab.py`，仅定位当前所选技能。不要从账号data_dir推断，不扫描整仓、用户目录或依赖缓存；没有唯一实际路径则请用户提供导入目录。

首次runtime.start包含隔离依赖安装，终端本次调用应给足时限；宿主允许时使用300秒。不要修改九问全局配置。安装仅访问随包wheels和配置的国内镜像，不改九问或全局Python。

```text
python "<skill_directory>/scripts/ioalab.py" runtime.start --config "<外置配置文件>"
python "<skill_directory>/scripts/ioalab.py" runtime.status --config "<外置配置文件>"
python "<skill_directory>/scripts/ioalab.py" identity.status --config "<外置配置文件>"
```

runtime.start已经完成安装、配置校验及装配启动。runtime.install只用于单独预装，runtime.validate只做只读配置检查，普通接入不必另做。上述名称是本脚本参数，不是原生工具或技能商店动作。

| 真实结果 | 下一步 |
|---|---|
| start返回waiting | 沿同一配置查询runtime.status；不重复启动 |
| 工具观察超时 | 查原终端真实终态和同一实例；仍在运行则等待。只有明确终止后才重发原命令，不并行启动 |
| status完成 | 分别核对身份bound、platformConnected、宿主available及角色执行能力；未发布服务仍可executionReady=false |
| configuration_changed / installation_changed | 用空JSON文件调用runtime.stop，等待实际安全停止后按原配置启动；不改data_dir或私钥 |
| unknown | 保留原参数文件及operationId，按原角色查询/恢复；不重做签约、付款或通知 |

两个角色默认共用~/.ioalab/settings.local.json，也可用同一个--config指定其他外置文件。配置不要放在技能目录或data_dir中；原生覆盖导入会替换技能目录。data_dir保管稳定身份、原Session和业务，依赖缓存是另一个目录，不得删除运行数据来修复安装。

角色操作需要先写UTF-8 JSON参数文件，即使输入为空也用{}；调用格式为 `python "<skill_directory>/scripts/ioalab.py" <operation> --config "<外置配置文件>" --args-file "<参数文件>"`。SDK在参数文件中保留operationId，恢复必须复用原文件；改变已提交内容使用新文件/新操作。runtime.start/status及identity.status按上方命令执行。

需要用户确认时，用{}参数调用runtime.open_ui，请用户在本机受控页面核对完整预览并确认，再恢复原角色操作。不能用模型confirmed、自写批准记录或普通RPC代替真实用户。

配置由用户或本机脚本读取；模型不得读取配置正文来检查密码，也不在聊天和日志输出凭据。只汇报实际stdout回执及缺项，不读源码推演成功，不写报告或长期记忆充当安装。业务包安装不等于启用工具，权限只来自用户明确配置。
