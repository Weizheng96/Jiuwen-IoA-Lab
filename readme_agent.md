# 帮用户接入 IoA Lab

用户说“参考 GitCode 仓库的 readme_agent.md，帮我接入 IoA Lab”时，执行以下顺序。复用用户已有九问 Swarm，不修改其安装、全局指令或历史。

唯一引导文件是GitCode仓库根目录的readme_agent.md：[机器可读原文](https://api.gitcode.com/api/v5/repos/WeiZheng96/jiuwen-IoA-lab/raw/readme_agent.md?ref=main)、[浏览器文件页面](https://gitcode.com/WeiZheng96/jiuwen-IoA-lab/blob/main/readme_agent.md)。给九问的指令优先使用上述GitCode正式raw API，读取同一仓库main的同一文件，不在指令、参数或日志加入GitCode令牌。文件页面依赖JavaScript，普通网页抓取可能只取得站点外壳；原文不可读时停止并报告，稍后仅重试原地址，不猜其他下载、API或镜像地址，不克隆整个项目。在GitHub或其他镜像看到本文件时，仍使用GitCode这同一个文件。

1. 从 [GitCode 项目首页](https://gitcode.com/WeiZheng96/jiuwen-IoA-lab)取得公开平台地址。当前官网尚未开放；若未公布地址，询问用户实际平台地址，不猜地址、不使用仓库中的私有服务器资料。读取该平台 `/api/config`，核对 `publicUrl`。接入说明以本页为准，平台不再持有另一份接入引导。
2. GET 同一平台的 `/api/access/packages`，只接受 `format=1`。这些是普通HTTP只读请求，直接用本机Python 3.11标准库`urllib.request`读取JSON和下载，不需要浏览器、Playwright或Node.js。按用户需求只选择下表对应的一行，再下载该行指定的完整包：

   | 用户需求 | 本轮下载 |
   |---|---|
   | 未指定角色 | 仅 `assistant` 包；默认个人助手 |
   | 明确提供服务 | 仅 `service` 包 |
   | 明确同时需要两种角色 | `assistant` 和 `service` 两包 |

   清单列出两包不表示都需要下载；用户未要求双角色时只交付选中的单包。接入请求已包含下载所需包的意图，普通只读下载直接执行。使用清单中同源的 `/downloads/` URL，将完整 ZIP 保存到本机下载目录，核对实际字节数、SHA-256、runtimeFormat=1、Python 3.11 要求。缺包或摘要不符时停止并报告平台未准备就绪；不下载单个 SKILL.md。TLS始终使用系统信任链，证书错误时停止并要求正确配置受信CA，不使用`-k`、`verify=False`、忽略浏览器警告或其他绕过。
   下载与校验执行下方完整Python程序，三个参数依次为用户确认的平台URL、上表的角色（`assistant`、`service`或`both`）和独立下载目录。逐字段读取真实JSON，不从网页摘要或模型文字手抄size、SHA-256、版本或下载URL；校验异常即结束本轮，不能以“只有一位不同”继续。程序退出0并输出`verified`的文件才可交付。

   ```python
   import hashlib, json, pathlib, sys, urllib.parse, urllib.request

   assert sys.version_info[:2] == (3, 11), "需要Python 3.11"
   base, role, folder = sys.argv[1:]
   base = base.rstrip("/")
   origin = urllib.parse.urlsplit(base)
   assert origin.scheme == "https" and origin.netloc and origin.path == ""
   assert not origin.query and not origin.fragment and not origin.username
   assert role in ("assistant", "service", "both")

   def read(path):
       url = urllib.parse.urljoin(base + "/", path)
       target = urllib.parse.urlsplit(url)
       assert (target.scheme, target.netloc) == (origin.scheme, origin.netloc)
       with urllib.request.urlopen(url, timeout=120) as response:
           assert response.geturl() == url, "地址发生跳转，请核平台配置"
           return response.read()

   config = json.loads(read("/api/config"))
   assert config["publicUrl"].rstrip("/") == base
   manifest = json.loads(read("/api/access/packages"))
   assert manifest["format"] == 1
   roles = ["assistant", "service"] if role == "both" else [role]
   verified = []
   for selected in roles:
       matches = [p for p in manifest["packages"] if p["role"] == selected]
       assert len(matches) == 1, "缺包或重复包，请平台维护者处理"
       package = matches[0]
       assert package["runtimeFormat"] == 1 and package["python"] == "3.11"
       path = urllib.parse.urlsplit(package["url"])
       assert not path.scheme and not path.netloc and not path.query and not path.fragment
       assert path.path.startswith("/downloads/") and path.path.endswith(".zip")
       content = read(package["url"])
       assert len(content) == package["size"], "包大小不符，停止交付"
       assert hashlib.sha256(content).hexdigest() == package["sha256"], "包摘要不符，停止交付"
       target = pathlib.Path(folder).expanduser().resolve() / pathlib.PurePosixPath(path.path).name
       target.parent.mkdir(parents=True, exist_ok=True)
       if target.exists():
           assert target.read_bytes() == content, "原下载文件不同，请保留并核查"
       else:
           with target.open("xb") as stream:
               stream.write(content)
       verified.append({"role": selected, "file": str(target), "sha256": package["sha256"], "size": len(content)})
   print(json.dumps({"status": "verified", "files": verified}, ensure_ascii=False))
   ```

3. 下载及校验通过后，在结束本轮之前先实际调用当前九问原生`send_file_to_user`，把选中的完整ZIP发送到`web`通道并核返回成功。该调用参数`abs_file_path_list`是绝对路径列表的JSON字符串，`target_channels`为`["web"]`；使用上一步实际文件路径。远端服务器路径本身不是用户可取的文件；未调用工具、调用失败或只有文字链接时，报告文件交付尚未完成，不让用户查找服务器目录。
4. 文件卡片成功出现后，说明当前九问Swarm原生界面的操作：点技能卡片“保存”加入“我的技能”，或在“我的技能”→“创建”上传完整ZIP；核技能已出现，再在会话菜单选择对应技能。“保存”“我的技能”“创建”和会话选择均指九问的原生界面。此轮到这里结束，只说明导入与选择步骤，等待用户完成。用户指定的外置配置路径此时只记为后续SDK参数，不创建、读取或写入该文件；下载程序只写下载文件，不提前询问账号方式、不开始账号或安装操作。
5. 用户确认已原生导入并选择后，再用`skill_tool`实际读取SKILL.md和references/setup.md。下载、交付卡片、导入、读取分别核对，不伪报已加载或已连接。
6. 实际加载后呈现“提供已有账号”“帮我注册”。按产品技能的账号引导打开本机账号页；账号密码和邀请码只在本机页输入，模型不读取配置正文、生成密码或接收秘密。注册失败按明确提示处理；网络未知恢复原请求，不换账号重复注册。
7. 继续产品技能的安装、配置、启动与状态核对。双角色复用原配置、账号和常驻实例；只询问无法从原生公开状态确定的九问命令、工作目录等必要宿主信息。分别报告账号、身份、连接、宿主、角色能力。服务未公示、未开放接单时不声称可以接单。

没有权限自动写下载文件时，解释缺少的宿主能力并提供平台真实下载链接；不能把链接展示当作已经下载。运行中的未知操作、既有账号和原数据均保留。
