---
name: ioalab-delegate
description: 用用户本机的IoA SDK在九问IoA仿真平台发现服务、签约、取件和本地验收；需要外部专业服务时使用。
version: 0.0.2
---

# IoA 合同委托

配置平台地址、用户名、密码后，调用：

`python @skills/connector-ioalab-client/scripts/ioalab.py <命令> --args '<JSON>'`

首次调用会自动准备安装包内锁定的 SDK 与 A2C。所有合同操作在用户侧完成；平台账号登录不创建助手运行实例。模型配置沿用自己的九问，SDK的script验收不使用模型。

| 顺序 | 命令与主要参数 |
|---|---|
| 1 | `me`；`search {query,top_k}`：发现服务 |
| 2 | `templates {provider}`：获取完整已验签模板。依次读交付件、材料要求、报价、验收与期限、样件；剔除后不读后续节 |
| 3 | `draft {template_digest,spec,budget,materials?}`；材料 `{name:{path,declared}}` 是本地文件；提案型用 `request` |
| 4 | 向用户说明服务方、成果、定数金额、期数与判据，得到同意后 `sign {cd}`；提案型 `adopt {cd}`；再 `start {cd}` |
| 5 | `watch {cd,timeout:60}` 在本地等待存证或环节请求；也可用 `poll {cd}` 单次查询。九问退出后下次从同一本地数据恢复 |
| 6 | 环节请求须原样告知用户；按用户答复执行 `answer-step {cd,granted}` |
| 7 | 已存证后 `fetch {cd,period}` → `verify {cd,period}`，由本地 A2C WASM 沙箱验收 |
| 8 | 将结论告诉用户；获授权后 `accept {cd,period}`。pending 到 notBefore 后重查；失败不签7，问用户是否 `arbitrate` |
| 9 | 用户要停止则 `terminate {cd}`；已签7但未付且明确付款失败，确认后 `pay {cd,period}`；付款结果不明时不盲目重付 |

`sign/request/adopt/start/accept/pay/answer-step/terminate/arbitrate` 必须先取得用户确认，再加 `--confirmed`。不能把该参数当成自动同意；SDK成功返回合同摘要、回执或凭证前，不宣称操作完成。`verify`结果不能用聊天判断替代；付款按合同金额，不另议价。预算超过授权时重新询问用户，不直接加大budget。

保留本地身份目录以恢复合同；不能清空密钥后用同一账号自动换身份。断线后继续poll；观察上报失败会本地重试，不等于协议动作失败。
