"""Real HTTP infrastructure and an independent provider host, explicit sample mode."""
import os
import socket
import sys
import threading
import time
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]
CHAIN_LAYER=ROOT/'tests/chain_layer.json'


def pytest_configure(config):
    config.addinivalue_line('markers','chain: 整链路、真实进程及 opt-in 真实宿主用例；清单在 tests/chain_layer.json，由 --layer fast/chain 选择')


def pytest_collection_modifyitems(config,items):
    """Manifest functions carry the chain marker; parametrizations follow their function."""
    if not CHAIN_LAYER.is_file():return
    import json
    listed={(path,name) for path,names in json.loads(CHAIN_LAYER.read_text(encoding='utf-8'))['chain'].items() for name in names}
    for item in items:
        rest=item.nodeid.split('::')[1:]
        try:path=item.path.resolve().relative_to(ROOT).as_posix()  # independent of pytest rootdir
        except ValueError:continue
        if rest and (path,'::'.join([*rest[:-1],rest[-1].split('[')[0]])) in listed:item.add_marker(pytest.mark.chain)


def platform_settings(**values):
    """Explicit legacy funding profile for protocol tests, separate from product defaults."""
    from ioalab.config import Settings
    return Settings(**{'autoseed':True,'stub':True,'admin_password':'test',
        'seed_file':ROOT/'deploy/default-seed.json','initial_credit':'0','repeat_daily_credit':True,
        'auth_max_failures':5,'auth_window_seconds':3600,'local_unlimited':False,**values})


def write_platform_configuration(directory,**runtime):
    from ioalab.configuration import PlatformConfiguration,defaults
    from ioalab.configuration_permissions import protect_new
    directory.mkdir();protect_new(directory,directory=True)
    document=defaults();document['runtime'].update({'mode':'dev','stub':True,'autoseed':False,**runtime})
    return PlatformConfiguration.create(directory/'platform.json',document)

def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1',0));return s.getsockname()[1]

def wait_for(predicate,timeout=20):
    until=time.monotonic()+timeout
    while time.monotonic()<until:
        if predicate(): return
        time.sleep(.05)
    raise AssertionError('Timed out waiting for a participant')

@pytest.fixture(scope='session')
def platform(tmp_path_factory):
    import uvicorn
    from ioalab.config import Settings
    from ioalab.app import Platform
    from ioalab.web.app import create_app
    # This protocol fixture retains its deliberately funded-on-login profile.
    # Default deployment awards and daily eligibility have their own tests.
    pf=Platform(platform_settings(port=free_port(),data=tmp_path_factory.mktemp('core')))
    app=create_app(pf);pf.app=app
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=pf.s.port,log_level='critical'))
    thread=threading.Thread(target=server.run,daemon=True);thread.start()
    wait_for(lambda:server.started)
    yield pf
    server.should_exit=True;thread.join(8)

@pytest.fixture
def client(platform):
    import httpx
    with httpx.Client(base_url=platform.s.base_url,trust_env=False,timeout=120) as http:
        yield http

@pytest.fixture
def protocol_participant(platform,tmp_path):
    """Real account/identity owner; protocol tests give Peer exclusive WS control."""
    from ioalab_sdk.runtime.config import RuntimeConfig
    from ioalab_sdk.runtime.files import private_json
    from ioalab_sdk.runtime.resident import ResidentRuntime
    from ioalab_sdk.runtime.ipc import LocalClient
    from _support import new_user,PASSWORD
    user=new_user(platform)
    path=tmp_path/'protocol-participant.json'
    private_json(path,{'platform_url':platform.s.base_url,'username':user['username'],'password':PASSWORD,
                       'data_dir':'./protocol-state','identities':[{'key':'personal','role':'assistant','name':'协议测试参与方'}]})
    runtime=ResidentRuntime(RuntimeConfig.load(path))
    # The transport under test, not a competing resident reconnect loop, owns WS.
    runtime.ipc.start()
    try:
        result=LocalClient(runtime.config.home).call('identity.status')
        assert result['status']=='completed' and len(result['data'])==1
        yield runtime
    finally:runtime.close()


@pytest.fixture
def public_transport():
    import httpx
    from a2c.binding import A2ATransport
    transport=A2ATransport(http=lambda:httpx.AsyncClient(timeout=30,trust_env=False))
    try:yield transport
    finally:transport.close()
