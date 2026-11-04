"""Internal public-key and service calls must reach the actual bind address."""
import socket
import threading

import httpx
import pytest
import uvicorn

from conftest import wait_for
from ioalab.config import Settings
from ioalab.app import Platform
from ioalab.web.app import create_app


@pytest.mark.parametrize('bind,authority',[
    ('127.0.0.1','127.0.0.1'),('0.0.0.0','127.0.0.1'),('192.0.2.7','192.0.2.7'),
    ('::1','[::1]'),('::','[::1]'),('2001:db8::7','[2001:db8::7]')])
def test_internal_address_uses_bound_interface_or_same_family_loopback(bind,authority):
    settings=Settings(bind=bind,port=18750,public_url_env='')
    expected='http://'+authority+':18750'
    assert settings.local_url==settings.public_url==settings.base_url==expected
    external=Settings(bind=bind,port=18750,public_url_env='https://ioa.example')
    assert external.local_url==expected and external.public_url=='https://ioa.example'


def test_ipv6_only_core_is_reachable_through_its_advertised_local_url(tmp_path):
    try:
        sock=socket.socket(socket.AF_INET6,socket.SOCK_STREAM);sock.bind(('::1',0))
    except OSError:
        pytest.skip('Host has no IPv6 loopback listener')
    port=sock.getsockname()[1];sock.close()
    platform=Platform(Settings(bind='::1',port=port,public_url_env='',data=tmp_path/'core',autoseed=False))
    server=uvicorn.Server(uvicorn.Config(create_app(platform),host='::1',port=port,log_level='critical'))
    thread=threading.Thread(target=server.run,daemon=True);thread.start()
    try:
        wait_for(lambda:server.started)
        with httpx.Client(trust_env=False,timeout=5) as client:
            config=client.get(platform.s.local_url+'/api/config').raise_for_status().json()
            assert config['pay'].startswith('http://[::1]:')
            assert client.get(config['pay']+'/.well-known/jwks.json').status_code==200
    finally:
        server.should_exit=True;thread.join(8)
        assert not thread.is_alive()
