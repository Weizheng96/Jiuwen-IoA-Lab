# 01：微软日历授权与 Outlook OAuth 发信可配置可使用

**目标行为：** 主人能经本机命令完成 Graph 日历的设备码登录，选用 `backend=graph` 时工厂取得有效令牌；通知选用 Outlook SMTP 时可配置 XOAUTH2 并实际发信。现状：`calendar.py` 有 `MicrosoftDeviceAuth.login` 与 `OutlookSMTPAuth`，但生产代码没有任何调用方，只有测试使用；`appointments.configure` 不接受 OAuth 选项且构造 `SMTPNotification` 时不传 `oauth`，微软 SMTP 端点因此总被拒绝；其错误信息所说的 `IOALAB_SMTP_AUTH=oauth2` 与 `smtp-login` 命令并不存在。`backend=graph` 要求的 `token_file` 也无入口生成。

**阻塞于：** 无

**状态：** ready

**范围：** 管理员助手。需读[日历业务工具](../接口.md#日历业务工具)与[会议预约](../../../流程/场景/会议预约.md)的“可选 Graph 与 Outlook SMTP”。部署配方的通知字段（`deploy/configuration.py` 的 `notification`）若需支持 OAuth，另在部署模块登记。

**定向测试：** outlook_auth、business_tools、assistant

- [ ] 主人可经本机命令完成 Graph 登录与 SMTP 登录，令牌分别缓存，错误账号不覆盖有效授权
- [ ] `calendar` 工厂选项可声明 OAuth 发信，构造出的 `SMTPNotification` 经 XOAUTH2 发送
- [ ] `SMTPNotification` 的错误信息只指向实际存在的命令与配置
