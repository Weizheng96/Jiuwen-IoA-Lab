"""Real HTTP infrastructure and an independent provider host, explicit sample mode."""
import os
import socket
import sys
import threading
import time
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]
os.environ.update(IOALAB_STUB='1',IOALAB_ADMIN_PASSWORD='test',IOALAB_AUTOSEED='1',
                  IOALAB_SEED_FILE=str(ROOT/'deploy/default-seed.json'))

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
    from ioalab.platform import Platform
    from ioalab.web.app import create_app
    pf=Platform(Settings(port=free_port(),data=tmp_path_factory.mktemp('core'),autoseed=True))
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

@pytest.fixture(scope='session')
def providers(platform,tmp_path_factory):
    import uvicorn
    from serve import ServiceHost,create_app
    from catalog import definitions
    selected=[next(d for d in definitions() if d.get('formation','preapproval')==f) for f in ('preapproval','countersign','proposal')]
    port=free_port()
    ids=[d['id'] for d in selected]+['wechat-posts']
    host=ServiceHost(platform=platform.s.base_url,public_url=f'http://127.0.0.1:{port}',data=tmp_path_factory.mktemp('providers'),
                     stub=True,only=ids)
    host.selected=selected
    host.all_definitions=definitions()
    server=uvicorn.Server(uvicorn.Config(create_app(host),host='127.0.0.1',port=port,log_level='critical'))
    thread=threading.Thread(target=server.run,daemon=True);thread.start()
    wait_for(lambda:host.ready or host.error)
    assert host.ready,host.error
    yield host
    server.should_exit=True;thread.join(8)

@pytest.fixture
def local_client(platform,tmp_path):
    from ioalab_sdk import LabClient
    from _support import new_user,PASSWORD
    user=new_user(platform)
    client=LabClient(platform.s.base_url,user['username'],PASSWORD,tmp_path/'local',source='experiment')
    yield client
    client.close()
