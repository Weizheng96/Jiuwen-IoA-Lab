"""Idle observation must not retain subscriptions or prevent normal server exit."""
import asyncio
import threading
from types import SimpleNamespace

import httpx
import pytest
import uvicorn
from websockets.sync.client import connect

from conftest import free_port, platform_settings, wait_for
from ioalab.app import Platform
from ioalab.web.app import create_app
from ioalab.web.cookies import session_cookie_name


@pytest.mark.parametrize('stop_with_connection_open',[False,True])
def test_admin_idle_socket_releases_route_and_server(tmp_path,stop_with_connection_open):
    port=free_port()
    platform=Platform(platform_settings(data=tmp_path/'core',port=port))
    server=uvicorn.Server(uvicorn.Config(create_app(platform),host='127.0.0.1',port=port,log_level='critical'))
    thread=threading.Thread(target=server.run,daemon=True);thread.start()
    wait_for(lambda:server.started)
    socket=None
    try:
        with httpx.Client(base_url=f'http://127.0.0.1:{port}',trust_env=False) as http:
            http.post('/api/admin/login',json={'username':'admin','password':'test'}).raise_for_status()
            socket=connect(f'ws://127.0.0.1:{port}/ws/admin',additional_headers={'Cookie':'ioalab_session='+http.cookies['ioalab_session']})
        wait_for(lambda:len(platform.bus.subscribers)==1)
        if stop_with_connection_open:
            server.should_exit=True
        else:
            socket.close()
            wait_for(lambda:not platform.bus.subscribers)
            server.should_exit=True
        thread.join(5)
        assert not thread.is_alive(), 'Normal shutdown retained an idle administrator route'
        assert not platform.bus.subscribers and not server.server_state.tasks
    finally:
        if socket is not None:socket.close()
        server.should_exit=True
        if thread.is_alive():
            server.force_exit=True;thread.join(5)


@pytest.mark.parametrize('failure',['send','receive','cancel'])
def test_admin_route_cleans_both_tasks_on_failure_or_cancellation(platform,failure):
    endpoint=next(r.endpoint for r in create_app(platform).routes if getattr(r,'path',None)=='/ws/admin')
    async def run():
        before=set(asyncio.all_tasks());ready=asyncio.Event()
        async def accept():ready.set()
        async def send(_):
            if failure=='send':raise OSError('disconnected sender')
        async def receive():
            if failure=='receive':raise OSError('disconnected receiver')
            await asyncio.Future()
        ws=SimpleNamespace(cookies={'ioalab_session':platform.auth.token('admin')},headers={},accept=accept,send_text=send,receive=receive)
        task=asyncio.create_task(endpoint(ws));await ready.wait()
        await asyncio.sleep(0)
        if failure=='cancel':task.cancel()
        try:await asyncio.wait_for(task,1)
        except asyncio.CancelledError:
            assert failure=='cancel'
        assert not platform.bus.subscribers
        assert not (set(asyncio.all_tasks())-before), 'Route retained its sibling task'
    asyncio.run(run())


def test_https_admin_socket_rejects_same_host_insecure_origin(platform,monkeypatch):
    monkeypatch.setattr(platform.s,'public_url_env','https://192.0.2.10')
    endpoint=next(r.endpoint for r in create_app(platform).routes if getattr(r,'path',None)=='/ws/admin')
    async def run():
        closed=[]
        async def close(*,code):closed.append(code)
        ws=SimpleNamespace(cookies={session_cookie_name(platform.s.public_url):platform.auth.token('admin')},
                           headers={'origin':'http://192.0.2.10','host':'192.0.2.10'},close=close)
        await endpoint(ws)
        assert closed==[4403] and not platform.bus.subscribers
    asyncio.run(run())
