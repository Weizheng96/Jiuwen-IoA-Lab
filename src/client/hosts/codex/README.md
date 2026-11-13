# Codex宿主适配

独立可选包，通过用户已有Codex App Server实现SDK的AgentHost。配置显式提供Codex可执行argv和模型名；运行使用原宿主认证，不复制凭据，不接管已有桌面聊天。

```python
from ioalab_codex import CodexHost

host = CodexHost(["/absolute/path/to/codex"], model="<已配置模型>")
capabilities = host.probe()  # 不调用模型
# 两角色通过host.run(HostTurn(...))执行固定SDK工具轮次。
```

统一客户端已通过host.type=codex选择本适配器，使用同一SDK完成配置、常驻连接及两角色装配；启动命令为python -m ioalab_codex start --config <原配置>。Codex配置仅含公开command、显式model和可选skill_directories，不使用九问数据目录。SDK与宿主发布依据及消费者锁随本次客户端更新；平台完整委托、当前桌面聊天通知及异常恢复仍按客户端17分别验收。原生生产工具和原生提问回调未支持时明确拒绝。

契约见[Codex适配](../../../../设计/组件/客户端/Codex适配/接口.md)。
