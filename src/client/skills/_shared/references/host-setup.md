# 账号完成后的宿主接入

触发：原account.setup.status已completed，继续首次接入。复用账号阶段已定位的脚本、外置配置和原非秘密参数，不打开配置文件。SDK负责保留账号、密码、data_dir与身份；模型只提交以下host字段。

1. 查询SDK脱敏配置，不带args-file：

```text
python3.11 "<skill_directory>/scripts/ioalab.py" runtime.configuration --config "<原外置配置文件>"
```

Windows使用已核的python。保留此stdout回执，读取其data中的configurationDigest及hostFields。已有command和data_dir且用户没有要求修改时直接复用。缺项时先读取本进程公开运行信息：用Python的shutil.which('jiuwenswarm-process')定位原版Process可执行文件，command为包含该绝对路径的argv数组；host.data_dir只取环境变量JIUWENSWARM_DATA_DIR。任一值缺失或无法确认属于当前九问时才询问用户，不猜模块名、默认目录或追加--dotenv，不读取.env、模型配置或密钥。SDK回执的dataDir属于IoA，不能用于host.data_dir，两个目录保持分离。用户已指定模型时，一起提交实际selection_key到host.model；未指定则省略，由原版公开目录选择当前模型。

Linux读取上述非秘密元数据可执行：

```text
python3.11 -c "import os,shutil,json; print(json.dumps({'command':[shutil.which('jiuwenswarm-process')],'data_dir':os.environ.get('JIUWENSWARM_DATA_DIR')}))"
```

2. 用新的独立UTF-8文件提交仅包含宿主的更改，例如：

```json
{"expectedDigest":"<上述SDK回执的configurationDigest>","changes":{"host":{"command":["<用户既有Process入口>"],"data_dir":"<用户既有九问目录>","model":"<用户已指定的实际selection_key>"}}}
```

未指定model就省略该字段。参数文件只从用户给出的宿主值与这份SDK回执组装，不从账号配置复制、拼接或读取原字段。执行runtime.configure --config "<同一外置配置>" --args-file "<本次独立更改文件>"。摘要冲突重新查询原runtime.configuration；实例持锁按shared-runtime.md正常停止后继续原配置，不换账号或身份。

3. 配置完成后调用runtime.start --config "<同一外置配置>"，再读取status.md，按其只读命令核实际账号、身份、连接、宿主模型和角色能力。用户名与账号ID只取account字段，agentId只取身份字段；服务未配置和公示时保持executionReady=false，不称可接单。这里只接入既有宿主，不配置业务、修改九问安装或全局设置。
