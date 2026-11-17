# 41：Linux管理员远程用户确认验收通道

状态：ready，环境与装配缺口；不影响已通过的Codex桌面收发主路径，阻塞通用流程4的admin实际签约、验收和付款。未实现，不增模块开放票数量；本轮业务矩阵仍归[客户端36](../../../组件/客户端/进展/36-Windows三宿主四流程统一验收.md)。

## 已证事实

2026-11-18，原Codex客户端已发布杭州旅游极简建议，固定1元、零押金、countersign；published/templateReady/executionReady均为true，accepting=true、容量1。现有Linux admin通过公开assistant.delegation.flow begin创建真实1元任务授权，flow为`65b7c639-2237-5824-9711-b43eb77eb2d7`、confirmation为`d18e9e28-368a-59ed-a7f2-9839c692e6ea`，结果waiting/confirmation。尚未批准或发出合同征集，未成约、生产、交付或付款。原操作和预览保留，不换号重做。

现有admin没有正式的Windows Chrome远程确认装配。runtime.open_ui只在实例本机打开，不向模型返回入口凭据；单纯转发回环端口不足以取得浏览器会话。部署教程的九问网页转发也不是SDK合同确认通道。当前管理员助手prepare仅准备固定预设，不提供委托、合同、验收或付款批准。在线试用Gateway的席位/epoch不能授权任意admin。

## 后续处理边界

先选择可操作的Linux真实本机浏览器/受控桌面，或按既有TrustedUserPort/TrustedUserServer接缝正式装配管理员确认Gateway。后者由受信部署绑定固定actor/generation，Gateway承担浏览器认证、同源、CSRF和真实用户eventId；须先落实设计及独立验收，不复制试用席位授权。

不提取页面token、不配置临时browser_opener、不通过RPC或直接写库批准，也不以聊天中的“允许”代替实际预览决定。原合同、授权、验收与付款仍各自独立确认。

## 验收

- [ ] 人类或已授权的真实浏览器操作可查看admin原授权预览、作出决定，并沿原flow恢复。
- [ ] 合同、验收和付款逐项展示真实对象，拒绝、过期、未知及错误actor不产生越权或重复效果。
- [ ] admin真实委托触发Codex桌面生产、交付和结清；与服务公示通过分别记录。
