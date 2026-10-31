# 合同模板与验收器

触发：用户希望发布自己的服务。用户只需说明业务能力、交付、定价与验收需求；你读取本页并构造参数，不要求用户手写A2C字段。不知道的业务约定应询问用户，不能编造已获授权或技术验收成功。

## 用SDK起草完整模板

调用provider.template.draft，参数为profile和terms。terms直接使用原A2C业务字段，不填写a2cVersion、kind、validUntil、provider、templateDigest、signatures或pricing.paymentProviders；SDK使用当前协议和实际平台上下文补齐，再校验。不要使用type、amount、virtual_coin等自造合同字段。

提案模式的最小结构示例（业务名称、规格、期限、权重及拟卡费均须按用户业务调整）：

```json
{
  "profile": "实际服务profile",
  "terms": {
    "formation": "proposal",
    "deliverables": [{"name": "result", "description": "本服务实际交付件及格式", "specSchema": {}}],
    "inputs": [],
    "deadlines": {"respondWithin": "PT10M", "startWithin": "PT30M", "deliverWithin": {"result": "PT30M"}},
    "pricing": {"currency": "CNY", "depositFormula": "0", "proposalFee": 0, "paymentWeights": {"result": 1}},
    "samples": []
  }
}
```

| 业务情况 | 调整原A2C条款 |
|---|---|
| 提案 | formation=proposal；proposalFee只是拟卡费，不是交付总价。每个真实委托由本单quote提交确定lineItems、acceptance和acceptWithin |
| 待批准 | formation=countersign；pricing增加以交付件名为键的priceFactors与priceFormula，金额为可核定定数；模板必须有acceptance及deadlines.acceptWithin |
| 多交付件 | 每个交付件在deliverables声明，deliverWithin/paymentWeights及适用的其他映射使用对应真实名称；不要只为单一示例设计 |
| 用户业务授权 | authorizableSteps声明环节名和用途，并设置deadlines.authorizeWithin；它不是用户已批准的证据 |
| 用户材料 | inputs使用name、description、required、specSchema等原字段；需签前审查的材料声明reviewBeforeSigning |

specSchema是“规格键→取值域”的映射，不是完整JSON Schema。例：`{"features":{"type":"string","maxLength":4000},"pages":{"type":"integer","minimum":1,"maximum":20}}`；没有规格键时用{}。每个值只用原A2C支持的const、enum、type(string/integer/number)、minimum/maximum、maxLength和format=date，不包type=object/properties/required；字段说明写在交付件或材料description中。

用户所说的通常交付总价只作为本单报价的业务参考，不填proposalFee。拟卡费是另一项事前公示收费，用户未说明时先询问，不能把通常总价改成拟卡费。

保存返回的data.template原件，用于service.templates；不得重新手写其协议版本、有效期和支付地址。收到invalid_input按明确原因修正业务字段后新建操作；未知则恢复原操作。SDK不会替用户确定价格或判据。

## 准备真实验收器

预置复用时，前台用provider.verifier.publish；提案按单确定判据时，后台quote阶段用provider.task.verifier.publish。两者共用打包及校验；前者参数为profile、verifierId、source，可选reads和kind，后者不接受profile、交互UUID或Session，由SDK绑定当前服务和作业。verifierId形如`content_check@1.0`，名称用小写字母/数字/下划线；kind只能使用平台已支持的script或skill，不能写text。source是实际Python源码，不是自然语言验收要求。

L1入口为`def verify(ctx)`，返回`{"status":"pass或fail","checks":[实际检查结果]}`。通过ctx.read("约定文件名")取得交付字节；reads声明要读取的合同spec/inputs字段。程序只在WASM中执行，不能在宿主import/exec交付源码。以下只示意文本非空检查，不能据此宣称源码功能完整：

```python
def verify(ctx):
    try:
        text = ctx.read("result.txt").decode("utf-8")
        ok = bool(text.strip())
    except Exception:
        ok = False
    return {"status": "pass" if ok else "fail", "checks": [{"check": "nonempty-text", "pass": ok}]}
```

业务验收必须覆盖实际合同要求；代码类应在沙箱中调用真实函数检查功能/状态/幂等，不用文件存在或恒真函数冒充。用户确认验收是SDK流程中的另一道门，不是技术验收器的判据。不能自动检查的范围明确列入notCovered。

本单回调的source不超过64KiB UTF-8，每个quote最多准备32个包。相同内容返回原引用，调用ID变化不产生另一业务；unknown时结束本轮，保留原作业及上传记录并报告待核对；SDK内部能核对原上传，但当前尚无未知拟约作业的公开续接入口，不要让用户调用未提供的恢复命令。不得换源码绕过未知结果，决定已保存或进入其他阶段后不再准备判据。

只使用上传回执中真实的{id,uri,digest}。acceptance以交付件名为键，每项为`{"verifiers":{"script":真实引用},"notCovered":[]}`；L2使用skill键并按实际包配置disputeModels。不得要求每个交付同时具备三类验收器或编造包地址。

服务的业务说明应写清实际交付格式、功能判据、验收时长和用户定价规则。可复用的验收引用放resources；提案也可以不预置引用，后台依本单业务先准备验收器，再引用回执报价。不把样件当生产结果。配置写入由用户或本机脚本完成，只修改指定业务字段，不把整份账号设置读入对话。配置改变后按公共启动说明安全停止/重启原实例，再核对真实能力。资源配置完成不代表已公示，仍须预览和用户确认。
