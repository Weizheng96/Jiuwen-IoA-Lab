"""Private connection input and pinned SSH setup; never contact a real host."""
import base64
import hashlib
import json

import pytest

from tools import server_connection

paramiko = pytest.importorskip('paramiko')


@pytest.fixture
def connection_file(tmp_path):
    key = paramiko.RSAKey.generate(1024)
    value = {'format': 'ioalab.server-connection/1', 'host': '192.0.2.9', 'port': 22,
             'username': 'fixture', 'password': 'fixture-private-password',
             'host_key_type': key.get_name(), 'host_key_base64': key.get_base64(),
             'host_key_sha256': 'SHA256:' + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('='),
             'connect_timeout_seconds': 20}
    path = tmp_path/'connection.json'
    path.write_text(json.dumps(value), encoding='utf-8')
    return path, value, key


@pytest.mark.parametrize('change', ['invalid_json', 'old_text', 'wrong_fingerprint', 'missing_key', 'wrong_host', 'wrong_port'])
def test_invalid_connection_is_rejected_without_private_values(connection_file, change):
    path, value, _ = connection_file
    if change == 'invalid_json': path.write_text('{fixture-private-password', encoding='utf-8')
    elif change == 'old_text': path.write_text('fixture\nfixture-private-password\n192.0.2.9', encoding='utf-8')
    else:
        if change == 'wrong_fingerprint': value['host_key_sha256'] = 'SHA256:wrong'
        elif change == 'missing_key': value.pop('host_key_base64')
        elif change == 'wrong_host': value['host'] = 'not-an-ip'
        else: value['port'] = True
        path.write_text(json.dumps(value), encoding='utf-8')
    with pytest.raises(RuntimeError) as error:
        server_connection.configuration(path)
    assert str(error.value) == 'Private server connection configuration unavailable or invalid'


@pytest.mark.parametrize('port', [22, 2222])
def test_connect_uses_only_selected_json_and_exact_host_key(connection_file, monkeypatch, port):
    path, value, key = connection_file
    value['port'] = port; path.write_text(json.dumps(value), encoding='utf-8')
    observed = []
    def connect(client, hostname, **kwargs):
        name = hostname if port == 22 else f'[{hostname}]:{port}'
        assert client.get_host_keys().check(name, key)
        assert isinstance(client._policy, paramiko.RejectPolicy)
        assert kwargs == {'port': port, 'username': value['username'], 'password': value['password'],
                          'look_for_keys': False, 'allow_agent': False, 'timeout': 20,
                          'banner_timeout': 20, 'auth_timeout': 20}
        observed.append(hostname)
    monkeypatch.setattr(paramiko.SSHClient, 'connect', connect)
    with server_connection.connect(source=path): pass
    assert observed == [value['host']]


def test_changed_host_key_is_not_retried_or_accepted(connection_file, monkeypatch):
    path, value, key = connection_file
    other = paramiko.RSAKey.generate(1024)
    attempts, closed = [], []
    def connect(client, hostname, **kwargs):
        attempts.append(hostname)
        assert not client.get_host_keys().check(hostname, other)
        raise paramiko.BadHostKeyException(hostname, other, key)
    monkeypatch.setattr(paramiko.SSHClient, 'connect', connect)
    monkeypatch.setattr(paramiko.SSHClient, 'close', lambda client: closed.append(True))
    with pytest.raises(paramiko.BadHostKeyException):
        with server_connection.connect(source=path): pytest.fail('untrusted host was accepted')
    assert attempts == [value['host']] and closed == [True]
