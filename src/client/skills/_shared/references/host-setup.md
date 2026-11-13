# 账号完成后的宿主接入

原account.setup.status为completed后，使用同一脚本和外置配置继续，不读取配置正文。

1. 不带args-file调用runtime.configuration --config "<原配置>"，保留configurationDigest、hostType、supportedHostTypes与hostFields。已有hostType且用户未要求迁移时复用；首次按当前宿主选择jiuwen、codex或claude_code，不猜默认宿主，不把SDK dataDir当宿主目录。
2. 仅使用当前宿主公开信息补齐字段，不读取.env、登录凭据、模型密钥或历史库：

| 宿主 | host字段 | 来源与边界 |
|---|---|---|
| Codex | type="codex"、command为argv数组、model为显式模型名；skill_directories可选 | shutil.which('codex')定位既有命令；模型取用户明确选择或原版公开model/list实际返回的模型，不猜模型名。缺项才询问；不提供九问data_dir或business_tools |
| Claude Code | type="claude_code"、command为只含一个真实CLI绝对可执行路径的数组、model显式选择；skill_directories可选 | 复用已安装CLI与原认证；Windows使用claude.exe而非claude.ps1/.cmd包装脚本。模型取官方Agent SDK公开model_catalog返回的value；公开default别名可用，不猜首个模型为默认。遇到包装脚本可读其公开安装清单或包装内容定位真实可执行文件；不能定位时只询问缺项 |
| 九问 | type="jiuwen"、command为argv数组、data_dir为原九问目录；model、skill_directories可选 | shutil.which('jiuwenswarm-process')及JIUWENSWARM_DATA_DIR定位，缺项或归属不明才询问。model只取原版公开model.list的selection_key；SDK与宿主目录互不包含 |

3. 用新建独立UTF-8参数文件提交expectedDigest和changes.host，例如Codex：

~~~json
{"expectedDigest":"<原SDK摘要>","changes":{"host":{"type":"codex","command":["<既有Codex绝对命令>"],"model":"<实际已选模型>"}}}
~~~

九问使用type="jiuwen"及实际command、data_dir。执行runtime.configure --config "<同一原配置>" --args-file "<新文件>"。参数仅含非秘密变更，不从账号配置复制字段。摘要冲突重新查询；持锁沿shared-runtime.md正常停止，不删锁或数据。已有host.type不自动切换，转换须保留账号、原Session与业务并显式迁移。
4. 配置完成后调用runtime.start --config "<同一原配置>"，再按status.md查询runtime.status和identity.status。waiting只查询原实例，不重启。分别报告authenticated、host.available、platformConnected、身份bound；服务未配置/公示时不称可接单。Codex和Claude Code首版不开放原生生产工具、原生问题回调及当前桌面聊天通知，需要这些能力时如实报告not_ready，不以模型文字代替用户批准。
