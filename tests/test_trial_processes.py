import json
from pathlib import Path
import threading
from types import SimpleNamespace
import pytest

from ioalab_trial import processes,agent


def test_pid1_namespace_is_required_before_process_scan_or_signal(monkeypatch):
    monkeypatch.setattr(processes.os,'getpid',lambda:123)
    monkeypatch.setattr(processes.os,'kill',lambda *a:pytest.fail('Unexpected signal'))
    with pytest.raises(RuntimeError,match='PID1'):processes.terminate_owned({10003})


def test_detached_owned_worker_is_normally_stopped_and_pid_replacement_refused(monkeypatch):
    row={'pid':78,'uid':10003,'parent':1,'start':'11','state':'S'};live=[row];sent=[]
    monkeypatch.setattr(processes,'descendants',lambda:list(live))
    monkeypatch.setattr(processes,'require_pid1',lambda:None)
    monkeypatch.setattr(processes.os,'WNOHANG',1,raising=False)
    monkeypatch.setattr(processes.os,'waitpid',lambda *a:(_ for _ in ()).throw(ChildProcessError()))
    def signal(pid,sig):sent.append((pid,sig));live.clear()
    monkeypatch.setattr(processes.os,'kill',signal)
    processes.terminate_owned({10003})
    assert sent==[(78,processes.signal.SIGTERM)]
    queries=iter([[row],[{**row,'start':'replacement'}]])
    monkeypatch.setattr(processes,'descendants',lambda:next(queries))
    with pytest.raises(RuntimeError,match='changed'):processes.terminate_owned({10003})
    assert len(sent)==1


def test_unknown_root_process_is_not_signalled(monkeypatch):
    monkeypatch.setattr(processes,'require_pid1',lambda:None)
    monkeypatch.setattr(processes.os,'listdir',lambda path:['1','8'])
    monkeypatch.setattr(processes,'identity',lambda pid:{'pid':pid,'uid':0,'parent':1,'start':'1','state':'S'})
    with pytest.raises(RuntimeError,match='Unknown process'):processes.descendants()


@pytest.mark.parametrize('replaced',[False,True])
def test_reap_only_adopted_target_uid_without_consuming_other_role_exit(monkeypatch,replaced):
    worker={'pid':78,'uid':10003,'parent':1,'start':'11','state':'Z'}
    core={'pid':41,'uid':10001,'parent':1,'start':'9','state':'Z'}
    reaped=[]
    monkeypatch.setattr(processes,'require_pid1',lambda:None)
    monkeypatch.setattr(processes,'descendants',lambda:[worker,core])
    monkeypatch.setattr(processes,'identity',lambda pid:{**worker,'start':'replacement'} if replaced else worker)
    monkeypatch.setattr(processes.os,'WNOHANG',1,raising=False)
    def wait(pid,flags):
        assert pid==78, 'Other roles must retain their Popen wait owner and exit code'
        reaped.append(pid)
        return pid,0
    monkeypatch.setattr(processes.os,'waitpid',wait)
    monkeypatch.setattr(processes.os,'kill',lambda *args:pytest.fail('Zombie must not receive a signal'))
    if replaced:
        with pytest.raises(RuntimeError,match='changed'):processes.terminate_owned({10003})
        assert reaped==[]
    else:
        processes.terminate_owned({10003})
        assert reaped==[78]


def test_sdk_drain_and_detached_hosts_finish_before_core_shutdown(tmp_path,monkeypatch):
    owner=object.__new__(agent.SeatProcesses);owner.control=tmp_path;owner.readers=[]
    owner.record={'roles':{}};calls=[]
    owner.signal=lambda name:calls.append(('signal',name))
    owner.wait=lambda name:calls.append(('wait',name))
    monkeypatch.setattr(processes,'terminate_owned',lambda uids:calls.append(('descendants',uids)))
    monkeypatch.setattr(processes,'descendants',lambda:[])
    owner.shutdown()
    assert calls.index(('wait','services'))<calls.index(('signal','native'))
    assert calls.index(('descendants',{10003}))<calls.index(('signal','core'))
    assert json.loads((tmp_path/'lifecycle.json').read_text())['phase']=='stopped'


@pytest.mark.parametrize('ready_code',[0,1])
def test_core_public_readiness_precedes_sdk_launch(tmp_path,monkeypatch,ready_code):
    for name in ('core','native','assistant','services'):(tmp_path/name).mkdir()
    monkeypatch.setattr(agent.os,'chown',lambda *a:None,raising=False)
    calls=[]
    def launch(name,command,**kwargs):
        calls.append((name,command));return SimpleNamespace(poll=lambda:ready_code,returncode=ready_code)
    owner=SimpleNamespace(launch=launch,wait=lambda *a,**kw:None)
    plan={'processes':{name:[name] for name in ('core','native','assistant','services')}}
    if ready_code:
        with pytest.raises(RuntimeError,match='publicly ready'):
            agent.start_roles(owner,plan,tmp_path,{},threading.Event(),agent.time.monotonic()+30,'private-key')
        assert [name for name,_ in calls]==['core','core-ready']
    else:
        agent.start_roles(owner,plan,tmp_path,{},threading.Event(),agent.time.monotonic()+30,'private-key')
        assert [name for name,_ in calls]==['core','core-ready','native','assistant','services']
    assert 'ioalab.readiness' in calls[1][1]


def test_private_diagnostics_record_first_observed_failure_and_redact_secrets(tmp_path):
    owner=object.__new__(agent.SeatProcesses);owner.control=tmp_path
    owner.record={'phase':'running','roles':{'assistant':{},'core':{}}}
    owner.children={'assistant':SimpleNamespace(poll=lambda:7),'core':SimpleNamespace(poll=lambda:None)}
    owner.note_exits()
    assert owner.record['firstObservedExits'][0]['role']=='assistant' and owner.record['firstObservedExits'][0]['exitCode']==7
    output=agent.redacted('password="secret with spaces" Authorization: Bearer private-token token=opaque', ['opaque'])
    assert not any(value in output for value in ('secret with spaces','private-token','opaque'))


def test_unverified_stop_keeps_supervisor_alive_until_explicit_retry(tmp_path):
    owner=SimpleNamespace(record={},save=lambda:None,note_exits=lambda:None)
    attempts=[]
    def shutdown():
        attempts.append(True)
        if len(attempts)==1:raise TimeoutError('still running')
    owner.shutdown=shutdown
    class ExplicitRetry:
        def clear(self):pass
        def wait(self,timeout):
            assert owner.record['phase']=='stop_unverified' and len(attempts)==1
            return True
    agent.shutdown_preserving(owner,ExplicitRetry())
    assert len(attempts)==2



@pytest.mark.parametrize('fails',[False,True])
def test_sdk_entry_only_wraps_the_host_assembly_with_trusted_confirmation(monkeypatch,tmp_path,fails):
    import contextlib
    import ctypes
    import sys
    import ioalab_jiuwen
    import ioalab_sdk.runtime as runtime_package
    from ioalab_trial import sdk_entry
    events=[]
    monkeypatch.setattr(ctypes,'CDLL',lambda *a,**k:SimpleNamespace(prctl=lambda *args:0))
    monkeypatch.setenv('IOALAB_TRIAL_EPOCH','a'*32);monkeypatch.setenv('IOALAB_TRIAL_SDK_KEY','k'*40)
    class Assembly:
        config=SimpleNamespace(identities=[{'key':'personal','role':'assistant'}])
        runtime=SimpleNamespace(identities=[SimpleNamespace(profile='personal',agent_id='agent-1')],confirmations='store')
        def run(self):
            events.append('run')
            if fails:raise RuntimeError('host probe failed')
    @contextlib.contextmanager
    def assembled(path):
        events.append(('enter',str(path)))
        try:yield Assembly()
        finally:events.append('exit')
    class Server:
        def __init__(self,port,*,key,listen_port):events.append(('server',port,listen_port))
        def start(self):events.append('server.start');return self
        def close(self):events.append('server.close')
    monkeypatch.setattr(ioalab_jiuwen,'assembled',assembled)
    monkeypatch.setattr(runtime_package,'TrustedUserServer',Server)
    monkeypatch.setattr(runtime_package,'TrustedUserPort',lambda confirmations,*,actor,generation,authorize:('port',confirmations,actor,generation))
    monkeypatch.setattr(sys,'argv',['sdk_entry','--config',str(tmp_path/'config.json'),'--confirmation-port','8768'])
    if fails:
        with pytest.raises(RuntimeError,match='probe'):sdk_entry.main()
    else:sdk_entry.main()
    assert events==[('enter',str(tmp_path/'config.json')),('server',('port','store','agent-1','a'*32),8768),
                    'server.start','run','server.close','exit']
