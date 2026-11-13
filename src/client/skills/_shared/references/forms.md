# 本地交互表单

触发：用户希望接入平台、定义服务或通过界面完成业务。首次接入先用 `runtime.connect`：核验同一个已安装客户端，缺账号时打开本机受控页面；用 `account.setup.status` 查询阶段，`account.setup` 重新打开账号及本地配置页。账号字段只能在该受控页面输入，不能放进表单 values。

安装完成后用 `runtime.mcp.configuration` 取得准确的本机 stdio 命令、九问条目及 Codex/Claude Code 登记命令。按宿主公开入口登记；原生审批/重载不跳过，同名已有配置先核对，不覆盖无关配置。前台 MCP 支持和后台 AgentHost 能力分开检查，同一配置复用原安装与身份。

九问通过“扩展→我的扩展→连接器→创建→配置连接器”登记，在“添加JSON代码”粘贴回执中的 jiuwenJson。缺少原生登记工具时向用户展示该 JSON 和菜单步骤，等待完成后核实际连接；不读取或改写宿主 config.yaml、MCP 私有目录，不猜测登记 API。

所属实例就绪后，个人助手提供 `registry.interfaces`（定义、更新与停用收信接口）、`messaging.interaction`（发现接口、结构化发送、多轮回复与关闭）、`delegation.trade`（授权、发现、报价比较、合同、履约、验收付款、解除与仲裁）。`messaging.interface` 是直接定义接口的短入口。原委托等待期间可用 `delegation.case` 按原报价/合同申请或恢复争议。服务方使用 `services.management` 管理公示、接单状态及原任务。页面顶部“接入与客户端配置”可设置本地专业说明、已安装业务技能及容量；已有实例先正常排空，配置后再启动。目录以本机实际返回为准。

有 IoA MCP 工具时，调用 `ioa_forms` 发现可用类型及原引用，再用 `ioa_open_form`（kind、可选 values）打开表单。values 只放用户已说明或已可靠取得的普通业务初值，缺项交给页面填写。支持标准 MCP Apps 的宿主显示嵌入页面；未声明能力时客户端打开本机浏览器。九问 A2UI 与标准 Apps 不等同，不以能调用 MCP 工具推断能嵌入页面。不得把密码、私钥、令牌或整份配置放进参数。

没有 MCP 时，按 calling.md 的完整命令和原外置配置调用 `runtime.forms.list`，用独立参数文件执行 `runtime.forms.open`，取得 data.reference 后调用 `runtime.forms.open_ui`（reference）。这些操作使用同一公共客户端，不另安装一份 SDK 或另起常驻实例。无表单能力时报告实际版本或角色缺项，不猜工具名或修改宿主源码。

页面让用户编辑、保存、预览、提交。提交仅申请具体操作；waiting 时由页面提供受控确认入口，用户实际决定后点击“按原操作继续”。Agent 不用普通工具、聊天确认或页面可见性代替批准，也不替用户点击批准。

多阶段表单以同一 reference 保存各阶段及原业务引用。已受理作业和已作出的真实确认由常驻客户端续接到下一项业务选择；页面关闭也不重建业务。需要新输入时停止在草稿；unknown 保持原操作等待核实。再次读取可能已经进入新 revision，先显示最新阶段，不拿旧页面值覆盖它。每次输入的含义由角色流程决定，不让模型自行拼接低层调用顺序。

需要通过普通工具编辑时，`runtime.forms.update` 接收 reference、当前 revision 和完整普通 values，保存后 revision 递增；`runtime.forms.preview` 接收 reference、最新 revision，校验必填项并生成真实预览。对应 MCP 工具为 `ioa_update_form`、`ioa_preview_form`。冲突先读取原表单，不覆盖用户正在编辑的内容；已提交修订不可修改。

恢复时用 `ioa_get_form` 或 `runtime.forms.get`（reference）。外层 completed 只表示读取表单成功，业务结果看 data.phase 与 data.result.status。表单 waiting/unknown 不能解释为已经发布。用户在可信页面决定后，可用原 reference、revision 调用 `ioa_submit_form` 或 `runtime.forms.submit` 续接同一操作。返回的真实业务状态完成才说明完成；结果未知先读原表单，不重新打开同一业务以换号重做。

关页、MCP 重连或退出不删除草稿或停止平台长连接。`ioa_open_browser` / `runtime.forms.open_ui` 可重新打开原引用；只看 opened，不提取或抄写浏览器入口令牌。变更已提交内容需要新的草稿与新的具体确认，原记录保留。
