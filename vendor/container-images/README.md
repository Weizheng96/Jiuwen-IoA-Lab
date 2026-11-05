# 随仓官方容器基础镜像

当前收录Docker Official Image的Python 3.11 slim-bookworm、linux/amd64原始docker save归档，来源、RepoDigest、ImageID及压缩和解压摘要见[清单](python-3.11-slim-bookworm.json)。压缩仅做确定性gzip封装，原镜像层不修改；上游及发行版许可证/版权文件保留在原镜像内。

在仓库根目录运行 `python tools/container_images.py verify` 检查所有原件，然后运行 `python tools/container_images.py load` 导入本机Docker并复核真实镜像身份。两个入口均不执行pull。构建前复核已加载标签的实际镜像身份，使用本地已核标签并设置--pull=false；裸sha256 ID不作为FROM镜像名；普通依赖与构建流程见[试用部署](../../deploy/trial/README.md)。

更新时同时维护来源清单、原归档、摘要和验证证据；不能以本机已有同名标签代替校验，也不将模型配置、账号、业务卷或运行日志放入镜像资产。
