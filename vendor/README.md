# 随仓部署依赖

[SOURCES.json](SOURCES.json)是机器可核对的来源清单：原仓地址、固定提交、收录范围、许可证及文件摘要。来源地址用于追溯；安装使用本仓文件。

| 组件 | 内容 | 部署入口 |
|---|---|---|
| A2X注册中心 | [普通源码快照](A2X-registry/)、原许可证、267份文件摘要 | 根requirements.txt本地安装 |
| A2C | SDK源码归档、已验证wheel及声明来源 | 根requirements.txt及技能安装包 |
| 九问Swarm | 原始固定提交源码归档、包含原网页的预构建wheel、许可证及NOTICE | personal-assistant/install_swarm.py |
| A4P、intelli-router、openJiuwen core | 固定提交的组件源码归档、本地wheel与许可证 | 九问完整锁文件显式安装 |
| 九问Process SDK | 本地wheel；源码位于九问归档sdks/python | 根requirements.txt |

源码归档在[sources/](sources/)，安装包在[wheels/](wheels/)，原许可和声明在[licenses/](licenses/)。第三方原始README中的在线安装示例仅供追溯；实际部署使用本仓根README和部署脚本。A2X的可选基准数据库不属于平台依赖，本平台分类树和预置服务由本仓生成。

服务器只需同步本仓，普通pip依赖从部署方可达镜像安装；不执行子模块更新、远端Git依赖解析或网页编译。仅禁止GitHub与完全离线不同；全离线包需按目标系统、Python和CPU架构准备。通过复制目录升级时用`bash deploy/upgrade.sh --local`，从GitCode克隆时使用该仓配置的origin。

维护者更新依赖：先更新来源清单中的固定提交，再在联网开发机准备对应的干净参考仓，将组件id到本地路径的映射写入不入库的JSON，运行`python tools/vendor_dependencies.py --checkouts <映射JSON> --work <新构建目录>`。该工具从git archive归档，保留原许可，用原命令构建九问网页及可移植wheel；服务器不运行该工具。A2C先运行tools/sync_a2c.py，再按SOURCE.json更新本清单并重新归档其源码；可用--only a2c仅重建该组件。A2X快照按清单复制并核对，不在第三方源码内加入平台业务修改。

检查：`python tools/check_vendor.py`、`python personal-assistant/install_swarm.py --verify-only`。摘要不匹配、安装包缺网页或锁文件含远端VCS项均拒绝通过。新部署的专用九问工作区关闭自动更新；已有配置和技能账号保留。

实测结果见[部署依赖验证](../deploy/verification/dependencies.json)。
