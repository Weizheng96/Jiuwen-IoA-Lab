# 可选在线试用应用

依据见[在线试用设计](../../设计/工程/在线试用.md)。当前 VERSION 为 `v0.0.0`，表示新组件尚未完成交付验证，不是三席上线证明。

`ioalab-trial --config <受限配置文件>`启动一个协调器/网关。正式平台只签发一次准入票据；本应用只保存队列、席位、会话和轮次控制记录。正式平台和SDK不导入本包。SDK/合同/付款仍由各隔离席位自己的普通运行实例负责。

`coordinator.py`/`store.py`按显式部署profile管理实际席位，`gateway.py`代理固定目标并撤销旧cookie/WS，`sandbox.py`管理完整所有权标签的容器与volume；`agent.py`在容器内以不同UID运行核心、个人九问、助手SDK和四候选服务，`bootstrap.py`复用公开配置和资源准备入口。原生九问与助手SDK不同UID；试用Skill副本的脚本经`model_bridge.py`封闭调用普通SDK操作，模型无法读取SDK数据目录、确认库和私钥。SDK远程可信确认由同SDK进程中的`TrustedUserServer`负责。

部署步骤见[试用部署](../../deploy/trial/README.md)。没有Linux容器与真实原版浏览器专项，本组件保持未就绪。健康探针查询真实原生Web/WS、技能、SDK、服务状态及公开空业务状态，同时要求实际原版UI/A2UI/Skill调用验收记录。缺少记录或公开接口与锁定版本不符均隔离席位；验收记录不能代替原始浏览器与Linux证据。

定向回归位于`tests/test_trial_*.py`；真实Linux停止、目录权限、旧连接/下载/确认撤销与三席业务需另验。每epoch自动装配固定模型sidecar：供应商密钥只在该代理的只读secret挂载，席位仅持本轮专用token；撤权先停止并确认代理退出，旧token和模型流失效，随后停止席位。公共核心种子从官方`seed_manifest()`筛四候选摘要，不包含生产指令、样例或验收器。业务账号为公开注册的普通trial-user；试用赠币1000不代替用户预算授权。原生前端绝对WS地址、文件路由和CSP仍需实际浏览器确认，普通本地HTTP成功不能代表原生网页可用。

SDK确认页直接复用SDK渲染，保留闭合问题表单和未知提交恢复。原生网页与可信确认使用不同HTTPS hostname，同一网关进程按Host/Origin严格分路。正式平台分别签发两种aud票据；可信会话在消费时绑定当前席位及epoch，不接受原生cookie。原生host不提供确认页面/API，确认host不代理原生网页、文件和WS；确认页禁止iframe嵌入。未取得席位时明确等待，取得后从正式平台重新打开确认入口。结束原子撤销两种aud会话。

本轮没有启动容器、模型或付费/邮件业务，没有资源实测。`tools/verification/trial_resources.py`只采真实Docker计数，未取得的模型令牌、宿主PSS及业务时延明确标missing。
