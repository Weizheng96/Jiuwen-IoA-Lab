# 帮用户接入 IoA Lab

用户说“参考 GitCode 仓库的 readme_agent.md，帮我接入 IoA Lab”时，执行以下顺序。复用用户已有九问 Swarm，不修改其安装、全局指令或历史。

唯一引导文件是GitCode仓库根目录的readme_agent.md：[机器可读原文](https://raw.gitcode.com/WeiZheng96/jiuwen-IoA-lab/raw/main/readme_agent.md)、[浏览器文件页面](https://gitcode.com/WeiZheng96/jiuwen-IoA-lab/blob/main/readme_agent.md)。给九问的指令优先使用原文地址。文件页面依赖JavaScript，普通网页抓取可能只取得站点外壳；遇到外壳时读取上述原文，不猜`gitcode.com/.../raw/...`地址，不克隆整个项目。在GitHub或其他镜像看到本文件时，仍使用GitCode这同一个文件。

1. 从 [GitCode 项目首页](https://gitcode.com/WeiZheng96/jiuwen-IoA-lab)取得公开平台地址。当前官网尚未开放；若未公布地址，询问用户实际平台地址，不猜地址、不使用仓库中的私有服务器资料。读取该平台 `/api/config`，核对 `publicUrl`。接入说明以本页为准，平台不再持有另一份接入引导。
2. GET 同一平台的 `/api/access/packages`，只接受 `format=1`。这些是普通HTTP只读请求，直接用本机Python 3.11标准库`urllib.request`读取JSON和下载，不需要浏览器、Playwright或Node.js。未指定角色选择 `assistant`；明确提供服务选择 `service`；双角色分别下载两个包。使用清单中同源的 `/downloads/` URL，将完整 ZIP 保存到本机下载目录，核对实际字节数、SHA-256、runtimeFormat=1、Python 3.11 要求。缺包或摘要不符时停止并报告平台未准备就绪；不下载单个 SKILL.md。TLS始终使用系统信任链，证书错误时停止并要求正确配置受信CA，不使用`-k`、`verify=False`、忽略浏览器警告或其他绕过。
3. 报告 ZIP 的实际绝对路径。远端九问还应通过原生`send_file_to_user`提供文件卡片，供用户在浏览器取得该完整ZIP，不让用户查找服务器目录。引导用户在九问原生“我的技能”导入 ZIP，并在会话菜单选择对应技能。等待用户完成；再用 `skill_tool` 实际读取导入的 SKILL.md 和 references/setup.md。下载、导入、读取是三个阶段，不伪报已加载或已连接。
4. 实际加载后呈现“提供已有账号”“帮我注册”。按产品技能的账号引导打开本机账号页；账号密码和邀请码只在本机页输入，模型不读取配置正文、生成密码或接收秘密。注册失败按明确提示处理；网络未知恢复原请求，不换账号重复注册。
5. 继续产品技能的安装、配置、启动与状态核对。双角色复用原配置、账号和常驻实例；只询问无法从原生公开状态确定的九问命令、工作目录等必要宿主信息。分别报告账号、身份、连接、宿主、角色能力。服务未公示、未开放接单时不声称可以接单。

没有权限自动写下载文件时，解释缺少的宿主能力并提供平台真实下载链接；不能把链接展示当作已经下载。运行中的未知操作、既有账号和原数据均保留。
