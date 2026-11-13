# 18：统一客户端与Git标签接入

**目标行为：** 一条GitCode引导安装单一客户端，用户通过两个角色Skill选择操作身份，复用账号和常驻实例。

**阻塞于：** 无

**状态：** claimed

**范围：** src/client目录、pip VCS构建、bootstrap、两角色Skill、README_AGENT.md、默认平台地址、部署构建及设计路径消费者。

**定向测试：** access_products、background_installation、package_installation、installation_identity、access_discovery。

- [x] 客户端源码与所有现行路径消费者迁入src/client
- [x] 默认平台https://120.46.80.150、显式配置优先、连接失败后才请求地址的SDK定向验证
- [x] README_AGENT.md大写及现行引用同步
- [x] 单一wheel及Git tag引导的正常、拒绝和已有目录保护验证
- [x] 两个轻量Skill复用同一客户端，原身份与权限边界回归
- [x] 本机定向与快速层、真实构建和隔离安装验证
- [ ] GitCode包含新客户端的发布tag可安装及Linux原生九问完整接入验收

开发中，与当前设计有偏离：main171已发布GitHub/GitCode，但首轮原生安装受宿主复制型venv的基础Python版本影响失败；模型还删除失败目录重试，已正常停止。bootstrap已改为核验并使用版本化CPython3.11创建环境，创建后再核版本；README补足失败停止/保留步骤。23项定向通过，待发布修复并在新原生会话验证安装、Skill导入、账号及两角色连接，不记整票完成。证据见[统一客户端验证](../../../工程/实施资料/统一客户端接入.md)。
