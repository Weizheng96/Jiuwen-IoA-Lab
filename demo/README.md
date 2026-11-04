# 演示视频与真实录屏

九问IoA实验平台（Jiuwen IoA Lab）：面向智能体互联网的仿真验证与开放体验平台。

## 已完成的三段演示

| 视频 | 范围与证据 |
|---|---|
| [发布服务演示](out/发布服务演示.mp4) | 用户真实注册、接入、发布确认及启用接单；[成片记录](verification/native-provider-onboarding-video.json) |
| [发起委托演示](out/发起委托演示.mp4) | 委托授权、具体合同、实名演练及验收付款；合同款299、三家拟卡费共3；[原单核验](verification/native-assistant-delegation-recording.json)、[成片检查](verification/native-assistant-delegation-video.json) |
| [私人交互演示](out/私人交互演示.mp4) | 两侧九问真实批准、留言、冲突及同一交流改约；片尾说明邮箱调用异常、邀请结果未确认；[播放页](messaging/播放.html)、[修订检查](verification/native-messaging-video-revision.json) |

用户已确认第二阶段完成。上述视频各自证明的范围见原记录；发布服务片没有拍ZIP实际导入、配置填写或后续交易，私人交互片不作为邮箱邀请成功证据。当前产品接入使用两个[产品技能](../src/access/skills/README.md)，普通接入与运行入口见根[README](../README.md)。

原始录屏、账号和模型配置保留在本机忽略目录，不提交为公开证据。现有媒体与历史证据保留当时的事实和来源路径，不因源码迁移改写历史。

## 场景资产

服务方的七家美甲业务定义、Skill、验收说明与构造源由 [delegation-provider/src/scenario](delegation-provider/src/scenario/README.md) 持有。它复用官方包的通用领域与验收资源，不再从旧官方执行器目录读取固定候选。

个人助手侧的安全预约运行器归本场景所有，说明见[预约工具](delegation-assistant/预约工具.md)。运行器只执行仓库固定逻辑，不执行交付件中的服务方源码：

```powershell
python demo/delegation-assistant/src/booking_app.py --config <SDK取件返回的booking.json> --database data/booking-demo.sqlite --port 8768
```

已有[全局视角](全局视角/播放.html)、[美甲委托剧本](美甲委托/剧本.md)、[助手交互剧本](助手交互/剧本.md)及原版成片保留，用于追溯已发布素材。剧本源 `global_story.py`、`case_story.py` 与当前三片审定源 `review_story.py` 分别保留原职责；不从旧剧本推导当前产品功能。

## 媒体制作与检查

当前保留配音、剪辑、发布、反伪造校验和只读同单补录工具。它们消费已有真实录屏与证据，不启动已归档的个人助手、服务执行器或自动付款流程。

```powershell
python demo/script_documents.py --check
python demo/edit_cases.py --recording recordings/<本轮> --voice-timing data/<配音目录>/voice.json
python demo/review_cases.py --recording recordings/<本轮>
```

`approved_voice.py`、`build_voice.py` 处理配音，`assemble.py` 合成媒体，`check_recording.py` 和 `artifacts.py` 核验原片、证据及最终摘要。旧全景专用的[混合流量指标](../历史备份/旧执行链/demo/mixed_activity.py)随旧录制器归档，原采样和结果继续作为历史证据保留。`publish_cases.py`、`publish_global.py` 发布前核对证据与成片摘要。影片重新合成后必须重新取得匹配的新检查结果，不能沿用旧结果。

`record_provider_view.py` 只读核查已结案合同后补录相同运营台，需要原轮真实配置和来源。旧 `summarize_delegation.py` 只处理已退出执行链的私有历史与旧库，已随旧业务脚本归档；它已生成的公开证据保留原字节，不从历史脚本重建当前业务结果。

全景重制可复用入库素材，保持“回放”标记：

```powershell
python demo/record_global.py --source demo/assets/run/global --voice-timing demo/assets/voice/global/voice.json --render-helper <共用录制器路径> --out recordings/global-rebuild
```

`render_global.py` 明确接收共用录制器路径，所需工具须已存在。当前不提供旧流量生成器重采命令；新实验参与方应通过公开SDK另行验证后接入。

## 已退出的录制业务链

旧全景自动业务采集、旧 HTTP 服务、旧收件器和旧技能驱动录制器已移至[历史备份](../历史备份/README.md)的 `旧执行链/demo/`。它们仅供源码追溯，不参与运行、构建、导入或当前验收；不从历史备份导入实现来恢复旧入口。

旧片脚本的源码路径可继续出现在当时的证据JSON和剧本中，含义是“当时采用的来源”，不是当前运行命令。当前工具不得以这些历史来源创建新的成功证据。媒体、业务授权边界、摘要和原回执校验继续保留。

当前证据范围与待验收能力统一见[验证记录](verification/README.md)，产品职责见[架构](../设计/架构.md)，后续工作见[开发计划](../设计/工程/实施资料/开发计划.md)。文档整理和媒体检查不发送邮件、不运行真实委托。
