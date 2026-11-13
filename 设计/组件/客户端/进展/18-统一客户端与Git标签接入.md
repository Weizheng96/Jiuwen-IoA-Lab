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

实现及本机验证完成，正在合入main、发布GitCode tag并执行Linux原生验收。发布前完整检查发现旧测试未传现行角色参数，已修正并通过；本机传输连接中止已按原断言复核通过，生产代码未改。Linux独立原生工作区的真实模型和Bash通路已通过，客户端下载、Skill导入及平台连接仍待实际执行，不记整票完成。证据见[统一客户端验证](../../../工程/实施资料/统一客户端接入.md)。
