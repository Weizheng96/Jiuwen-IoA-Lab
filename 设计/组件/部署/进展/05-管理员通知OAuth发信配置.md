# 05：部署配置的管理员通知支持 Outlook OAuth 发信

**目标行为：** 部署配置的 `admin_assistant.notification` 可声明微软 SMTP 的 OAuth 发信（`client_id`、`token_file`），安装器原样写入准备配方，管理员助手的 `calendar` 工厂即经 XOAUTH2 发送；令牌仍由主人在部署后经 `ioalab-outlook smtp-login` 生成，安装器不登录、不保存令牌。现状：`deploy/configuration.py` 的 `notification()` 只接受 AgentMail（api_key）与 SMTP 密码模式字段，微软端点在工厂里被拒绝；工厂侧已支持 `client_id`、`token_file`（管理员助手接口页“日历业务工具”与“微软授权”）。

**阻塞于：** 无

**状态：** ready

**范围：** 部署模块的配置解析与准备配方生成（`deploy/configuration.py`、`deploy/configs.py`），部署接口页的配置字段表。token_file 的位置应落在管理员助手私有目录（`<状态根>/agents/admin-assistant/`）并交由参与方账号持有。

**定向测试：** deployment_configuration、admin_assistant_preparation

- [ ] 部署配置可声明 OAuth 发信字段，与密码字段互斥，拒绝非微软端点
- [ ] 准备配方带 OAuth 字段时 meeting.request 启用；令牌缺失时工具装配 not_ready 并指向 `ioalab-outlook smtp-login`
- [ ] 安装与重装不读取、不复制、不输出令牌
