# Claude Code宿主适配

显式SDK应用通过官方claude-agent-sdk==0.2.181使用用户已安装Claude CLI，配置command为一个真实可执行文件绝对路径的数组，model显式选择，skill_directories为可选目录映射。它采用隔离的SDK设置和工具范围，不是统一产品的原生失败回退，也不能据此宣称继承某个已有聊天的全部设置。

统一产品类型为claude_code，以S01 submit和运行期S02 check为共同入口。check的版本／握手／目录结果不证明推理或认证；产品接入不以它作启动门槛。账号和业务状态由共用SDK管理。

196原生迁移A切片新增`native_host.NativeClaudeHost`，尚未切换包默认入口或显式SDK应用装配。它使用静态登记和随包Mods资源、真实来源包装、绑定到SDK同一resident的私有control handler；不增加模型/命令配置或启动probe。资源见`ioalab_claude_code/resources/native/README.md`，无可选自动安装也必须完整交付。

当前partial实现有同SID角色隔离、原SDK boot刷新、严格原输入行／全文摘要／core turn因果门禁，以及工具真实call ID与回调排空运输。缺完整证据时保持unknown，不把ACK／文本／Stop作为终态。B2定向16项与4light通过，均是边界替身，无真实CLI／模型。

公共execution仍明确不可用；原项目配置自动继承、通用caller/ensure-running、授权文件传输、实际可见新会话和完整原请求终态尚待集成与原生验收。缺新会话项目定位时开窗前报缺项，不用SDK数据目录替代。未知原请求不重放，旧SDK诊断结果不计为这些原生能力已通过。
