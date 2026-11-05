"""Readiness failures remain explicit and cannot become a successful smoke probe."""
from types import SimpleNamespace
import httpx
import pytest
from tools import smoke


def test_readiness_retries_transient_connection_failure_and_returns_actual_receipt(monkeypatch):
    calls=[]
    def read():
        calls.append(True)
        if len(calls)==1:raise httpx.ConnectError('not listening yet')
        return {'ipcReady':True,'platformConnected':True}
    monkeypatch.setattr(smoke.time,'sleep',lambda _:None)
    assert smoke._wait(read,SimpleNamespace(poll=lambda:None))=={'ipcReady':True,'platformConnected':True}
    assert len(calls)==2


def test_exited_process_never_becomes_ready_from_a_stale_receipt():
    with pytest.raises(RuntimeError,match='exited'):
        smoke._wait(lambda:{'ipcReady':True},SimpleNamespace(poll=lambda:1))


def test_deadline_reports_failure_instead_of_a_partial_status():
    with pytest.raises(TimeoutError,match='did not become ready'):
        smoke._wait(lambda:None,SimpleNamespace(poll=lambda:None),seconds=0)


@pytest.mark.parametrize('pid,parent,valid',[(10,None,True),(11,10,True),(11,99,False)])
def test_runtime_owner_is_the_launched_process_or_verified_direct_child(monkeypatch,pid,parent,valid):
    child=SimpleNamespace(pid=10,poll=lambda:None)
    state={'pid':pid,'bootId':'original-boot','runtimeId':'original-runtime'}
    monkeypatch.setattr(smoke,'_parent_pid',lambda actual:parent)
    if valid:smoke._assert_owner(child,state,dict(state))
    else:
        with pytest.raises(AssertionError,match='not owned'):smoke._assert_owner(child,state,dict(state))


@pytest.mark.parametrize('field',['pid','bootId','runtimeId'])
def test_authenticated_status_cannot_disagree_with_its_descriptor(field):
    child=SimpleNamespace(pid=10,poll=lambda:None)
    state={'pid':10,'bootId':'original-boot','runtimeId':'original-runtime'}
    with pytest.raises(AssertionError,match='disagree'):
        smoke._assert_owner(child,state,{**state,field:'foreign-instance'})


def test_parent_lookup_failure_cannot_accept_an_unverified_runtime(monkeypatch):
    def unavailable(pid):raise RuntimeError('CIM lookup unavailable')
    monkeypatch.setattr(smoke,'_parent_pid',unavailable)
    child=SimpleNamespace(pid=10,poll=lambda:None)
    state={'pid':11,'bootId':'boot','runtimeId':'runtime'}
    with pytest.raises(RuntimeError,match='CIM lookup unavailable'):
        smoke._assert_owner(child,state,dict(state))


def test_stop_receipt_requires_zero_exit_and_actual_lease_release(tmp_path,monkeypatch):
    from ioalab_sdk.runtime.lease import Lease
    calls=[]
    rpc=SimpleNamespace(call=lambda operation,args:(calls.append((operation,args)) or {'status':'completed'}))
    child=SimpleNamespace(wait=lambda **kwargs:0)
    with Lease(tmp_path/'client.lock',timeout=0):
        smoke._assert_lease_held(tmp_path)
        with pytest.raises(RuntimeError):smoke._stop_participant(rpc,{'bootId':'original'},child,tmp_path)
    smoke._stop_participant(rpc,{'bootId':'original'},child,tmp_path)
    assert calls==[('runtime.stop',{'bootId':'original'})]*2
    with pytest.raises(AssertionError,match='does not own'):smoke._assert_lease_held(tmp_path)
    with pytest.raises(AssertionError,match='exit cleanly'):
        smoke._stop_participant(rpc,{'bootId':'original'},SimpleNamespace(wait=lambda **kwargs:1),tmp_path)


def test_windows_cleanup_waits_for_the_actual_owned_job_after_front_exit(monkeypatch):
    calls=[];members=iter([[123],[123],[]])
    class Terminate:
        def __call__(self,handle,code):calls.append(('stop',handle,code));return True
    kernel=SimpleNamespace(TerminateJobObject=Terminate(),CloseHandle=lambda handle:calls.append(('close',handle)))
    api=SimpleNamespace(api=kernel,jobs={'owned':77},members=lambda name:next(members))
    process=SimpleNamespace(_smoke_job=(api,'owned'),poll=lambda:0,wait=lambda **kwargs:calls.append(('wait',)))
    monkeypatch.setattr(smoke.time,'sleep',lambda _:None)
    smoke._terminate_windows(process)
    assert calls==[('stop',77,1),('wait',),('close',77)]
    assert api.jobs=={}


def test_windows_cleanup_refuses_an_unknown_job_without_signalling():
    with pytest.raises(RuntimeError,match='no verified Windows job'):
        smoke._terminate_windows(SimpleNamespace(poll=lambda:None))


def test_windows_cleanup_timeout_retains_owned_job_and_cannot_claim_release():
    class Terminate:
        def __call__(self,*args):return True
    api=SimpleNamespace(api=SimpleNamespace(TerminateJobObject=Terminate()),
        jobs={'owned':77},members=lambda name:[123])
    with pytest.raises(TimeoutError,match='still has live members'):
        smoke._terminate_windows(SimpleNamespace(_smoke_job=(api,'owned')),seconds=0)
    assert api.jobs=={'owned':77}


def test_released_tree_waits_for_transient_windows_handle_rundown(monkeypatch,tmp_path):
    calls=[]
    locked=OSError('fixture handle pending');locked.winerror=32
    def remove(folder):
        calls.append(folder)
        if len(calls)==1:raise locked
    monkeypatch.setattr(smoke.shutil,'rmtree',remove)
    monkeypatch.setattr(smoke.time,'sleep',lambda _:None)
    smoke._remove_released_tree(tmp_path)
    assert calls==[tmp_path,tmp_path]


@pytest.mark.parametrize('winerror',[32,5])
def test_unreleased_or_unknown_tree_error_remains_failure(monkeypatch,tmp_path,winerror):
    locked=OSError('fixture cannot release');locked.winerror=winerror
    def remove(folder):raise locked
    monkeypatch.setattr(smoke.shutil,'rmtree',remove)
    with pytest.raises(OSError) as error:smoke._remove_released_tree(tmp_path,seconds=0)
    assert error.value is locked
