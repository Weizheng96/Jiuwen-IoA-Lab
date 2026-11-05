"""Exercise actual Docker argv/inspection validation without requiring Docker."""
import json
from types import SimpleNamespace
from pathlib import Path

import httpx
import pytest

from ioalab_trial.sandbox import DockerSandbox


EPOCH='a'*32


def configuration():
    return {'deployment':'ioa-trial','image':'seat@sha256:'+'b'*64,'control_key':'k'*40,
        'egress_network':'bridge','port_base':19000,'provider_base_url':'https://provider.test/v1',
        'proxy_secret_file':str(Path.cwd()/'temp/provider.json'),'model':{'name':'trial-model'}}


class Docker:
    def __init__(self,sandbox):
        self.name=sandbox.identity(1,EPOCH)
        self.calls=[]
        self.stopped=False
        self.stopped_names=set()
        self.fail=None
        self.container_override={}
        self.proxy_override={}
        self.secret=str(sandbox.secret_path)
        self.network_override={}
        self.volume_label=self.name
        self.remaining_volume=''

    def __call__(self,argv,**kwargs):
        assert argv[0]=='docker' and kwargs['timeout']==90 and kwargs['capture_output']
        args=argv[1:];self.calls.append(args)
        if self.fail and args[:len(self.fail)]==self.fail:
            return SimpleNamespace(returncode=1,stdout='',stderr='private Docker error')
        output=''
        if args[:2]==['container','inspect']:
            stopped=self.stopped or args[2] in self.stopped_names
            value={'Config':{'Labels':{'ioalab.trial.owner':self.name}},
                'Mounts':[{'Name':self.name,'Destination':'/epoch'}],
                'State':{'Running':not stopped,'Pid':0 if stopped else 10,'Status':'exited' if stopped else 'running'}}
            if args[2].endswith('-model'):
                value['Config']['Labels']['ioalab.trial.role']='model'
                value['Mounts']=[{'Type':'bind','Source':self.secret,'Destination':'/run/model/provider.json','RW':False}]
                value.update(self.proxy_override)
            else:value.update(self.container_override)
            output=json.dumps([value])
        elif args[:2]==['network','inspect']:
            value={'Labels':{'ioalab.trial.owner':self.name},'Internal':True,'Driver':'bridge',**self.network_override}
            if args[2]=='bridge':value={'Driver':'bridge','Internal':False}
            output=json.dumps([value])
        elif args[:2]==['volume','inspect']:
            output=json.dumps([{'Labels':{'ioalab.trial.owner':self.volume_label}}])
        elif args[0]=='wait':self.stopped_names.add(args[1])
        elif args[:2]==['volume','ls']:output=self.remaining_volume
        return SimpleNamespace(returncode=0,stdout=output,stderr='')


@pytest.fixture
def sandbox(monkeypatch,tmp_path):
    secret=tmp_path/'provider.json';secret.write_text('{"api_key":"private-provider-key"}')
    sandbox=DockerSandbox({**configuration(),'proxy_secret_file':str(secret)},deployment_profile='linux-server')
    # Docker command tests isolate Linux file metadata; dedicated profile tests cover the real checker.
    monkeypatch.setattr('ioalab_trial.sandbox.check_secret_permissions',lambda path:None)
    docker=Docker(sandbox);sandbox.run=docker
    def post(url,**kwargs):
        assert kwargs['trust_env'] is False
        assert kwargs['headers']['Authorization']=='Bearer '+sandbox.key(1,EPOCH)
        docker.calls.append(['http',url])
        return httpx.Response(200,json={'revoked':True},request=httpx.Request('POST',url))
    monkeypatch.setattr('ioalab_trial.sandbox.httpx.post',post)
    return sandbox,docker


def test_prepare_has_epoch_network_readonly_root_and_one_owned_volume(sandbox):
    target,docker=sandbox
    target.prepare(1,EPOCH)
    name=target.identity(1,EPOCH)
    create=next(call for call in docker.calls if call[:2]==['network','create'])
    assert create==['network','create','--internal','--label','ioalab.trial.owner='+name,name+'-net']
    assert ['network','connect','--alias','model-egress',name+'-net',name+'-model'] in docker.calls
    run=next(call for call in docker.calls if call[0]=='run' and call[call.index('--name')+1]==name)
    assert run[run.index('--network')+1]==name+'-net'
    assert '--read-only' in run and '--cap-drop=ALL' in run and '--security-opt=no-new-privileges' in run
    assert run.count('--mount')==1 and run[run.index('--mount')+1]=='type=volume,src='+name+',dst=/epoch'
    bindings=[run[i+1] for i,value in enumerate(run) if value=='-p']
    assert bindings==['127.0.0.1:19002:8080','127.0.0.1:19003:8767']
    assert not any('docker.sock' in arg or 'host'==arg for arg in run)
    assert str(target.secret_path) not in ' '.join(run) and 'private-provider-key' not in ' '.join(run)
    proxy=next(call for call in docker.calls if call[0]=='run' and call[call.index('--name')+1]==name+'-model')
    assert str(target.secret_path) in ' '.join(proxy) and 'readonly' in ' '.join(proxy)
    assert target.model_key(1,EPOCH) in ' '.join(run) and target.model_key(1,EPOCH) in ' '.join(proxy)
    assert target.model_key(1,EPOCH)!=target.model_key(2,EPOCH)!=target.model_key(1,'c'*32)
    assert target.model_key(1,EPOCH)!=target.key(1,EPOCH)
    assert target.identity(2,EPOCH)!=name and target.identity(1,'c'*32)!=name


def test_invalid_secret_metadata_rejected_before_any_docker_resource(sandbox,monkeypatch):
    target,docker=sandbox
    def reject(path):raise ValueError('Provider secret must have Linux root:root ownership and mode 600')
    monkeypatch.setattr('ioalab_trial.sandbox.check_secret_permissions',reject)
    with pytest.raises(ValueError,match='root:root'):target.prepare(1,EPOCH)
    assert docker.calls==[]


@pytest.mark.parametrize('seat,epoch',[(0,EPOCH),(4,EPOCH),(1,'../formal'),(1,'A'*32)])
def test_invalid_target_never_invokes_docker(sandbox,seat,epoch):
    target,docker=sandbox
    with pytest.raises(ValueError):target.prepare(seat,epoch)
    assert docker.calls==[]


@pytest.mark.parametrize('updates',[{'image':'seat:latest'},{'control_key':'short'},{'model':{}},
    {'egress_network':'--host'},{'deployment':'../formal'},
    {'model':{'name':'trial','api_key':'provider-key-must-never-enter-seat'}}])
def test_invalid_deployment_configuration_rejected(updates):
    with pytest.raises(ValueError):DockerSandbox({**configuration(),**updates},deployment_profile='linux-server')


@pytest.mark.parametrize('network',[{'Internal':False},{'Driver':'host'}])
def test_noninternal_network_cannot_launch_container(sandbox,network):
    target,docker=sandbox;docker.network_override=network
    with pytest.raises(RuntimeError):target.prepare(1,EPOCH)
    assert not any(call[0]=='run' for call in docker.calls)


@pytest.mark.parametrize('mounts',[
    [{'Name':'formal-data','Destination':'/epoch'}],
    [{'Name':'x','Destination':'/epoch'},{'Source':'/var/run/docker.sock','Destination':'/run/docker.sock'}],
])
def test_foreign_or_extra_mount_blocks_all_stop_and_delete(sandbox,mounts):
    target,docker=sandbox;docker.container_override={'Mounts':mounts}
    with pytest.raises(RuntimeError):target.stop(1,EPOCH)
    assert not any(call[0] in ('kill','rm','http') for call in docker.calls)


def test_revoke_stop_exit_then_full_volume_and_network_destroy(sandbox):
    target,docker=sandbox
    target.stop(1,EPOCH);target.wipe(1,EPOCH)
    calls=docker.calls;name=target.identity(1,EPOCH)
    positions=[next(i for i,call in enumerate(calls) if call[:len(prefix)]==prefix)
               for prefix in (['kill','--signal=TERM',name+'-model'],['wait',name+'-model'],['http'],
                              ['kill','--signal=TERM',name],['wait',name],['rm',name],['rm',name+'-model'],['volume','rm'],['network','rm'])]
    assert positions==sorted(positions)
    assert calls[positions[0]]==['kill','--signal=TERM',name+'-model']
    assert calls[positions[7]]==['volume','rm',name]
    assert not any('KILL' in item for call in calls for item in call)


@pytest.mark.parametrize('stage',[['kill'],['wait']])
def test_stop_failure_never_implies_stopped(sandbox,stage):
    target,docker=sandbox;docker.fail=stage
    with pytest.raises(RuntimeError):target.stop(1,EPOCH)
    assert not any(call[0]=='rm' or call[:2]==['volume','rm'] for call in docker.calls)


def test_live_writer_or_foreign_volume_prevents_wipe(sandbox):
    target,docker=sandbox
    with pytest.raises(RuntimeError):target.wipe(1,EPOCH)
    assert not any(call[0]=='rm' for call in docker.calls)
    docker.stopped=True;docker.volume_label='another-owner'
    with pytest.raises(RuntimeError):target.wipe(1,EPOCH)
    assert not any(call[0]=='rm' for call in docker.calls)


@pytest.mark.parametrize('stage',[['rm'],['volume','rm'],['network','rm']])
def test_each_destroy_failure_propagates(sandbox,stage):
    target,docker=sandbox;docker.stopped=True;docker.fail=stage
    with pytest.raises(RuntimeError):target.wipe(1,EPOCH)


def test_volume_still_present_after_delete_is_failure(sandbox):
    target,docker=sandbox;docker.stopped=True;docker.remaining_volume=docker.name
    with pytest.raises(RuntimeError,match='not confirmed'):target.wipe(1,EPOCH)


def test_preparation_rejects_foreign_network_label(sandbox):
    target,docker=sandbox;docker.network_override={'Labels':{'ioalab.trial.owner':'foreign'}}
    with pytest.raises(RuntimeError):target.prepare(1,EPOCH)
    assert not any(call[0]=='run' for call in docker.calls)


@pytest.mark.parametrize('failed',['stop','wipe','prepare','healthy'])
def test_each_reset_phase_failure_quarantines_without_reassignment(tmp_path,failed):
    from ioalab_trial.coordinator import Coordinator
    from ioalab_trial.store import Store
    store=Store(tmp_path/'control.sqlite',deployment_profile='linux-server')
    coordinator=Coordinator(store,audience='https://native.test',admission_key='k'*40,clock=lambda:1000)
    with store.transaction() as db:
        db.execute("UPDATE seats SET state='quarantined'")
        db.execute("UPDATE seats SET state='revoking',epoch=?,owner='alice',expires=1100,seen=1000 WHERE id=1",(EPOCH,))
        db.execute("INSERT INTO queue VALUES('bob',1000)")
    calls=[]
    class Lifecycle:
        def phase(self,name,*args):
            calls.append(name)
            if name==failed:raise OSError('Explicit injected phase failure')
        def stop(self,*args):self.phase('stop',*args)
        def wipe(self,*args):self.phase('wipe',*args)
        def prepare(self,*args):self.phase('prepare',*args)
        def healthy(self,*args):self.phase('healthy',*args)
    try:
        coordinator.reconcile(Lifecycle())
        assert store.seats()[0]['state']=='quarantined'
        assert calls==['stop','wipe','prepare','healthy'][:['stop','wipe','prepare','healthy'].index(failed)+1]
        assert coordinator.status('bob')['state']=='queued'
    finally:store.close()


@pytest.mark.parametrize('foreign',['proxy-mount','network-owner','network-member','proxy-live'])
def test_wipe_preflights_all_resources_before_any_delete(sandbox,foreign):
    target,docker=sandbox;docker.stopped=True
    if foreign=='proxy-mount':docker.proxy_override={'Mounts':[{'Type':'bind','Source':'/formal/private','Destination':'/run/model/provider.json','RW':False}]}
    elif foreign=='network-owner':docker.network_override={'Labels':{'ioalab.trial.owner':'another-epoch'}}
    elif foreign=='network-member':docker.network_override={'Containers':{'foreign':{'Name':'other-seat'}}}
    else:docker.proxy_override={'State':{'Running':True,'Pid':123,'Status':'running'}}
    with pytest.raises(RuntimeError):target.wipe(1,EPOCH)
    assert not any(call[0]=='rm' or call[:2] in (['volume','rm'],['network','rm']) for call in docker.calls)


def test_proxy_is_stopped_even_when_seat_control_is_offline(sandbox,monkeypatch):
    target,docker=sandbox
    def offline(*args):raise httpx.ConnectError('offline')
    monkeypatch.setattr(target,'control',offline)
    with pytest.raises(httpx.HTTPError):target.stop(1,EPOCH)
    assert docker.name+'-model' in docker.stopped_names
    assert docker.name not in docker.stopped_names
    assert not any(call[0]=='rm' for call in docker.calls)
