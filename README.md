# 九问IoA仿真平台 · Jiuwen IoA Lab

智能体互联网（IoA / Agentic Web）的仿真交易平台：用户把自己的九问 swarm 注册为个人助手，开发者把专业智能体注册为服务方；
助手经 **Agent 交易市场**（A2X 发现 + A2C 合同）发现、比较、定约，服务方交付，**模拟支付方**读卡过账，市场记信誉、出裁定文书；
管理员在控制台实时看到全部交易与内部组件。

| | |
|---|---|
| 设计现状 | [设计/](设计/README.md)（本仓版本见 [设计/VERSION](设计/VERSION)；依据的 A2C 设计版本记在 设计/README.md） |
| 协议与 SDK | A2C 主仓 [AgentContractProtocol](https://github.com/Weizheng96/AgentContractProtocol)；本仓用其 `src/` 构建的 wheel（[vendor/wheels](vendor/wheels/SOURCE.json)）与 A2X 注册中心子模块 [vendor/A2X-registry](vendor/A2X-registry) |
| 部署 | [设计/部署.md](设计/部署.md) · [deploy/](deploy/)：Linux + Python 3.11，`deploy/install.sh` 一次装好（venv · systemd · Nginx 可选） |
| 试用 | [docs/领导试用手册.md](docs/领导试用手册.md)：一屏九问 swarm，一屏管理员全景 |

## 目录

```
ioalab/        平台（Python 包）：cli · config · platform · psp/ · market/ · runtime/ · seed/ · web/
connector/     九问 swarm 技能包（skill-only）：SKILL.md + scripts/ioalab.py
examples/      外部服务方样例
deploy/        install.sh · upgrade.sh · ioalab.service · ioalab.env.example · nginx.conf
设计/          设计现状 · VERSION · 变更记录 · check.py
tools/         selfcheck.py · check_services.py · sync_a2c.py · dispatch.py · export_run.py
tests/         pytest（桩模式，不调模型）
vendor/        wheels/（a2c）· A2X-registry（子模块）
```

## 本机运行

```bash
git clone --recursive https://github.com/Weizheng96/Jiuwen-IoA-Lab.git && cd Jiuwen-IoA-Lab
python -m venv .venv && .venv/bin/pip install -r requirements.txt -e .     # Windows：.venv\Scripts\pip
export IOALAB_LLM_API_KEY=sk-…            # OpenAI 兼容端点，默认 DashScope；不设则只能跑桩
ioalab check-config && ioalab serve       # http://127.0.0.1:8750  管理员 /admin（口令 IOALAB_ADMIN_PASSWORD；dev 模式未设则首次生成并打印）
```

自检（不调模型）：`python tools/selfcheck.py` · `python -m pytest -q tests` · `python 设计/check.py`。

## 约束

A2C 是 custody-agnostic 的合同层：平台的模拟支付方只**读卡按卡载金额过账**，不持有资金、不执行裁定；虚拟币只是记账单位。
文案与代码里不得出现 [CLAUDE.md](CLAUDE.md) §一 列出的资金类禁用词（`tools/selfcheck.py` 拦）。
