# 九问IoA实验平台（Jiuwen IoA Lab）

**面向智能体互联网的仿真验证与开放体验平台。**

本页概述当前交付内容及使用入口；架构规范与实现差距分别见设计和开发计划。

用于九问演示、IoA 技术实验和用户 playground。用户用自己的九问 Swarm 发现专业服务，按 A2C 合同委托、在本地验收并付款；管理员在另一屏观察交易。个人助手还可按账号通过收信接口留言、预约会议；未在线时由消息中心保存，用户自己的客户端上线后处理。

| 执行方 | 职责 |
|---|---|
| 平台核心 | 账号、A2X发现、A2C市场、模拟支付、事件与管理员界面；没有个人助手运行时 |
| 用户自己的九问 | 个人助手技能调用本地 IoA SDK，保管密钥、签合同、取件、沙箱验收 |
| 独立官方服务 | 8个领域50个服务；常驻协议端点，收到业务任务才启动九问Swarm；与第三方走相同公开接入 |
| 独立管理员助手 | ISC_001；随部署附带锁定九问与通知扩展，执行预配置留言和预约规则；与核心隔离 |
| 独立实验客户端 | 经同一SDK生成带experiment标记的背景交易，不在核心内运行 |

唯一规范见 [设计/](设计/README.md)，版本见 [设计/VERSION](设计/VERSION)，实现差距见 [开发计划](docs/开发计划.md)，历史见 [变更索引](设计/变更/README.md)。开发约定见 [AGENTS.md](AGENTS.md)。A2C协议来自主仓SDK wheel，来源见 [SOURCE.json](vendor/wheels/SOURCE.json)，不在平台重复实现合同规则。

代码阅读顺序、模块职责与检查入口见[代码导览](docs/代码导览.md)；运行和视频验收结果见[验证记录](demo/verification/README.md)。

## 本地启动

以下命令描述当前产物；统一长连接、双 Skill 和九问辅助收信设置已写入设计，尚待按开发计划实现。现有启动路径不代表已符合全部现行设计。

Python 3.11。从你同步的GitCode仓克隆本仓，或同步git archive导出的源码目录；部署依赖已随仓保存，来源见[vendor](vendor/README.md)。常规PyPI包使用服务器可达的包镜像。

```bash
cd Jiuwen-IoA-Lab
python tools/check_vendor.py
python -m venv .venv
.venv/bin/pip install -r requirements.txt -e . -e ./sdk
.venv/bin/python tools/build_connector.py
.venv/bin/ioalab serve
```

另开终端执行 `.venv/bin/python official-services/serve.py`；真实模式需先安装并配置兼容的九问进程入口，配置见 [部署](设计/部署.md)。Windows 将 `.venv/bin/` 换为 `.venv/Scripts/`。

用户访问核心首页注册，下载可直接导入九问技能页的ioalab-skill.zip，按技能说明配置本机账号。浏览器登录后可切换个人助手和服务方工作台；有管理角色的账号还可进入全景与消息审计。核心默认8750，官方服务默认8751；两个对外地址都须能被用户访问。内部账号已预注册，但保持空闲；SDK关联本地身份后才显示助手。

## 验证与试用

```bash
python 设计/check.py
python tools/selfcheck.py
python -m pytest -q tests
```

技能独立安装验收：构建安装包后执行 `python tools/check_bootstrap.py`，会在临时干净环境安装依赖并清理，首次需要网络。

以上协议检查为显式桩测试，不调模型；`ioalab smoke` 启动独立核心/服务进程并在第三方本地Client完成合同。`python experiments/traffic.py --base http://127.0.0.1:8750 --count 3` 产生可识别实验流量。真实九问技能委托、通知与独立Swarm生产已分别实测；本地录屏与真实邮件验收入口见 [demo/README.md](demo/README.md)。Linux实机与Outlook真实授权仍按 [试用手册](docs/领导试用手册.md) 单独验收，结果记录在 [验证记录](demo/verification/README.md)。

虚拟币只是记账单位。模拟支付方只读卡按卡载金额过账，市场出争议裁定文书，不执行付款。

## 演示视频

| 视频 | 内容 |
|---|---|
| [全局视角](demo/out/01-global.mp4) · [循环播放](demo/全局视角/播放.html) | 片头、接入、赠币、发现、合同履约、支付信誉、助手消息与平台价值；随机错峰讲解动画切入单一全屏真实监控 |
| [美甲委托](demo/out/02-delegation.mp4) | 原生预选IoA技能后一句需求，七家筛选与三份真实提案，四处确认、Swarm交付、十项本地WASM验收、299付款、服务方记录及实际预约/取消 |
| [助手交互](demo/out/03-messaging.mp4) | 真实九问留言、账号白名单、冲突拒绝与改约邀请；本人接受后入历以旁白和流程图说明 |

剧本和复现入口见[录屏说明](demo/README.md)，成片证据与交互正式片剩余事项见[验证记录](demo/verification/README.md)。
