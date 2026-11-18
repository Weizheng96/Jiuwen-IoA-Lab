# 41：Linux管理员远程用户确认验收通道

状态：claimed；已通过临时真实Linux Chrome桌面打通admin原用户确认，后续合同/验收/付款仍待逐项验证。不影响已通过的Codex桌面收发主路径，不增模块开放票数量；本轮业务矩阵仍归[客户端36](../../../组件/客户端/进展/36-Windows三宿主四流程统一验收.md)。

## 已证事实

2026-11-18，原Codex客户端已发布杭州旅游极简建议，固定1元、零押金、countersign；published/templateReady/executionReady均为true，accepting=true、容量1。现有Linux admin通过公开assistant.delegation.flow begin创建真实1元任务授权，flow为`65b7c639-2237-5824-9711-b43eb77eb2d7`、confirmation为`d18e9e28-368a-59ed-a7f2-9839c692e6ea`，结果waiting/confirmation。尚未批准或发出合同征集，未成约、生产、交付或付款。原操作和预览保留，不换号重做。

现有admin没有正式的Windows Chrome远程确认装配。runtime.open_ui只在实例本机打开，不向模型返回入口凭据；单纯转发回环端口不足以取得浏览器会话。部署教程的九问网页转发也不是SDK合同确认通道。当前管理员助手prepare仅准备固定预设，不提供委托、合同、验收或付款批准。在线试用Gateway的席位/epoch不能授权任意admin。

## 后续处理边界

本轮已选并实测现有真实本机浏览器方案：官方仓安装Chrome157.0.8103.78，原非root受管用户运行，保NoNewPrivileges=yes；真实chrome://sandbox显示Namespace、PID/网络namespace、Seccomp-BPF均开启。临时Xvfb/openbox与原实例共享PrivateTmp，密码保护VNC仅回环，经既有严格主机校验SSH隧道在Windows Chrome查看并用CUA操作。原SDK仅按正常user_browser打开服务器本机Chrome，未提取URL fragment或增加RPC批准。原1元授权页核对后实际点击，待办1→0；原runtimeId、账号和身份保持。一次root配置写入导致文件归属变为root、受管启动拒绝，已按原单位/目录归属恢复专用用户0600并正常启动；后续配置和撤销须由原受管用户执行，不能把该运维错误归为SDK回执失败。临时桌面撤销、浏览器配置准确恢复与最终合同流程待核。

先选择可操作的Linux真实本机浏览器/受控桌面，或按既有TrustedUserPort/TrustedUserServer接缝正式装配管理员确认Gateway。后者由受信部署绑定固定actor/generation，Gateway承担浏览器认证、同源、CSRF和真实用户eventId；须先落实设计及独立验收，不复制试用席位授权。

不提取页面token、不配置临时browser_opener、不通过RPC或直接写库批准，也不以聊天中的“允许”代替实际预览决定。原合同、授权、验收与付款仍各自独立确认。

## 验收

2026-11-18用户最初报告“似乎点了两次确认授权”，随后澄清应为同时同意旧委托合同确认和新“ADMIN杭州验收02（用户确认）”预算授权；这与只读状态吻合：旧flow`65b7c639-2237-5824-9711-b43eb77eb2d7`变为signed/external_processing，服务方收到decision任务`025c9ee0-8b00-515d-a5aa-681541bca42e`；新flow`638df6b2-8235-5ef0-aa99-4989fd242afa`仅begin完成。本次按两张不同原卡分别获批记录，不列为同一按钮重复提交或重复生成授权故障，也无据宣称重复合同或扣款。用户明确选择只验证已投送的旧委托，新委托停留预算授权后，未签第二份合同；卡片区分与确认后反馈的体验改进可后续评估。

旧委托入站后因三条未释放原生记录占满临时max_model_runs=3而排队。主协调经公开stop/configure/start增加到4，保原runtimeId和未知记录；重启后原endpoint的内存deferred续处理对象丢失，任务变unknown，cause=`The original deferred endpoint needs reconciliation`，sessionId仍空。该轮未完成接单或交付；关联40的排空/恢复问题，不能把客户端在线或配置成功当作业务通过。

- [x] 已授权的真实浏览器操作查看admin原授权预览、作出决定，并沿原flow恢复；search/inspect取得真实已签模板。
- [x] 结合用户澄清和原flow状态，确认本次两次操作对应旧合同与新预算授权，不误记同卡重复提交故障。
- [ ] 合同、验收和付款逐项展示真实对象，拒绝、过期、未知及错误actor不产生越权或重复效果。
- [ ] admin真实委托触发Codex桌面生产、交付和结清；与服务公示通过分别记录。
