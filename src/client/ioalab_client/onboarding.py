"""Read-only, sanitized handoff assembled from the SDK and native skill status."""
from pathlib import Path

from ioalab_sdk.runtime import (RuntimeConfig, RuntimeFault, LocalClient, require_configuration,
                                read_configuration, DEFAULT_PLATFORM_URL)

from .layout import configuration_path, locations
from .native_skills import status as skill_status


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
                data['pending'].append('经统一 runtime.configure 登记当前宿主，保留已有默认宿主。')
            notify = [ref for ref, item in entries.items()
                      if any(isinstance(tool, dict) and tool.get('factory') == 'jiuwen-web'
                             for tool in item.get('business_tools', []))]
            data['notifications'].update(configured=bool(notify), hostRefs=notify,
                                         channel='jiuwen-web' if notify else None)
        except RuntimeFault:
            data['pending'].append('经统一 runtime.configure 显式迁移原宿主配置，保留账号及在途业务。')
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
            data['pending'].append('启动或恢复原客户端，并核对账号、执行宿主、平台连接及两角色身份。')
    except RuntimeFault:
        data['pending'].append('通过绑定 Skill 的 account.setup 完成本机账号页；不要读取配置正文或重新创建已有账号。')
    data['website'] = {'home': platform, 'login': platform+'/login', 'myAgents': platform+'/workbench',
                       'instructions': '使用客户端同一账号和密码登录官网，进入“我的智能体”；SDK连接不等于浏览器已登录。'}
    for skill in data['skills']:
        if not skill['installed']:
            data['pending'].append('完成 '+skill['name']+' 的原生安装/导入；用 skills-install 查看实际路径与手动步骤。')
    if {p['role'] for p in data['roles']} != {'assistant', 'service'}:
        data['pending'].append('经受控配置入口准备缺少的角色，保留原账号、配置与服务政策；活跃实例需先显式安全停止。')
    elif not all(p['bound'] for p in data['roles']):
        data['pending'].append('两角色身份尚未全部核实绑定。')
    if not data['connected']: data['pending'].append('线上连接尚未核实。')
    if not data.get('executionHostAvailable'): data['pending'].append('后台执行宿主可用性尚未核实。')
    data['pending'].append('在新会话分别加载两份 Skill 并调用 runtime.status，核对同一客户端、账号和 runtimeId；保留原生发现及调用证据。')
    return {'status': 'waiting', 'data': data, 'error': None}


def markdown(result):
    data = result['data']; web = data['website']
    lines = ['IoA Lab 接入交付摘要（ioalab.onboarding/1）',
             '1. 接入状态：'+('待完成验证' if data['pending'] else '已完成'),
             '2. 客户端：'+data['clientDirectory'], '   程序：'+data['programDirectory'],
             '   数据：'+str(data['dataDirectory'] or '尚未配置'), '   配置：'+data['configurationFile'],
             '3. 账号：'+str(data['accountName'] or '尚未配置'), '   账号/密码配置位置：'+data['credentialsLocation'],
             '   查看方式：'+data['credentialsView'], '4. 两角色 Skill：']
    for item in data['skills']:
        lines.append('   '+item['name']+'：'+('文件已安装' if item['installed'] else '待原生安装/导入')+
                     '；位置：'+str(item['path'] or '九问原生导入')+'；使用入口：'+item['entry']+'；新会话：待验')
    lines.extend(['5. 调用入口：'+data['callingHost']+'（'+data['callingHostRef']+'）；后台执行宿主：'+str(data['executionHost'] or '尚未配置')+'（'+str(data['executionHostRef'] or '未选择')+'）',
                  '   通知通道：'+str(data['notifications'].get('channel') or '未配置')+'；当前聊天投递：未验证',
                  '6. 官网：'+web['home'], '   登录：'+web['login']+'；我的智能体：'+web['myAgents'],
                  '   '+web['instructions'], '待完成事项：', *('   - '+item for item in data['pending'])])
    return '\n'.join(lines)
