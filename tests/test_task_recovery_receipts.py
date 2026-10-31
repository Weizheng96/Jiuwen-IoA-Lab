"""Resume acknowledgement replay stays separate from the latest business state."""
import uuid
import json

import httpx
from ioalab_sdk.runtime.ipc import LocalClient
from conftest import wait_for
from test_provider_task_recovery import stopped,resume,recovered_quote
from test_resident_contracts import confirm
from test_resident_fulfillment import invoke,operation
from test_task_verifiers import contract_state


def test_old_resume_receipt_does_not_retry_a_second_unknown_preparation(platform,tmp_path,monkeypatch):
    with stopped(platform,tmp_path,monkeypatch) as (provider,assistant,host,_,_,reference,snapshot,_):
        api=provider.account.api;submitted=[]
        def lose_another_reply(*args,**kwargs):
            value=api(*args,**kwargs)
            if args[:2]==('POST','/provider/packages'):
                submitted.append(value)
                if len(submitted)==1:raise httpx.ReadTimeout('second unknown upload',request=httpx.Request('POST','http://local.test'))
            return value
        monkeypatch.setattr(provider.account,'api',lose_another_reply)
        operation_id=str(uuid.uuid4());first=resume(provider,snapshot,operation_id)
        assert first['status']=='accepted',first
        wait_for(lambda:len(submitted)==1 and provider.scheduler.get(snapshot['taskRef'])['status']=='unknown')
        wait_for(lambda:provider.scheduler.status()['active']==0)
        old=resume(provider,snapshot,operation_id)
        assert old['status']=='accepted' and old['data']['resumeReceipt']==first['data']['resumeReceipt']
        assert old['data']['currentTask']['status']=='unknown' and old['data']['currentTask']['canResume']
        for _ in range(3):provider.modules[0].execution.scan()
        assert len(submitted)==1 and len(host.turns)==1
        assert resume(provider,snapshot)['status']=='accepted'
        recovered_quote(assistant,reference)
        assert len(submitted)==2 and len(host.turns)==2
        assert len(platform.db.rows("SELECT * FROM transfers WHERE purpose='proposal-fee' AND payee_id=?",provider.account.account['id']))==1


def test_original_resume_operation_cannot_be_retargeted_to_a_production_task(platform,tmp_path,monkeypatch):
    with stopped(platform,tmp_path,monkeypatch) as (provider,assistant,_,_,_,reference,snapshot,_):
        operation_id=str(uuid.uuid4());assert resume(provider,snapshot,operation_id)['status']=='accepted'
        quote=recovered_quote(assistant,reference);selected,_,_=confirm(assistant,quote)
        contract=selected['data']['contractRef'];wait_for(lambda:contract_state(assistant,contract).get('formed'))
        key,_=invoke(assistant,contract,'start');wait_for(lambda:operation(assistant,contract,key))
        wait_for(lambda:contract_state(assistant,contract).get('availablePeriods')==1)
        items=LocalClient(provider.config.home).call('provider.tasks.get',{'profile':'service'})['data']['items']
        production=next(item for item in items if item['phase']=='production')
        changed=resume(provider,production,operation_id)
        assert changed['status']=='rejected' and changed['error']['code']=='id_conflict',changed
        assert provider.store.db.execute('SELECT COUNT(*) FROM runtime_job_resumptions').fetchone()[0]==1



def test_known_failed_host_round_can_recover_only_its_unknown_verifier_upload(platform,tmp_path,monkeypatch):
    from dataclasses import replace
    from test_provider_task_recovery import ResumeHost
    host=ResumeHost();execute=host.execute
    def run(turn):
        result=execute(turn)
        if not json.loads(turn.input).get('preparedVerifiers') and json.loads(turn.input)['phase']=='quote':
            return replace(result,status='failed',error={'code':'original_model_tool_name_denied'})
        return result
    host.run=run
    with stopped(platform,tmp_path,monkeypatch,host=host) as (provider,assistant,host,_,_,reference,snapshot,_):
        assert snapshot['canResume']
        proof=provider.modules[0].execution.tasks.proof(provider.identities[0],snapshot['taskRef'])
        assert proof['hostStatus']=='failed'
        assert resume(provider,snapshot)['status']=='accepted'
        recovered_quote(assistant,reference);assert len(host.turns)==2
