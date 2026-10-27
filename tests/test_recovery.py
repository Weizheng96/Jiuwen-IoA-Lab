"""Recovery across lost subscriptions, local process exits and persisted service state."""
import copy
import importlib.util
import json
import sys
from pathlib import Path
import pytest
from _support import prepare,fetch_when_ready


def test_local_restart_and_lost_market_watch(platform,providers,tmp_path):
    from ioalab_sdk import LabClient
    from _support import new_user,PASSWORD
    user=new_user(platform);home=tmp_path/'client'
    first=LabClient(platform.s.base_url,user['username'],PASSWORD,home,source='experiment')
    try:
        _,_,cd,agent=prepare(first,providers)
        signer=first.signer.id
        watch=first.store.get('cards',cd)['watch']['task']
        from conftest import wait_for
        wait_for(lambda:bool(agent.provider.card(cd)['signatures']['5']))
    finally: first.close()
    # Task subscriptions are intentionally volatile on a market restart.
    from a2c.binding import build_app
    platform.market.market.tasks.clear()
    platform.market.market.watchers.clear()
    # The pinned A2A transport owns detached producers/consumers. Keep the retired
    # app alive until this server loop shuts down, as a real process restart does.
    # Dropping it here lets Python collect live coroutine cycles mid-request.
    platform._retired_test_apps=getattr(platform,'_retired_test_apps',[])+[platform.router.apps['market']]
    platform.router.apps['market']=build_app(platform.market.market,name='Restarted market',base_url=platform.market_id,
                                            role='market',jwks={'keys':[platform.market_signer.jwk]})
    second=LabClient(platform.s.base_url,user['username'],PASSWORD,home,source='experiment')
    try:
        assert second.signer.id==signer
        fetch_when_ready(second,cd)
        assert second.verify(cd,1)['status']=='pass'
        assert second.accept(cd,1,confirmed=True)['status']=='pass'
        assert second.store.get('cards',cd)['watch']['task']!=watch
    finally: second.close()


def test_service_restart_restores_keys_tasks_and_does_not_redeliver(providers,local_client):
    from ioalab_sdk.provider import ProviderAgent
    d,t,cd,agent=prepare(local_client,providers)
    fetch_when_ready(local_client,cd)
    queued=[]
    restored=ProviderAgent(copy.deepcopy(d),agent.account,agent.url,agent.home,lambda a,j:queued.append(j))
    route=next(r for r in providers.app.routes if getattr(r,'path','')=='/a2a/providers/svc-'+d['id'])
    oldapp=route.app
    try:
        assert restored.provider.signer.jwk==agent.provider.signer.jwk
        assert restored.provider.tasks
        assert restored.publish()['templateDigest']==t['templateDigest']
        before=copy.deepcopy(restored.provider.card(cd)['signatures']['4'])
        assert restored.deliver(cd,d['sample'])['alreadyDelivered']
        assert restored.provider.card(cd)['signatures']['4']==before
        from serve import ServiceHost
        host=ServiceHost(platform=agent.account.base,public_url='http://localhost:8751',data=agent.home,stub=True)
        from a2c.card import card_digest
        job=next(j for j in restored.store.all('jobs').values() if j['kind']=='deliver' and card_digest(j['object'])==cd)
        job['state']='running';restored.store.put('jobs',job['id'],job)
        host.execute(restored,job['id'])
        assert restored.store.get('jobs',job['id'])['result']['alreadyDelivered']
        from ioalab_sdk.provider_app import build_provider_app
        route.app=build_provider_app(restored.provider,name=d['name'],base_url=agent.url)
        # A fresh A2A app must still expose the pre-restart task over real HTTP.
        assert local_client.poll(cd)['card']['signatures']['6']
        assert local_client.accept(cd,1,confirmed=True)['status']=='pass'
    finally:
        providers._retired_test_apps=getattr(providers,'_retired_test_apps',[])+[route.app]
        route.app=oldapp
        restored.close()


def test_model_completion_without_sdk_submission_is_failure(providers,local_client,tmp_path):
    from serve import ServiceHost
    from conftest import wait_for
    import threading
    d,_,cd,agent=prepare(local_client,providers)
    fetch_when_ready(local_client,cd)
    host=ServiceHost(platform=agent.account.base,public_url='http://localhost:8751',data=tmp_path,stub=False)
    jid='fake-completion'
    agent.store.put('jobs',jid,{'id':jid,'state':'queued'})
    host.execute=lambda a,j:None  # Equivalent to a completed model message with no SDK call.
    worker=threading.Thread(target=host.work,daemon=True);worker.start()
    try:
        host.submit(agent,jid)
        wait_for(lambda:agent.store.get('jobs',jid)['state']=='failed')
        assert 'without submitting' in agent.store.get('jobs',jid)['error']
    finally:
        host.stopping.set();worker.join(2)


def test_swarm_machine_mode_and_result_validation(tmp_path):
    from swarm import SwarmEngine
    fake=tmp_path/'fake_swarm.py'
    fake.write_text('import sys,json\nif "--help" in sys.argv: print("--run-json --run-jsonl")\nelse: print(json.dumps({"type":"result","status":"failed"}))\n')
    engine=SwarmEngine([sys.executable,str(fake)]);engine.check()
    with pytest.raises(RuntimeError,match='failed'):
        engine.run({'id':'job'}, {},tmp_path,'http://localhost/task','test-token')
    fake.write_text('print("incompatible CLI")')
    with pytest.raises(RuntimeError,match='machine mode'): engine.check()


def test_bootstrap_rejects_tampered_wheel_without_ready_marker(tmp_path):
    root=Path(__file__).resolve().parents[1]
    spec=importlib.util.spec_from_file_location('bootstrap',root/'connector/ioalab-client/skills/ioalab-delegate/scripts/ioalab.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    assets=tmp_path/'assets';(assets/'wheels').mkdir(parents=True)
    (assets/'wheels/fake.whl').write_bytes(b'tampered')
    (assets/'runtime.json').write_text(json.dumps({'sdkVersion':'0.0.2','wheels':[{'file':'fake.whl','sha256':'0'*64}]}))
    cache=tmp_path/'runtimes'
    with pytest.raises(RuntimeError,match='校验失败'): module.ensure_runtime(assets,cache)
    assert not list(cache.rglob('.ready'))
    assert not list(cache.glob('*.installing'))


def test_directory_lease_released_after_close(tmp_path):
    from ioalab_sdk.lease import Lease
    first=Lease(tmp_path/'identity.lock',timeout=0)
    try:
        with pytest.raises(RuntimeError,match='already in use'): Lease(tmp_path/'identity.lock',timeout=0)
    finally: first.close()
    Lease(tmp_path/'identity.lock',timeout=0).close()
