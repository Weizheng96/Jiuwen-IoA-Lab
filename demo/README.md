# 演示视频与真实录屏

名称：九问IoA实验平台（Jiuwen IoA Lab）。介绍：面向智能体互联网的仿真验证与开放体验平台。

成片入口见根[演示视频](../README.md#演示视频)，当前进展与未验收项集中在[验证记录](verification/README.md)。原始配置和录像在本机忽略目录，不提交为公开验收摘要。

全局演示可直接打开[播放页](全局视角/播放.html)：讲解结束自动切入单一全屏真实管理员监控录屏并循环播放，持续标注回放。实际平台的`/admin?view=panorama`提供组件点选说明与账号聚焦；视频本身不代表可操作的实时页面。

| Demo | 剧本 | 当前视频 |
|---|---|---|
| 全局视角 | [剧本.md](全局视角/剧本.md) | [正式片](out/01-global.mp4) |
| 美甲委托 | [剧本.md](美甲委托/剧本.md) | [正式片](out/02-delegation.mp4) |
| 助手交互 | [剧本.md](助手交互/剧本.md) | [预览，Outlook镜头待补录](out/03-messaging-preview.mp4) |

```powershell
python demo/script_documents.py          # 生成全部场景的剧本.md
python demo/script_documents.py --check  # 核对剧本、镜头覆盖与已发布视频哈希
```

剧本由场景源、配音时间轴、画面操作说明和脱敏证据共同生成，不手改。预览未包含的Outlook镜头仍保留为待补录计划，不编造片中时间。原散放剧本与重复镜头方案已移至[历史备份](../历史备份/README.md)。

## 全局视角

`global_story.py`定义字幕与旁白，`global_web/`定义只读讲解地图。0—8按剧本隔离动画，左侧用户与个人助手一一配对；9—10用户先虚化消失、助手重排，再柔焦渐变进入单一全屏真实管理员监控，旁白后延续网络活动。概念片与真实界面共用组件布局及短连接；真实界面没有讲解用户。上下线、授权和条件仲裁为机制示意；执行A2X分类树，ANN只介绍方案。实际统计和成片检查见[global.json](verification/global.json)。

复用入库素材重制，无需模型或邮箱凭据：

```powershell
python demo/record_global.py --source demo/assets/run/global --voice-timing demo/assets/voice/global/voice.json --render-helper ../AgentContractProtocol/demo/_lib/video/record.py --out recordings/global-rebuild
```

重新采集时先执行`capture_global.py --model-env <本地模型env> --out data/<新目录> --seconds 150 --participants 36`；`global_story.py --prepare data/<新配音目录>`生成字幕，再按下文配音和录制。保持概念动画与实际数据分开标识，不修改A2C共用录制器。

真实监控与概念地图共用`ioalab/web/static/network-map.js`和`network-map.css`。讲解段使用固定种子的有限随机计划，收发错峰、部分并发、传输结束撤除；真实监控仅响应实际事件，历史状态不产生常驻连线。身份中心、委托中心统一命名；灰色悬赏中心与意图中心仅展示规划，没有业务接口、交互或旁白。意图中心副标题为“用户权限白名单管理”。

真实流量按实际绘制的委托/P2P传输接近各半调整，不以订单数直接对比消息数。独立实验客户端分散调用不同服务；原始订单、消息、绘制计数分别保留，未达目标与失败的录制不作为成片素材。

真实监控为助手、等高平台组件、服务、行为日志四列。日志只显示真实事件元数据，最近200条，支持暂停滚动；字幕避开日志区域。多发起方密度由瞬时短线、不同发起方与每秒新增传输检查。讲解切入实录时同时对齐助手/组件/服务，2.8秒渐变中保持已预加载的同一首帧，再继续播放；最终编码提供七个过渡检查时点。

委托与消息各用独立工作线程持续运行，搜索或履约等待不会暂停收发；首次消息也铺开到完整周期，避免开头集中一波。录制前等待真实混合流量稳定，再按管理员实际绘制计数定时采样；每个完整十秒窗口必须同时包含两类传输。`public-run.json`内`proof.mixedActivity`保存逐窗结果，剪辑与发布均拒绝时段分离的素材。片尾与循环播放页使用同一份合格实录。

```powershell
python tools/check_panorama.py  # 使用demo录制环境的Playwright；离线DOM及残影回归，不调用模型
python demo/publish_global.py --recording recordings/<已检查全局片> --source data/<本轮真实运行>
python demo/script_documents.py
```

发布工具核对成片哈希和真实运行检查，保存去掉初始化空场的监控片、配音、逐镜头最终编码抽帧和脱敏数据。原始失败运行保留在本机；没有用删除旧合同的方式恢复服务容量。

## 两段真实用例

| 用例 | 实际执行与镜头 |
|---|---|
| 美甲委托 | 一句需求→委托授权→A2X及七家筛选→三份真实提案→推荐码上飞→确认合同→实名环节授权→独立Swarm生产→本地WASM→确认验收及付款→服务运营台同单收入→预约/取消 |
| 助手交互 | 王经理定义交互接口与账号范围→赵专员的九问查询inputSchema→留言→冲突回执→改约→邀请交王经理接受；真实Outlook接受与入历另行验收 |

`case_story.py`是两段用例字幕与旁白的唯一源，`script_documents.py`维护画面操作并生成上表剧本。52px短字幕、较快旁白，A2C念“A to C”。按实测音频安排镜头，剪短等待时标明；工作台详情按钮聚焦同一合同。报价叠层明确为讲解，数字来自实际签名提案。

两段用例的个人助手工作台固定上方网络，下方记录、接口与详情独立滚动。网络复用管理员组件目录；仅其他个人助手匿名；服务使用公开目录的真实名称，非关联服务弱化。灰色短连接为本地示意，不请求其他账号的私有活动，也不进入业务统计。本人活动依真实事件变化播放，不循环补造业务。接口区统一为“我定义的交互接口”，助手间交互不预设都是协作。使用录屏环境运行`python tools/check_workbench.py`复查布局、隐私请求、轮询展开保持和短线清除。

前提：按[独立助手说明](../personal-assistant/README.md)安装锁定九问与通知扩展，确认Web和Process模型可用，构建技能包；录屏Python环境安装`demo/requirements.txt`并执行`python -m playwright install chromium`。

每轮正式委托必须新建**参与方数据目录**，不能只换录像目录。旧未采纳提案可能占服务容量；入口在有旧合同时拒绝继续。

```powershell
python tools/build_connector.py
python demo/case_stack.py --scenario delegation --folder data/<新委托参与方目录> --model-env <九问本地模型env> --notification-env <通知env> --service-model <model.list的selection_key>
# 保持上方进程运行；另开终端，使用其生成的配置，不输出口令
python demo/record_cases.py --config data/<新委托参与方目录>/run.json --case delegation --out recordings/<新委托录像目录>
# 委托录制结束后另建 --scenario messaging 的独立参与方目录，再录交互片；两轮依次运行，避免覆盖本地技能配置
python demo/record_cases.py --config data/<新交互参与方目录>/run.json --case messaging --out recordings/<新交互录像目录>
```

`case_stack.py`关闭样例替身：服务由真实Swarm生产，独立A客户端读取本地日程并使用部署方真实通知邮箱。账号、密钥和配置只落本机忽略目录。启动不发信；消息用例改约会向配置收件人提交一封邀请。邮箱形式B账号只是平台账号字符串，不代表企业邮箱认证。

网页模型资源ID不能充当Process选择键。服务启动先查询`model.list`，配置其`selection_key`；网页选对应资源别名。安装阶段确认宿主已索引技能并准备SDK，新会话通过原生菜单预选IoA技能；用户首句仅保留业务需求，不附加技能路径或技术提示。签字、验收和付款由九问调用用户侧SDK完成，录制器不替用户执行成功业务。

委托录制完成后执行`python demo/summarize_delegation.py --config data/<本轮参与方>/run.json --recording recordings/<本轮>`，从实际服务任务和本地WASM报告附加脱敏摘要，再进入剪辑。需要补录服务运营台时，使用`record_provider_view.py`的同一配置与录像参数；它核对已结案合同，保留独立source，不重新生产或付款。

## 配音、剪辑与核对

```powershell
python demo/case_story.py --case delegation --prepare data/<新配音目录>
python demo/build_voice.py --recording data/<新配音目录> --helper <A2C主仓>/demo/_lib/aliyun.py --speech-rate 1.6 --tempo 1.12
python demo/edit_cases.py --recording recordings/<本轮> --voice-timing data/<配音目录>/voice.json
python demo/review_cases.py --recording recordings/<本轮>
```

交互配音将case改为`messaging`。全景配音使用同一`build_voice.py`参数后，把voice.json传回`record_global.py`。`edit_cases.py`调用`assemble.py`和`check_recording.py`；`review_cases.py`核对每个编码镜头及配音窗口。输出包含剪辑清单、SRT、配音索引、证据、检查与MP4。

`qa.json`与`shot-review.json`均记录实际视频的SHA256；发布前必须同时匹配当前视频。影片重新合成后须重新运行这两项检查，不能沿用旧结果。

| 工具 | 单一职责 |
|---|---|
| `global_story.py` / `case_story.py` | 场景字幕与旁白；准备配音输入，不读凭据 |
| `script_documents.py` | 生成逐场景剧本，核对已发布素材 |
| `case_stack.py` | 准备本轮独立参与方环境，配置仅保留本地 |
| `record_cases.py` / `message_case.py` / `record_provider_view.py` | 真实界面及同单补录，保留独立时间原点 |
| `artifacts.py` / `summarize_delegation.py` | 共用证据判定；从实际任务和本地报告生成脱敏摘要 |
| `edit_cases.py` / `assemble.py` | 按实测配音裁切、标识等待剪辑与讲解叠层、合成 |
| `check_recording.py` / `review_cases.py` | 业务准入、最终编码及逐镜头声画检查 |
| `publish_cases.py` | 仅发布已核对成片、配音、抽帧和脱敏摘要 |

完整交互片须加入本轮真实Outlook接受及日历画面，剪辑清单记录独立source与时间原点。证据`outlook.status=accepted_and_visible`只能在实际界面核验后填写；不得以邀请受理、旧邀请或模型文字代替。缺此镜头时只能传`--preview`：省略未发生的接受入历旁白，持续显示待补录标识；正式编辑会拒绝缺证输入。浏览器URL/权限受限时停止该浏览器操作，不复制认证状态绕过。

`record_case_tail.py`仅供同一已完成合同的工具与收尾补录：先核签7、已付和摘要，不重造合同结果；独立source与原因写入证据。失败拍摄保留。

## 联调与边界

原`local_stack.py`、`verify_local.py`、`record_local.py`保留给回环SMTP、测试日历和样例服务联调，测试片持续标识，不作正式片。真实服务、消息、邮件与视频分别验收。

选用[AgentMail原生会议请求](../personal-assistant/README.md)时，`notification-check`只验证传输，不发邮件。Outlook由收件人接受后入历；本地安排不代表已读取私人日历。Graph和Outlook SMTP OAuth2仍是可选后端，本轮不要求重新配置Entra。

仅公开必要的演示画面、成片、配音与脱敏摘要；run.json、SQLite、私钥、模型/邮件配置、浏览器状态与私人消息不入库。失败、初剪、预览和正式片分名。跨仓经验见A2C主仓`demo-video/references/live-platform.md`。
