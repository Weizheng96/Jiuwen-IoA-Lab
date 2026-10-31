"""Real local-process ownership, authenticated IPC, delivery and crash recovery."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import signal
import re
import sqlite3
import subprocess
import sys
import time
import uuid

import httpx
import pytest

from _support import new_user,PASSWORD
from conftest import wait_for
from ioalab_sdk.runtime.config import RuntimeConfig
from ioalab_sdk.runtime.files import private_json,read_json,prepare_home,windows_sid
from ioalab_sdk.runtime.ipc import IpcServer,LocalClient
from ioalab_sdk.runtime.protocol import RuntimeFault
from ioalab_sdk.runtime.resident import ResidentRuntime
from ioalab_sdk.runtime.session import AccountSession
from test_agent_delivery import Peer,request,people


ROOT=Path(__file__).resolve().parents[1]


def uid():return str(uuid.uuid4())


def test_close_quiesces_roles_but_keeps_their_transport_until_active_jobs_finish(platform,tmp_path):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from ioalab_sdk.runtime.scheduling import JobHandler,JobOutcome
    started=threading.Event();release=threading.Event();quiesced=threading.Event();finished=threading.Event()
    class Role:
        closed=False
        def bind(self,runtime):return {'jobs':{'read':JobHandler(self.read,'read-v1',False)}}
        def start(self):pass
        def wake(self):pass
        def quiesce(self):quiesced.set()
        def close(self):
            assert finished.is_set(),'role transport was released before its active reader ended'
            self.closed=True
        def read(self,job):
            started.set();assert release.wait(10)
            assert not self.closed,'in-flight public key/package reads lost their transport'
            finished.set();return JobOutcome('completed',{})
    path,_=configuration(platform,tmp_path,service=False);role=Role()
    runtime=ResidentRuntime(RuntimeConfig.load(path),modules=(role,)).start()
    try:
        wait_for(lambda:runtime.connection.connected)
        runtime.scheduler.submit(runtime.identities[0].agent_id,'close-read','read',{},operation_id=uid())
        assert started.wait(5)
        with ThreadPoolExecutor(max_workers=1) as pool:
            closing=pool.submit(runtime.close)
            try:
                assert quiesced.wait(5)
                assert not role.closed and not closing.done()
            finally:release.set()
            closing.result(timeout=10)
        assert finished.is_set() and role.closed
    finally:release.set();runtime.close()


def configuration(platform,tmp_path,*,service=True):
    user=new_user(platform)
    profiles=[{'key':'personal','role':'assistant','name':'本地助手'}]
    if service:profiles.append({'key':'service','role':'service','name':'本地服务'})
    path=tmp_path/'config.json'
    value={'platform_url':platform.s.base_url,'username':user['username'],'password':PASSWORD,
           'data_dir':'./state','identities':profiles}
    private_json(path,value)
    return path,user


def query(client):
    try:return client.call('runtime.status')['data']
    except RuntimeFault:return None


@contextmanager
def process(config,*,fixture=False):
    config=Path(config)
    home=RuntimeConfig.load(config).home
    client=LocalClient(home,timeout=1)
    log=(config.parent/('fixture.log' if fixture else 'process.log')).open('a',encoding='utf-8')
    env={**os.environ,'PYTHONUTF8':'1','IOALAB_TEST_FIXTURE':'resident','IOALAB_TEST_RECEIPT':str(config.parent/'receipts.txt')}
    command=[sys.executable,str(ROOT/'tests/resident_fixture.py'),str(config)] if fixture else [sys.executable,'-m','ioalab_sdk.runtime','serve','--config',str(config)]
    child=subprocess.Popen(command,cwd=config.parent,env=env,stdout=log,stderr=log,
                           creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    status=None
    try:
        def ready():
            nonlocal status
            if child.poll() is not None:
                log.flush()
                raise AssertionError((config.parent/('fixture.log' if fixture else 'process.log')).read_text(encoding='utf-8'))
            status=query(client)
            return bool(status)
        wait_for(ready,timeout=25)
        yield child,client,status
    finally:
        if child.poll() is None:
            # Only this fixture's publicly verified runtime PID is eligible.
            latest=query(client)
            if latest:
                try:client.call('runtime.stop',{'bootId':latest['bootId']})
                except RuntimeFault:pass
            try:child.wait(timeout=20)
            except subprocess.TimeoutExpired:
                if status:os.kill(status['pid'],signal.SIGTERM)
                else:child.terminate()
                child.wait(timeout=10)
        log.close()


def test_two_roles_share_process_identity_login_and_cli_never_opens_sqlite(platform,tmp_path,monkeypatch):
    config,user=configuration(platform,tmp_path)
    with process(config) as (_,client,status):
        wait_for(lambda:(query(client) or {}).get('platformConnected'))
        assert len(status['identities'])==2 and all(row['bound'] for row in status['identities'])
        assert not any(row['executionReady'] for row in status['identities']) and not status['host']['available']
        assert PASSWORD not in json.dumps(status) and 'token' not in json.dumps(status)
        assert platform.wallets.balance(user['id'])==100
        with pytest.raises(RuntimeError,match='already in use'):ResidentRuntime(RuntimeConfig.load(config))
        with monkeypatch.context() as patch:
            patch.setattr(sqlite3,'connect',lambda *args,**kwargs:pytest.fail('CLI opened a database'))
            assert client.call('identity.status')['status']=='completed'
        before=platform.db.one('SELECT COUNT(*) AS n FROM account_operations WHERE owner=?',user['id'])['n']
        result=subprocess.run([sys.executable,'-m','ioalab_sdk.runtime','start','--config',str(config)],
                              cwd=tmp_path,capture_output=True,text=True,encoding='utf-8',timeout=20)
        assert result.returncode==0,result.stdout+result.stderr
        assert json.loads(result.stdout)['data']['bootId']==status['bootId']
        assert platform.db.one('SELECT COUNT(*) AS n FROM account_operations WHERE owner=?',user['id'])['n']==before
    assert not (tmp_path/'state/runtime.json').exists()


def test_local_rpc_origin_host_auth_and_size_are_enforced(platform,tmp_path):
    config,_=configuration(platform,tmp_path,service=False)
    with process(config) as (_,client,status):
        descriptor=read_json(tmp_path/'state/runtime.json')
        headers={'Authorization':'Bearer '+descriptor['token'],'X-Ioa-Instance':descriptor['bootId']}
        body={'operation':'runtime.status','operationId':uid(),'arguments':{}}
        with httpx.Client(trust_env=False) as http:
            url=descriptor['url']+'/v1/call'
            assert http.post(url,json=body,headers={**headers,'Origin':descriptor['url']}).status_code==403
            assert http.post(url,json=body,headers={**headers,'Host':'attacker.invalid'}).status_code==403
            assert http.post(url,json=body,headers={**headers,'Authorization':'Bearer wrong'}).status_code==401
            assert http.post(url,content=b'x'*(128*1024+1),headers={**headers,'Content-Type':'application/json'}).status_code==400
            assert http.post(url,json={**body,'unexpected':True},headers=headers).status_code==400
        rejected=client.call('assistant.delegation.confirm',{'confirmed':True})
        assert rejected['status']=='rejected' and rejected['error']['code']=='invalid_input'
        assert client.call('runtime.stop',{'bootId':uid()})['status']=='rejected'
        assert query(client)['bootId']==status['bootId']


def test_cli_persists_original_operation_without_direct_role_fallback(platform,tmp_path):
    config,_=configuration(platform,tmp_path,service=False)
    with process(config):
        args=tmp_path/'args.json'
        private_json(args,{'confirmed':True})
        command=[sys.executable,'-m','ioalab_sdk.runtime','call','assistant.delegation.confirm','--config',str(config),'--args-file',str(args)]
        one=subprocess.run(command,cwd=tmp_path,capture_output=True,text=True,encoding='utf-8',timeout=15)
        two=subprocess.run(command,cwd=tmp_path,capture_output=True,text=True,encoding='utf-8',timeout=15)
        first,second=json.loads(one.stdout),json.loads(two.stdout)
        assert one.returncode==two.returncode==1
        assert first['operationId']==second['operationId']==read_json(args)['operationId']
        assert first['error']['code']==second['error']['code']=='invalid_input'


def test_adding_service_profile_reuses_account_and_original_assistant(platform,tmp_path):
    config,user=configuration(platform,tmp_path,service=False)
    with process(config) as (_,_,status):
        original=status['identities'][0]['agentId']
        runtime=status['runtimeId']
    value=read_json(config)
    value['identities'].append({'key':'service','role':'service','name':'后添加的服务'})
    private_json(config,value)
    with process(config) as (_,_,status):
        assert status['runtimeId']==runtime
        assert len(status['identities'])==2 and status['identities'][0]['agentId']==original
        assert platform.wallets.balance(user['id'])==100


def test_old_endpoint_cleanup_cannot_remove_new_instance_descriptor(tmp_path):
    def result(operation,oid,args):return {'operationId':oid,'status':'completed','data':{},'error':None}
    old=IpcServer(tmp_path,uid(),result).start()
    current=IpcServer(tmp_path,uid(),result).start()
    try:
        old.close()
        assert read_json(tmp_path/'runtime.json')['bootId']==current.boot_id
        assert LocalClient(tmp_path).call('runtime.status')['status']=='completed'
    finally:current.close()


def test_runtime_rejects_unrelated_directory_without_changing_its_files(platform,tmp_path):
    config,user=configuration(platform,tmp_path,service=False)
    home=tmp_path/'state'
    home.mkdir()
    document=home/'personal-notes.txt'
    document.write_text('Keep this unrelated file',encoding='utf-8')
    with pytest.raises(RuntimeFault,match='dedicated SDK data'):ResidentRuntime(RuntimeConfig.load(config))
    assert document.read_text(encoding='utf-8')=='Keep this unrelated file'
    assert not (home/'client.lock').exists() and not (home/'account.sqlite').exists()
    assert platform.wallets.balance(user['id'])==0


@pytest.mark.skipif(os.name!='nt',reason='Windows ACL is separate from POSIX permissions')
def test_private_runtime_directory_and_descriptor_allow_only_the_owner_sid(tmp_path):
    home=tmp_path/'private'
    prepare_home(home)
    private_json(home/'runtime.json',{'token':'isolated-test-token'})
    for index,target in enumerate((home,home/'runtime.json')):
        saved=tmp_path/f'acl-{index}.txt'
        result=subprocess.run(['icacls',str(target),'/save',str(saved)],capture_output=True,timeout=10,
                              creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        assert result.returncode==0
        sddl=saved.read_text(encoding='utf-16-le')
        assert set(re.findall(r'\(A;[^)]*;;;([^)]+)\)',sddl))=={windows_sid()}


def test_resident_durably_acks_but_does_not_execute_without_rule_or_host(platform,client,people,tmp_path):
    config,user=configuration(platform,tmp_path,service=False)
    from test_agent_delivery import publish
    sender=people[1]
    with process(config) as (_,local,status):
        actor=status['identities'][0]['agentId']
        owner={**user,'actor':actor,'token':platform.auth.bearer(user['id'],restricted=True)}
        publish(client,owner,[sender['username']]).raise_for_status()
        original=request(actor)
        peer=Peer(client,sender)
        try:peer.call('message.send',original)
        finally:peer.close()
        wait_for(lambda:query(local)['delivery']['savedMessages']==1)
        wait_for(lambda:platform.db.one('SELECT delivery_state FROM delivery_messages WHERE id=?',original['messageId'])['delivery_state']=='delivered')
        assert platform.db.one('SELECT request_state FROM delivery_messages WHERE id=?',original['messageId'])['request_state']=='pending'
        assert query(local)['scheduler']['states']=={}
    with process(config) as (_,local,status):
        assert status['identities'][0]['agentId']==actor
        assert status['delivery']['savedMessages']==1
        assert platform.wallets.balance(user['id'])==100


def test_process_crash_retains_unknown_effect_and_does_not_repeat_it(platform,tmp_path):
    config,user=configuration(platform,tmp_path,service=False)
    operation=uid()
    arguments={'interaction':uid(),'session':'one-host-session','payload':{'task':'fixture'}}
    receipt=tmp_path/'receipts.txt'
    with process(config,fixture=True) as (child,local,status):
        original=local.call('test.enqueue',arguments,operation_id=operation)['data']['reference']
        wait_for(lambda:receipt.exists() and receipt.read_text(encoding='utf-8').strip()==original)
        assert query(local)['scheduler']['active']==1
        os.kill(status['pid'],signal.SIGTERM)
        child.wait(timeout=10)
    with process(config,fixture=True) as (_,local,status):
        reference={'kind':'job','id':original}
        assert local.call('operations.get',{'reference':reference})['data']['status']=='unknown'
        assert local.call('operations.resume',{'reference':reference})['status']=='unknown'
        replay=local.call('test.enqueue',arguments,operation_id=operation)
        assert replay['data']['reference']==original and replay['status']=='unknown'
        later=local.call('test.enqueue',{**arguments,'interaction':uid()},operation_id=uid())
        assert later['status']=='accepted'
        assert query(local)['scheduler']['states']=={'queued':1,'unknown':1}
        assert receipt.read_text(encoding='utf-8').splitlines()==[original]
        assert platform.wallets.balance(user['id'])==100


def test_control_remains_responsive_and_stop_drains_actual_running_work(platform,tmp_path):
    config,_=configuration(platform,tmp_path,service=False)
    with process(config,fixture=True) as (child,local,status):
        original=local.call('test.enqueue',{'interaction':uid(),'session':'model-session'})['data']['reference']
        wait_for(lambda:(tmp_path/'receipts.txt').exists())
        assert query(local)['scheduler']['active']==1
        result=local.call('runtime.stop',{'bootId':status['bootId']})
        assert result['status']=='waiting'
        assert query(local)['stopping']
        assert local.call('test.enqueue',{'interaction':uid()})['error']['code']=='closed'
        (tmp_path/'receipts.release').write_text('release this fixture',encoding='utf-8')
        child.wait(timeout=20)
        assert not (tmp_path/'state/runtime.json').exists()
        assert (tmp_path/'receipts.txt').read_text(encoding='utf-8').splitlines()==[original]


def test_offline_restart_uses_only_bound_identity_and_valid_cache(platform,tmp_path,monkeypatch):
    config,user=configuration(platform,tmp_path,service=False)
    with process(config) as (_,_,before):pass
    original=httpx.Client.request
    def offline(http,method,url,*args,**kwargs):
        if str(http.base_url.join(url)).startswith(platform.s.base_url):raise httpx.ConnectError('Platform is offline')
        return original(http,method,url,*args,**kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(httpx.Client,'request',offline)
        with ResidentRuntime(RuntimeConfig.load(config)) as runtime:
            result=LocalClient(tmp_path/'state').call('runtime.status')['data']
            assert not result['platformConnected'] and not result['account']['verifiedOnline']
            assert result['account']['authenticated']
            assert result['identities'][0]['agentId']==before['identities'][0]['agentId']
    assert platform.wallets.balance(user['id'])==100


def test_explicit_auth_rejection_and_missing_keys_do_not_rebuild_identity(platform,tmp_path,monkeypatch):
    config,user=configuration(platform,tmp_path)
    with process(config):pass
    original=httpx.Client.request
    def denied(http,method,url,*args,**kwargs):
        if str(url)==platform.s.base_url+'/api/v1/me':
            return httpx.Response(401,json={'error':'rejected'},request=httpx.Request(method,url))
        return original(http,method,url,*args,**kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(httpx.Client,'request',denied)
        with pytest.raises(RuntimeFault,match='explicitly log in'):ResidentRuntime(RuntimeConfig.load(config))
    (tmp_path/'state/identities/service/keys.json').unlink()
    count=platform.db.one('SELECT COUNT(*) AS n FROM agents WHERE owner_id=?',user['id'])['n']
    with pytest.raises(RuntimeFault,match='keys are missing'):ResidentRuntime(RuntimeConfig.load(config))
    assert platform.db.one('SELECT COUNT(*) AS n FROM agents WHERE owner_id=?',user['id'])['n']==count
    assert platform.wallets.balance(user['id'])==100


@pytest.mark.parametrize('change',[
    {'platform_url':'http://name:password@localhost'},
    {'identities':[{'key':'../outside','role':'assistant','name':'unsafe'}]},
    {'max_model_runs':True},
    {'max_pending_requests':0},
    {'host':'import arbitrary.module'},
])
def test_runtime_configuration_rejects_unsafe_or_ambiguous_inputs(platform,tmp_path,change):
    config,_=configuration(platform,tmp_path,service=False)
    private_json(config,{**read_json(config),**change})
    with pytest.raises(RuntimeFault):RuntimeConfig.load(config)
