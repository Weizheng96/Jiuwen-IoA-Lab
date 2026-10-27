# 三段演示的本地验收与录屏

全局片：[九问IoA实验平台](out/01-global.mp4)。唯一剧本源为`global_story.py`，生成[全局剧本](全局剧本.md)；实际运行与成片摘要见[global.json](verification/global.json)。委托与交互正式片仍在制作，旧本地测试片不替代它们。

| 部分 | 构建方式 |
|---|---|
| 0—8 | 固定剧本、逐段隔离路径的只读概念地图；完整片头、登录赠币、登记、需求授权、发现、合同履约、支付信誉、P2P消息 |
| 9—10 | 加速全景与上下线示意，穿插4倍真实管理员监控 |
| 数据 | 6个独立SDK客户端，本轮18笔委托、108条消息、18次真实A2X分类树搜索；样例服务和虚拟币明确标识 |
| 声画 | 52px高对比短字幕，20段较快旁白；A2C念A to C；镜头按实测音频时长安排 |
| 边界 | 上下线、支付授权与条件仲裁为机制示意；ANN只说明方案。本轮未执行ANN或发生裁定；不把概念动画当业务证据 |

已调研A2C主仓智能体互联网的story/script、导出素材、map、配音及逐帧录制：采用固定剧本控制镜头，真实运行提供统计，音频时长驱动时间轴。直接复用其`demo/_lib/video/record.py`逐帧录制器，不修改共享实现。原始监控和业务元数据经过筛选，仅包含本轮生成的实验账号；不含密码、密钥或消息正文。

复用入库素材重制，不需要模型或邮箱凭据：

```powershell
python demo/record_global.py --source demo/assets/run/global --voice-timing demo/assets/voice/global/voice.json --render-helper ../AgentContractProtocol/demo/_lib/video/record.py --out recordings/global-rebuild
```

重新采集一轮或改旁白时，显式传入部署者本地模型配置与资产helper：

```powershell
python demo/capture_global.py --model-env <本地模型环境文件> --out data/global-new-run --seconds 100
python demo/global_story.py --prepare data/global-new-voice
python demo/build_voice.py --recording data/global-new-voice --helper <A2C主仓>/demo/_lib/aliyun.py --speech-rate 1.6 --tempo 1.12
python demo/record_global.py --source data/global-new-run --voice-timing data/global-new-voice/voice.json --render-helper <A2C主仓>/demo/_lib/video/record.py --out recordings/global-new-film
python demo/global_story.py --timing recordings/global-new-film/voice.json
```

渲染先核对所有镜头的动效类别与52px字幕，保存20张抽帧；最终检查音轨、旁白衔接、分辨率与哈希。`--preview`只生成镜头检查，不输出正式视频。图形与文案源在`global_web/`；完成后将成片、配音、脱敏运行素材与验证摘要入库，原始日志和运行配置保留在忽略目录。

## 本地联调

1. 安装平台与SDK，执行`python tools/build_connector.py`。可选录屏环境安装`demo/requirements.txt`并执行`python -m playwright install chromium`。
2. 按`personal-assistant/README.md`配置锁定的独立九问及通知扩展，启动真实九问。
3. 启动`python demo/local_stack.py --folder data/local-e2e --port 8750 --swarm "ws://127.0.0.1:19000/ws?user_id=ioa-admin"`。将相同桥接口令注入此进程环境，不在命令行或聊天显示。该入口明确使用样例服务、测试日历、回环SMTP收件，绝不向外部发信。每次使用新folder，保留已有证据。
4. `python demo/verify_local.py --config data/local-e2e/run.json`验证合同签名、WASM验收、真实九问通知、邀请和冲突拒绝。账号口令只在该目录run.json，不输出。
5. 在九问技能页导入`dist/ioalab-skill.zip`，按技能说明填写本地配置。录屏中的B账号使用run.json中B，`IOALAB_HOME`为`<folder>/client-b`。模型必须实际可用；推荐在九问中注册资源后在run.json指定`swarmModelId`，仅传展示名称不足以保证选择生效。

run.json另可配置`swarmWeb`（九问网页地址）、`swarmModel`、`swarmModelId`、`skillDirectory`（安装器返回的绝对目录）。文件含本地演示凭据，不提交。默认模板里的固定样例只接受对应示范输入；正式服务由Swarm生产，不能用样例成功代替生产成功。

## 录制与成片

```bash
python demo/record_local.py --config data/local-e2e/run.json --case delegation
python demo/record_local.py --config data/local-e2e/run.json --case messaging
python demo/build_voice.py --recording recordings/<本次目录> --helper <部署者提供的Aliyun资产生成helper.py>
python demo/assemble.py --recording recordings/<本次目录>
python demo/check_recording.py --recording recordings/<本次目录>
```

用例①由真实九问发起一单，右侧个人助手工作台显示进展，再登录对应ISP服务方查看同一合同；只有新增completed合同及真实签7可通过。用例②从B页面查A接口、留言，A真实九问收到通知；预约后明确显示等待接受，再验证重复时段拒绝。脚本等待实际处理回执，不以固定等待时间判定完成。

输出包括左右原始WebM、双屏MP4、SRT、配音、实际工具事件、evidence.json、qa.json和抽检帧。本地测试片文件名`local-test.mp4`，始终显示测试配置标识；这不是Outlook接受邀请的正式验收。默认Windows字体，Linux用`--font`传中文字体文件。旁白由明确指定的资产helper读取部署者凭据，不从核心读取密钥；无helper时可直接拼带字幕的静音初稿。

## 正式验收

| 项 | 要求 |
|---|---|
| 委托 | 独立官方服务关闭STUB，真实Swarm生产；用户九问SDK完成验收；查询服务方记录 |
| 消息 | A配置允许账号，B读取格式；真实九问通知会话可见；非允许账号被拒绝 |
| 会议 | 显式选择的本地日程有效、通知邮箱真实可用，Outlook收到标准邀请；用户接受后查看日历。Graph读取为另一个可选后端 |
| 录制保护 | `--formal`拒绝fixtures；正式会议录制需`--headed`及本机已授权的`outlookStorageState`，用户在真实Outlook接受，脚本记录人工核对，不伪造接受回执 |

正式Outlook窗口由用户授权的浏览器状态打开，需另行对该窗口录屏（如OBS窗口/屏幕采集）；自动双屏成片只包含九问与IoA页。未完成Microsoft登录或通知配置时保持未验收。不要将浏览器状态、SMTP密码、模型密钥或原始个人消息推送远端。

使用Outlook通知邮箱时，先按[独立助手说明](../personal-assistant/README.md)完成两份设备授权：`calendar-login`登录被预约邮箱，`smtp-login`登录通知邮箱；`smtp-check`只验证发信身份，不发邮件。通知邮箱公开地址可以对外展示，密码、设备授权和令牌仍属本机配置。Outlook不使用SMTP密码回退；应用注册及用户同意未完成时，不以模拟响应替代真实验收。

通知方可选AgentMail，按独立助手说明注册、验证并写入本机配置，再执行`notification-check`验证所选传输；`smtp-check`只作SMTP诊断。AgentMail会议使用原生Calendar API，不能以普通SMTP附件代替会议请求。单独验证邮件通路可用`python demo/verify_notification.py --env data/personal-assistant.env --appointment-file data/notification-appointment.json --out data/notification-check --send`；预约文件包含title、start、end，时间必须带时区且在将来。该入口不读取日历、不运行测试日历、不代替消息中心端到端验收；只向配置的收件邮箱发送一封真实邀请，记录邮件受理。相同out目录有发送意图即拒绝重发；接收与接受仍需收件人核对。
