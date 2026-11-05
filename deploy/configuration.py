"""Parse deployment inputs as data; never evaluate shell configuration."""
from __future__ import annotations

import ipaddress
from pathlib import Path
from urllib.parse import urlsplit

from .environment import mirror
from .release import read_json


def text(value, field):
    if not isinstance(value, str) or not value or value != value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError('Invalid deployment field: ' + field)
    return value


def url(value, field, *, root=False):
    value = text(value, field)
    try:
        parts = urlsplit(value)
        valid = (parts.scheme in ('http', 'https') and parts.hostname and not parts.username
                 and not parts.password and not parts.query and not parts.fragment
                 and not any(c.isspace() for c in value) and '\\' not in value)
        parts.port
    except ValueError:
        valid = False
    if not valid or (root and parts.path not in ('', '/')):
        raise ValueError('Invalid deployment URL: ' + field)
    return value.rstrip('/')


def integer(value, field, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError('Invalid deployment integer: ' + field)
    return value


def model(value):
    if value is None:
        return None
    required = {'name', 'base_url', 'api_key'}
    if not isinstance(value, dict) or not required <= set(value) or set(value) - required - {'provider'}:
        raise ValueError('Provide complete model name, base_url and api_key, or omit model')
    if value.get('provider', 'OpenAI') != 'OpenAI':
        raise ValueError('Deployment currently supports only the OpenAI-compatible provider')
    return {'name': text(value['name'], 'model.name'), 'base_url': url(value['base_url'], 'model.base_url'),
            'api_key': text(value['api_key'], 'model.api_key'), 'provider': 'OpenAI'}


def mailbox(value, field):
    import re
    value = text(value, field)
    if len(value) > 254 or not re.fullmatch(r'[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+', value):
        raise ValueError('Invalid deployment mailbox: ' + field)
    return value


def notification(value, *, mode):
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError('Use an explicit notification object or null')
    transport = value.get('transport')
    allowed = {'transport', 'sender', 'api_key'} if transport == 'agentmail' else {'transport', 'sender', 'host', 'port', 'username', 'password'}
    if set(value) - allowed or transport not in ('agentmail', 'smtp', 'smtp-capture'):
        raise ValueError('Choose an explicit AgentMail or SMTP notification transport')
    if transport == 'smtp-capture' and mode != 'dev':
        raise ValueError('SMTP capture is only available in explicit development mode')
    result = {'transport': transport, 'sender': mailbox(value.get('sender'), 'notification.sender')}
    if transport == 'agentmail':
        result['api_key'] = text(value.get('api_key'), 'notification.api_key')
    else:
        host = text(value.get('host'), 'notification.host')
        if any(c.isspace() for c in host) or any(c in host for c in '/\\@?#'):
            raise ValueError('Use a notification host name, without a URL or credentials')
        result.update(host=host, port=integer(value.get('port', 587), 'notification.port', 1, 65535),
                      username=value.get('username', ''), password=value.get('password', ''))
        if not isinstance(result['username'], str) or not isinstance(result['password'], str) or bool(result['username']) != bool(result['password']):
            raise ValueError('SMTP authentication requires both username and password')
        for field in ('username', 'password'):
            if result[field]: text(result[field], 'notification.' + field)
        if transport == 'smtp-capture' and host not in ('localhost', '127.0.0.1', '::1'):
            raise ValueError('SMTP capture must use a loopback receiver')
    return result


def admin_assistant(value, *, mode):
    value = {} if value is None else value
    if not isinstance(value, dict) or set(value) - {'enabled', 'recipient', 'notification'} or type(value.get('enabled', True)) is not bool:
        raise ValueError('Use the documented administrator assistant configuration')
    return {'enabled': value.get('enabled', True),
            'recipient': mailbox(value.get('recipient', 'zhengw.cc@outlook.com'), 'admin_assistant.recipient'),
            'notification': notification(value.get('notification'), mode=mode)}


def swarm_ports(value, core_port):
    result = {'agent_server': 18902, 'gateway': 18900, 'web': 18901, 'frontend': 18753}
    if value is not None:
        if not isinstance(value, dict) or set(value) - set(result):
            raise ValueError('Use the four documented Swarm port names')
        result.update(value)
    for key, port in result.items(): integer(port, 'swarm_ports.' + key, 1, 65535)
    if len(set(result.values())) != 4 or core_port in result.values():
        raise ValueError('Swarm ports must be distinct from each other and the core port')
    return result


def platform(path):
    """Deployment reads only its needed projection; core validates its complete schema."""
    path=Path(path)
    if not path.is_absolute() or path.is_symlink() or path.resolve()!=path.absolute() or not path.is_file():
        raise ValueError('Use the absolute regular private platform configuration file')
    value=read_json(path)
    if not isinstance(value,dict) or value.get('format')!='ioalab.platform-config/1' or type(value.get('revision')) is not int:
        raise ValueError('Use the documented platform configuration format')
    runtime=value.get('runtime');admin=value.get('admin');llm=value.get('llm')
    if any(not isinstance(group,dict) for group in (runtime,admin,llm)):
        raise ValueError('Provide the complete platform deployment fields')
    result={'public_url':url(runtime.get('public_url'),'runtime.public_url',root=True),
        'port':integer(runtime.get('port'),'runtime.port',1,65535),
        'bind':text(runtime.get('bind'),'runtime.bind'),'mode':runtime.get('mode'),
        'isc_count':integer(runtime.get('isc_count'),'runtime.isc_count',1,10000),
        'model':model({'name':llm.get('model'),'base_url':llm.get('base_url'),'api_key':llm.get('api_key')}),
        'admin_username':text(admin.get('username'),'admin.username'),'admin_password':text(admin.get('password'),'admin.password')}
    if result['mode'] not in ('trial','dev'):raise ValueError('Invalid deployment mode')
    try:ipaddress.ip_address(result['bind'])
    except (ValueError,TypeError):raise ValueError('Deployment bind must be an IP address') from None
    return result


def validate(value):
    allowed={'platform_config','pypi_index','max_model_runs','admin_assistant','swarm_ports','trial'}
    if not isinstance(value,dict) or 'platform_config' not in value or set(value)-allowed:
        raise ValueError('Use platform_config and the documented installation-only fields')
    path=text(value['platform_config'],'platform_config')
    source=platform(path)
    index=value.get('pypi_index','https://pypi.tuna.tsinghua.edu.cn/simple')
    from .units import trial_binding
    return {'platform_config':str(Path(path).absolute()),'pypi_index':mirror(url(index,'pypi_index')),
        'max_model_runs':integer(value.get('max_model_runs',2),'max_model_runs',1,64),
        'admin_assistant':admin_assistant(value.get('admin_assistant'),mode=source['mode']),
        'swarm_ports':swarm_ports(value.get('swarm_ports'),source['port']),
        'trial':trial_binding(value.get('trial'))}


def load(path):
    path=Path(path)
    if path.is_symlink() or not path.is_file():raise ValueError('Deployment input must be a regular private JSON file')
    return validate(read_json(path))
