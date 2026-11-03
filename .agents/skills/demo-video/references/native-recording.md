# 录制与剪辑

安排原生镜头、全景或同单补录时阅读。实际参数与运行方式以 [demo/README.md](../../../../demo/README.md)及当前脚本为准。

## 选择真实入口

| 素材 | 现有入口与适用范围 |
|---|---|
| 概念全景与实际监控 | `demo/global_story.py`、`capture_global.py`、`record_global.py`；动画与监控分别标识 |
| 新批准稿与旁白计划 | `demo/review_story.py`、`script_documents.py --drafts`、`approved_voice.py`；不生成业务事实 |
| 既有场景录制与接续 | `demo/record_cases.py`、`record_case_tail.py`、`record_provider_view.py`；先读其前置检查、调用与证据格式 |
| 既有场景剪辑与发布 | `demo/edit_cases.py`、`check_recording.py`、`review_cases.py`、`publish_cases.py`；仅在当前场景及证据契约匹配时复用 |
| 新批准稿原生剪辑 | `demo/edit_cases.py --scene delegation-provider\|delegation-assistant\|messaging`；指定新 `--recording` 目录和对应 `--voice-timing` 文件，缺镜头或证据时用 `--review-candidate` |
| 当前所有者的原生页面原帧 | [capture_frames.mjs](../scripts/capture_frames.mjs)；注入已绑定 Tab/CDP 与文件系统，分次 await 采集，不启动浏览器或操作页面 |

旧编排脚本不直接等同于新版原生教程。核对它是否启动业务、创建账号、触发费用或依赖旧接口；若与现行设计或批准稿不符，先由所属开发任务修正，不能为出片恢复已替代的参数或绕过正常接入。

实际九问优先复用用户指定的原生页面与会话；按需使用 jiuwen-swarm 技能定位公开接口。开始前核录制器、输出目录和任务所有者，不另开同一会话制造重复订阅或抢占路由。操作在原浏览器顺序提交，提交前等本轮输出及真实问题稳定，答案填写后回读并核新回执。

每条关键业务发起前，核对当前待发送输入中的真实 Skill 引用 chip。上一条设置消息或历史消息曾选择 Skill，不代表本条仍带引用；正文中的技能名也不能代替实际选择。缺少当前入口引用的失败，不能据此判定已选 Skill 的路由实现有缺陷。

原生编辑器已有 chip 时，用当前 CUA 文档支持的追加 type 接口输入正文，避免整块 fill 擦除引用；必要时先写正文，再通过 slash 菜单选择 Skill。发送前再次核对当前输入的 chip 仍在。

## 当前所有者的有界原帧采集

先读本次 CUA 返回的 CDP 文档。脚本只接收当前会话已经绑定的 `tab`；不同智能体的浏览器数字 ID 可能不同，需核 `extensionInstanceId` 才能判断是否同实例。真正的标签页 session 归属拒绝由所属会话采集，不换浏览器或绕过。CDP 没有可靠的活动 screencast 查询：调用者先确认当前页无其他或未知活动流，才传 `knownIdle:true`；旧事件不是空闲证明。脚本拒绝同 Tab 重复启动，不会替调用者停止未知流。

在 CUA JavaScript 会话中导入已读的本仓模块及 Nodefs，复用已有 `tab`。下例使用新的忽略目录，不指向任何旧原片；先准备父目录：

```javascript
var nativeCapture = await import('file:///D:/project/Jiuwen-IoA-Lab/.agents/skills/demo-video/scripts/capture_frames.mjs');
var captureFs = await import('node:fs/promises');
await captureFs.mkdir('D:/project/Jiuwen-IoA-Lab/recordings/native-new-take', {recursive:true});
var roleCapture = await nativeCapture.startCapture({
  tab, fs:captureFs,
  folder:'D:/project/Jiuwen-IoA-Lab/recordings/native-new-take/role-a',
  knownIdle:true
});
nodeRepl.write(nativeCapture.captureSummary(roleCapture));
```

下一次工具调用完整 await 一个窗口，再返回模型处理业务操作；每个窗口至多 25000ms，不创建跨工具返回仍运行的 Promise。需要继续时仍传同一显式 handle；多角色各自持有不同 handle，不能复用闭包变量：

```javascript
nodeRepl.write(await nativeCapture.captureWindow(roleCapture, 20000));
```

只有返回的 `firstFrame` 非空才表示真实首帧已保存，其中像素来自 JPEG 本体；Visibility 事件、start 成功或零帧窗口都不是就绪证据。默认最多 15 分钟／5000 帧，到限自动结束自己的流。首次 start 未获得成功回执时状态为 `start_unknown`，不能盲目 stop；交回当前所有者核实。

收到停止要求、完成或发生错误后，显式停止此 handle。停止后的 handle 不能再次追加窗口；只清理本模块确已成功 start 的流：

```javascript
nodeRepl.write(await nativeCapture.stopCapture(roleCapture));
```

每帧 JPEG 原字节、实际 `metadata.timestamp` 与事件 `sequence` 保存到 `frames.jsonl`，逐帧 ack；`capture.json` 保存有界窗口、cursor、失败及 truncated 指标。未知结果和局部文件失败保留证据，不输出 base64、页面消息或任意异常正文。目录必须全新；后补静态画面单列来源，不补入旧时间轴。

采集与 ffmpeg 离线编码分工。编码依据原时间戳保留 VFR 和间隙，实测 JPEG 尺寸，必要时只补到偶数，不把 CSS `deviceWidth/deviceHeight` 当成原帧像素。完整保存现有帧不等于连续完整录屏，未记录的 truncated 也不等于零。

维护此模块时运行成功与失败边界，不启动真实浏览器：

```powershell
node --test .agents/skills/demo-video/scripts/capture_frames.test.mjs
```

## 布局先试拍静态画面

用户要求同一桌面整屏录制完整使用流程时，先将真实页面在指定浏览器中同时摆好，核对账号隔离及窗口角色，再试录并检查实际像素。正式录制使用连续屏幕源，保留操作、确认及真实等待，后期统一加速和剪辑；不要将逐页采帧、事后补录或拼接画面冒称这次完整整屏实录。

由用户操作时，先交付每个窗口的准确链接、登录身份和排列位置；账号使用浏览器正常支持的独立配置或隐私窗口隔离，不能让同一 Cookie 会话冒充两个用户。页面就位和试录通过后再提示开始，一次只给当前步骤；独立后台核验不反复打断录制，业务失败则保留当轮结果并先排错。

按批准稿选左右分屏或四宫格：角色对话、本人工作台、相关服务/收件方、平台过程各有明确归属。角标说明当前角色与演示环境，放大前后保持观众能够定位；不把未登录的示意面板冒充真人工作台。

网络区保持可见，详情区独立滚动；展开合同、工具 Schema、回执时核页面真实请求及选中对象，轮询后保持展开和聚焦。先在最终分辨率抽帧确认文字、字幕和遮挡范围，避免缩到四宫格后仍依赖细小终端字。普通账号只读取获准数据；需要背景参与方时明确匿名示意，不先拉全局私人数据再遮挡。隐藏凭据和无关联系人资料；遮挡不能盖掉支撑结论的状态与必要标识。

监控拓扑沿用实际页面及共享布局，不另画一套成功流。历史事件刷新只恢复状态，不当新传输重播；真实传输线须结束，不留常驻连线或伪造循环。拍混合业务时核实际窗口内的两类事件及来源，不能用单类流量剪成并发结论。

概念画面过渡到真实监控时核节点几何、第一帧和加载时点，使用同一布局来源，避免淡化中跳位、空白或重复出现。概念动画可用共用逐帧工具；实际业务等待遵守真实时钟，不能用假时钟催成约或付款。

## 时间轴与补录

原片、接续和尾段各保留独立文件与时间原点；每段写清 `source`、原始起点、时长及关联证据。新版在 `segments.json` 按批准镜头 ID 指定 `layout` 和 `panels`，各面板绑定真实源文件 SHA-256、`start`、`duration`、`role` 与标签。双格按 `native/platform`、四格按 `wang-platform/zhao-platform/wang-native/zhao-native` 归位，先保比例合成为 1920×1080，再叠字幕与旁白。编辑决策表把原片区间映射到成片，跨源不能直接比较绝对时间戳。现有 [artifacts.py](../../../../demo/artifacts.py) 的 `waiting_was_cut` 仅比较同源区间，应保留这个边界。

可压缩真实等待并标记“等待片段已剪短”，保留操作、请求和实际结果的先后。不能通过换源删除关键追问、拒绝或失败，将后来恢复剪成首次自主成功。事后只读回看设 `captureKind: later-review`；原片不够长时可用 `holdLastFrame` 和 `holdReason` 明确标注定格讲解，也可延长可用实录区间或重排剪辑，不补造未录操作。

`--review-candidate` 只生成带待补标识的审阅候选，缺失镜头和证据仍须补齐，不能发布或标为最终 QA 通过。批准稿、音频和源文件摘要不符始终失败，不能降级为候选继续出片。

补录前先核原合同、已完成效果、授权及期限；只补缺失画面，明确原因。若已有交付需展示本地工具，输入和文件摘要须对应原交付，并区分工具本地演示与外部产品上线。验收器与交付代码执行遵守根 AGENTS 和当前设计，录制工作不另开宿主执行通道。

原片、失败 take、已发布旧片及其摘要保留；新片使用独立输出和明确状态，检查完成后才通过既有发布入口更新正式链接。
