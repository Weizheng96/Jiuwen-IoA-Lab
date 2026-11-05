# 实例配置与组件验证

为 Windows 本机或 Linux 服务器配置模型、核模型就绪与实际调用证据时阅读。命令只在[部署教程](../../../../设计/工程/部署.md)维护；运行权威仍依平台配置、SDK与在线试用职责页，本参考不定义第二套配置。

## 操作者初始化与运行边界

1. 使用已授权的统一私有源 `.agents/llm_apikey`，选明确 profile；本仓当前已准备的 profile 名为 `default`。只在操作者初始化进程内读取 `tools.llm.profile()`，不打印对象、key 或提供方错误正文，不把值放参数、全局环境或 Git。保留原空间 API 入口，不能换成猜测的公共默认地址。
2. 新实例使用选定发布源码实际提供的 `deploy.configure.initialize(..., llm=...)` 或 `python -m tools.llm`，只创建唯一平台 JSON。Windows 指定实际已构建资源路径时使用 initialize 的 assets 参数；现有 tools.llm CLI 不提供 assets 参数，不虚构该选项。旧发布没有 llm 参数时，先用其 deploy.configure 创建新私有文件，再由操作者显式映射同一 profile 的 api_key/base_url/name 到该文件 llm.api_key/base_url/model。已经存在的文件不重复初始化，变更沿原部署 owner 的停机/配置或显式迁移流程。
3. Linux 原远程操作使用已核主机指纹的 pin SSH/SFTP 会话，私有载体先受 root 所有及0600权限保护，只传所选模型输入，不复制完整开发凭据库或把秘密放命令文本。没有已核会话或安全载体时，部署 owner 先向主控给最小方案，不请求用户贴 key。初始化后运行服务只读取自己的实例配置，不跨读开发源或临时传输载体。
4. 配置中的主模型、检索、嵌入和仲裁等实际用途分别核供应商权限与类型；单一主模型字段非空不能证明所有用途可调用。已配置实例及人工修改保留，换 profile 不自动覆盖运行文件或重建参与方工作区。

## 三类调用路径与证明

| 对象 | 配置与实际通路 | 最小组件证明与边界 |
|---|---|---|
| 平台 | 唯一 platform.json 的 llm 段经核心设置进入所属模型调用；例如注册中心 Discovery 的真实 A2X 树导航使用检索模型，向量退路另涉及嵌入模型 | 由部署 owner 选择已存在且无业务写效果的组件入口，记录实际走的模型模式、请求结果和候选/配置修订；树、字符或缓存退路不能算 LLM 成功。没有安全入口先报最小探针方案，不加临时 debug API |
| 官方预置服务 | 部署配置投影模型，经公开 Workspace.prepare/upsert_model_resource 准备独立官方 Swarm；官方配置 host 保存其 command/data_dir/model selection，SDK经九问适配调用所属原版 Process 执行 | 经该已装配 Host 的公开 Process 做低量无业务问答，并核同工作区/选择键/宿主来源。只证明共享模型宿主通路；不签合同、授权预算、付款或宣称所有领域生产质量通过 |
| 试用个人助手 | 普通本机个人助手使用其所属九问模型资源与产品Skill/SDK Host；Linux在线试用由 epoch bootstrap 配置本轮个人 Swarm 与SDK，调用只经本轮模型sidecar | 核本人的公开原生/Process调用、选中模型及所属SDK/Host关联。管理员助手的模型就绪不代替个人助手；原生UI/A2UI/可信确认与Skill完整业务另验，不能伪造真人批准 |

model.list、hostAvailable、executionReady、HTTP200 和 `/api/config` 的 llm 标记只证明相应配置或探针。`tools.llm.complete()` 直调只证明供应商连接，不能充当以上组件通路。每类保留脱敏调用回执、实际运行候选、结果/失败与未验证范围；无安全入口、实例正在停止或归属未知时保持待验，不发新模型任务制造成功。

## Linux 三席模型归属

在线试用是独立应用，按[试用部署](../../../../deploy/trial/README.md)操作。操作者从所选 profile 准备受保护的 proxy_secret_file 与 provider_base_url；供应商 key 只在每轮独立 sidecar 的只读 secret 挂载。席位个人助手及其核心/SDK使用本轮内网代理地址和 epoch token，不取得供应商 key，也不读取正式平台或开发源。三席及跨轮的 token、工作区和撤权边界保持原设计；主平台模型配置成功不等于三席已配置或可用。现有 tools.llm 初始化仅处理平台 JSON，不声称自动创建三席 secret、席位或原生验收记录。

## 当前证据边界

开发工具已用假凭据验证导入、权限、初始化拒覆盖、API/TTS错误脱敏，未以真实提供方调用替代组件证明。部署 owner 的原运行候选、配置迁移及模型执行各以其本轮回执为准：已有Windows配置/宿主目录证据，Linux原候选模型目录证据；尚不能据此宣称平台、官方服务和试用个人助手三类模型全部执行通过。结果只更新既有实施资料摘要，不把本参考变成逐轮部署报告。
