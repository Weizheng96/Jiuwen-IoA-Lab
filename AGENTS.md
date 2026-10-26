# 九问IoA仿真平台（Jiuwen IoA Lab）· Codex 项目约定

本仓完全使用 Codex，开发约定入口是本文件。当前交接与待验证事项见 [docs/开发计划.md](docs/开发计划.md)。
`connector/ioalab-client/skills/` 是交付给九问 swarm 的产品技能，保持其格式和运行方式。

本仓是 A2C（Agent 2 Contract）协议的**平台产品**：账号 · Agent 交易市场（A2X 发现 + A2C 合同）· 模拟支付 · 观察与管理员控制台；用户与官方服务在独立执行边界。
协议本身（合同卡 · 签名链 · 验收器 · 交易市场规则）在 A2C 主仓 [AgentContractProtocol](https://github.com/Weizheng96/AgentContractProtocol)，
本仓以其 `src/` 构建的 wheel 为依赖（`vendor/wheels/SOURCE.json`），**不改协议、不复述协议设计**。

## 〇、唯一事实来源

| 唯一事实来源 | 记什么 | 要求 |
|---|---|---|
| [设计/](设计/README.md) | 平台设计现状（九份文档） | 精简，只写现状 |
| [设计/VERSION](设计/VERSION) | 本仓版本号，**唯一的一处**，当前格式 `v0.0.N`，由 `v0.0.1` 起步 | Codex 可自动递增最后一位；倒数第二位及更高位仅由用户明确决定，不自动进位 |
| [设计/README.md](设计/README.md) 的「依据的 A2C 设计版本」 | 本仓依据哪一版 A2C 设计 | 必须与 `vendor/wheels/SOURCE.json` 一致（`设计/check.py` 核） |

历史在 [设计/变更记录.md](设计/变更记录.md)（变更表）与 [设计/变更/](设计/变更/)（来龙去脉）。
`ioalab/` `sdk/` `official-services/` `experiments/` `connector/` `examples/` `deploy/` `personal-assistant/` `demo/` 各有 `VERSION` 记它依据的本仓版本；README、docstring、设计文档里**不得散写版本号**。

| 情形 | 做法 |
|---|---|
| 改设计或口径 | 先改 `设计/`，`设计/VERSION` +1，变更表加一行，来龙去脉写 `设计/变更/YYYY-MM-DD-v0.0.N-主题.md`，提交信息以「设计 v0.0.N：」开头 |
| 改某个产物 | 只改它；若体现了设计里没有的东西，先改设计；改完把它目录里的 `VERSION` 改成现在表现的版本 |
| 升级 A2C 依赖 | `python tools/sync_a2c.py --src <主仓>/src`（重建 wheel、改 `SOURCE.json` 与 `requirements.txt`），再改 `设计/README.md` 的依据版本 |

## 一、协议边界（不可逾越，与主仓 AGENTS.md 一致）

A2C 是 custody-agnostic 的合同层。本仓的模拟支付方**只读卡按卡载金额过账**（拟卡费 · 定金 · 每期款 · 解除余额各一笔），只认签5(i) + 签7(i)，
不持有资金、不托管、不执行裁定；虚拟币只是记账单位。裁定文书由市场出、引用卡哈希、不上卡，执行交给法律。

对外文案不得承诺以下业务能力；扫描器排除约束本身。代码与文案里**不能**出现：持有资金、托管、代收代付、账户余额、清算、结算通道、资金沉淀、按裁定强制划转（`tools/selfcheck.py` 拦）。
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
| 零模型调用 | 合同控制与script验收不调用模型；服务生产、提案拟卡与发现模型单独计量，不宣称整笔业务零模型调用 |
| 时序 | 开工（`work.start`）独立于存证订阅与验收通知，不混在同一调用栈 |
| 字符串替换 | 优先用 `apply_patch`；若走脚本，**必须断言替换成功** |
| 不入库 | `data/`（SQLite · 私钥 · 交付件 · A2X 数据集 · 回放 · 日志）· `.env` · `.venv/`；凭据绝不入库 |
| 只杀自己的进程 | 清理测试或录制进程时按 PID / 路径，不 `taskkill /IM` |

## 三、写作约定

能统一为固定格式的一律用固定格式（表格、定长字段），避免成段自然语言；专业用语，不口语化；不写与目的无关的内容。
用户名密码、口令等示例值写成明显占位（`sk-xxxxxxxx`、`change-me…`）。

Windows 本地验证使用 `.venv/Scripts/python.exe`；Linux 部署使用 `.venv/bin/python`。
设计版本仅在设计或口径发生变更时递增；迁移开发工具约定本身不要求协议依赖升级。

## 四、Git 与远端版本管理

- 每次完成一项修改并验证后，在本仓提交，说明改动目的与验证结果，再推送到其远端跟踪分支；跨仓修改分别提交与推送。
- 提交前检查 diff 与暂存清单，只纳入本次任务的文件；原有未跟踪草稿、凭据和运行期数据不随手提交。
- 推送前获取远端状态，保留已有历史；设计版本更新创建 `design-v<版本>` 标签并同步推送。
- 报告提交哈希、远端同步结果与未通过的检查。需要撤销已推送改动时优先 `git revert` 生成回退提交，不重写共享历史。

## 五、执行边界

核心不创建或运行个人助手；随部署附带的管理员助手在personal-assistant独立进程经公开SDK接入。核心不保存用户/服务私钥，不替用户取件验收、签字付款。`sdk/`直接依赖A2C，不依赖核心包；官方服务在独立进程经公开接口接入，Swarm按需运行；实验客户端同样走SDK。先改设计、版本和变更记录，再动实现；未完成的产物如实登记落后。
