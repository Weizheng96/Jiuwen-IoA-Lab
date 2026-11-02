# 真实生产

只在 production 阶段执行。已确认的合同、规格、材料和验收包是本单依据，不能修改它们以迁就成果。

先读取本卡声明的 authorizableSteps；需要执行该演练环节时，用 provider.task.request_step 请求实际环节名，
收到 waiting 后结束本轮。只有原绑定请求的真实 granted 回到本单，才继续受保护的动作。
当前场景只演练实名注册授权，不提交身份证件、不申请微信账号、不声称通过微信审核或已经上线。

## 交付格式

用已授权 write_file 在本次 artifactDirectory 内创建新的 booking.json，内容是 UTF-8 JSON 对象：

| 字段 | 来源与格式 |
|---|---|
| format | ioalab.booking |
| shop、open、close、capacity | 与本卡 spec.booking 对应字段逐项相等；后三项为整数 |
| services | 两项，按 service1Name/service1Minutes、service2Name/service2Minutes 的合同顺序组装 name/minutes；不改名称或时长 |
| allowCancel、idempotent | true；还必须有实际可运行行为，不能以标志代替实现 |
| engineSource | 纯 Python 源码字符串，定义下述 BookingEngine；不导入外部模块 |
| miniProgram | 微信小程序源码文本映射，至少包含 app.json、pages/index/index.js、pages/index/index.wxml |

BookingEngine(capacity) 持有真实预约状态。reserve(request, slot) 成功时返回含 id、slot 的字典，
同一 slot 满额时返回 None；list() 返回未取消记录；cancel(id) 可重复调用且不报错。
同一 request 重试必须返回同一预约，不重复占位。不同 slot 独立计数，取消释放名额，重复取消不误减其他名额。

miniProgram.app.json 的 pages 必须指向真实非空 .js、.wxml 文件；页面实现时段选择、预约列表、提交预约与取消。
交付实际源码字符串，不用空函数或说明文字冒充页面功能。微信客户端实际执行、主体认证和上线不在本次自动行为验收内。

## 同一文件自测与交付

使用以下实际文件引用调用 provider.task.selftest：

```json
{"files":{"booking.json":{"artifactPath":"booking.json","mediaType":"application/json"}}}
```

该引用是本次 artifacts 下的相对路径，不读取任意宿主文件。只有合同 WASM 报告明确通过时，
才以同一文件调用 provider.task.deliver。失败先按真实检查项修正源码或配置，再自测；不能换验收器、缩减需求或伪造结果。
源码只由 A2C python.wasm 沙箱执行，不在宿主 Python、Shell 或本机浏览器中执行交付 Python。
取得真实交付回执后结束本轮，等待本期委托方验收及付款，不能替委托方批准。
