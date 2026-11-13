# 脚本定位

Codex先使用本次解压或加载的实际Skill目录，只检查其scripts/ioalab.py；不存在时询问实际目录，不搜索九问目录。九问首次调用本技能脚本前执行本页命令，只读取九问公开的路径环境变量并检查一个文件。原生skill_tool的模型正文可能不含skill_directory，不能猜路径或从账号配置所在目录推断。

Windows使用下例的已核`python`；Linux先用`python3.11 --version`核实，将下例首项替换为`python3.11`或其已核绝对路径。后续沿用同一解释器，详见[公共调用](calling.md#调用格式)。

```text
python -X utf8 -c "import os; from pathlib import Path; base=Path(os.environ.get('JIUWENSWARM_DATA_DIR') or (Path(os.environ.get('JIUWENSWARM_HOME') or Path.home())/'.jiuwenswarm')); p=(base/'agent/workspace/skills/__IOALAB_SKILL_NAME__/scripts/ioalab.py').expanduser().resolve(); print(str(p) if p.is_file() else 'NOT_FOUND')"
```

保存输出的实际绝对路径，后续直接复用。命令不读取账号文件，也不执行脚本。NOT_FOUND时停止并向用户询问实际导入目录；不扫描整仓、依赖缓存或用户目录，不改用旧示例、官方服务启动器或其他角色脚本。用户明确给出当前技能目录时，也只检查其scripts/ioalab.py。
