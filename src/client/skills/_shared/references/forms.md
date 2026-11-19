# 本地交互表单

触发：用户希望接入平台、定义服务或通过界面完成业务。接入请求先按[mcp-setup.md](mcp-setup.md)在当前聊天启用IoA，再按[account-setup.md](account-setup.md)展示账号页；不先runtime.connect/start或以脚本打开浏览器账号页。账号字段只能在受控页面输入，不能放进表单values。

MCP登记、原生审批/加载及不可用时的降级统一按mcp-setup.md，不把当前聊天没有工具当成宿主不支持。前台MCP与后台AgentHost分别核验，复用同一客户端和原身份。

所属实例就绪后，个人助手提供 `registry.interfaces`（定义、更新与停用收信接口）、`messaging.interaction`（发现接口、结构化发送、多轮回复与关闭）、`delegation.trade`（授权、发现、报价比较、合同、履约、验收付款、解除与仲裁）。`messaging.interface` 是直接定义接口的短入口。`delegation.renew` 提供原taskRef的独立续期表单，用户明确期限后填写validDays，核真实批准及平台生效，不把付款确认当作续期。原委托等待期间可用 `delegation.case` 按原报价/合同申请或恢复争议。服务方使用 `services.management` 管理公示、接单状态及原任务。页面顶部“接入与客户端配置”可设置本地专业说明、已安装业务技能及容量；已有实例先正常排空，配置后再启动。目录以本机实际返回为准。

新建收信接口时，未明确的权限初值为allowedSenders=[]、enabled=false、tools=[]；不能自行填成所有已登录账号、启用或授予工具权限。名称、用途、消息格式和处理规则可拟为待核对草稿，权限由用户在页面选择；已有草稿/接口恢复保持原值。

有 IoA MCP 工具时，调用 `ioa_forms` 发现可用类型及原引用，再用 `ioa_open_form`（kind、可选 values）打开表单。values 只放用户已说明或已可靠取得的普通业务初值，缺项交给页面填写。支持标准 MCP Apps 的宿主以 ioa_open_form 新建、ioa_get_form 恢复并直接显示内嵌页面；不要为“展示、恢复、交给用户填写”再调用 ioa_open_browser 或 runtime.forms.open_ui，也不要用终端或其他打开链接工具绕过。外部业务页仅在用户点击页面的“在浏览器打开”时打开，委托与收信接口发布确认优先在聊天内的独立卡片完成；其他确认或不支持内嵌时由用户点击浏览器确认入口。ioa_open_browser 在 Apps 宿主仅供页面调用，导航信息由 App 自动取得；模型不推测、不索要。未声明 Apps 能力时客户端自动打开本机浏览器，不需要重复打开。九问 A2UI 与标准 Apps 不等同，不以能调用 MCP 工具推断能嵌入页面。不得把密码、私钥、令牌或整份配置放进参数。

没有 MCP 时，按 calling.md 的完整命令和原外置配置调用 `runtime.forms.list`，用独立参数文件执行 `runtime.forms.open`，取得 data.reference 后调用 `runtime.forms.open_ui`（reference）。这些操作使用同一公共客户端，不另安装一份 SDK 或另起常驻实例。无表单能力时报告实际版本或角色缺项，不猜工具名或修改宿主源码。

页面让用户编辑、保存、预览、提交。提交仅申请具体操作；waiting 时由页面提供受控确认入口，用户实际决定后点击“按原操作继续”。Agent 不用普通工具、聊天确认或页面可见性代替批准，也不替用户点击批准。

多阶段表单以同一 reference 保存各阶段及原业务引用。已受理作业和已作出的真实确认由常驻客户端续接到下一项业务选择；页面关闭也不重建业务。需要新输入时停止在草稿；unknown 保持原操作等待核实。再次读取可能已经进入新 revision，先显示最新阶段，不拿旧页面值覆盖它。每次输入的含义由角色流程决定，不让模型自行拼接低层调用顺序。

需要通过普通工具编辑时，`runtime.forms.update` 接收 reference、当前 revision 和完整普通 values，保存后 revision 递增；`runtime.forms.preview` 接收 reference、最新 revision，校验必填项并生成真实预览。对应 MCP 工具为 `ioa_update_form`、`ioa_preview_form`。冲突先读取原表单，不覆盖用户正在编辑的内容；已提交修订不可修改。

恢复时用 `ioa_get_form` 或 `runtime.forms.get`（reference）。外层 completed 只表示读取表单成功，业务结果看 data.phase 与 data.result.status。表单 waiting/unknown 不能解释为已经发布。用户在可信页面决定后，可用原 reference、revision 调用 `ioa_submit_form` 或 `runtime.forms.submit` 续接同一操作。返回的真实业务状态完成才说明完成；结果未知先读原表单，不重新打开同一业务以换号重做。

关页、MCP 重连或退出不删除草稿或停止平台长连接。无 Apps 的入口可用 `ioa_open_browser` / `runtime.forms.open_ui` 重新打开原引用；支持 Apps 时用 `ioa_get_form` 内嵌恢复，页面提示导航信息缺失或连接更新时先刷新原表单，不新建业务；只看 opened，不提取或抄写浏览器入口令牌。变更已提交内容需要新的草稿与新的具体确认，原记录保留。

委托或收信接口表单waiting且有确认引用时，支持MCP Apps的宿主优先显示独立确认卡片，让用户在聊天里核对并决定；不要主动调用runtime.open_ui或另开浏览器。卡片不可用时由用户点击“在浏览器确认”，无Apps才使用原浏览器入口。宿主允许工具运行不是业务批准；模型不读取_meta中的入口能力、不代点允许。用户决定后读取原表单核实结果，不把卡片“已允许”说成已签约或已付款。

无Apps的原表单明确恢复使用 runtime.forms.recover，参数为原 reference、revision；仅页面实际允许恢复且用户要求时执行，不新建业务或重复未知效果。

界面阅读成本：先复用原草稿和用户已说明的信息，不重复询问。可拟定名称、说明、格式与处理步骤并标为待核对建议；金额、对象、权限和承诺不猜。只在目标/意图存在歧义时简短追问，当前Schema可收集的缺项交给页面；机器字段由客户端生成或取已核实值。页面“检查并继续”会保存实际输入再生成预览，不自动提交；用户决定仍经独立确认。展示页面后聊天只说明当前步骤和必要提醒，不复述整份JSON、哈希、引用和工具过程；用户询问技术细节时再解释。

messaging.inbox是只读收件查询页：打开或刷新即返回原记录，点击详情只改变查询条件；queryOnly=true时不做保存草稿/预览/提交/批准。模型可用ioa_update_form更新messageRef或分页条件，必须保留原reference和当前revision。
