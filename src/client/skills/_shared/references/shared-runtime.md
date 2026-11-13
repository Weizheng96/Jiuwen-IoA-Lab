# 公共SDK启动与恢复

一句话接入优先使用 `runtime.connect`；它核验已有统一客户端、受控账号接入、现有配置校验和启动。无配置时先在本机页面登录或注册，`account.setup.status` 查进度、`account.setup` 重开页面。`runtime.mcp.configuration` 返回准确的 stdio 启动命令和宿主配置资料。不得读取配置正文或让用户在聊天中提供密码。已有配置、原身份及在途业务按下文恢复规则复用。

`stage=configuration` 表示账号已保存但宿主字段缺失。使用回执中的 configurationDigest 和 requiredFields，通过 `runtime.configure` 补齐后续接 `runtime.connect`，不要重复启动。`host.command` 是 argv 数组，指向既有九问安装提供的 `jiuwenswarm-process`（可先用 `shutil.which` 定位并核对 `--help`），不是网页、gateway 或 `jiuwenswarm.app` 的启动命令。`host.data_dir` 取当前宿主公开的 `JIUWENSWARM_DATA_DIR`；定位不到 Process 可执行文件时，向用户收集该非秘密路径，不从账号配置或模型配置中找。模型选择以公开 Process `model.list` 的 selection_key 为准；沿用既有默认模型时可不填 model。

触发：完成本机角色配置后安装、启动、诊断、停止或打开确认页。先用skill_tool读取location.md并确定唯一脚本路径；其余流程页也使用relative_file_path读取。

客户端已由GitCode tag的bootstrap安装在独立环境，两个角色Skill使用同一解释器；Skill不下载或安装客户端。先执行runtime.install.status核客户端安装完整性，失败回到原bootstrap安装记录诊断，保留目录和原账号数据，不自行补包或读取源码猜安装结果。

```text
python "<skill_directory>/scripts/ioalab.py" runtime.install.status
python "<skill_directory>/scripts/ioalab.py" runtime.start --config "<外置配置文件>"
python "<skill_directory>/scripts/ioalab.py" runtime.status --config "<外置配置文件>"
python "<skill_directory>/scripts/ioalab.py" identity.status --config "<外置配置文件>"
```

上述python只用于运行薄调用脚本，脚本会转交bootstrap导出时绑定的客户端Python3.11；不把九问环境当作客户端环境。安装完成不代表已上线；首次先完成account-setup.md，再按host-setup.md启动。SDK启动返回waiting时只查询同一配置，不重复启动，不轮询数分钟占用前台。

configuration_changed或installation_changed时按原配置正常停止并核锁释放，再启动；不换data_dir或私钥。unknown保留原参数和operationId，查询原业务，不重做签约、付款或通知。status分别核身份bound、platformConnected、宿主available与角色执行能力；未发布服务不能报告可接单。

账号恢复：`python "<skill_directory>/scripts/ioalab.py" account.login --config "<外置配置文件>"`。此命令不需要args-file，由SDK持同一实例锁，只恢复账号会话，不启动九问、模型或业务。有效凭据复用不赠币；明确失效时才新登录。原登录未知时恢复同一标识，结果仍未知或输入已变则保留现场，不改密码/操作标识后反复请求。已明确拒绝后可修正本机配置并再次显式登录；newLogin只表示本次是否建立新的登录操作，不能据历史credited推定再次到账。普通重启不暗中重登。

有效凭据可在当前SDK上用独立{}参数文件调用account.refresh；不赠币，失败不自动密码登录。先按实际状态区分认证、连接和宿主能力，不能通过登录重试修复模型或发布错误。

两个角色默认共用~/.ioalab/settings.local.json，也可用同一个--config指定其他外置文件。配置不要放在技能目录或data_dir中；原生覆盖导入会替换技能目录。data_dir保管稳定身份、原Session和业务，依赖缓存是另一个目录，不得删除运行数据来修复安装。

需要九问辅助增加角色、业务能力或调整并发时，先runtime.install.status核安装完成，再调用runtime.configuration --config "<外置配置文件>"取得configurationDigest、身份和可修改字段；不要读取设置正文。生成独立UTF-8 JSON文件，例如：

```json
{"expectedDigest":"<实际configurationDigest>","changes":{"max_model_runs":2,"identities":[{"key":"coding","role":"service","name":"我的编程服务"}],"provider":{"services":{"coding":{"instructions":"用户明确的业务处理规则","skills":[],"max_active_delegations":2}}}}}
```

用runtime.configure --config "<外置配置文件>" --args-file "<修改参数文件>"提交。身份按key新增/更新，服务按字段合并，原角色、其他服务和凭据保留；不提供账号、密码、SDK数据目录、resources或host.business_tools修改。host的command/data_dir/model/skill_directories可由本机用户明确提供后配置，指向其既有九问，不改九问文件、模型设置或权限。返回changed只表示配置已保存；继续runtime.start并核实际能力，不能宣称已发布或接单。若实例持锁，先沿原配置runtime.stop并等实际释放再修改；不自动停服务、换身份或删状态。异摘要重新查询，同值重试只返回当前事实，不当作旧业务回执。runtime.validate仅做结构校验，不运行九问或业务工具。

首次账号认证后，hostFields已含command和data_dir时先复用并启动；缺项才补齐既有九问的公开Process入口和原数据目录。可靠来源是用户明确给出的值、当前九问公开启动信息及其JIUWENSWARM_DATA_DIR；不能确定就只询问缺少的命令或目录。不要猜jiuwenswarm-cli，不把HOME、SDK的dataDir或本次聊天工作目录当作九问数据目录，也不把SDK桥接模块当作九问入口。配置被拒绝后按已声明格式修正；入口或目录不明时结束本轮等待用户，不通过读取SDK源码、另调内部模块或反复试命令寻找执行链。

host.command是非空argv数组，host.data_dir是原九问数据目录；例如以下独立修改文件（所有占位值须由上述可靠来源取得）：

```json
{"expectedDigest":"<实际configurationDigest>","changes":{"host":{"command":["/既有九问环境/bin/jiuwenswarm-process"],"data_dir":"/原九问数据目录"}}}
```

host.model可选，只用该既有九问公开model.list返回的selection_key，不把API模型名当选择键。host.skill_directories是可选的业务技能“名称→绝对目录”对象，例如{"用户明确选择的业务技能":"/该业务技能目录"}；它不是数组、冒号字符串或全部技能目录。首次接入不需要该字段，不把ioalab-delegate或ioalab-provider接入技能加入业务生产技能源。沿原runtime.configure提交后再runtime.start，分别以runtime.status、identity.status核真实结果；host尚不可用时如实报告，不继续猜测或宣称完成。

业务参数、真实确认及原操作恢复统一见[公共调用与确认](calling.md)，开始角色业务前先读取该页。

配置由用户或本机脚本读取；模型不得读取配置正文来检查密码，也不在聊天和日志输出凭据。只汇报实际stdout回执及缺项，不读源码推演成功，不写报告或长期记忆充当安装。业务包安装不等于启用工具，权限只来自用户明确配置。
