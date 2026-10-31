# 发布服务

触发：本机用户希望公开提供专业服务。先完成实际业务配置，不把合同模板当生产程序。

| 步骤 | operation 与参数 | 检查 |
|---|---|---|
| 查看身份和分类 | identity.status；provider.categories（空参数） | 使用已配置 service profile；分类以平台实际树为准 |
| 发布验收器 | provider.verifier.publish：profile、verifierId、source，可选 reads、kind | source 是明确内容检查，不能恒真；平台打包并返回实际 id/uri/digest，不在宿主执行源码 |
| 准备公示 | service 对象：profile、name、description、tags、templates、categoryId | templates 是原 A2C 未签模板草稿，不自行填 provider、templateDigest、signatures；默认提案或待批准，金额为定数，验收器引用实际已发布包 |
| 预览 | provider.preview：service | 返回实际内容摘要、expectedRevision 和合同预览。依错误修正草稿，不能删验收条款掩盖问题 |
| 用户确认与发布 | provider.publish：同一 service、contentDigest、expectedRevision | waiting 时用户在本机受控页确认，后重发原操作。恢复原请求，不重新签名或改价，分别核发布回执与当前修订 |
| 核对就绪 | provider.status：profile；runtime.status | 公示、分类、连接、模型/工具能力、容量分别核验；未就绪先报告实际缺项 |

按业务说明从平台实际分类树选择匹配的 categoryId，不能编造或回落到无关类别；不能可靠判断时给用户候选。当前公共入口要求分类，自动匹配入口未配置时不得宣称已自动分类。模板和验收规则来自用户业务，不从演示场景复制固定价格、固定服务选择或候选数量。

接单配置存于本机共享配置文件。需要改变当前配置时安全停止/重启，并核平台及本机实际状态；不要调用没有配置的操作并宣称成功。已承接合同继续使用原冻结模板、权限与会话。
