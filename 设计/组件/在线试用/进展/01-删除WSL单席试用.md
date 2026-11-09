# 01：删除 WSL 单席试用

**目标行为：** `ioalab-trial` 只接受 `deployment_profile=linux-server`（容量 3），其他取值在打开或创建控制库、构造沙箱前明确拒绝；仓库中不再有 Windows 经 WSL 启动单席试用的入口。现状：`src/trial/ioalab_trial/profile.py` 仍有 `windows-wsl`（容量 1）并按 WSL2 内核放行，`__main__.py` 的 `--deployment-profile` 接受 `windows-wsl`，`deploy/trial/start_windows.py` 以该 profile 启动，`deploy/trial/README.md` 与 `tools/verification/trial_resources.py` 仍描述并采集单席阶段；与[试用规则](../接口/试用规则.md#模块与配置)“原生Windows或WSL不能以此入口提供在线席位”不符（设计 v0.0.130 起由可信本机体验取代）。

**阻塞于：** 无

**状态：** ready

**范围：** 在线试用（`profile.py`、`__main__.py`、`deploy/trial/start_windows.py`、`deploy/trial/README.md`）；开发工具 `tools/verification/trial_resources.py`。需读[试用规则](../接口/试用规则.md)与本模块[实现](../实现.md#入口)。既有 windows-wsl 控制库按原规则先沿原部署清退，不补记或改写 profile。

**定向测试：** trial_profiles、trial_resources

- [ ] `capacity`、`validate_environment` 与 `--deployment-profile` 只认 linux-server；windows-wsl 配置被拒且不创建控制库
- [ ] 删除 `deploy/trial/start_windows.py` 及其测试，入口表与接口页同步
- [ ] 部署说明与资源采集脚本不再提供单席阶段
