# 演示视频与真实录屏

名称：九问IoA实验平台（Jiuwen IoA Lab）。介绍：面向智能体互联网的仿真验证与开放体验平台。

成片入口见根[演示视频](../README.md#演示视频)，当前进展与未验收项集中在[验证记录](verification/README.md)。原始配置和录像在本机忽略目录，不提交为公开验收摘要。

## 全局视角

`global_story.py`生成[全局剧本](全局剧本.md)，`global_web/`定义只读讲解地图。0—8按剧本隔离动画；9—10播放加速概念全景并穿插4倍真实管理员监控。上下线、授权和条件仲裁为机制示意；执行A2X分类树，ANN只介绍方案。实际统计和成片检查见[global.json](verification/global.json)。

复用入库素材重制，无需模型或邮箱凭据：

```powershell
python demo/record_global.py --source demo/assets/run/global --voice-timing demo/assets/voice/global/voice.json --render-helper ../AgentContractProtocol/demo/_lib/video/record.py --out recordings/global-rebuild
```

重新采集时先执行`capture_global.py --model-env <本地模型env> --out data/<新目录> --seconds 100`；`global_story.py --prepare data/<新配音目录>`生成字幕，再按下文配音和录制。保持概念动画与实际数据分开标识，不修改A2C共用录制器。

## 两段真实用例

| 用例 | 实际执行与镜头 |
|---|---|
| 美甲委托 | 用户九问→A2X→两份真实提案→比较报价和判据→确认合同→独立Swarm生产→本地WASM验收→再次确认→签7与模拟付款→服务方同单收入→交付配置预约/取消 |
| 助手交互 | A的精确账号白名单与本地可编辑安排→B的九问查询inputSchema→留言→忙碌拒绝→改约→AgentMail邀请→收件人在真实Outlook接受并查看日历 |

`case_story.py`是[委托剧本](委托剧本.md)、[交互剧本](交互剧本.md)的唯一源。52px短字幕、较快旁白，A2C念“A to C”。按实测音频安排镜头，剪短等待时标明；工作台详情按钮聚焦同一合同。报价叠层明确为讲解，数字来自实际签名提案。

前提：按[独立助手说明](../personal-assistant/README.md)安装锁定九问与通知扩展，确认Web和Process模型可用，构建技能包；录屏Python环境安装`demo/requirements.txt`并执行`python -m playwright install chromium`。

每轮正式委托必须新建**参与方数据目录**，不能只换录像目录。旧未采纳提案可能占服务容量；入口在有旧合同时拒绝继续。

```powershell
python tools/build_connector.py
python demo/case_stack.py --folder data/<新一轮> --model-env <九问本地模型env> --notification-env <通知env> --service-model <model.list的selection_key>
# 保持上方进程运行；另开终端，使用其生成的配置，不输出口令
python demo/record_cases.py --config data/<新一轮>/run.json --case delegation --out recordings/<新委托录像目录>
python demo/record_cases.py --config data/<新一轮>/run.json --case messaging --out recordings/<新交互录像目录>
```

`case_stack.py`关闭样例替身：服务由真实Swarm生产，独立A客户端读取本地日程并使用部署方真实通知邮箱。账号、密钥和配置只落本机忽略目录。启动不发信；消息用例改约会向配置收件人提交一封邀请。邮箱形式B账号只是平台账号字符串，不代表企业邮箱认证。

网页模型资源ID不能充当Process选择键。服务启动先查询`model.list`，配置其`selection_key`；网页选对应资源别名。录制提示明确真实技能路径，模型不应去在线市场找技能。签字、验收和付款由九问调用用户侧SDK完成，录制器不替用户执行成功业务。

## 配音、剪辑与核对

```powershell
python demo/case_story.py --case delegation --prepare data/<新配音目录>
python demo/build_voice.py --recording data/<新配音目录> --helper <A2C主仓>/demo/_lib/aliyun.py --speech-rate 1.6 --tempo 1.12
python demo/edit_cases.py --recording recordings/<本轮> --voice-timing data/<配音目录>/voice.json
python demo/review_cases.py --recording recordings/<本轮>
```

交互配音将case改为`messaging`。全景配音使用同一`build_voice.py`参数后，把voice.json传回`record_global.py`。`edit_cases.py`调用`assemble.py`和`check_recording.py`；`review_cases.py`核对每个编码镜头及配音窗口。输出包含剪辑清单、SRT、配音索引、证据、检查与MP4。

完整交互片须加入本轮真实Outlook接受及日历画面，剪辑清单记录独立source与时间原点。证据`outlook.status=accepted_and_visible`只能在实际界面核验后填写；不得以邀请受理、旧邀请或模型文字代替。缺此镜头时只能传`--preview`：省略未发生的接受入历旁白，持续显示待补录标识；正式编辑会拒绝缺证输入。浏览器URL/权限受限时停止该浏览器操作，不复制认证状态绕过。

`record_case_tail.py`仅供同一已完成合同的工具与收尾补录：先核签7、已付和摘要，不重造合同结果；独立source与原因写入证据。失败拍摄保留。

## 联调与边界

原`local_stack.py`、`verify_local.py`、`record_local.py`保留给回环SMTP、测试日历和样例服务联调，测试片持续标识，不作正式片。真实服务、消息、邮件与视频分别验收。

选用[AgentMail原生会议请求](../personal-assistant/README.md)时，`notification-check`只验证传输，不发邮件。Outlook由收件人接受后入历；本地安排不代表已读取私人日历。Graph和Outlook SMTP OAuth2仍是可选后端，本轮不要求重新配置Entra。

仅公开必要的演示画面、成片、配音与脱敏摘要；run.json、SQLite、私钥、模型/邮件配置、浏览器状态与私人消息不入库。失败、初剪、预览和正式片分名。跨仓经验见A2C主仓`demo-video/references/live-platform.md`。
