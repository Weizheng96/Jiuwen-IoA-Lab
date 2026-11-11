# IoA Lab 产品接入技能

普通用户的公开引导统一为 [GitCode接入引导](https://gitcode.com/WeiZheng96/jiuwen-IoA-lab/blob/main/readme_agent.md)，GitHub镜像也指向此页。先下载校验完整包，等待用户原生导入和实际加载，再通过本机账号页提供已有账号或代注册。密码不经模型或聊天。

个人助手和服务方分别由 `assistant/`、`provider/` 提供说明；共用安装及调用脚本只维护在 `_shared/ioalab.py`。这两个技能交付给原版九问，与 `.agents/skills/` 的 Codex 开发技能分开。

执行 `python tools/build_access_skills.py` 构建两个可直接导入的技能 ZIP。构建生成各包的 `scripts/ioalab.py`、SDK/宿主/官方可选工具及协议 wheels、锁定清单和摘要；不编辑生成副本。用户配置、参数、私钥和运行数据不分发。

依赖约束维护在 `_shared/requirements.lock`；开发环境依赖变动后用构建器 `--update-lock` 刷新并重新验证干净安装。服务器普通构建读取此锁，不要求核心环境安装参与方包。

官方工具安装不等于启用；只有本机明确配置后才加载，SDK独立发行仍不依赖业务包。运行时报告启动时冻结的安装信息，启动与普通业务调用核对发布、环境及版本；不匹配时先安全停止，用原配置和数据目录重启。`python tools/check_bootstrap.py --report <文件>`可执行真实干净安装及已签合同接管检查；需要国内普通包镜像，不发送外部业务。

安装与调用规则见[公共接入](../../../设计/组件/客户端/共用运行时/接口.md)；当前迁移差距见[进展](../../../设计/工程/进展.md)。VERSION 继承原助手技能依据，完成全部产品接入验收后再升级。
