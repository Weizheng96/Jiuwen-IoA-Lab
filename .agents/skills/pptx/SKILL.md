---
name: pptx
description: 创建、编辑和分析 PowerPoint/PPTX；支持领导汇报、现有模板、原生图表与讲稿，并执行结构校验和逐页视觉检查。
license: Apache-2.0，见 LICENSE.txt
---
# PowerPoint 制作

采用 Anthropic 官方 pptx 技能，完整指南与脚本保留于本目录。

1. 确认受众、核心结论、页数和交付路径；领导汇报先给投资或决策主张，再给业务依据。
2. 按任务读取 [上游指南](references/upstream.md)：新建用 PptxGenJS，编辑先读模板与 XML 流程。指南中的脚本路径均相对此技能根目录。
3. 保留原生文字、表格、图表和流程图的可编辑性；事实及图片来源写入对应页讲稿备注。
4. 依环境已安装依赖生成文件；Windows 可用 PowerPoint COM 导出 PNG/PDF，Linux 按指南用 LibreOffice 与 Poppler。
5. 使用 `python scripts/office/validate.py <文件.pptx>` 检查结构，提取文字核对内容，并逐页看图修正溢出、遮挡、对齐和可读性。
6. 最终成品保存到用户指定目录；一次性源码与验证日志放项目 temp，复用数据遵循项目约定。

来源与本地适配见 [来源记录](references/source.md)。新增技能下轮对话可自动发现，本轮直接读取使用。
