---
name: llm
description: 为开发和验收工具管理统一私有模型凭据来源及显式实例初始化；不改变运行组件的配置权威。
---
# 开发模型输入

开发与验收工具统一读取 Git 忽略的 `.agents/llm_apikey`，调用与安全初始化复用 `tools/llm.py`。凭据内容不进入 Git、日志、命令参数或全局环境；不得输出私有源正文。

统一源保留由 `.agents/newllmkey.txt`（字段 `key`、`url`）生成的 `default` 和原阿里云 `aliyun-tts` profile。profile 名是索引，用途以用户最新明确选择为准；用户已指定新Linux平台与官方服务使用原阿里云，初始化时显式选 `aliyun-tts`，不因名称含 tts 沿用旧的“仅配音”限制。保留该 profile 的原空间 API 地址，不覆盖 `default` 或其他实例的选择。

私有源使用 `profiles` 对象，以操作者选择的名字索引模型对象；字段复用 deploy.configuration.model 的 `name`、`base_url`、`api_key` 与可选 `provider`。开发调用使用 `tools.llm.profile()` 或 `complete()`，不在每个工具重新解析秘密源。`python -m tools.llm --profile <名字> --root <实例根> --state <状态目录> --config-dir <配置目录>` 仅经既有 deploy.configure 显式初始化；name 在写入唯一平台 JSON 时转换为 llm.model，不生成旁路运行配置。

运行实例经显式初始化取得属于自身的单 JSON，之后只读自己的配置。核心配置仍由[平台配置规范](../../../设计/组件/平台/平台基础/接口/平台配置.md)定义，参与方由各自运行时读取；运行组件不跨读开发源，不以环境逐字段覆盖形成第二权威。

为 Windows、Linux、官方预置服务或试用个人助手准备模型时，读[实例配置与组件验证](references/instance-configuration.md)；实际命令统一见[部署教程](../../../设计/工程/部署.md)。先核选定发布入口是否支持初始化参数；已有实例沿原配置恢复或显式迁移，不重放生成器。操作者从已授权私有源取值，不要求用户在聊天中粘贴 key。

部署前先在实际目标主机验证主模型与嵌入 HTTP 调用，再写入实例；本机可用、DNS/TCP/TLS通都不替代目标主机成功。失败按上述参考的“目标主机诊断”分层处理，不猜换 API 地址、自动切 key 或靠增加超时掩盖问题。

迁移旧源前列出所有消费者，确认新路径已 ignore 且发布 guard 能拒绝无扩展秘密路径，先安全初始化并核验消费者，再移除已无引用旧源。并发 Windows/服务器部署使用中的私有输入不得移动或删除。初始化工具拒绝覆盖原文件，失败不输出服务商错误或密钥。


首次迁移阿里 CSV 使用 `python -m tools.import_model_source --csv <已授权CSV> --profile <名字> --model <模型>`，仅一次显式导入；严格核重复字段、HTTPS 原空间入口和目标不存在，先限制文件 ACL 再写秘密，失败清除本次空目标并保留原源。原源与在途输入不自动删除。导入工具不是运行组件的旧格式回退。

语音工具只使用 `tools.llm.Aliyun` 和源内 `tts` 配置（引用 profile，不复制凭据）；官方 SDK 的 key 仅设在客户端实例，不修改全局 API key 或环境。TTS 的响应、错误、提前关闭及空音频均有确定性替身测试；真实服务连通和听感仍属授权后 D 专项，不为源码整理调用付费服务。`default` 的供应商没有阿里云实时语音入口，配音按用户决定继续使用 `aliyun-tts` profile 中的原阿里云密钥。
