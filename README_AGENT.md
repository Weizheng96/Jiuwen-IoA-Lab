# 帮用户接入 IoA Lab

复用当前宿主：九问Swarm选jiuwen，Codex选codex，Claude Code选claude_code；有原配置就复用原宿主。只有当前环境无法确定或用户要求换框架时才询问。一个客户端、同一账号和常驻实例，个人助手与服务方由两个角色Skill区分。默认平台https://ioalab.openjiuwen.com，原显式地址优先。

1. 核Git与CPython3.11，从GitCode公开标签中按数字N选择最新已发布design-v0.0.N，Git下载包含bootstrap.py的客户端：

   ~~~text
   git clone --depth 1 --filter=blob:none --sparse --single-branch --branch <实际发布tag> https://gitcode.com/WeiZheng96/jiuwen-IoA-lab.git <新源码目录>
   git -C <新源码目录> sparse-checkout set src/client
   python <新源码目录>/src/client/bootstrap.py --tag <同一tag> --host <jiuwen或codex或claude_code>
   ~~~

   Windows使用已核CPython3.11；Linux用python3.11。默认安装~/.ioalab/client并导出~/.ioalab/role-skills；隔离测试可指定--install-dir和--output。仅安装所选宿主依赖。安装器核官方来源、tag/commit及自身内容，从固定Git对象按需取得受审vendor并构建，不再让pip克隆客户端。source保留真实pip来源，checkout另报仓库/tag/commit；依赖与文件校验通过仍为connected=false。
2. 按当前宿主加载所选角色ZIP。九问：用send_file_to_user发送两个真实绝对路径ZIP，用户在“我的技能→创建”导入、选择，再用skill_tool读取。Codex及Claude Code：在本次用户工作目录解压所选ZIP，用本机文件工具读取实际SKILL.md和references/setup.md。使用同一薄脚本，不改宿主全局设置或历史聊天。
3. 按setup.md进入SDK本机账号页，录入或生成账号；秘密只留在页面，不读账号配置正文。账号完成后按host-setup.md补齐公开命令、模型及宿主字段，启动并分别核authenticated、host.available、platformConnected与身份bound。已有角色共用原配置，不重新注册。

Git对象、包或命令失败时保留目录、原任务引用及脱敏错误；短时限用宿主原生后台能力并查原任务，不重复安装，不关闭TLS或现场改发布源码。升级先安全停止原实例，保留账号、身份与在途业务。Codex/Claude首版原生生产工具、原生问题回调和当前桌面聊天通知尚不支持，按实际能力报告；业务批准只走SDK可信确认页。
