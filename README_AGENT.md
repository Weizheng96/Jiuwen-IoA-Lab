# 帮用户接入 IoA Lab

复用用户已有九问Swarm。IoA只有一个客户端，个人助手和服务方由两个角色Skill区分；不按角色重复安装客户端。

1. 使用GitCode中已发布、包含`src/client/pyproject.toml`的客户端tag。标签形如`design-v0.0.N`，按数字N核对版本。下载同一tag的[bootstrap.py](https://api.gitcode.com/api/v5/repos/WeiZheng96/jiuwen-IoA-lab/raw/src/client/bootstrap.py?ref=TAG)到本机独立工作目录，将URL中的TAG替换为实际tag。原文不可读、tag不存在或版本尚未发布时报告实际原因，不猜其他地址，不使用平台网站下载客户端，不关闭TLS校验。
2. 核本机Git和CPython3.11，在终端执行下载的脚本（Windows使用已核的Python3.11；Linux用python3.11）：

   ```text
   python bootstrap.py --tag <实际已发布tag>
   ```

   默认使用持久客户端目录`~/.ioalab/client`和Skill导出目录`~/.ioalab/role-skills`。需要其他持久目录时才指定`--install-dir`和`--output`。宿主终端有短时限时，用其原生后台任务能力执行并保留任务引用；观察超时只查询原任务，不重复安装。

   脚本通过pip从GitCode tag解析出的固定commit安装`src/client`，检查依赖、安装文件摘要及来源，导出个人助手和服务方两个轻量Skill。普通第三方依赖与构建工具使用本机配置的可信包源；客户端无需发布到PyPI。安装失败时停止本次接入，报告脱敏错误并保留原目录及原任务引用，等待安装程序修复；不自行删除目录、修改脚本或改写安装步骤。成功不代表账号或平台连接已就绪。
3. 按真实回执中的绝对文件路径，用九问原生`send_file_to_user`发送两个Skill ZIP到`web`（`abs_file_path_list`为绝对路径列表的JSON字符串，`target_channels`为`["web"]`），核交付成功。引导用户在原生“我的技能→创建”上传或保存文件卡片，再在会话中选择“个人助手”或“服务方”Skill；实际用`skill_tool`读取所选Skill及`references/setup.md`，继续其账号、宿主、启动与状态流程。账号秘密只在本机账号页填写，角色切换复用原账号、配置、身份与常驻客户端。

已有客户端也可直接在其独立环境执行等价pip安装：

```text
python -m pip install "ioalab-client @ git+https://gitcode.com/WeiZheng96/jiuwen-IoA-lab.git@<实际已发布tag>#subdirectory=src/client"
python -m ioalab_client skills --output <角色Skill导出目录>
```

首次接入推荐bootstrap，以便自动核验来源并保留可恢复的安装归属。升级前按Skill的安全停止流程处理原实例，不在活动环境中直接覆盖安装。
