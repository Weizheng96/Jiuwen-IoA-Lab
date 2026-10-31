# IoA Lab 产品接入技能

个人助手和服务方分别由 `assistant/`、`provider/` 提供说明；共用安装及调用脚本只维护在 `_shared/ioalab.py`。这两个技能交付给原版九问，与 `.agents/skills/` 的 Codex 开发技能分开。

执行 `python tools/build_access_skills.py` 构建两个可直接导入的技能 ZIP。构建生成各包的 `scripts/ioalab.py`、依赖 wheel、锁定清单及摘要；不编辑生成副本。用户配置、参数、私钥和运行数据不分发。

依赖约束维护在 `_shared/requirements.lock`；开发环境依赖变动后用构建器 `--update-lock` 刷新并重新验证干净安装。服务器普通构建读取此锁，不要求核心环境安装参与方包。

安装与调用规则见[公共接入](../../../设计/接入/SDK共用机制.md)；当前迁移差距见[开发计划](../../../docs/开发计划.md)。VERSION 继承原助手技能依据，完成全部产品接入验收后再升级。
