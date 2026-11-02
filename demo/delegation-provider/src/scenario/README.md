# 原生美甲业务场景

本目录提供当前服务方角色使用的美甲业务 Skill、可执行验收说明及未签模板输入，不包含影片或已完成交易。
固定业务定义暂复用 `official-services/scenario.py` 的唯一现行来源；验收源码复用官方 `nail_contract.py`，
生成文档不维护第二份源码，旧生成器和样件不进入新业务包。

在仓库根使用项目 Python 构建到新目录：

```powershell
.venv/Scripts/python.exe demo/delegation-provider/src/scenario/build.py --out temp/nail-scene-build --profile miniapp
```

输出 `nail-miniapp.zip`、`template-draft-input.json`、`execution-fields.json` 和摘要报告。
ZIP 包含普通业务技能，须通过九问原生技能界面导入；接入技能与业务技能分别配置。
取得原生实际安装目录后，经公开 `runtime.configuration/configure` 设置已有 profile 的 instructions、skills、
明确授权的 write_file 及 host.skill_directories；execution-fields.json 只是支持字段素材，不是可直接提交的完整配置请求。
不读取账号配置正文，不修改 resources，不更换身份、data_dir 或原业务状态。

模板输入用于 `provider.template.draft`。生成的是正式场景的 booking 标量规格、两份必要材料、1 虚拟币拟卡费和
实名注册演练环节；与当前泛化模板的差异须在真实发布预览中说明并取得用户确认。
随后按 `provider.preview`、`provider.publish` 和真实本机确认发布新模板；保留旧模板及其提案，不改写已签内容。

本单 quote 通过受限读取工具实际读取生成的 acceptance.md，再用 `provider.task.verifier.publish` 取得真实引用。
报价必须引用该实际回执；不能把包加载、关键词检查或文件存在解释为十项行为通过。
生产后通过本单 selftest/deliver 使用同一新成果；完整原生授权、验收付款与信誉证据仍须实际运行。

只构建不执行模型、发布、收费、安装或账号配置。检查采用轻量与定向范围；整阶段交付另行执行完整检查。
