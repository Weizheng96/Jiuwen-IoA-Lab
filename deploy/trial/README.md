# 在线试用部署

配置模板`config.example.json`中的`deployment_profile`必须按目标环境填写；规则唯一归[在线试用](../../设计/工程/在线试用.md)。这只限定可选试用应用，不限制正式平台账号或普通SDK连接。Linux服务器使用linux-server；Windows本机在已准备的WSL2发行版、Linux控制器环境与Docker Desktop集成上使用windows-wsl。同一个协调器执行相同生命周期。

Windows在仓库根目录执行（参数都是WSL内已安装的实际路径，不是Windows虚拟环境）：

```powershell
python deploy/trial/start_windows.py --distribution IoA-Trial --python /opt/ioalab-trial-controller/bin/python --config /etc/ioalab-trial/config.json
```

该薄入口不安装WSL/Docker、不复制密钥、不修改全局设置；启动或环境核对失败返回非零。配置须填写windows-wsl，调用指定发行版原解释器运行普通ioalab_trial模块，WSL内核不匹配时拒绝。Linux服务器直接执行下文控制器入口，配置须填写linux-server。

已有无profile控制库或切换profile/容量时启动会拒绝并保留原库；先在原部署完整结束全部轮次、撤销旧访问并核任务及数据重置成功，再选新的专用control_directory。不要删除旧数据库或改profile字段来绕过拒绝；新目录不能证明旧容器或业务已清退。原版网页、可信确认与整轮重置未过时仍不提供试用。

程序、控制库、镜像能力和资源标签必须绑定当前epoch-ingress/1布局；旧两容器控制库不能补记版本后接管。入口在开放网关前核镜像布局与固定ingress能力，原镜像缺声明时明确失败。仍先沿原部署完整清退原轮次，再启用新的专用控制目录；不删库绕过。

协调器及资源采样固定使用本机unix:///var/run/docker.sock，不继承外部DOCKER_HOST或当前Docker context；不会操作SSH目标上的既有容器。

每轮由seat、model、ingress三个容器组成：seat仅连接禁用IPv6的isolated内部桥，供应商secret仍只归model；ingress无secret/卷、非root，只发布loopback并透明转发到本轮seat的8080/8767，不解释CONNECT为新目标。Docker按已核seat ID与数值IPv4固定hosts映射，不依赖运行期DNS解析；程序核真实发布、全部网络和角色归属后才向端口发控制请求。不支持isolated或IPv6隔离时拒绝准备，不给seat添加外网。

资源脚本须由已安装trial的Linux控制器解释器执行，Windows同样先进入所选WSL发行版再使用该绝对解释器，不用Windows项目Python采样。Windows资源采集使用`--deployment-profile windows-wsl`，阶段为baseline、one-idle、one-active、after-reset；三席阶段在该profile被拒绝。下文三席命令仅用于Linux服务器。目标容量及实测结果必须同时记录。

实际容量及隔离规则见[在线试用](../../设计/工程/在线试用.md)，尚未完成原版业务专项验收。每席位全部可写状态归一个epoch volume。原生九问UID10003不能读写SDK UID10002、核心UID10001或服务方UID10004数据。只读程序和完整离线wheelhouse共享。

普通正式发行只含`wheels/`本地项目包，不包含可选ioalab-trial，也不是完整第三方wheelhouse。先校验普通发行，保持它不可变；在受审源码仓库根目录的Linux/WSL shell中独立构建可选包、汇总专用上下文。下列路径变量替换为真实绝对Linux路径：BUILD_PYTHON是已有setuptools/pip的维护端解释器；TRIAL_IMAGE_WHEELS是已按镜像Python3.11/bookworm核过三个Linux锁的依赖缓存；TRIAL_CONTROLLER_WHEELS与TRIAL_CONTROLLER_BOOTSTRAP须匹配控制器实际Python/平台（本机已备Python3.12缓存，不能与镜像缓存互换）。不向已核普通发行或依赖缓存回写。

```sh
REVIEWED_RELEASE=/absolute/reviewed-release
BUILD_PYTHON=/absolute/maintainer-venv/bin/python
TRIAL_IMAGE_WHEELS=/absolute/reviewed-cp311-bookworm-wheel-cache
TRIAL_CONTROLLER_WHEELS=/absolute/reviewed-controller-cp312-wheel-cache
TRIAL_CONTROLLER_BOOTSTRAP=/usr/bin/python3.12
TRIAL_WORK=$(mktemp -d /var/tmp/ioalab-trial-build.XXXXXXXX)
"$BUILD_PYTHON" tools/build_packages.py --out "$TRIAL_WORK/optional-wheels" src/trial
mkdir -p "$TRIAL_WORK/image/locks" "$TRIAL_WORK/image/wheelhouse" "$TRIAL_WORK/controller-wheelhouse" "$TRIAL_WORK/image/vendor/jiuwen" "$TRIAL_WORK/image/vendor/wheels"
cp "$REVIEWED_RELEASE"/wheels/*.whl "$TRIAL_WORK/image/wheelhouse/"
cp "$TRIAL_IMAGE_WHEELS"/*.whl "$TRIAL_WORK/image/wheelhouse/"
cp "$TRIAL_WORK/optional-wheels"/*.whl "$TRIAL_WORK/image/wheelhouse/"
cp "$REVIEWED_RELEASE"/wheels/*.whl "$TRIAL_WORK/controller-wheelhouse/"
cp "$TRIAL_CONTROLLER_WHEELS"/*.whl "$TRIAL_WORK/controller-wheelhouse/"
cp "$TRIAL_WORK/optional-wheels"/*.whl "$TRIAL_WORK/controller-wheelhouse/"
cp deploy/trial/{Dockerfile,plan.json,service-ids.json,install_swarm_wheels.py} "$TRIAL_WORK/image/"
cp vendor/SOURCES.json "$TRIAL_WORK/image/vendor/"
cp vendor/jiuwen/requirements-swarm.lock "$TRIAL_WORK/image/vendor/jiuwen/"
cp vendor/wheels/*.whl "$TRIAL_WORK/image/vendor/wheels/"
cp deploy/locks/linux-x86_64/{core,participant,swarm}.lock "$TRIAL_WORK/image/locks/"
unzip "$REVIEWED_RELEASE/skills/ioalab-assistant-skill.zip" -d "$TRIAL_WORK/skill-unpack"
cp -a "$TRIAL_WORK/skill-unpack/ioalab-delegate" "$TRIAL_WORK/image/assistant-skill"
"$BUILD_PYTHON" tools/verification/trial_acceptance.py context --context "$TRIAL_WORK/image" --source "$(git rev-parse HEAD)"
(cd "$TRIAL_WORK" && find image controller-wheelhouse optional-wheels -type f -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS)
(cd "$TRIAL_WORK" && sha256sum -c SHA256SUMS)
docker build --network=none --build-arg BASE_IMAGE=python:3.11-slim@sha256:<实际摘要> -t ioalab-trial-seat "$TRIAL_WORK/image"
```

普通依赖缓存由维护端按原core/participant/swarm锁准备，完整包含传递依赖且实际核目标Python ABI与manylinux兼容；这里只复制已审核输入，不现场访问GitHub。四个原版Git来源wheel继续使用下述原manifest与requirements，不能从PyPI/VCS替换。可选包构建使用现有隔离build_packages入口，不把trial添加进普通发行类别。核专用SHA256SUMS及输入出处后才使用，不能将两种ABI混合缓存。基础镜像须预先准备目标Python原生库并固定摘要；容器不挂宿主Docker socket、正式数据、邻席位或管理员目录，仅映射loopback原生UI和受限控制端口。

Swarm环境先安装现有Linux普通依赖锁，再由install_swarm_wheels.py读取原requirements中的本地wheel条目并核SOURCES.json摘要，按同一Linux锁约束离线安装原版包，最后pip check及原Process入口检查。元数据与原wheel原样进入专用vendor构建目录，不复制运行配置、secret或原生状态，不另维护原版版本清单。缺件、摘要或依赖不符时构建失败。

协调器为每epoch自动启动独立模型sidecar，入口为镜像内`python -m ioalab_trial.model_egress`。不需要注册外部共享代理。sidecar连接显式`egress_network`外网bridge及该epoch独立internal bridge；席位只连接内网。只有本轮sidecar在内网具有`model-egress`别名，不连接其他席位网络。

将上游密钥写入专用`proxy_secret_file`，内容只含`{"api_key":"实际供应商密钥"}`，属主root:root、权限600，不入库、不放构建上下文。sidecar默认UID0且移除全部capability，不能靠root绕过宿主非root文件的600权限；必须设置正确属主，不放宽为全局可读。该文件仅以只读bind挂到sidecar的`/run/model/provider.json`，席位容器完全没有此挂载。供应商地址由固定`provider_base_url`配置，例如`https://provider.example.test/compatible-mode/v1`；允许兼容API前缀路径，不允许query、fragment或userinfo。sidecar仅转发固定chat/completions、embeddings及models后缀，拒绝任意URL、CONNECT和重定向，不透传调用方Host/Authorization。请求上限2MiB，响应流上限32MiB；上游错误不回传供应商正文或私有响应头。

席位模型配置只有模型名和自动生成的固定内网地址`http://model-egress:8081/v1`及epoch专用token。token按控制密钥、完整席位身份及轮次域分离派生，三席和跨轮均不同，不是供应商密钥；配置中不接受`model.api_key`。停止时先持久撤销网页/确认会话，再停止并核该轮sidecar退出，确保旧token与旧模型流失效；即使席位控制监听离线也不能继续调用供应商。随后停止席位并核全部数据/网络/sidecar归属后才销毁。任何一步未知均隔离，不分配下一人。

镜像配置使用实际不可变sha256:ImageID或已有name@sha256:RepoDigest，不使用漂移tag；admission_key与正式平台私有JSON的runtime.trial_admission_key一致，control_key另生成。`model`只填写`name`，供应商secret从不进入席位环境、原生config.get或试用网关回执。

正式平台私有JSON的runtime.trial_url填写原生试用origin，runtime.trial_confirm_url填写独立确认origin。两者必须为不同HTTPS origin；公网IP部署可用同一IP的8443与9443端口，正式平台使用443。域名同hostname的不同端口不作为独立部署入口。两类Cookie各使用独立__Host名称、Secure及HttpOnly；Cookie不按端口隔离，原生代理必须剥离Cookie、Authorization、Set-Cookie和Clear-Site-Data。应用配置的`origin`、`confirm_origin`分别匹配二者，`formal_origin`只生成返回正式平台的链接。正式平台的两个入口分别签不同aud票据，正式cookie不出正式host。用户先取得席位，再从正式平台点击确认入口；可信cookie消费时绑定当前seat/epoch，原生cookie不能访问确认端点。两个TLS入口都转发同一网关进程并保留各自含端口的Host及原始Origin；确认host不代理任何原生内容或WS。

```sh
"$TRIAL_CONTROLLER_BOOTSTRAP" -m venv /opt/ioalab-trial-controller
/opt/ioalab-trial-controller/bin/pip install --no-index --find-links="$TRIAL_WORK/controller-wheelhouse" -c deploy/locks/linux-x86_64/core.lock -c deploy/locks/linux-x86_64/participant.lock ioalab-trial
/opt/ioalab-trial-controller/bin/pip check
/opt/ioalab-trial-controller/bin/ioalab-trial --config /etc/ioalab-trial/config.json
```

控制器使用该环境中的`/opt/ioalab-trial-controller/bin/ioalab-trial`启动。镜像participant安装trial时也同时应用原core/participant锁约束，避免完整离线wheelhouse有多个FastAPI/uvicorn版本时任意挑选；不另建一套依赖锁。

专用TLS反代保持Host和Origin，将HTTP及WS转到127.0.0.1:8766；不透传正式平台cookie，不直接开放席位端口。该进程需宿主Docker权限，权限不授予容器。

正式三席的健康门槛同时要求程序实时探测和镜像内的受审浏览器证据。缺少证据的工程镜像不能分配给普通用户。为避免“须先上线才能验收、须先验收才能上线”的循环，先走独立工程轮次：

1. 在完整构建上下文生成`acceptance/inputs.json`（上文context命令），此文件只有sourceRevision与原程序/锁/wheel/Skill上下文摘要，不包含通过断言。构建没有native-acceptance记录的工程镜像，记录实际ImageID。
2. 使用独立的工程配置：`sandbox.deployment`以`-engineering`结尾，`port_base`使用不与正式席位相交的专用范围，image写实际`sha256:ImageID`。配置仍复用同一隔离规则、只读程序、模型代理和固定转发器。通过下列公开工程入口创建一个epoch，不加入正式协调器或修改其控制库。

```sh
/opt/ioalab-trial-controller/bin/python tools/verification/trial_engineering.py prepare --config /etc/ioalab-trial/engineering.json --state /var/lib/ioalab-trial/engineering-operation.json --inputs "$TRIAL_WORK/image/acceptance/inputs.json"
/opt/ioalab-trial-controller/bin/python tools/verification/trial_engineering.py observe --config /etc/ioalab-trial/engineering.json --state /var/lib/ioalab-trial/engineering-operation.json --inputs "$TRIAL_WORK/image/acceptance/inputs.json"
```

3. observe读取真实原生HTTP及WS公开查询，仅报告工程观察；不会生成生产通过记录。root通过受控的工程HTTPS反代在真实Chrome观察原版页面、WSS、实际A2UI交互和个人助手Skill调用已连接SDK。工程访问仅供管理员验收，不向陌生人开放。配置开启或直接SDK探针均不能替代实际A2UI与Skill调用。保存脱敏截图/调用证据和摘要；原聊天和业务数据留在工程volume。
4. root完成四项真实观察后，提供JSON review：`reviewedBy=root-chrome`、实际`browserSessionId`、`engineeringImageId`、与inputs相同的`sourceRevision/contextSha256`，以及四项检查`originalUiProxy/originalWsProxy/a2ui/skillSdkCall`。每项包含真实`status`与`artifacts`（相对review文件目录的脱敏证据文件名→SHA256）。不可填写预设通过值代替执行。只将审核可公开的工程证据提供给recorder；密钥、Cookie和私人业务正文不得进入镜像。

```sh
/opt/ioalab-trial-controller/bin/python tools/verification/trial_acceptance.py record --context "$TRIAL_WORK/image" --review /var/lib/ioalab-trial/review/root-chrome-review.json
```

recorder重新核原上下文，校每份原证据的实际字节摘要，输出`acceptance/native-acceptance.json`及脱敏证据。Dockerfile将整个acceptance目录拷进镜像；probe校来源/上下文、工程ImageID格式、浏览器review及每份证据摘要后才使用通过结果。工程ImageID仅记录验收来源，不要求最终镜像与工程镜像ID相等（加入证据必然改变ID），相同源码输入在不同机器重建不会因生成不同镜像ID而被永久拒绝。
5. 使用未变的程序/锁/wheel/Skill上下文加上验收目录重建最终镜像，记录最终不可变ID；通过engineering retire命令正常停机、核退出并清除该工程轮次。正式配置引用最终ID，启动正式三席协调器；只有三个新epoch均通过原实时health门槛，正式安装才能完成。此时再从正式网页按钮取得票据，实际验证原生和确认两个origin、旧轮次失效及完整重置。任何步骤失败保留原工程状态，不自动接管部分创建或手工把正式席位改ready。

```sh
/opt/ioalab-trial-controller/bin/python tools/verification/trial_engineering.py retire --config /etc/ioalab-trial/engineering.json --state /var/lib/ioalab-trial/engineering-operation.json --inputs "$TRIAL_WORK/image/acceptance/inputs.json"
```


结束先持久撤销用户会话和epoch访问并关闭WS，在首个TERM前完整核三个容器、卷及内部网络的归属和固定形状，再停本轮model、经仍活ingress撤销SDK确认、SIGTERM席位所属进程组并核退出，最后SIGTERM ingress关闭活动连接并核退出。未知保持quarantined，不强杀伪装停止。首个删除动作前再次完整核验；三个容器均成功Exited/Pid=0后移除三个容器、整个volume和本轮网络，再准备新epoch，共用外网桥保留。未知部分创建不盲目接管；默认隔离，人工核完整owner标签和真实资源后恢复。

业务使用公开注册的普通`trial-user`，不是默认管理员身份。core公共seed只含四个编程候选的公共摘要；模型指令、样例和验收器仍归外部服务资源。仅试用环境显式登录赠1000虚拟币，独立于用户预算/授权；探针使用首次注册原cookie只读核钱包可支持500预算样例，不反复登录或赠币。

```sh
/opt/ioalab-trial-controller/bin/python tools/verification/trial_resources.py --deployment-profile linux-server --deployment ioa-trial --phase three-idle --seconds 60 --out temp/three-idle.jsonl
/opt/ioalab-trial-controller/bin/python tools/verification/trial_resources.py --deployment-profile linux-server --deployment ioa-trial --phase three-active --seconds 120 --out temp/three-active.jsonl
```

同样保存baseline、one-active、after-reset；包含核心与服务方。Docker内存不是PSS；脚本明确标出待补的宿主/PSS、模型请求令牌及业务时延，不用空闲数值推算三席执行负担。
