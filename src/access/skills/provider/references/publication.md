# 发布服务

触发：本机用户希望公开提供专业服务。先完成实际业务配置，不把合同模板当生产程序。先读取[合同模板与验收器](contracts.md)，不要让用户补写A2C协议字段。

| 步骤 | operation 与参数 | 检查 |
|---|---|---|
| 接单意图 | 用户要求先不接单时，先调用provider.pause：profile | 核policyReceipt和currentPolicy.accepting=false；可在公示前暂停，不只在聊天中答应 |
| 起草模板 | provider.template.draft：profile、terms | terms使用本页引用的原A2C业务字段；保存真实data.template，不猜版本、有效期或支付地址 |
| 查看身份 | identity.status | 使用已配置 service profile；默认不预选分类，省略categoryId交由注册中心自动匹配 |
| 发布验收器 | provider.verifier.publish：profile、verifierId、source，可选 reads、kind | source 是明确内容检查，不能恒真；平台打包并返回实际 id/uri/digest，不在宿主执行源码 |
| 准备公示 | service对象：profile、name、description、tags、templates，可选categoryId | templates 是原 A2C 未签模板草稿，不自行填 provider、templateDigest、signatures；默认提案或待批准，金额为定数，验收器引用实际已发布包 |
| 预览 | provider.preview：service | 省略分类时先取得accepted和job引用，沿原operationId查询provider.preview直到完成，不重建操作。保存完成后返回的完整service、category、内容摘要和expectedRevision。category.status为choose_category时先请用户选择实际分类，再创建新的预览操作；没有可发布摘要时不进入publish。依错误修正草稿，不能删验收条款掩盖问题 |
| 申请确认 | provider.publish：预览返回的完整service、contentDigest、expectedRevision，写入独立参数文件 | 先执行这一次调用；返回waiting及confirmationRef时尚未签名或公示，不能等聊天同意后才申请 |
| 用户决定与恢复 | waiting后用{}调用runtime.open_ui；实际批准后重发上一步同一参数文件 | 页面应显示当前服务与实际分类。opened不代表批准；不要在聊天中要求“确认发布”替代页面操作。恢复原请求，不改operationId、内容或价格，核发布回执与当前修订；拒绝/过期停止 |
| 核对就绪 | provider.status：profile；runtime.status | 公示、分类、连接、模型/工具能力、容量分别核验；未就绪先报告实际缺项 |

默认省略categoryId，由注册中心匹配实际叶类；只有用户明确指定分类，或预览返回choose_category时，才调用provider.categories查询实际树并让用户选择，不由助手提前代选。向用户展示返回的真实路径和依据，再确认发布。模型未配置或匹配不明时请用户手选，不编造或回落到无关类别。发布消费预览返回的完整service，不重新分类；同一预览操作重传恢复原结果，改说明或手选分类使用新操作。模板和验收规则来自用户业务，不从演示场景复制固定价格、固定服务选择或候选数量。

## 参数归属

预览参数外层只有service；service只含profile、name、description、tags、templates及可选categoryId。不添加resources、acceptance或容量字段。下面用Python变量示意构造，draft是SDK起草的真实回执，其他值来自当前用户业务，完成后写入JSON参数文件：

```python
service = {
    "profile": profile, "name": name, "description": description,
    "tags": tags, "templates": [draft["data"]["template"]]
}
preview_args = {"service": service}
# 真实preview完成后，从其data提取；不要把整个预览/operationId复制进去。
publication_args = {key: preview_result["data"][key]
                    for key in ("service", "contentDigest", "expectedRevision")}
```

需要后续复用的起草回执可将脚本stdout保存为结果JSON，按字段提取原件，不重新拼写。待批准acceptance在原模板中，提案acceptance由实际本单quote给出；本地业务资源属于外置配置provider.services.<profile>.resources，不属于公开service。

接单配置存于本机共享配置文件。需要改变当前配置时安全停止/重启，并核平台及本机实际状态；不要调用没有配置的操作并宣称成功。已承接合同继续使用原冻结模板、权限与会话。
