"""Shutdown requires original authenticated ownership and actual released execution leases."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
from pathlib import Path
import threading
import time
import uuid

import pytest
from conftest import wait_for
from test_official_deployment import deployment,prepare,active,UnusedHost
from ioalab_services import coordinator
from ioalab_services.lifecycle import Shutdown,released,serve,stop
from ioalab_sdk.runtime.lease import Lease
from ioalab_sdk.provider import ProviderRole
from ioalab_sdk.runtime.config import RuntimeConfig
from ioalab_sdk.runtime.files import private_json,read_json
from ioalab_sdk.runtime.ipc import LocalClient
from ioalab_sdk.runtime.protocol import RuntimeFault
from ioalab_sdk.runtime.resident import ResidentRuntime
from ioalab_sdk.runtime.scheduling import JobHandler,JobOutcome


@contextmanager
def running(deployment,jobs):
    account=deployment.domains[0]['account'];config=RuntimeConfig.load(deployment.config_path(account));host=UnusedHost()
    runtime=ResidentRuntime(config,modules=(ProviderRole(host),),host_status=host.capabilities,jobs=jobs,assembly='ioalab-jiuwen')
    errors=[]
    def work():
        try:runtime.run()
        except BaseException as exc:errors.append(exc)
        finally:runtime.close()
    thread=threading.Thread(target=work,daemon=True);thread.start()
    try:
        def ready():
            try:result=LocalClient(config.home).call('runtime.status')
            except RuntimeFault:return False
            return (result['status']=='completed' and result['data']['bootId']==runtime.ipc.boot_id
                    and result['data']['ipcReady'] and result['data']['platformConnected'])
        wait_for(ready)
        yield runtime
    finally:
        if thread.is_alive():
            try:LocalClient(config.home,boot_id=runtime.ipc.boot_id).call('runtime.stop',{'bootId':runtime.ipc.boot_id})
            except RuntimeFault:
                # A normal close may remove its descriptor before this join.
                # Do not infer release: the worker and OS lease are checked.
                pass
        thread.join(10)
        assert not thread.is_alive() and not errors,errors
        assert released(config.home),'The original runtime directory still has an execution owner'


def test_active_job_drains_then_original_queued_job_identity_and_session_resume(deployment):
    prepare(deployment);entered=threading.Event();release=threading.Event();seen=[]
    def job(item):
        seen.append(item['payload']['n'])
        if item['payload']['n']==1:entered.set();assert release.wait(10)
        return JobOutcome('completed',{'n':item['payload']['n']},session='original-session')
    jobs={'fixture.hold':JobHandler(job,'fixture-v1',False)}
    with running(deployment,jobs) as runtime:
        actor=runtime.identities[0].agent_id
        runtime.scheduler.submit(actor,'original-interaction','fixture.hold',{'n':1},operation_id=str(uuid.uuid4()),session='original-session')
        assert entered.wait(5)
        queued=runtime.scheduler.submit(actor,'original-interaction','fixture.hold',{'n':2},operation_id=str(uuid.uuid4()),session='original-session')
        try:
            group=Shutdown(deployment);pending=group.wait(.1)
            assert pending['status']=='waiting' and not pending['allRuntimesReleased']
            assert runtime.stop_requested.is_set() and not released(runtime.config.home)
            assert runtime.scheduler.get(queued['reference'])['status']=='queued' and seen==[1]
        finally:release.set()
        assert group.wait(8)['status']=='completed'
    with running(deployment,jobs) as resumed:
        assert resumed.identities[0].agent_id==actor
        wait_for(lambda:resumed.scheduler.get(queued['reference'])['status']=='completed')
        original=resumed.scheduler.get(queued['reference'])
        assert original['sessionId']=='original-session' and original['interactionId']=='original-interaction' and seen==[1,2]


def test_idle_completed_stop_ack_is_not_directory_release(deployment):
    prepare(deployment)
    with active(deployment) as (runtime,ipc,_):
        group=Shutdown(deployment);result=group.poll()
        assert result['status']=='waiting' and result['accounts'][0]['phase']=='draining'
        assert runtime.scheduler.idle and runtime.stop_requested.is_set() and not released(runtime.config.home)
    assert group.poll()['allRuntimesReleased']


def test_lost_stop_reply_keeps_original_boot_and_operation_until_release(deployment,monkeypatch):
    prepare(deployment);original=LocalClient.call;calls=[]
    def lose(client,name,*args,**kwargs):
        reply=original(client,name,*args,**kwargs)
        if name=='runtime.stop':
            calls.append((client.boot_id,kwargs['operation_id']))
            if len(calls)==1:raise RuntimeFault('unknown_result','isolated stop response loss')
        return reply
    monkeypatch.setattr(LocalClient,'call',lose)
    with active(deployment) as (runtime,_,_):
        group=Shutdown(deployment)
        assert group.poll()['status']=='waiting' and group.poll()['status']=='waiting'
        assert len(calls)==2 and calls[0]==calls[1] and calls[0][0]==runtime.ipc.boot_id
    assert group.poll()['status']=='completed'


def test_unreachable_ipc_with_live_lease_never_means_stopped(deployment):
    prepare(deployment);group=Shutdown(deployment);home=group.targets[0]['home']
    with Lease(home/'client.lock',timeout=0):
        result=group.wait(.05)
        assert result['status']=='waiting' and result['accounts'][0]['error']['code']=='not_ready'
        assert group.targets[0]['bootId'] is None
    assert group.poll()['status']=='completed'


def test_replacement_instance_is_not_stopped_by_original_shutdown(deployment):
    prepare(deployment)
    with active(deployment) as (original,_,_):
        group=Shutdown(deployment);assert group.poll()['status']=='waiting'
        boot=original.ipc.boot_id
    with active(deployment) as (replacement,_,_):
        assert replacement.ipc.boot_id!=boot
        result=group.poll()
        assert result['status']=='rejected' and result['accounts'][0]['error']['code']=='instance_changed'
        assert not replacement.stop_requested.is_set()


def test_identity_mismatch_does_not_send_stop(deployment):
    prepare(deployment)
    with active(deployment) as (runtime,_,_):
        group=Shutdown(deployment);group.targets[0]['expected']={}
        result=group.poll()
        assert result['status']=='rejected' and result['accounts'][0]['error']['code']=='identity_conflict'
        assert not runtime.stop_requested.is_set()


def test_edited_configuration_does_not_redirect_diagnostic_stop(deployment,tmp_path,monkeypatch):
    prepare(deployment)
    with active(deployment) as (runtime,_,account):
        path=deployment.config_path(account);value=read_json(path)
        foreign=tmp_path/'owner-edited-home';foreign.mkdir();sentinel=foreign/'owner.txt';sentinel.write_text('preserved')
        value.update(data_dir=str(foreign));value.pop('password',None);value['password_env']='MISSING_DIAGNOSTIC_PASSWORD'
        private_json(path,value)
        monkeypatch.delenv('MISSING_DIAGNOSTIC_PASSWORD',raising=False)
        assert stop(deployment)['status']=='waiting' and runtime.stop_requested.is_set()
        assert sentinel.read_text()=='preserved' and list(foreign.iterdir())==[sentinel]
    assert stop(deployment)['status']=='completed'


def test_independent_stop_rejects_active_deployment_owner(deployment):
    prepare(deployment)
    with active(deployment) as (runtime,_,_):
        with deployment.lock():
            with pytest.raises(RuntimeFault,match='service manager'):stop(deployment)
        assert not runtime.stop_requested.is_set()


def test_coordinator_keeps_deployment_lock_until_actual_sdk_release(deployment,monkeypatch):
    prepare(deployment);event=threading.Event()
    def finish_tick(*_args,**kwargs):
        assert kwargs['stop_event'] is event
        event.set();return {'status':'waiting','allServicesReady':False}
    monkeypatch.setattr(coordinator,'tick',finish_tick)
    with active(deployment) as (runtime,_,_):
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(serve,deployment,stop_event=event,interval=.01)
            try:
                wait_for(lambda:runtime.stop_requested.is_set())
                assert not future.done()
                with pytest.raises(RuntimeError):deployment.lock()
            finally:runtime.close()
            assert future.result(timeout=8)==0
    with deployment.lock():pass


def test_stopping_tick_does_not_launch_other_accounts_or_publish(deployment,monkeypatch):
    prepare(deployment);event=threading.Event();event.set()
    monkeypatch.setattr(coordinator,'connected',lambda *_a,**_k:pytest.fail('A stopped tick launched a runtime'))
    result=coordinator.tick(deployment,stop_event=event)
    assert result['status']=='waiting' and result['stopping'] and not result['allServicesReady']


def test_stop_after_connection_prevents_new_publication(deployment,monkeypatch):
    prepare(deployment);event=threading.Event();entry=next(iter(deployment.state(prepared=True)['accounts'].values()))
    def connection(*_a,**_k):
        event.set();return object(),{'identities':entry['identities'],'platformConnected':True,'host':{'available':True},'stopping':False}
    monkeypatch.setattr(coordinator,'connected',connection)
    result=coordinator.tick(deployment,stop_event=event)
    assert result['stopping'] and result['accounts'][0]['services']==[]


def test_stop_cli_reports_waiting_then_actual_release_without_credentials(deployment,capsys):
    from ioalab_services.__main__ import main
    prepare(deployment)
    with active(deployment):
        assert main(['stop','--config',str(deployment.path),'--wait-seconds','.05'])==2
        result=json.loads(capsys.readouterr().out)
        assert result['status']=='waiting' and not result['allRuntimesReleased']
    assert main(['stop','--config',str(deployment.path)])==0
    assert json.loads(capsys.readouterr().out)['allRuntimesReleased']


def test_missing_ownership_evidence_is_waiting_without_recreating_it(deployment):
    prepare(deployment);group=Shutdown(deployment);path=group.targets[0]['home']/'client.lock'
    backup=path.with_suffix('.preserved');path.rename(backup)
    result=group.poll()
    assert result['status']=='waiting' and result['accounts'][0]['error']['code']=='not_ready'
    assert not path.exists() and backup.is_file()


@pytest.mark.parametrize('seconds',[-1,31,float('nan')])
def test_invalid_stop_wait_cannot_begin_shutdown(deployment,seconds):
    with pytest.raises(RuntimeFault,match='zero and thirty'):stop(deployment,wait_seconds=seconds)


def test_already_released_target_cannot_start_a_new_stop_on_replacement(deployment):
    prepare(deployment);group=Shutdown(deployment)
    assert group.poll()['status']=='completed'
    with active(deployment) as (replacement,_,_):
        result=group.poll()
        assert result['status']=='rejected' and result['accounts'][0]['error']['code']=='instance_changed'
        assert not replacement.stop_requested.is_set()


def test_stop_received_during_preview_prevents_new_publish(platform,deployment,monkeypatch):
    from ioalab_services.authorization import authorize
    prepare(deployment);authorize(deployment,platform.auth.token('admin'));event=threading.Event();original=LocalClient.call
    def stopping(client,name,*args,**kwargs):
        assert name!='provider.publish','A cancelled tick created a publication'
        result=original(client,name,*args,**kwargs)
        if name=='provider.preview':event.set()
        return result
    with active(deployment) as (runtime,_,_):
        monkeypatch.setattr(LocalClient,'call',stopping)
        result=coordinator.tick(deployment,launch=False,stop_event=event)
        assert event.is_set() and result['stopping'] and not result['allServicesReady']
        actual=platform.publications.status(runtime.account.account['id'],runtime.identities[0].agent_id)
        assert not actual['published']


def test_active_job_keeps_execution_permit_through_async_callback_while_draining(deployment):
    import asyncio
    prepare(deployment);entered=threading.Event();release=threading.Event();finished=[]
    def job(item):
        entered.set();assert release.wait(10)
        runtime.require_execution()
        async def callback():await asyncio.to_thread(runtime.require_execution)
        asyncio.run(callback());finished.append(item['id'])
        return JobOutcome('completed',{'toolCompletedAfterDrain':True})
    with running(deployment,{'fixture.drain':JobHandler(job,'fixture-drain-v1',False)}) as runtime:
        task=runtime.scheduler.submit(runtime.identities[0].agent_id,'drain-tools','fixture.drain',{},operation_id=str(uuid.uuid4()))
        assert entered.wait(5)
        try:
            group=Shutdown(deployment);assert group.poll()['status']=='waiting'
            with pytest.raises(RuntimeFault):runtime.require_execution()
            with pytest.raises(RuntimeFault):runtime.scheduler.submit(runtime.identities[0].agent_id,'new-after-stop','fixture.drain',{},operation_id=str(uuid.uuid4()))
        finally:release.set()
        assert group.wait(8)['allRuntimesReleased']
    assert finished==[task['reference']],'Stopping interrupted the original active task tool callback'


def test_completed_job_context_cannot_reuse_draining_execution_permit(deployment):
    from contextvars import copy_context
    prepare(deployment);captured=[]
    with active(deployment) as (runtime,ipc,_):
        runtime.scheduler.handlers['fixture.capture']=JobHandler(lambda job:(captured.append(copy_context()) or JobOutcome('completed',{})),'capture-v1',False)
        task=runtime.scheduler.submit(runtime.identities[0].agent_id,'capture-context','fixture.capture',{},operation_id=str(uuid.uuid4()))
        wait_for(lambda:runtime.scheduler.get(task['reference'])['status']=='completed' and runtime.scheduler.idle)
        ipc.call('runtime.stop',{'bootId':runtime.ipc.boot_id})
        assert runtime.connection.connected
        with pytest.raises(RuntimeFault):captured[0].run(runtime.require_execution)


def test_active_draining_job_still_rejects_connection_loss_and_foreign_context(deployment):
    from ioalab_sdk.runtime.scheduling import CURRENT_JOB
    prepare(deployment);entered=threading.Event();release=threading.Event();checks=[]
    def job(item):
        entered.set();assert release.wait(10)
        token=CURRENT_JOB.set((object(),item['id']))
        try:
            with pytest.raises(RuntimeFault):runtime.require_execution()
        finally:CURRENT_JOB.reset(token)
        prior_error=runtime.connection.error;runtime.connection.error=RuntimeFault('not_ready','isolated connection loss')
        try:
            with pytest.raises(RuntimeFault):runtime.require_execution()
        finally:runtime.connection.error=prior_error
        runtime.require_execution();checks.append(True)
        return JobOutcome('completed',{})
    with running(deployment,{'fixture.guard':JobHandler(job,'guard-v1',False)}) as runtime:
        runtime.scheduler.submit(runtime.identities[0].agent_id,'guard-context','fixture.guard',{},operation_id=str(uuid.uuid4()))
        assert entered.wait(5)
        try:
            group=Shutdown(deployment);pending=group.poll()
            assert pending['status']=='waiting' and any(row['phase']=='draining' for row in pending['accounts'])
            assert runtime.stop_requested.is_set(),'Negative execution checks require the original stop to be received first'
        finally:release.set()
        assert group.wait(8)['allRuntimesReleased']
    assert checks==[True]


def test_user_wait_context_does_not_receive_draining_execution_permit(deployment):
    prepare(deployment);entered=threading.Event();release=threading.Event();denied=[]
    def job(item):
        try:
            with runtime.scheduler.user_wait(item['id']):
                entered.set();assert release.wait(10)
                with pytest.raises(RuntimeFault):runtime.require_execution()
                denied.append(True)
        except RuntimeFault as exc:assert exc.code=='closed'
        return JobOutcome('waiting',{'needsUser':True})
    with running(deployment,{'fixture.userwait':JobHandler(job,'userwait-v1',True)}) as runtime:
        runtime.scheduler.submit(runtime.identities[0].agent_id,'wait-context','fixture.userwait',{},operation_id=str(uuid.uuid4()))
        assert entered.wait(5)
        try:
            group=Shutdown(deployment);pending=group.poll()
            assert pending['status']=='waiting' and any(row['phase']=='draining' for row in pending['accounts'])
            assert runtime.stop_requested.is_set(),'Negative execution checks require the original stop to be received first'
        finally:release.set()
        assert group.wait(8)['allRuntimesReleased']
    assert denied==[True]
