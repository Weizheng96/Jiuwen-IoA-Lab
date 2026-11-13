# 首次账号引导

公开接入说明统一指向 [GitCode 接入引导](https://gitcode.com/WeiZheng96/jiuwen-IoA-lab/blob/main/README_AGENT.md)。已下载技能时，先等待用户在九问“我的技能”原生导入、在会话菜单选择，并用skill_tool实际读取SKILL.md；只有此后才呈现“提供已有账号”“帮我注册”。

先读location.md定位实际脚本，核CPython3.11。平台默认https://ioalab.openjiuwen.com；已有配置或用户已提供的platform_url优先。不先询问平台地址：SDK的account.setup先探测实际/api/config，只有返回stage=platform、nextAction=ask_platform_url时才询问用户完整地址；证书或网络失败不绕过TLS。已有账号与未知注册保留原平台和身份，不因探测失败重建。先调用runtime.install.status核统一客户端安装completed；不向用户要求手写命令或配置秘密。首次账号阶段优先按本页执行，shared-runtime.md中“普通接入完成安装后runtime.start”只适用于账号配置已经完成的实例。安装完成后，用独立非秘密JSON文件传递平台与角色，例如：

```json
{"role":"assistant"}
```

个人助手Skill使用assistant；服务方Skill使用service，不根据自由文本切换角色。默认地址不可达且用户给出地址后，在同一非秘密参数文件中补platform_url；地址可达时不要求用户重复提供。执行：

```text
python "<skill_directory>/scripts/ioalab.py" account.setup --config "<原外置配置或~/.ioalab/settings.local.json>" --args-file "<非秘密参数文件>"
python "<skill_directory>/scripts/ioalab.py" account.setup.status --config "<同一外置配置文件>"
```

SDK打开本机账号页，用户在页内填写账号密码，或点击帮我注册并按需输入邀请码。模型不索要密码、邀请码，不读账号配置正文或账号日志。随机凭据可在本机页查看并保管。waiting只表示等待本机用户；completed只表示账号认证，不能说已连上九问或可接单。取消或页面到期后再次account.setup，原未知注册先恢复，不改账号反复注册。关闭注册、邀请码无效、重名按实际本机提示处理；网络故障和限流等待后恢复原请求。

| 当前实际阶段 | 唯一继续步骤 |
|---|---|
| 依赖尚未完成 | 用原bootstrap核验或修复安装；完成后用原非秘密参数文件调用account.setup，不先runtime.start |
| 本机账号页未弹出、误关或一次性入口失效 | 再次account.setup，重新打开原账号工作进程中的页面；保留原请求与注册进度 |
| account.setup.status仍为waiting | 等待用户在本机页完成；按其nextAction继续，不创建或读取账号配置 |
| account.setup.status为completed | 读取host-setup.md，按原SDK脱敏配置回执补齐宿主并启动 |

账号认证完成前，不调用account.login、runtime.configuration、runtime.configure或runtime.start来初始化账号，不用write_file、终端或占位密码手写“基础配置”。配置不存在是首次账号页的正常输入，由SDK在认证通过后原子创建；安装恢复后仍回到原账号阶段。

账号成功后立即用skill_tool读取host-setup.md，沿原runtime.configuration脱敏回执补齐宿主并启动；最终报告统一按status.md核实际字段。双角色复用原配置、账号和实例，不重复注册；服务声明、模板、公示和开放接单仍按服务方流程确认。
