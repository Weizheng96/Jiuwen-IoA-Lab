# 专项实验验证

当前保留原版网页通知、安装发行及 Linux/systemd 验证脚本。它们须显式运行，使用独立输出目录；脚本存在不表示对应专项已通过，结果以本次报告和[验证记录](../../设计/工程/实施资料/部署发行.md#历史验证资料归档)的证明范围为准。

- [native_web.py](native_web.py)：从指定的原版九问工作区准备隔离网页实例，保留来源配置摘要和本次进程信息。
- [verify_web_receipt.py](verify_web_receipt.py)：使用已确认的原通知 ID 核验同件回执、冲突拒绝及原生唯一历史记录。
- [verify_deployment.py](verify_deployment.py)：构建并安装 Linux 发行，验证激活入口、HTTP/WS/IPC、原身份重启、显式登录恢复及锁释放，再调用管理员预设探针。
- [verify_admin_assistant.py](verify_admin_assistant.py)：用已安装的参与方 Python 和 Linux root，在新目录验证固定管理员预设的准备、公示、配置权限及只读重放；不启动模型或发送邮件。
- [verify_default_deployment.py](verify_default_deployment.py)：在可丢弃的 Linux/systemd 主机验证默认安装和升级、排空与权限边界。

旧全景自动业务驱动 `traffic.py`、`messages.py`、`network.py` 已归入[历史备份](../../历史备份/旧执行链/experiments)，不再提供当前运行命令。原片和历史证据保留原记录；归档依据及有效断言迁移见[测试迁移索引](../../历史备份/旧执行链/测试迁移索引.json)。

仅供旧驱动使用的[账号占位样例](../../历史备份/旧执行链/experiments/accounts.example.json)、[需求输入](../../历史备份/旧执行链/experiments/needs)和[素材](../../历史备份/旧执行链/experiments/materials)随驱动保留，不作为当前 SDK 的配置入口。

## 原版网页通知验证

`native_web.py`复用已安装的原版程序，在新的私有目录和独立端口建立实验实例。显式指定本任务可复用的模型配置来源；不覆盖已有工作区，不安装依赖到九问环境。原配置摘要不变才报告就绪；输出保存本次PID和日志位置，不自动重启另一实例。

```powershell
.venv/Scripts/python.exe tools/verification/native_web.py --python .swarm-venv/Scripts/python.exe --source-workspace .swarm-runtime --output data/native-web-check
$env:IOALAB_NATIVE_WEB_CONFIG="D:/project/Jiuwen-IoA-Lab/data/native-web-check/settings.local.json"
$env:IOALAB_NATIVE_WEB_REPORT="D:/project/Jiuwen-IoA-Lab/data/native-web-check/delivery.json"
.venv/Scripts/python.exe -m pytest -q tests/test_native_web_delivery.py
.venv/Scripts/python.exe tools/verification/verify_web_receipt.py --config data/native-web-check/settings.local.json --report data/native-web-check/delivery.json
```

示例绝对路径须替换为自己的输出目录；端口占用时可选--port-offset。原生扩展及历史是真实调用，集成测试的模型轮次和确认驱动是明确替身；实际网页另行检查，证明范围见[回执](../../设计/工程/实施资料/部署发行.md#历史验证资料归档)和[截图](../../设计/工程/实施资料/配图/native-web-notification.jpg)。重放核验只使用已确认的原ID，核对同件原回执、异内容/库标识冲突及唯一历史记录。私人配置、数据库和日志仍留data，不提交。

实验进程仅由本次processes.json登记的PID标识；结束前核对命令行确属该输出目录，再停止本实验进程，不按python等通用名字批量关闭。原有九问保持运行，原生菜单/完整用户配置及影片仍需另外验收。

## Linux安装发行与固定管理员预设验证

`verify_deployment.py`要求 Linux x86_64 CPython 3.11，在新的专用输出目录构建、安装并激活发行。它启动真实核心和常驻 SDK，经 HTTP/WS/IPC 检查身份重启保持、显式登录恢复、重复恢复不重复记账及停止后的锁释放；随后在实验核心中显式初始化账号，用已安装的参与方 Python 运行 `verify_admin_assistant.py`。

管理员探针仅在新目录中以 Linux root 准备固定预设，复用原版公开工作区初始化；不改系统账号、服务或已有九问。它检查真实 HTTP 公示、root 归属、危险配置权限拒绝、重复准备与状态只读及 SDK 锁释放。整个专项不调用模型、不发送邮件；不代表助手已上线、真实业务、默认安装或 systemd 已通过。

```bash
python3.11 tools/verification/verify_deployment.py --output /tmp/ioalab-installed-release-check
```

管理员探针需要 Linux root；从非 root 启动发行验证时，脚本通过 `sudo -n` 调用该探针，须预先具备相应权限。不要对既有平台目录运行。


## 默认安装与升级的systemd验证

`verify_default_deployment.py`仅用于全新、可丢弃的Linux root/systemd/cgroup v2主机，不在已有平台或用户九问环境运行。它调用默认安装/升级命令，核独立任务、真实角色身份、权限、8账号50服务、原版预构建网页及管理员SDK；在私有源码副本中产生另一发行，检查实际排空、备份、单集合切换、原身份/暂停/开机设置与手动停止的保留。已有账号、组、单位或输出目录一律拒绝接管，不删除数据。

```bash
sudo python3.11 tools/verification/verify_default_deployment.py --isolated-runner --output /tmp/ioalab-default-check --index https://pypi.org/simple
```

脚本提供只读模型目录夹具，任何模型业务调用都会失败；不发邮件、不代用户确认、不证明真实业务或影片。CI 的部署专项独立触发，结果以实际任务报告为准。公开产物仅`report.json`；初始配置、原始日志、身份库和备份不上传。观察超时保留原安装任务，不重启或强杀；成功后有序停止本次运行单位，核实际控制组为空且SDK锁已释放。
