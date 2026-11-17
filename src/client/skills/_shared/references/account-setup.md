# 首次账号引导

公开接入沿README_AGENT.md取得统一客户端。九问通过原生菜单导入并选择技能；Codex和Claude Code通过原生技能目录加载。实际读取本角色SKILL.md及引用页后继续。

接入前先完成[mcp-setup.md](mcp-setup.md)的当前会话启用核验；本机已有安装或配置文件也不能跳过。

当前会话已提供 IoA MCP 工具时，账号接入优先调用 ioa_setup（只传已知的 platform_url 和绑定角色 role，不传账号密码），由服务根据宿主能力内嵌展示；不要再调用脚本 account.setup 额外打开浏览器。用 ioa_setup_status 核对公开阶段。内嵌页连接被宿主阻止时，用户可点“在浏览器继续”，仍恢复原账号流程。当前聊天未挂载时返回mcp-setup.md引导原生启用并等待；只有确认宿主不支持、无法启用或用户明确选择浏览器后，才使用下文脚本流程。

先读location.md定位实际脚本，核CPython3.11。平台默认https://ioalab.openjiuwen.com；已有配置或用户已提供的platform_url优先。不先询问平台地址：SDK的account.setup先探测实际/api/config，只有返回stage=platform、nextAction=ask_platform_url时才询问用户完整地址；证书或网络失败不绕过TLS。已有账号与未知注册保留原平台和身份，不因探测失败重建。先调用runtime.install.status核统一客户端安装completed；不向用户要求手写命令或配置秘密。首次账号阶段优先按本页执行，shared-runtime.md中“普通接入完成安装后runtime.start”只适用于账号配置已经完成的实例。安装完成后，用独立非秘密JSON文件传递平台与角色，例如：

```json
{"role":"assistant"}
```

如用户指定浏览器，首次参数可加user_browser为已核浏览器可执行文件的绝对路径，SDK直接用它打开账号页并保留到最终配置；不传浏览器参数或shell字符串。已有配置的浏览器沿runtime.configure修改。

个人助手Skill使用assistant；服务方Skill使用service，不根据自由文本切换角色。默认地址不可达且用户给出地址后，在同一非秘密参数文件中补platform_url；地址可达时不要求用户重复提供。执行：

```text
python "<skill_directory>/scripts/ioalab.py" account.setup --args-file "<非秘密参数文件>"
python "<skill_directory>/scripts/ioalab.py" account.setup.status
```

SDK展示账号页（MCP Apps 优先内嵌，无 Apps 或脚本接入使用浏览器），用户在页内填写账号密码，或点击帮我注册并按需输入邀请码。模型不索要密码、邀请码，不读账号配置正文或账号日志。随机凭据可在本机页查看并保管。waiting只表示等待本机用户；completed只表示账号认证，不能说宿主已就绪或可接单。取消或页面到期后再次account.setup，原未知注册先恢复，不改账号反复注册。关闭注册、邀请码无效、重名按实际本机提示处理；网络故障和限流等待后恢复原请求。

| 当前实际阶段 | 唯一继续步骤 |
|---|---|
| 依赖尚未完成 | 用原bootstrap核验或修复安装；完成后用原非秘密参数文件调用account.setup，不先runtime.start |
| 本机账号页未弹出、误关或一次性入口失效 | 再次account.setup，重新打开原账号工作进程中的页面；保留原请求与注册进度 |
| account.setup.status仍为waiting | 等待用户在本机页完成；按其nextAction继续，不创建或读取账号配置 |
| account.setup.status为completed | 读取host-setup.md，按原SDK脱敏配置回执补齐宿主并启动 |

账号认证完成前，不调用account.login、runtime.configuration、runtime.configure或runtime.start来初始化账号，不用write_file、终端或占位密码手写“基础配置”。配置不存在是首次账号页的正常输入，由SDK在认证通过后原子创建；安装恢复后仍回到原账号阶段。

账号成功后按当前宿主方式读取host-setup.md，沿原runtime.configuration脱敏回执补齐宿主并启动；最终报告统一按status.md核实际字段。双角色复用原配置、账号和实例，不重复注册；服务声明、模板、公示和开放接单仍按服务方流程确认。

已有接入：phase=existing 时 SDK 自动发现并核验原客户端，用户在账号页点击“继续使用”。不要求用户提供配置路径，不反复恢复登录、不停止原实例。phase=reused 后改用 existingConfiguration，只读核验原实例，不再配置或启动新实例。无可核候选时说明原实例暂不可用，由 SDK 诊断，不扫描私人目录或猜测配置。

新账号配置同时准备个人助手与服务方身份，服务方默认不接单、不公示。已有接入沿原配置复用，不隐式补角色、改变政策或重启。
