# 12：SDK 命令入口迁出共用运行时

**目标行为：** `ioalab-access` 与 SDK 模块命令的程序入口归 [SDK装配](../SDK装配/接口.md)，共用运行时不再导入 SDK装配，依赖图里没有 共用运行时 → SDK装配 的边。现状：`runtime/__main__.py` 已改为由 `run(argv,*,modules,validate,service_fields)` 接收注入的角色、服务方校验和可编辑字段。但为了让 `python -m ioalab_sdk.runtime` 和 `ioalab_sdk.runtime.__main__:main` 保持不变，`main` 仍以函数内导入委托 `ioalab_sdk.access.main`，这是剩下的唯一反向边。

**阻塞于：** 无

**状态：** ready

**范围：** 共用运行时 `runtime/__main__.py`、SDK装配 `access.py`、`src/access/sdk/pyproject.toml`；消费者为 `_shared/ioalab.py`、`deploy/units.py`、`tools/smoke.py`、`tools/verification/verify_deployment.py`、`tools/check_bootstrap.py`、`src/access/sdk/README.md` 与测试。按展开、迁移、收口三步做：

1. 展开：新增 SDK装配 的模块命令，例如 `python -m ioalab_sdk.access`；控制台脚本 `ioalab-access` 指向 `ioalab_sdk.access:main`；`start` 的默认子进程命令同步改。
2. 迁移：上述消费者切换到新命令。
3. 收口：已装技能与受管部署随一次发布升级之后，删除 `runtime/__main__.py` 的 `main` 及 `python -m ioalab_sdk.runtime`。

需读[共用运行时 接口](../共用运行时/接口.md#本机生命周期与调用)、[SDK装配 接口](../SDK装配/接口.md)与[部署 · 安装与升级](../../部署/接口.md)。

**定向测试：** access_products、configuration_boundary、configuration_edit、installation_identity、resident_runtime、package_installation、cli_encoding、authentication_recovery

- [ ] `src/access/sdk/ioalab_sdk/runtime/` 中没有对 `access` 的导入，共用运行时 依赖页删去 SDK装配 行
- [ ] 升级说明写明旧技能与受管部署怎样换到新命令，命令输出与退出码不变
