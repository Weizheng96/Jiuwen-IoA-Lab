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


def validate(value):
    allowed = {'public_url', 'bind', 'port', 'mode', 'pypi_index', 'model', 'admin_password', 'max_model_runs', 'isc_count'}
    if not isinstance(value, dict) or 'public_url' not in value or set(value) - allowed:
        raise ValueError('Use the documented deployment JSON fields')
    result = {'public_url': url(value['public_url'], 'public_url', root=True),
              'port': integer(value.get('port', 8750), 'port', 1, 65535),
              'mode': value.get('mode', 'trial'), 'bind': value.get('bind', '127.0.0.1'),
              'max_model_runs': integer(value.get('max_model_runs', 2), 'max_model_runs', 1, 64),
              'isc_count': integer(value.get('isc_count', 10), 'isc_count', 1, 10000),
              'model': model(value.get('model')), 'admin_password': value.get('admin_password', '')}
    if result['mode'] not in ('trial', 'dev'):
        raise ValueError('Invalid deployment mode')
    if not isinstance(result['bind'], str):
        raise ValueError('Deployment bind must be an IP address')
    try:
        ipaddress.ip_address(result['bind'])
    except (ValueError, TypeError):
        raise ValueError('Deployment bind must be an IP address') from None
    index = value.get('pypi_index', 'https://pypi.tuna.tsinghua.edu.cn/simple')
    result['pypi_index'] = mirror(url(index, 'pypi_index'))
    if result['admin_password']:
        text(result['admin_password'], 'admin_password')
        if result['mode'] == 'trial' and len(result['admin_password']) < 12:
            raise ValueError('Trial administrator password requires at least 12 characters')
    elif not isinstance(result['admin_password'], str):
        raise ValueError('Invalid deployment field: admin_password')
    return result


def load(path):
    path = Path(path)
    if path.is_symlink():
        raise ValueError('Deployment input must be a regular private JSON file')
    if not path.is_file():
        raise ValueError('Deployment input must be a regular private JSON file')
    return validate(read_json(path))
