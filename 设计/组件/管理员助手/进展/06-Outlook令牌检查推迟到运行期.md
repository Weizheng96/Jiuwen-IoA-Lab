# 06：Outlook OAuth 发信令牌检查推迟到运行期

**目标行为：** 部署配置声明微软 SMTP OAuth 发信时，受管安装可以一次完成；主人在部署完成后运行 `ioalab-outlook smtp-login` 写入令牌，管理员助手的 `calendar` 工厂在 start/status 与实际发信时才核令牌，缺令牌只使会议通知 not_ready 并指向登录命令，不阻塞安装与其他业务工具。现状：部署05 之后，准备阶段装配日历工具即核令牌，缺令牌使安装任务停在助手准备，须主人以 root 写入令牌后用同一来源与输入重提安装续接。

**阻塞于：** 无

**状态：** claimed（deploy-trial）

**范围：** 管理员助手 `ioalab_tools/appointments.py` 工厂装配与 `ioalab_admin_assistant/preparation.py`；部署接口页 `Linux受管安装.md` 的令牌流程描述。

**定向测试：** admin_assistant_preparation、deployment_configs、outlook_auth

- [ ] 缺令牌时安装完成，status 报会议通知 not_ready 并给出 `ioalab-outlook smtp-login`
- [ ] 写入令牌后无需重装即可发信
