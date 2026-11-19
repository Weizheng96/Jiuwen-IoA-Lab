# 先连接，再展示账号页

触发：用户在普通聊天中要求接入平台（即使本机已经安装、存在配置文件或旧接入）。目标是让用户通过界面办理，用户不需要知道工具名；不要把“安装存在”或“有配置文件”等同于账号/宿主已就绪。

## 当前聊天能否使用 IoA

1. 当前实际工具列表已有 IoA 的 ioa_setup：直接回到 account-setup.md 调用它，使用用户给定平台与本角色；不再用脚本打开浏览器。不凭历史回执或连接器名字宣称工具可用。
2. 当前工具列表没有 IoA：按 location.md、calling.md 定位脚本，用 runtime.install.status 核已安装客户端。已安装completed跳过README的首次bootstrap步骤；只有尚未安装才回到README_AGENT.md的原bootstrap，安装错误保留并诊断；不要为接入重装一份，也不要先 runtime.status/start/connect 或读取账号配置。
3. 已安装后生成登记资料，此操作不要求账号配置存在或账号已登录：

```text
python "<skill_directory>/scripts/ioalab.py" runtime.mcp.configuration
```

脚本已绑定原客户端和配置，不传--config，也不创建文件；已存在但无效的配置不得由模型补字段或改路径掩盖错误。completed仅表示取得资料，stage=mcp、nextAction=enable_in_current_chat表示下一步是宿主启用；accountRequired=false不是账号认证成功。setupArguments给出绑定角色，平台地址来自用户本次指令或原配置。

## 让宿主启用

九问当前工具包含 `connect_mcp` 时，直接使用回执 `jiuwenAutoSetup.arguments` 调用该原生工具。工具会核对当前聊天并自动复用匹配连接器，没有才创建、连接和挂载；无需用户进入扩展菜单或复制JSON。读取实际回执：`sessionEnabled=true` 后在当前工具清单核IoA工具并继续账号页；冲突或失败按原回执诊断，不重复创建、覆盖同名连接器或修改宿主私有文件。原生权限审批由宿主处理，不以聊天口头确认代替。此原生能力只在实际工具列表存在时使用，不猜方法名、不自行拼WebSocket地址或会话标识。

没有上述自动能力，但宿主提供其他正式登记/选择工具时使用该入口，并遵守原生审批与重载。否则先引导用户在连接器列表中选择已有IoA连接器到本次聊天，不覆盖同名不同配置，不要求用户再次注册或安装。没有已有连接器时，Codex/Claude Code分别使用回执codexCommand/claudeCommand经公开CLI登记；九问展示回执的完整jiuwenJson及步骤：九问“扩展→我的扩展→连接器→创建→配置连接器”，在“添加JSON代码”粘贴并保存，随后点击该连接器的“使用”进入带连接器的聊天；若当前版本支持在原聊天选择连接器，也可在原聊天启用。原生入口不同就以实际界面为准，不猜API或写config.yaml/MCP私有目录。

仅需要用户手动启用时，说明“需要先启用IoA连接器，接下来登录页才能显示在聊天里”，等待用户完成。没有工具注入是未启用，不是宿主不支持。用户完成后重新核当前工具清单，仍无工具则说明需要宿主加载/新会话，不改用浏览器来掩盖。自动或手动启用成功后直接打开账号页，不必让用户说出ioa_setup等术语；新聊天用户只需说“继续接入IoA Lab”。

仅宿主明确没有MCP/Apps能力、原生启用明确不可用或用户明确选择浏览器时，说明原因后沿account-setup.md的脚本分支；不反复重试登记。支持MCP但不支持Apps时仍由同一MCP服务按协商降级浏览器，不另开第二条账号流程。内嵌页连接被阻止时由用户点击“在浏览器继续”。

## 接入后

按account-setup.md核认证和“继续使用”的真实结果，首次接入按host-setup.md与status.md核后台执行能力；已有接入仅按status.md只读核验后结束，不迁移或启停原实例。phase=reused时仅用SDK回执的existingConfiguration复用原实例，不重复配置/启动；若登记的MCP配置路径与它不同，用runtime.binding取得绑定Python，以该Python执行 ioalab_client --role <本角色> runtime.mcp.configuration --config "<existingConfiguration>"取得正确资料；角色Skill需要重绑定时沿skills-install的显式入口核对原绑定，不修改脚本正文，按宿主原生入口更新本次IoA连接并重新加载，其他连接器不动。核实际ioa_status/身份后再开始业务，不把注册连接器或登录成功当作业务连接就绪。
