# 底层合同原语（通用接入与诊断）

美甲任务使用主技能的delegate阶段入口，不使用本表替换阶段编排。

| 顺序 | 命令与主要参数 |
|---|---|
| 0 | `authorize {purpose,budget,proposal_fee_limit,valid_days:7}`，用户确认后加`--confirmed`；恢复用`scope {scope_id}`；`inspect {cd}`读取真实合同与模板；`reputation {provider}`读取并验签市场信誉 |
| 1 | `me`；`search {query,top_k}`：发现服务 |
| 2 | `templates {provider}`：获取完整已验签模板。依次读交付件、材料要求、报价、验收与期限、样件；剔除后不读后续节 |
| 2a | `compare {providers,selected,reason,cd?}`：重新读取2—8项真实候选的完整验签模板，记录筛选理由，标reported；cd存在时中选方必须与合同一致。不能用过程上报代替签名或付款证据 |
| 3 | `draft {template_digest,spec,budget,materials?}`；材料 `{name:{path,declared}}` 是本地文件；提案型用 `request {template_digest,spec,budget,scope_id}` |
| 4 | 向用户说明服务方、成果、定数金额、期数与判据，得到同意后 `sign {cd}`；提案型 `adopt {cd}`；再 `start {cd}` |
| 5 | `watch {cd,timeout:60}` 在本地等待存证或环节请求；也可用 `poll {cd}` 单次查询。九问退出后下次从同一本地数据恢复 |
| 6 | 环节请求须原样告知用户；按用户答复执行 `answer-step {cd,granted}` |
| 7 | 已存证后 `fetch {cd,period}` → `verify {cd,period}`，由本地 A2C WASM 沙箱验收 |
| 8 | 将结论告诉用户；获授权后 `accept {cd,period}`。pending 到 notBefore 后重查；失败不签7，问用户是否 `arbitrate` |
| 9 | 用户要停止则 `terminate {cd}`；已签7但未付且明确付款失败，确认后 `pay {cd,period}`；付款结果不明时不盲目重付 |

`authorize/sign/request/adopt/start/accept/pay/answer-step/terminate/arbitrate` 必须先取得用户确认，再加 `--confirmed`。不能把该参数当成自动同意；SDK成功返回合同摘要、回执或凭证前，不宣称操作完成。`verify`结果不能用聊天判断替代；付款按合同金额，不另议价。预算超过授权时重新询问用户，不直接加大budget。

用户已在本轮明确同意的对象、用途和金额范围内，直接将该授权通过`--confirmed`传给相应命令，不重复询问。若漏加该参数导致“User confirmation is required”，先检查已有授权；已有明确授权时补参数重试，新增对象/用途或超额仍需询问。提案型服务须实际执行`request --confirmed`取得返回的cardDigest与derived，不能把模板参考价或自然语言推荐说成已获得签名提案。用户要求暂不签约或暂不接受时，严格停在对应边界。

`spec`按模板deliverables的name分组，例如模板交付件name为`table`，字段为medicines和days时，参数为`{"template_digest":"真实摘要","spec":{"table":{"medicines":["维生素C"],"days":7}},"budget":100}`。字段、值域和交付件name均以真实模板为准；不要把数组直接当作交付件对象。单交付件也保留这一层名称。

保留本地身份目录以恢复合同；不能清空密钥后用同一账号自动换身份。断线后继续poll；观察上报失败会本地重试，不等于协议动作失败。

