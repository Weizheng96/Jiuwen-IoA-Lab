# 部署 · 内部CA与域名HTTPS

部署独占CA/服务器私钥、服务器证书签发续期、Nginx绑定及公共证书导出；客户端信任语义只在[平台TLS信任](../../客户端/共用运行时/接口/平台TLS信任.md)定义。当前实施见[部署07](../进展/07-域名HTTPS与公共地址切换.md)，下列维护入口为待实现设计。

## 证书与数据归属

本项目采用同一内部CA：服务器保留CA证书和受限私钥，以它签发包含`ioalab.openjiuwen.com` DNS SAN、用途为serverAuth的独立叶证书。客户端只携带该CA公共证书。继续保持原局域网来源策略；签发及续期使用本机CA，无需ACME外部验证，也不要求域名方维护TXT记录。

CA实际证书/私钥路径、指纹和叶证书引用由部署专用受限`配置根/tls/authority.json`登记，format为`ioalab.internal-ca/1`，字段为ca_certificate、ca_private_key、ca_sha256、server_certificate、server_private_key。续期域名从唯一平台JSON的runtime.public_url取得，不在authority.json复制公共地址；首次迁移可显式准备目标域名证书，不修改平台运行参数。引用均为核归属、非符号链接的绝对普通文件；私钥仅部署维护身份可读，Nginx按原受限服务模型读取叶私钥。CA有效期、BasicConstraints CA、签名用途及证书/私钥匹配都须检查。

当前服务器已有内部CA时登记并复用其实际原件与指纹，不因更新、更换域名或test-reset创建另一套CA。首次部署没有CA时才由部署者显式初始化。证书、CA和登记文件属于必需私有部署配置，保留于内部测试数据清理之外；公共CA证书可随客户端发布，私钥与运行配置不可入库。CA过期或私钥不匹配时拒绝签发，不能现场静默替换CA。

## 维护接口

统一由`python -m deploy.tls`提供维护入口，与业务安装/测试重置分离；使用系统OpenSSL与现行Nginx，不修改已安装wheel或运行环境。

| 接口 | 输入与结果 | 拒绝与恢复 | 测试 |
|---|---|---|---|
| `status --state` | 核部署归属和authority.json，返回CA/叶证书指纹、SAN、到期时间、续期单元状态；不输出密钥 | 缺登记只报未配置，不创建CA | deployment_configs、deployment_entry |
| `issue --state --authority <JSON> --hostname <域名>` | 显式登记现有CA，按明确目标hostname提前签叶证书，核CA/私钥与路径；输出脱敏回执，不修改平台公共地址 | 非法hostname、归属冲突或CA无效拒绝；已有合格叶证书复用，不更换CA | deployment_configs、deployment_entry |
| `renew --state` | 叶证书有效期90天，剩余30天以内签发并替换；未达窗口返回changed=false；同一维护锁串行 | CA剩余寿命不足以覆盖新叶证书时拒绝并报告换CA；保留原证书和失败日志 | deployment_configs、deployment_lifecycle |
| `export-ca --state --origin <HTTPS Origin> --output <目录>` | 只导出已核CA公共PEM、DER指纹和明确目标Origin，供客户端受审发布形成信任清单 | 输出非空/越界或证书校验失败拒绝，不导出密钥，不自动发布客户端 | deployment_configs、package_installation |

issue/export的明确目标仅用于签发与发布准备，不成为另一份平台地址配置；切换验收与后续renew必须核SAN/Origin与唯一平台JSON一致。服务器自身的公共HTTPS就绪探针仍用系统信任链，部署者在服务器系统信任库安装或复用同一CA公共证书；这不扩展到客户端系统信任库。

签发候选在受限临时目录内核证书链、SAN、有效期、serverAuth和私钥匹配，再以完整证书集合切换Nginx引用；执行nginx -t、reload及带SNI的实际握手核对指纹。失败沿原维护回执恢复，不能凭已写文件报告成功；只允许恢复本次受管证书引用，不改应用数据或放宽TLS验证。

续期由服务器自己的systemd timer每日检查，调用同一renew入口，成功部署后重载Nginx并核实际握手。定时任务绑定受管、固定的维护来源，应用更新时同步来源引用及归属，不能依赖开发机SSH或被清理的temp/旧源码。测试重置和单版本清理保留CA与定时任务；到期前的实际签发、reload、握手和重复执行均须专项验证，timer存在不等于续期通过。

## 公共地址切换

Nginx按精确域名绑定新的叶证书，根页面、API、/official/和/ws/保留现有反代及来源覆盖。HTTP跳转只在原允许的来源内配置，不为证书验证扩大80/443来源。

域名证书签发成功后，公共地址切换仍遵守平台首次初始化后锁定的身份规则。当前测试服务器先正常stop并核空控制组与执行锁，再显式修改唯一平台JSON，通过同一test-reset重新初始化内部身份和角色配置；实施前须验证受管入口确实按新公共地址重建所有消费者，拒绝时报告缺口，不直接改数据库、地址锁定标记、任务记录或已生成角色配置来绕过。

变更后核平台publicUrl、官方/管理员助手SDK配置与身份地址、账号注册/登录及HTTPS/WSS一致，旧IP配置不自动映射或重试新域名。浏览器不读取客户端包内CA；网页浏览需要用户或组织另行安装CA公共证书，不由SDK改写系统/浏览器信任。

## 验收条件

| 场景 | 必须证明 | 测试 |
|---|---|---|
| 受管配置 | 复用原CA，CA/叶私钥不入客户端或状态输出；来源策略及私有配置保留 | deployment_configs、deployment_test_server |
| 签发与恢复 | 候选证书合格后切换；错误SAN/私钥/CA过期拒绝；失败不破坏仍有效的服务证书 | deployment_configs、deployment_lifecycle |
| 实际部署 | 无系统CA信任的客户端依发行资源完成域名HTTPS登录与WSS；正式六单元、官方与助手分别就绪；业务专项独立记录 | installed_deployment_probe、sdk_tls |
| 续期与保留 | 实机验证续期窗口、重复执行、Nginx重载、实际指纹及test-reset后CA不变 | deployment_lifecycle、deployment_test_server |
