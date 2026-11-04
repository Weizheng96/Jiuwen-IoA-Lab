# Linux三席试用部署

当前未在Linux实机验收。固定三席，每席位全部可写状态归一个epoch volume。原生九问UID10003不能读写SDK UID10002、核心UID10001或服务方UID10004数据。只读程序和完整离线wheelhouse共享。

先完成普通发行构建并校验摘要，准备独立上下文，不把配置、私钥、模型密钥或运行数据放入镜像。

```sh
mkdir -p temp/trial-image/locks temp/trial-image/wheelhouse
cp deploy/trial/{Dockerfile,plan.json,service-ids.json} temp/trial-image/
cp deploy/locks/linux-x86_64/{core,participant,swarm}.lock temp/trial-image/locks/
cp /absolute/reviewed-release/wheelhouse/*.whl temp/trial-image/wheelhouse/
unzip dist/ioalab-assistant-skill.zip -d temp/trial-unpack
cp -a temp/trial-unpack/ioalab-delegate temp/trial-image/assistant-skill
docker build --network=none --build-arg BASE_IMAGE=python:3.11-slim@sha256:<实际摘要> -t ioalab-trial-seat temp/trial-image
```

wheelhouse须含锁定上游wheel、全部普通依赖及本次发行的ioalab、SDK、九问适配、官方资源和trial包。pip均使用`--no-index`，缺wheel失败，不现场访问GitHub。基础镜像需预先准备目标Python原生库并固定摘要。容器不挂宿主Docker socket、正式数据、其他席位或管理员目录；仅映射loopback的原生UI及受限控制端口。

协调器为每epoch自动启动独立模型sidecar，入口为镜像内`python -m ioalab_trial.model_egress`。不需要注册外部共享代理。sidecar连接显式`egress_network`外网bridge及该epoch独立internal bridge；席位只连接内网。只有本轮sidecar在内网具有`model-egress`别名，不连接其他席位网络。

将上游密钥写入专用`proxy_secret_file`，内容只含`{"api_key":"实际供应商密钥"}`，属主root:root、权限600，不入库、不放构建上下文。sidecar默认UID0且移除全部capability，不能靠root绕过宿主非root文件的600权限；必须设置正确属主，不放宽为全局可读。该文件仅以只读bind挂到sidecar的`/run/model/provider.json`，席位容器完全没有此挂载。供应商地址由固定`provider_base_url`配置，例如`https://provider.example.test/compatible-mode/v1`；允许兼容API前缀路径，不允许query、fragment或userinfo。sidecar仅转发固定chat/completions、embeddings及models后缀，拒绝任意URL、CONNECT和重定向，不透传调用方Host/Authorization。请求上限2MiB，响应流上限32MiB；上游错误不回传供应商正文或私有响应头。

席位模型配置只有模型名和自动生成的固定内网地址`http://model-egress:8081/v1`及epoch专用token。token按控制密钥、完整席位身份及轮次域分离派生，三席和跨轮均不同，不是供应商密钥；配置中不接受`model.api_key`。停止时先持久撤销网页/确认会话，再停止并核该轮sidecar退出，确保旧token与旧模型流失效；即使席位控制监听离线也不能继续调用供应商。随后停止席位并核全部数据/网络/sidecar归属后才销毁。任何一步未知均隔离，不分配下一人。

镜像配置改为实际完整digest；admission_key与正式平台IOALAB_TRIAL_ADMISSION_KEY一致，control_key另生成。`model`只填写`name`，供应商secret从不进入席位环境、原生config.get或试用网关回执。

正式平台IOALAB_TRIAL_URL填写原生试用origin，IOALAB_TRIAL_CONFIRM_URL填写独立确认origin。两者必须不同HTTPS hostname，不能只换端口。应用配置的`origin`、`confirm_origin`分别匹配二者，`formal_origin`只生成返回正式平台的链接。正式平台的两个入口分别签不同aud票据，正式cookie不出正式host。用户先取得席位，再从正式平台点击确认入口；可信cookie消费时绑定当前seat/epoch，原生cookie不能访问确认端点。两个TLS虚拟host都转发同一网关进程并保留各自Host/Origin；确认host不代理任何原生内容或WS。

```sh
/usr/bin/python3 -m venv /opt/ioalab-trial-controller
/opt/ioalab-trial-controller/bin/pip install --no-index --find-links=/absolute/reviewed-release/wheelhouse -c deploy/locks/linux-x86_64/core.lock -c deploy/locks/linux-x86_64/participant.lock ioalab-trial
ioalab-trial --config /etc/ioalab-trial/config.json
```

控制器使用该环境中的`/opt/ioalab-trial-controller/bin/ioalab-trial`启动。镜像participant安装trial时也同时应用原core/participant锁约束，避免完整离线wheelhouse有多个FastAPI/uvicorn版本时任意挑选；不另建一套依赖锁。

专用TLS反代保持Host和Origin，将HTTP及WS转到127.0.0.1:8766；不透传正式平台cookie，不直接开放席位端口。该进程需宿主Docker权限，权限不授予容器。

真实健康后仍需原版UI/WS/A2UI/Skill工具调用和双origin确认边界专项。`probe.py`查询实际公开接口并要求镜像中`/opt/trial/native-acceptance.json`记录`originalUiProxy`、`originalWsProxy`、`a2ui`、`skillSdkCall`的真实验收结果；Dockerfile不会伪造该文件。审核证据后冻结镜像摘要，不能手写true冒充实测。应实测原生host的活动内容不能取得确认cookie或CSRF，旧epoch在两个host均无法读写；不得将确认API临时搬回原生host绕过验收。

结束先持久撤销用户会话和epoch访问并关闭WS，再停本轮模型sidecar、通知容器撤销SDK确认并SIGTERM所属进程组。PID1等待全部后代；未退出保持quarantined，不强杀伪装停止。所有待删除资源的标签、挂载、状态与网络成员在首个删除动作前统一核对；核两个容器Exited/Pid=0后移除容器、整个volume和本轮网络，再准备新epoch。未知部分创建不盲目接管；默认隔离，人工核完整owner标签和真实资源后恢复。

业务使用公开注册的普通`trial-user`，不是默认管理员身份。core公共seed只含四个编程候选的公共摘要；模型指令、样例和验收器仍归外部服务资源。仅试用环境显式登录赠1000虚拟币，独立于用户预算/授权；探针使用首次注册原cookie只读核钱包可支持500预算样例，不反复登录或赠币。

```sh
python experiments/trial_resources.py --deployment ioa-trial --phase three-idle --seconds 60 --out temp/three-idle.jsonl
python experiments/trial_resources.py --deployment ioa-trial --phase three-active --seconds 120 --out temp/three-active.jsonl
```

同样保存baseline、one-active、after-reset；包含核心与服务方。Docker内存不是PSS；脚本明确标出待补的宿主/PSS、模型请求令牌及业务时延，不用空闲数值推算三席执行负担。
