# 验证、资源安装与公开发布

创建资产后的检查与交接读本页。使用项目Python；下列`python`在Windows对应`.venv/Scripts/python.exe`，Linux对应`.venv/bin/python`。命令在仓库根执行，按[testing](../../testing/SKILL.md)串行选范围、用新报告目录保留日志与真实退出码；不是要求每次全跑。

## 静态与定向入口

| 变更 | 已存在的生成/检查入口与证明范围 |
|---|---|
| 仅技能/说明 | `python .agents/skills/develop/scripts/check.py --report-dir temp/checks/official-docs-<新标识>`；A与技能结构校验，不跑模型或业务 |
| 官方目录资产 | `python tools/build_seed.py`生成唯一初始化清单，再`python tools/build_seed.py --check`核同源；不访问平台、不创建账号 |
| 单服务样本/合同/包 | `python tools/check_services.py <实际service-id>`核原A2C模板/卡、验收包及样本WASM结果；这是隔离样本，不是模型生产或真实订单 |
| 资产、账号与模板构造 | 总入口加`--tests tests/test_official_resources.py tests/test_seed.py`；有数量变化同步真实目录计数和唯一映射断言，不删除归属/重复账号拒绝覆盖 |
| 生产或新判据 | 总入口加`--tests <本业务专属测试>`；编程现有`tests/test_programming_services.py`、`tests/test_nail_scene.py`、`tests/test_resident_nail.py`，按实际影响选取 |
| 准备、公示、重启与未知恢复 | `tests/test_official_deployment.py`、`tests/test_official_service_runtime.py`、`tests/test_official_lifecycle.py`；SDK授权改动补`tests/test_publication_authorizations.py`及受影响发布/接单测试 |
| 资源/安装结构 | `tests/test_package_installation.py`构建真实wheel，离开checkout安装并读取资源/版本；新资源类型须补安装后读取断言 |

B示例：`python .agents/skills/develop/scripts/check.py --tests tests/test_official_resources.py tests/test_seed.py --report-dir temp/checks/official-assets-<新标识>`。尖括号项应换为本次实际值，不原样运行。阶段完整检查与部署/模型专项按testing另行触发；已通过且输入未变的结果复用。

### 覆盖选择

- 成功：原协议接受模板与规格，发布包摘要可核，真实交付满足声明判据。
- 拒绝：必要材料缺失、载体/必要功能不支持、规格越界、损坏源码、伪造包引用、交付路径越界；业务类别选择相关反例，不能给非编程服务套源码测试。
- 归属与恢复：同类型服务复用所属账号但各自身份分离；错profile/账号不能借包或业务；原上传/发布丢回执恢复同一操作，拒绝/暂停/未知结果不被重启覆盖。
- 样件和真实生产分开：sample只作可复现实验，不装入执行配置冒充交付。保留美甲已有容量、取消、幂等回归；新增非美甲/非编程服务围绕自己的语义设正反例，例如翻译服务核原句含义与格式边界，不强制跑美甲全流程。

验收器及交付核心仅在A2C `python.wasm`执行，先核包摘要；不在宿主import/exec交付代码。源码/结构、WASM核心行为、客户端实际行为、构建、安装部署分别记录。构建要真实退出码与产物hash，部署要目标环境实际结果；前一层不推出后一层。需运行客户端或构建交付源码时使用授权的独立隔离环境专项，不用SDK或宿主直接执行绕过沙箱。

## 发行与安装证明

实际资产变更核所属`src/official/VERSION`及现行设计符合范围，按[develop交付](../../develop/references/delivery.md)同步真实受影响固定版本与锁输入；本技能说明修改不升级产品或设计版本。资源构造保持无副作用，不读凭据、注册或发起模型。

复用现工具构建：`python tools/build_packages.py --out temp/official-wheel-<新标识> src/official`。wheel构建本身不证明包资源完整；还要用现安装测试在checkout外的隔离环境安装，核`importlib.resources`可读取新领域/验收/业务资源、`account_of`归属及导入路径来自安装包。涉及最终发行时依交付参考走`tools/build_deployment.py`与目标锁，不新建第二套wheel/清单生成脚本。记录源码SHA、源资源与wheel摘要、版本、锁输入和目标环境。

## 公开SDK路径与授权门槛

资产作者应知道下面的真实消费顺序，不把这张表当作执行外部业务的授权。操作参数从[服务方SDK](../../../../设计/组件/客户端/服务方端/接口.md)与产品[发布流程](../../../../src/client/skills/provider/references/publication.md)读取，不自行拼JSON签名或编造回执。

| 阶段 | 现有入口及必须保存的事实 |
|---|---|
| 包引用 | 所属profile经`provider.verifier.publish`上传实际源码/reads/kind，取得真实id/uri/digest；失败/未知恢复原operationId |
| 构造 | `ioalab_services.prepare`消费真实引用与平台支付声明，输出未签service/execution；`provider.preview`核完整公示与实际类别；构造成功不等于已发布 |
| 官方准备 | `ioalab-official prepare --config <本机显式配置>`复用上述路径，准备身份/资源但不公示或运行模型；会连接平台和上传资源，不能为写文档自动运行 |
| 初始公示授权 | `ioalab-official authorize --config <配置> --admin-username <部署管理员> --admin-password-env <受限变量名>`只在用户授权的部署流程使用；不把密码写日志或命令参数值，不让serve持管理员权限 |
| 公示与运行 | `ioalab-official serve --config <配置>`由原版宿主探测与SDK连接核就绪后依授权`provider.publish`；分别核签认、公示/索引、在线、能力及接单状态。普通服务仍用自身可信确认，不默认取得部署授权 |
| 查询 | `ioalab-official status --config <配置>`及所属`provider.status`核实际状态，保存原回执与当前观察的区别；不以启动器返回、历史模板或模型文字当成交付验收 |

官方生命周期在固定发布授权范围内按现30天模板/7天余量/60秒检查策略续发，保存原参数及operationId，只刷新允许的模板有效期并保留原合同/接单状态；它不赋予标准SDK任意自动续约、替用户批准或换业务条款的能力。配置/授权/条款失配停下核原记录，不清状态重发。策略若变化以官方实例、注册中心及SDK职责页为准。

交接最小结果：文件清单与diff、账号/服务映射和生成清单、安装资源/版本摘要、各层真实检查及失败、是否实际公示和当前可接单状态、模型生产/构建/部署等未验证范围。仅方法或资产开发任务应明确这些外部阶段未执行；不为填满报告启动真实业务。
