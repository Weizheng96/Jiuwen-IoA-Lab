# 脚本定位

首次调用本技能脚本前执行本页命令，只读取九问公开的路径环境变量并检查一个文件。原生skill_tool的模型正文可能不含skill_directory，不能猜路径或从账号配置所在目录推断。

```text
python -X utf8 -c "import os; from pathlib import Path; base=Path(os.environ.get('JIUWENSWARM_DATA_DIR') or (Path(os.environ.get('JIUWENSWARM_HOME') or Path.home())/'.jiuwenswarm')); p=(base/'agent/workspace/skills/__IOALAB_SKILL_NAME__/scripts/ioalab.py').expanduser().resolve(); print(str(p) if p.is_file() else 'NOT_FOUND')"
```

保存输出的实际绝对路径，后续直接复用。命令不读取账号文件，也不执行脚本。NOT_FOUND时停止并向用户询问实际导入目录；不扫描整仓、依赖缓存或用户目录，不改用旧示例、官方服务启动器或其他角色脚本。用户明确给出当前技能目录时，也只检查其scripts/ioalab.py。

平台部署预装的技能可能已绑定本机SDK。只检查上述实际脚本同目录的`deployment.local.json`是否存在，不读取绑定文件或账号配置正文。存在时按calling.md的部署绑定方式调用，不另建配置或安装环境；脚本会核验原安装、包摘要及设置定位。不存在时仍使用用户提供的原外置`--config`。绑定存在但校验失败时停止并报告，不能删除绑定、换配置或回退到普通安装。
