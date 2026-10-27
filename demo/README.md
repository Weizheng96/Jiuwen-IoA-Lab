# 两个用例的本地验收与录屏

录制真实九问网页与真实平台页面，协议调用来自运行中的参与方；不使用管理员回放或预编排事件代替执行。原始录像、账号配置和运行日志位于忽略的`data/`、`recordings/`，不提交凭据或个人消息。

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
| 会议 | Microsoft委托读取授权、通知SMTP真实可用，Outlook收到标准邀请；用户接受后查看日历 |
| 录制保护 | `--formal`拒绝fixtures；正式会议录制需`--headed`及本机已授权的`outlookStorageState`，用户在真实Outlook接受，脚本记录人工核对，不伪造接受回执 |

正式Outlook窗口由用户授权的浏览器状态打开，需另行对该窗口录屏（如OBS窗口/屏幕采集）；自动双屏成片只包含九问与IoA页。未完成Microsoft登录或通知配置时保持未验收。不要将浏览器状态、SMTP密码、模型密钥或原始个人消息推送远端。
