# 九问IoA仿真平台 · Jiuwen IoA Lab

用于九问演示、IoA 技术实验和用户 playground。用户用自己的九问 Swarm 发现专业服务，按 A2C 合同委托、在本地验收并付款；管理员在另一屏观察交易。

| 执行方 | 职责 |
|---|---|
| 平台核心 | 账号、A2X发现、A2C市场、模拟支付、事件与管理员界面；没有个人助手运行时 |
| 用户自己的九问 | 个人助手技能调用本地 IoA SDK，保管密钥、签合同、取件、沙箱验收 |
| 独立官方服务 | 8个领域48个服务；常驻协议端点，收到业务任务才启动九问Swarm；与第三方走相同公开接入 |
| 独立实验客户端 | 经同一SDK生成带experiment标记的背景交易，不在核心内运行 |

设计现状见 [设计/](设计/README.md)，版本见 [设计/VERSION](设计/VERSION)，实现差异与未验收项见 [变更记录](设计/变更记录.md)。开发约定见 [AGENTS.md](AGENTS.md)。A2C协议来自主仓SDK wheel，来源见 [SOURCE.json](vendor/wheels/SOURCE.json)，不在平台重复实现合同规则。

## 本地启动

Python 3.11，首次克隆包含 A2X 子模块：

```bash
git clone --recursive https://github.com/Weizheng96/Jiuwen-IoA-Lab.git
cd Jiuwen-IoA-Lab
python -m venv .venv
.venv/bin/pip install -r requirements.txt -e . -e ./sdk
.venv/bin/python tools/build_connector.py
.venv/bin/ioalab serve
```

另开终端执行 `.venv/bin/python official-services/serve.py`；真实模式需先安装并配置兼容的九问进程入口，配置见 [部署](设计/部署.md)。Windows 将 `.venv/bin/` 换为 `.venv/Scripts/`。

用户访问核心首页注册，下载连接器、导入自己的九问，填平台地址和账号。核心默认8750，官方服务默认8751；两个对外地址都须能被用户访问。内部账号已预注册，但保持空闲；SDK关联本地身份后才显示助手。

## 验证与试用

```bash
python 设计/check.py
python tools/selfcheck.py
python -m pytest -q tests
```

技能独立安装验收：构建安装包后执行 `python tools/check_bootstrap.py`，会在临时干净环境安装依赖并清理，首次需要网络。

以上协议检查为显式桩测试，不调模型；`ioalab smoke` 启动独立核心/服务进程并在第三方本地Client完成合同。`python experiments/traffic.py --base http://127.0.0.1:8750 --count 3` 产生可识别实验流量。真实Swarm和Linux部署需要另行按 [试用手册](docs/领导试用手册.md) 验收，不用桩通过代替。

虚拟币只是记账单位。模拟支付方只读卡按卡载金额过账，市场出争议裁定文书，不执行付款。
