"""Read-only, sanitized handoff assembled from the SDK and native skill status."""
import json
from pathlib import Path

from ioalab_sdk.runtime import (RuntimeConfig, RuntimeFault, LocalClient, require_configuration,
                                read_configuration, DEFAULT_PLATFORM_URL)

from .layout import configuration_path, locations
from .native_skills import owner, status as skill_status

HOST_NAMES = {'claude_code': 'Claude Code', 'codex': 'Codex', 'jiuwen': '九问'}


def step(data, identifier, actor, instruction):
    """One structured pending step: agents run agent steps, relay user steps verbatim, then run verify."""
    data['pending'].append({'id': identifier, 'actor': actor, 'instruction': instruction,
                            'verify': [data['python'], '-I', '-X', 'utf8', '-m', 'ioalab_client', 'onboarding',
                                       '--host', data['callingHost'], '--host-ref', data['callingHostRef'],
                                       '--config', data['configurationFile'], '--verify', identifier]})


def summary(host, *, config=None, python=None, host_ref=None):
    from .runtime import registry
    path = configuration_path(config)
    host_ref = host_ref or host
    data = {'format': 'ioalab.onboarding/1', **locations(path, python), 'accountName': None,
            'dataDirectory': None, 'callingHost': host, 'callingHostRef': host_ref,
            'executionHost': None, 'executionHostRef': None, 'registeredHosts': [],
            'authenticated': False, 'platformConnected': False,
            'credentialsLocation': str(path), 'credentialsView': '通过任一已绑定角色 Skill 调用 account.setup，在本机账号页查看；不要把密码粘贴到聊天。',
            'roles': [], 'notifications': {'configured': False, 'currentChatDeliveryVerified': False},
            'skills': skill_status(host, config=path, python=python, host_ref=host_ref), 'connected': False,
            'newSessionVerified': False, 'pending': []}
    platform = DEFAULT_PLATFORM_URL
    configuration_blocked = None
    try:
        configured = RuntimeConfig.load(path)
        document = read_configuration(path)
        platform = configured.platform
        data.update(accountName=configured.username, dataDirectory=str(configured.home),

                    roles=[{'role': p['role'], 'profile': p['key'], 'bound': False} for p in configured.identities])
        if 'password_env' in document:
            data['credentialsLocation'] = '配置引用的环境变量：'+document['password_env']+'（引用文件：'+str(path)+'）'
        try:
            hosts = registry(configured.host, required=False)
            entries = hosts['registrations']
            default = hosts['default']
            data.update(executionHostRef=default,
                        executionHost=entries[default]['type'] if default else None,
                        registeredHosts=[{'ref': ref, 'type': item['type']} for ref, item in entries.items()])
            if host_ref not in entries or entries[host_ref]['type'] != host:
                arguments = {'expectedDigest': configured.public_summary()['configurationDigest'],
                             'changes': {'host': {'registrations': {host_ref: {'type': host}}}}}
                step(data, 'host', 'agent', '先调用绑定 Skill 的 runtime.configuration 刷新 configurationDigest；'
                     '新建 UTF-8 参数文件，使用该摘要作为 expectedDigest，当前参数为 '+json.dumps(arguments, ensure_ascii=False)+
                     '，再经同一 Skill 调用 runtime.configure --args-file <该文件绝对路径>。只选择适配类型，'
                     '无需采集命令、模型、会话或工作目录；首次登记自动选为默认，已有默认保持。')
            notify = [ref for ref, item in entries.items()
                      if any(isinstance(tool, dict) and tool.get('factory') == 'jiuwen-web'
                             for tool in item.get('business_tools', []))]
            data['notifications'].update(configured=bool(notify), hostRefs=notify,
                                         channel='jiuwen-web' if notify else None)
        except RuntimeFault:
            step(data, 'host_migration', 'agent', '经绑定 Skill 的统一 runtime.configure 显式迁移原宿主配置，保留账号及在途业务。')
        try:
            result = LocalClient(configured.home, timeout=1).call('runtime.status')
            if result.get('status') != 'completed': raise RuntimeFault('not_ready', 'Runtime status unavailable')
            require_configuration(result['data'], configured)
            state = result['data']
            data['authenticated'] = bool(state.get('account', {}).get('authenticated'))
            data['platformConnected'] = bool(state.get('platformConnected'))
            data['connected'] = data['authenticated'] and data['platformConnected']
            data['executionHostAvailable'] = bool(state.get('host', {}).get('defaultAvailable'))
            data['runtimeId'], data['bootId'] = state.get('runtimeId'), state.get('bootId')
            for role in data['roles']:
                identity = next((p for p in state.get('identities', []) if p.get('profile') == role['profile']), {})
                role.update(bound=bool(identity.get('bound')), agentId=identity.get('agentId'),
                            executionReady=bool(identity.get('executionReady')))
        except RuntimeFault:
            step(data, 'runtime', 'agent', '启动或复用原客户端：'+data['python']+' -I -X utf8 -m ioalab_client.runtime start --config '+str(path)+'；不要启动第二个实例。')
    except RuntimeFault as exc:
        if exc.recovery == 'inspect_configuration_permissions':
            from ioalab_sdk.runtime import public_error
            configuration_blocked = public_error(exc)
            data['credentialsView'] = '先恢复对原配置的授权读取；保留原账号，不生成新账号。'
            step(data, 'configuration_permissions', 'agent', '当前宿主读取原配置被权限拒绝：'+str(path)+
                 '。通过宿主正常授权的本机访问方式重新执行绑定 Skill 的 runtime.configuration；'
                 '若宿主要求批准，向用户展示实际请求。保留原配置、账号与运行实例，不重注册、不修改权限策略或绕过沙箱。')
        else:
            step(data, 'account', 'user', '请在 IoA Lab 本机账号页登录已有账号或生成新账号（页面由智能体调用绑定 Skill 的 account.setup 打开）；密码只在该页面查看，不要发到聊天。完成后告诉智能体。')
    data['website'] = {'home': platform, 'login': platform+'/login', 'myAgents': platform+'/workbench',
                       'instructions': '使用客户端同一账号和密码登录官网，进入“我的智能体”；SDK连接不等于浏览器已登录。'}
    if configuration_blocked:
        return {'status':'waiting','data':data,'error':configuration_blocked}
    for skill, folder in zip(data['skills'], ('assistant', 'provider')):
        if skill['installed']: continue
        if skill['path'] is None:
            package = path.parent/'role-skills'/host_ref/('ioalab-'+folder+'-skill.zip')
            step(data, 'skill:'+skill['name'], 'user', ' '.join(owner(host).manual_steps(package, skill['name']))+' 完成后告诉智能体。')
        else:
            step(data, 'skill:'+skill['name'], 'agent', '安装或重装两角色 Skill：'+data['python']+' -I -X utf8 -m ioalab_client skills-install --host '+host
                 +' --host-ref '+host_ref+' --config '+str(path)+'；回执为 manual_required 时把其中的说明原样转告用户。')
    if {p['role'] for p in data['roles']} != {'assistant', 'service'}:
        step(data, 'roles', 'agent', '经受控配置入口准备缺少的角色，保留原账号、配置与服务政策；活跃实例需先显式安全停止。')
    elif not all(p['bound'] for p in data['roles']):
        step(data, 'identities', 'agent', '客户端运行后核对两角色身份绑定；未绑定时按 runtime.status 的错误恢复，不要重新注册账号。')
    if not data['connected']: step(data, 'connection', 'agent', '客户端运行后核对账号认证与平台连接；失败时按 runtime.status 的错误恢复。')
    if not data.get('executionHostAvailable'): step(data, 'execution_host', 'agent', '核对后台执行宿主可用；不可用时按 runtime.status 报告的宿主与动作处理。')
    entries = [str(Path(item['path'])/'native'/'README.md') for item in data['skills'] if item['installed'] and item['path']]
    step(data, 'native_setup', 'agent', '账号、宿主登记及原客户端启动完成后，读取当前宿主两角色的 native/README.md：'+
         ('、'.join(entries) if entries else '两份导出包中的 native/README.md')+
         '，按实际路径完成原生入口安装；随后从当前真实原生入口以空业务参数执行 runtime.entry.bind。'
         '普通终端调用不能代替原生绑定；缺入口时报告准确缺项，不猜身份或要求用户填写。')
    step(data, 'new_session', 'user', '请新开一个 '+HOST_NAMES[host]+' 会话，分别说“用 ioalab-delegate 查询 IoA Lab 的 runtime.status”和“用 ioalab-provider 查询 IoA Lab 的 runtime.status”，把两次结果中的 runtimeId 告诉智能体。')
    return {'status': 'waiting', 'data': data, 'error': None}


def verify(host, identifier, **options):
    """Read-only, model-free check of one structured step; new-session discovery is never self-certified."""
    result = summary(host, **options)
    pending = next((item for item in result['data']['pending'] if item['id'] == identifier), None)
    if pending is None:
        return {'status': 'completed', 'data': {'id': identifier, 'verified': True}, 'error': None}
    message = ('原生发现或入口绑定需要实际原生回执，程序不自报完成' if identifier in ('new_session', 'native_setup') else '该步骤尚未完成')
    return {'status': 'rejected', 'data': {'id': identifier, 'verified': False, 'step': pending},
            'error': {'code': 'step_pending', 'message': message}}


def markdown(result):
    data = result['data']; web = data['website']
    account = data['accountName'] or ('未能读取（配置权限受限）' if (result.get('error') or {}).get('recovery') == 'inspect_configuration_permissions' else '尚未配置')
    lines = ['IoA Lab 接入交付摘要（ioalab.onboarding/1）',
             '1. 接入状态：'+('待完成验证' if data['pending'] else '已完成'),
             '2. 客户端：'+data['clientDirectory'], '   程序：'+data['programDirectory'],
             '   数据：'+str(data['dataDirectory'] or '尚未配置'), '   配置：'+data['configurationFile'],
             '3. 账号：'+str(account), '   账号/密码配置位置：'+data['credentialsLocation'],
             '   查看方式：'+data['credentialsView'], '4. 两角色 Skill：']
    for item in data['skills']:
        lines.append('   '+item['name']+'：'+('文件已安装' if item['installed'] else '待原生安装/导入')+
                     '；位置：'+str(item['path'] or '九问原生导入')+'；使用入口：'+item['entry']+'；新会话：待验')
    lines.extend(['5. 调用入口：'+data['callingHost']+'（'+data['callingHostRef']+'）；后台执行宿主：'+str(data['executionHost'] or '尚未配置')+'（'+str(data['executionHostRef'] or '未选择')+'）',
                  '   通知通道：'+str(data['notifications'].get('channel') or '未配置')+'；当前聊天投递：未验证',
                  '6. 官网：'+web['home'], '   登录：'+web['login']+'；我的智能体：'+web['myAgents'],
                  '   '+web['instructions'], '待完成事项：',
                  *('   - ['+('由你完成' if item['actor'] == 'user' else '智能体执行')+'] '+item['instruction'] for item in data['pending'])])
    return '\n'.join(lines)
