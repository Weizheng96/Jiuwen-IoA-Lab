"""Canceled public callers cannot abandon actual Core HTTP/WS/A2C work."""
import asyncio
import json
import threading
import time

import httpx
import pytest

from ioalab.config import Settings
from ioalab.errors import LabError
from ioalab.platform import Platform
from ioalab.store import DataOwner,DataInUse
from ioalab.web.app import create_app


@pytest.mark.parametrize('entry',['http','websocket','a2c','case-admin','case-query'])
def test_canceled_public_caller_keeps_original_writer_and_data_owned(tmp_path,monkeypatch,entry):
    core=Platform(Settings(data=tmp_path,autoseed=False,stub=True))
    app=create_app(core);entered=threading.Event();release=threading.Event();finished=threading.Event();failures=[]
    def writer(*_,**__):
        entered.set()
        try:
            assert release.wait(10)
            assert core.db.one('SELECT 1 AS original')['original']==1
        except BaseException as exc:
            failures.append(exc)
            raise
        finally:finished.set()
        return {}
    if entry=='http':monkeypatch.setattr(core,'components',writer)
    elif entry=='case-admin':monkeypatch.setattr(core.arbitration_cases,'admin_list',writer)
    elif entry=='case-query':monkeypatch.setattr(core.arbitration_cases,'by_admission',writer)
    elif entry=='websocket':monkeypatch.setattr(core.identities,'consume',writer)
    else:
        async def endpoint(scope,receive,send):
            await asyncio.to_thread(writer)
            await send({'type':'http.response.start','status':200,'headers':[]})
            await send({'type':'http.response.body','body':b'{}'})
        core.router.apps['isolated-writer']=endpoint

    async def call():
        if entry!='websocket':
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
                path={'http':'/api/admin/components','case-admin':'/api/admin/arbitrations',
                      'case-query':'/api/v1/arbitrations?agentId=isolated&admissionId=00000000-0000-0000-0000-000000000001'}.get(entry,'/a2a/isolated-writer')
                subject='assistant:admin' if entry=='case-query' else 'admin'
                await client.get(path,headers={'Authorization':'Bearer '+core.auth.token(subject)})
        else:
            queue=asyncio.Queue()
            await queue.put({'type':'websocket.connect'})
            await queue.put({'type':'websocket.receive','text':json.dumps({'type':'connection.auth','ticket':'isolated-test'})})
            async def send(_):pass
            await app({'type':'websocket','asgi':{'version':'3.0','spec_version':'2.4'},'path':'/ws/agents',
                'root_path':'','query_string':b'','headers':[(b'host',b'test')],'scheme':'ws',
                'client':('127.0.0.1',1),'server':('test',80),'subprotocols':[]},queue.get,send)

    async def run():
        caller=asyncio.create_task(call())
        try:
            deadline=time.monotonic()+5
            while not entered.is_set() and time.monotonic()<deadline:await asyncio.sleep(.01)
            assert entered.is_set()
            caller.cancel()
            shutdown=asyncio.create_task(core.shutdown())
            await asyncio.sleep(.04)
            assert not finished.is_set()
            assert not shutdown.done(),'Shutdown abandoned an actual original writer'
            with pytest.raises(DataInUse):DataOwner(tmp_path)
            release.set()
            with pytest.raises(asyncio.CancelledError):await caller
            await shutdown
            assert finished.is_set() and failures==[]
            with DataOwner(tmp_path):pass
        finally:
            release.set()
            if not caller.done():
                caller.cancel()
                await asyncio.gather(caller,return_exceptions=True)
            await core.shutdown()
            deadline=time.monotonic()+5
            while not finished.is_set() and time.monotonic()<deadline:await asyncio.sleep(.01)
    asyncio.run(run())


def test_shutdown_rejects_new_workers_without_running_their_function(tmp_path):
    core=Platform(Settings(data=tmp_path,autoseed=False,stub=True));calls=[]
    async def run():
        assert await core._worker(lambda x,*,y:x+y,2,y=3)==5
        await core.shutdown()
        with pytest.raises(LabError) as error:await core._worker(lambda:calls.append('unexpected'))
        assert error.value.code=='closed'
        assert calls==[]
    asyncio.run(run())
