# 07：域名HTTPS与公共地址切换

**状态：** claimed；域名切换及候选实机验收完成，正式发布与到期换证专项待验

**阻塞于：** 无；不为公共CA验证扩大原局域网来源策略。

**目标：** 以同一内部CA维护https://ioalab.openjiuwen.com，应用更新保留CA，正式六单元及客户端共用信任。

**已完成：** v174固定develop候选已部署，域名叶签发、精确SAN、HTTPS登录与WSS、HTTP308、六单元及52项官方服务、管理员助手连接均通过；test-reset来源接续保留新初始化身份、原CA/私钥/模型及安装input。原失败任务日志保留，旧来源及验证副本清理，当前只留一个集合/发行。

**验收：**

- [x] 域名、DNS及局域网验证条件确认；复用原CA，私钥留服务器
- [x] 维护入口真实签发、Nginx域名绑定、HTTP308与实际SNI指纹
- [x] 平台publicUrl与受管官方/助手配置一致，真实登录、WSS及全部正式组件就绪
- [x] 实际安装SDK零系统根环境下使用与服务器一致的包内CA；真实wheel离仓安装
- [x] test-reset与新来源接续后CA/私钥、模型及必需私有输入保留，仅留最新部署
- [x] 续期timer实际启用，未到期重复执行changed=false、现网reload/SNI与Linux真实OpenSSL/systemd校验
- [ ] 现网到期窗口自动换叶证书后客户端接续（当前新证书未到续期窗口）
- [ ] 客户端19正式GitCode受审发布及外部干净原生接入
- [x] 教程、deployment Skill、实施资料及工程进展同步，开发检查及develop推送

**证据：** [当前部署事实](../../../工程/实施资料/部署发行.md#域名与内部ca实机验收2026-11-13)。本机ca-resume-fast174为1732 passed、34 skipped，Linux隔离TLS/接续39 passed；最终离仓wheel通过。客户端18曾观察到两项FileNotFoundError，本轮复现并定位为Windows进程未提供OpenSSL CLI，保留失败；本次显式供给已有OpenSSL后5 passed、2 skipped，Linux专项全通过。缺工具的fixture明确skip，不再以宽泛rejected掩盖缺依赖。未合入main/GitCode，未执行公共CA或CA根轮换。

**最终完整检查：** 最终完整门禁temp/checks/ca-full174-final为2229 passed、53 skipped、14 subtests passed，pytest与selfcheck均退出0；53项按环境/显式专项条件跳过，不计原生、真实模型或Linux专项通过。受测运行源码及测试在整轮中未变，事实登记后另跑轻量检查。
