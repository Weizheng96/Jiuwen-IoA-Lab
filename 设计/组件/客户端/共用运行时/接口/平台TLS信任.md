# 共用运行时 · 平台TLS信任

本页归[共用运行时](../接口.md#接口)，定义自有平台内部CA的客户端信任。部署端证书生命周期归[内部CA与域名HTTPS](../../../部署/接口/内部CA与域名HTTPS.md)。本方案为设计，实施与实机验收见[客户端19](../../进展/19-平台内部CA与客户端信任.md)。

## 信任来源与范围

统一客户端发行包通过包资源携带`ioalab_sdk/runtime/trust/`下的本项目CA公共PEM及`platform-trust.json`（`ioalab.platform-trust/1`）清单，作为受审发布资源；初始来源是[安装与发布](安装与实例.md#安装与发布)的GitCode固定tag/commit，经系统CA验证来源，再核安装RECORD。CA私钥、服务器私钥及业务凭据均不进入发布资源。不能从尚未通过TLS验证的平台、Agent Card、模型输出或下载链接自动新增信任。

清单包含format、origins和anchors；origins为规范化HTTPS Origin（scheme、精确hostname、有效port），anchors各含相对PEM资源路径和证书DER的SHA-256指纹。路径限定在包内，拒绝链接、越界、空集合、非CA证书及摘要不符。本方案的首个发行仅授权`https://ioalab.openjiuwen.com:443`，不以域名后缀、子域通配或服务器返回的publicUrl扩大范围；IP不会自动映射为域名。

`Network(platform)`从实际已安装包读取清单：命中受管Origin时，在系统默认SSL上下文中加载对应CA；其他Origin只使用系统默认信任。受管清单/证书缺失、损坏、非CA或指纹不匹配时拒绝初始化；CA证书尚未生效或已过期时不加载该锚，在inactiveCaFingerprints中报告，仍严格验证系统公共CA链，内部CA服务器链不因此豁免到期检查。这样同一域名改用受系统信任的公共CA时，不会被旧内部CA到期阻断。HTTP或未校验TLS始终拒绝。系统默认信任集合不被清空，也不写系统、浏览器或其他Python环境的证书库。

## 唯一网络入口

首次账号页的5秒平台预检、注册、显式登录、AccountSession、两角色的协议公共读取与WSS均使用同一Network信任构造。常驻实例仍只持有一份SSL上下文和共享HTTPS池，所有请求目标限定到实例平台Origin；WSS的wss端点对应同一https Origin。连接其他平台、GitCode安装、模型供应商及邮件不使用本平台附加CA。

证书链、DNS/IP SAN、有效期及serverAuth用途全部校验；不提供verify=false、忽略主机名、失败降级或按指纹接受任意服务器证书的开关。CA公共证书是信任锚，服务器使用其签发的独立叶证书和私钥；客户端不持有TLS客户端证书，本方案不引入mTLS或改变账号、A2C签名与业务授权。

Network持有的trust视图只含source（bundled/system）、origin、policyDigest、caFingerprints和inactiveCaFingerprints；runtime.status在networkTrust字段公开该视图。它表示本实例已加载的策略，不能代替实际握手成功。失败信息区分资源损坏、未知CA、主机名不匹配和证书过期，不返回配置正文或私钥。

## 升级与换证

信任资源和版本随统一客户端wheel发布；轻量角色Skill只调用同一客户端，不复制CA或另建安装链。官方服务与管理员助手使用同一候选发行中的客户端资源。普通运行配置不新增凭据式CA字段，runtime.configure也不能修改信任锚或平台归属；需要新增CA或Origin时，按受审客户端发布升级处理。

实例启动时冻结policyDigest、caFingerprints和inactiveCaFingerprints；资源变化按[安装与实例核对](安装与实例.md#安装与实例核对)要求停止原实例、核实际锁释放后升级重启，不热改存活SSL上下文。未使用受审安装器的源码/自管环境也必须加载其自身包内资源并核清单，不能借相邻源码或临时路径补证书。

同一CA签发的新叶证书只需服务器部署，不要求客户端更新。CA轮换按三步完成：先发布同时含原CA与新CA的信任集合；确认目标客户端升级后切换服务器叶证书；再发布移除原CA的客户端。每步冻结同一Network策略；CA泄露时停用相关服务并分发移除旧锚的新客户端，不承诺未升级客户端自动撤销信任。

## 验收条件

| 场景 | 必须证明 | 测试 |
|---|---|---|
| 正常与拒绝 | 未安装系统CA的干净环境中，包内CA使同域HTTPS/WSS均成功；未知CA、错误域名、过期证书、损坏资源均拒绝 | sdk_network、sdk_tls |
| 信任范围 | 不同hostname/port、重定向和外源协议目标不取得附加CA或凭据；公共CA平台继续可连接 | sdk_network、sdk_tls |
| 首次接入与分发 | 包离开源码目录可读资源；账号预检、注册、登录及两角色用同一策略；完整wheel不含私钥 | account_setup、package_installation、access_products |
| 恢复与轮换 | 升级前核原实例与锁；叶证书续期无客户端改动；CA新增、切换与移除按各阶段实测 | installation_identity、sdk_network、sdk_tls |
