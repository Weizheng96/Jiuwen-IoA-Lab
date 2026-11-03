# 原生美甲业务场景

本目录提供当前服务方角色使用的美甲业务 Skill、可执行验收说明及未签模板输入，不包含影片或已完成交易。
七家定义由 `scene.py` 选择并装配：复用 `official-services/scenario.py` 的现有业务资产，
三家提案使用原 `nail_contract.py` 或 `nail_skill.py`，不复制维护源码、旧生成器或样件。
四家负例采用普通服务方已支持的待批准模式；展示页及 Android 范围使用本目录的限范围验收程序，
不继承与其公示矛盾的取消／微信能力。旧资源和已签内容保留。

在仓库根使用项目 Python 构建到新目录：

```powershell
.venv/Scripts/python.exe demo/delegation-provider/src/scenario/build.py --out temp/nail-scene-build --profile miniapp --service-id nail-mashangfei
```

`--service-id` 明确选择服务，`--profile` 为真实本地 profile。默认仍为码上飞／miniapp。
三家合格候选分别生成 `nail-lingxi.zip`、`nail-kuaida.zip`、`nail-miniapp.zip`，每包有独立业务主文档、
真实生产页与实际验收依据；所有业务文档从本目录同一来源渲染，不复制维护。
另输出 `publication-fields.json`、`execution-fields.json`、精确原源码的 `verifier-publish-input.json` 和摘要报告。
提案直接生成 `template-draft-input.json`。
ZIP 包含普通业务技能，须通过九问原生技能界面导入；接入技能与业务技能分别配置。
取得原生实际安装目录后，经公开 `runtime.configuration/configure` 设置已有 profile 的 instructions、skills、
明确授权的 write_file 及 host.skill_directories；execution-fields.json 只是支持字段素材，不是可直接提交的完整配置请求。
不读取账号配置正文，不修改 resources，不更换身份、data_dir 或原业务状态。

模板输入用于 `provider.template.draft`。七家均保留 booking 标量规格和两份必要材料；云构另需签前审核的
`architecture`，不能编造。灵犀／快搭／码上飞报价分别为 239／259／299，拟卡费各 1；
其他四家固定价分别为 180／199／219／1280，不收提案拟卡费。展示页没有取消，Android 不交付微信端。
实名注册演练仅在微信小程序业务声明，不进入展示页或 Android 合同。

待批准模板必须先取得真实验收包引用。未提供引用时只生成业务 ZIP、`verifier-publish-input.json` 和明确
`templateStatus=verifier_reference_required` 的报告，不生成不完整模板。所有者正常调用 `provider.verifier.publish`，
保存其实际 `{id,uri,digest}` 后，用新输出目录及 `--verifier-reference <真实引用JSON文件>` 再构建完整模板输入。
不能手写包地址或把离线准备称为发布就绪；输入必须属于所选服务的验收器。
与当前公示的差异须在真实发布预览中说明并取得用户确认。
随后按 `provider.preview`、`provider.publish` 和真实本机确认发布新模板；保留旧模板及其提案，不改写已签内容。

提案可先由所有者使用生成的 `verifier-publish-input.json` 正常调用 `provider.verifier.publish`，
保存真实 `{id,uri,digest}`，再用 `--verifier-reference <真实引用JSON文件>` 构建到新目录。
该引用进入业务 acceptance.md 和执行说明，提案模板仍不固定 acceptance。
原生导入新业务包并应用 execution-fields 中的业务说明后，本单 quote 通过受限读取工具读取 acceptance.md，
优先精确复用预发布引用，避免逐单重新转抄相同源码。包不可用或本单判据需变化时停止并说明，不能猜引用。
未绑定引用的包仍可通过 `provider.task.verifier.publish` 准备本单判据，但源码完整性必须重新核实。
报价只使用实际回执；灵犀为三项 skill 模型观察，保留实际 `disputeModels`，
构建报告明确模型可用性尚未核实，需原生目标环境确认。快搭为五项 script、码上飞为十项 script。
不能把包加载、关键词检查或文件存在解释为实际行为通过，不在场景或技能中固定推荐谁。
生产后通过本单 selftest/deliver 使用同一新成果；完整原生授权、验收付款与信誉证据仍须实际运行。

只构建不执行模型、发布、收费、安装或账号配置。检查采用轻量与定向范围；整阶段交付另行执行完整检查。
