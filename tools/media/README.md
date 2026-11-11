# 媒体制作工具

制作流程依 [demo-video 技能](../../.agents/skills/demo-video/SKILL.md)，产品范围依[演示制作规范](../../设计/工程/演示.md)。最终影片只放 demo；历史公开结论见[实施资料摘要](../../设计/工程/实施资料/部署发行.md#历史验证资料归档)，原始素材、录制与验证报告只放忽略的 .agents/data。

| 工作 | 源码入口 |
|---|---|
| 批准稿与生成 | review_story.py、script_documents.py |
| 旧稿来源 | global_story.py、case_story.py；保留原职责，不推导当前功能 |
| 配音与本地音频处理 | approved_voice.py、build_voice.py |
| 剪辑、画面与声轨检查 | edit_cases.py、assemble.py、check_recording.py、review_cases.py |
| 发布门槛与证据校验 | artifacts.py、publish_cases.py、publish_global.py |
| 全景录制与渲染 | record_global.py、render_global.py、global_web/ |
| 只读同单补录 | record_provider_view.py |
| 场景构造与预约工具 | [服务方场景源](delegation-provider/src/scenario/README.md)、[个人助手预约工具](delegation-assistant/预约工具.md) |

使用项目 Python：

```text
python tools/media/script_documents.py --sources-only
python tools/media/script_documents.py --drafts
python tools/media/script_documents.py --drafts --check
```

sources-only 只验证入库批准源和镜头规则，适用于 fresh clone；生成稿、配音计划与素材默认写 .agents/data/demo。需要私有素材的 --check、录制、TTS、剪辑、原生业务或历史成片核验属于显式专项，不因源码检查通过而报告成功。命令参数以相应工具 --help 为准。

工具复用原交易、会话与授权，未知结果沿原 ID 核回执；不为媒体整理触发模型、邮件或付款。原始记录保留当年路径与字节，迁移清单在本机 .agents/data/maintenance/migration-manifest.json；历史源码从 Git 历史中的 `历史备份/` 追溯，不导入旧执行链恢复兼容入口。
