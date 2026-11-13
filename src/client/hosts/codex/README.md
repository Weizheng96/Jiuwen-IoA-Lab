# Codex宿主适配

独立可选包，通过用户已有Codex App Server实现SDK的AgentHost。配置显式提供Codex可执行argv和模型名；运行使用原宿主认证，不复制凭据，不接管已有桌面聊天。

```python
from ioalab_codex import CodexHost

host = CodexHost(["/absolute/path/to/codex"], model="<已配置模型>")
capabilities = host.probe()  # 不调用模型
# 两角色通过host.run(HostTurn(...))执行固定SDK工具轮次。
```

开发期使用同仓SDK源码提供的冻结技能入口，SDK尚未发布该新增入口；正式发行前须按交付流程更新SDK依据与依赖锁。本阶段为宿主层开发与本机验证；产品Skill、常驻装配、通知及平台端到端验收见客户端17。原生生产工具未支持时明确拒绝，不因宿主能编程就授予本单执行权限。

契约见[Codex适配](../../../../设计/组件/客户端/Codex适配/接口.md)。
