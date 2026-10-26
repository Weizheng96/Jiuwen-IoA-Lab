# 九问IoA仿真平台（Jiuwen IoA Lab）项目约定

本仓是 A2C（Agent 2 Contract）协议的**平台产品**：账号 · Agent 交易市场（A2X 发现 + A2C 合同）· 智能体运行时 · 事件总线 · 管理员控制台。
协议本身（合同卡 · 签名链 · 验收器 · 交易市场规则）在 A2C 主仓 [AgentContractProtocol](https://github.com/Weizheng96/AgentContractProtocol)，
本仓以其 `src/` 构建的 wheel 为依赖（`vendor/wheels/SOURCE.json`），**不改协议、不复述协议设计**。

## 〇、唯一事实来源

| 唯一事实来源 | 记什么 | 要求 |
|---|---|---|
| [设计/](设计/README.md) | 平台设计现状（八份文档） | 精简，只写现状 |
| [设计/VERSION](设计/VERSION) | 本仓版本号，**唯一的一处**，格式 `vX.Y.Z` | 设计有变更最后一位 +1；前两位只在用户明确指令下改 |
| [设计/README.md](设计/README.md) 的「依据的 A2C 设计版本」 | 本仓依据哪一版 A2C 设计 | 必须与 `vendor/wheels/SOURCE.json` 一致（`设计/check.py` 核） |

历史在 [设计/变更记录.md](设计/变更记录.md)（变更表）与 [设计/变更/](设计/变更/)（来龙去脉）。
`ioalab/` `connector/` `examples/` `deploy/` 各有 `VERSION` 记它依据的本仓版本；README、docstring、设计文档里**不得散写版本号**。

| 情形 | 做法 |
|---|---|
| 改设计或口径 | 先改 `设计/`，`设计/VERSION` +1，变更表加一行，来龙去脉写 `设计/变更/YYYY-MM-DD-vX.Y.Z-主题.md`，提交信息以「设计 vX.Y.Z：」开头 |
| 改某个产物 | 只改它；若体现了设计里没有的东西，先改设计；改完把它目录里的 `VERSION` 改成现在表现的版本 |
| 升级 A2C 依赖 | `python tools/sync_a2c.py --src <主仓>/src`（重建 wheel、改 `SOURCE.json` 与 `requirements.txt`），再改 `设计/README.md` 的依据版本 |

## 一、协议边界（不可逾越，与主仓 CLAUDE.md 一致）

A2C 是 custody-agnostic 的合同层。本仓的模拟支付方**只读卡按卡载金额过账**（拟卡费 · 定金 · 每期款 · 解除余额各一笔），只认签5(i) + 签7(i)，
不持有资金、不托管、不执行裁定；虚拟币只是记账单位。裁定文书由市场出、引用卡哈希、不上卡，执行交给法律。

代码与文案里**不能**出现：持有资金、托管、代收代付、账户余额、清算、结算通道、资金沉淀、按裁定强制划转（`tools/selfcheck.py` 拦）。
对用户显示用「余量」「过账」「读卡付款」。

## 二、代码约定

| 项 | 约定 |
|---|---|
| 自检 | 任何改动后：`python tools/selfcheck.py` · `python -m pytest -q tests` · `python 设计/check.py`，全绿才算完 |
| 配置 | 只从 `IOALAB_*` 环境变量读（`ioalab/config.py`），不读任何凭据文件；新配置项同时写进 `deploy/ioalab.env.example` 与 `设计/部署.md` |
| 对外地址 | 参与方 id 内嵌 `IOALAB_PUBLIC_URL`，首次运行后锁定；进程内互调经 `LoopbackTransport` 走回环 |
| 表达式求值 | 服务方的 `priceFormula` **绝不 `eval`**，走 a2c 的 `expr` 白名单 |
| 验收器 | 只在 a2c 的 `python.wasm` 沙箱里运行，只认 `ioalab://pkg/<digest>` 的 script 包，**绝不在宿主进程里 import 或 exec** |
| 成约方式 | 预批准 / 待批准 / 提案三种产出同一种合同卡；不得为提案型另写算价逻辑 |
| 零模型调用 | 只能说「**签约后**零模型调用」；提案型服务方拟卡逐单调模型，拟卡费写在模板上事前公示 |
| 时序 | 开工（`work.start`）独立于存证订阅与验收通知，不混在同一调用栈 |
| 字符串替换 | 用 Edit 工具；若走脚本，**必须断言替换成功** |
| 不入库 | `data/`（SQLite · 私钥 · 交付件 · A2X 数据集 · 回放 · 日志）· `.env` · `.venv/`；凭据绝不入库 |
| 只杀自己的进程 | 清理测试或录制进程时按 PID / 路径，不 `taskkill /IM` |

## 三、写作约定

能统一为固定格式的一律用固定格式（表格、定长字段），避免成段自然语言；专业用语，不口语化；不写与目的无关的内容。
用户名密码、口令等示例值写成明显占位（`sk-xxxxxxxx`、`change-me…`）。
