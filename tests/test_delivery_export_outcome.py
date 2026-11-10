"""Fetch/recovery boundary negatives; real card/signature success is in resident lifecycle."""
import base64
import copy
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from a2c import objects
from a2c.codec import digest_bytes
from ioalab_sdk.assistant import fulfillment as module
from ioalab_sdk.assistant.fulfillment import Fulfillment
from ioalab_sdk.runtime.files import prepare_home
from ioalab_sdk.runtime.scheduling import JobOutcome


REF='sha256:'+'b'*64
ACTOR='urn:test:assistant'
BYTES=b'\x00\xffraw\r\n'


@pytest.fixture
def harness(tmp_path,monkeypatch):
    home=tmp_path/'sdk';prepare_home(home)
    manifest=objects.new('manifest',cardDigest=REF,period=1,
                         files=[{'name':'original.bin','digest':digest_bytes(BYTES),'mediaType':'application/octet-stream'}])
    source={'card':{'client':{'id':ACTOR},'fulfillment':{'periods':[{'manifest':manifest}]}},
            'deliveries':{'1':{'original.bin':base64.b64encode(BYTES).decode('ascii')}}}
    # These mocks isolate the consumer boundary; the complete resident test uses
    # real validate_card/verify_card and asserts no new fetch or slot 6.
    validation=Mock();verification=Mock(return_value=SimpleNamespace(period_ts=lambda slot,period:object()))
    monkeypatch.setattr(module,'validate_card',validation)
    monkeypatch.setattr(module,'verify_card',verification)
    monkeypatch.setattr(module,'card_digest',lambda _:REF)
    contract={'template':{}}
    record={'name':'fetch','args':{'contractRef':REF,'period':1},'phase':'prepared'}
    protocol=SimpleNamespace(store=SimpleNamespace(get=lambda *_:source),keys=object(),
                             transport=SimpleNamespace(operation=lambda _:nullcontext(),received=lambda _:None),
                             fetch_delivery=Mock(side_effect=AssertionError('Must not fetch/sign a cached delivery')))
    lifecycle=Fulfillment.__new__(Fulfillment)
    lifecycle.identity=SimpleNamespace(agent_id=ACTOR)
    lifecycle.runtime=SimpleNamespace(config=SimpleNamespace(home=home),require_execution=lambda:None)
    lifecycle.namespace='fulfillment:'+ACTOR
    lifecycle.role=SimpleNamespace(cases=SimpleNamespace(require_open=lambda *_:None))  # no open arbitration case
    lifecycle.client=lambda _: (protocol,contract)
    records={'operation':record}
    lifecycle.record=lambda operation:records[operation]
    def save(operation,original,**updates):
        records[operation]={**original,**updates};return records[operation]
    lifecycle.save=save
    lifecycle.finish=lambda operation,original,result,**kwargs:JobOutcome(kwargs.get('state','completed'),result)
    return SimpleNamespace(home=home,source=source,record=record,lifecycle=lifecycle,protocol=protocol,
                           contract=contract,validation=validation,verification=verification,records=records)


def test_normal_cached_run_and_unknown_recovery_have_same_verified_file_contract(harness):
    h=harness
    normal=h.lifecycle.run({'payload':{'operationId':'operation'}})
    assert normal.state=='completed' and h.validation.call_count==1 and h.verification.call_count==1
    assert h.records['operation']['fetchFromCache'] is True
    h.protocol.fetch_delivery.assert_not_called()
    results=[]
    h.lifecycle.store=SimpleNamespace(checkpoints=lambda _:h.records)
    h.lifecycle.runtime.scheduler=SimpleNamespace(reference=lambda *args:args[-1],get=lambda _: {'status':'unknown'},
                                                  resolve=lambda reference,outcome:results.append((reference,outcome)))
    h.lifecycle._recover()
    assert len(results)==1 and results[0][1].state=='completed'
    first=normal.data['files']['original.bin'];second=results[0][1].data['files']['original.bin']
    assert first=={**second,'alreadyPresent':False} and second['alreadyPresent'] is True
    assert h.validation.call_count==2 and h.verification.call_count==2
    h.protocol.fetch_delivery.assert_not_called()


@pytest.mark.parametrize('fault',['identity','receipt','signature','missing-cache','base64','extra','digest','duplicate-manifest'])
def test_invalid_original_evidence_fails_before_any_output(harness,fault):
    h=harness
    if fault=='identity':h.source['card']['client']['id']='another assistant'
    elif fault=='receipt':h.verification.return_value.period_ts=lambda *_:None
    elif fault=='signature':h.verification.side_effect=ValueError('invalid original signature')
    elif fault=='missing-cache':h.source['deliveries'].clear()
    elif fault=='base64':h.source['deliveries']['1']['original.bin']='!not base64!'
    elif fault=='extra':h.source['deliveries']['1']['extra.bin']=''
    elif fault=='digest':h.source['deliveries']['1']['original.bin']=base64.b64encode(b'changed').decode()
    else:
        manifest=h.source['card']['fulfillment']['periods'][0]['manifest']
        manifest['files'].append(copy.deepcopy(manifest['files'][0]))
    result=h.lifecycle.fetch_outcome('operation',h.record,h.protocol,h.contract)
    assert result.state=='failed' and result.data['error']['code']=='protocol_error'
    assert not (h.home/'deliveries').exists()
    h.protocol.fetch_delivery.assert_not_called()


def test_local_conflict_failure_preserves_signature_cache_and_recovery_continues(harness):
    h=harness
    directory=h.home/'deliveries'/REF[7:]/'1';directory.mkdir(parents=True)
    conflict=directory/'original.bin';conflict.write_bytes(b'owner file')
    original=copy.deepcopy(h.source)
    # Each failed recovery must finish its own job, rather than raising out of
    # the resident's scan and starving the next independent operation.
    h.records.update({'later':{**h.record,'fetchFromCache':True}})
    h.records['operation']={**h.record,'fetchFromCache':True}
    finished=[]
    h.lifecycle.store=SimpleNamespace(checkpoints=lambda _:h.records)
    h.lifecycle.runtime.scheduler=SimpleNamespace(reference=lambda *args:args[-1],get=lambda _: {'status':'unknown'},
                                                  resolve=lambda reference,outcome:finished.append((reference,outcome)))
    h.lifecycle._recover()
    assert [ref for ref,_ in finished]==['operation','later']
    for _,result in finished:
        assert result.state=='failed' and result.data['error']['code']=='local_export_failed'
        assert result.data['error']['recovery']=='new_fetch_from_cache'
        assert 'receipt signature were verified' in result.data['error']['message']
    assert h.source==original and conflict.read_bytes()==b'owner file'
    h.protocol.fetch_delivery.assert_not_called()


def test_unknown_notification_without_cache_only_checkpoint_does_not_claim_completion(harness):
    h=harness;resolved=[]
    h.lifecycle.store=SimpleNamespace(checkpoints=lambda _:h.records)
    h.lifecycle.runtime.scheduler=SimpleNamespace(reference=lambda *args:args[-1],get=lambda _: {'status':'unknown'},
                                                  resolve=lambda *args:resolved.append(args))
    h.lifecycle._recover()
    assert not resolved and not (h.home/'deliveries').exists()


@pytest.mark.parametrize('execution_error',[None,{'code':'unknown_result'}])
def test_notification_ok_requires_no_execution_error_and_does_not_block_later_operation(harness,execution_error):
    h=harness;resolved=[]
    received={'result':{'reply':{'body':{'ok':True}}}}
    if execution_error is not None:received['result']['executionError']=execution_error
    h.protocol.transport.received=lambda _:received
    h.lifecycle.store=SimpleNamespace(checkpoints=lambda _:h.records)
    h.lifecycle.runtime.scheduler=SimpleNamespace(reference=lambda *args:args[-1],get=lambda _: {'status':'unknown'},
                                                  resolve=lambda *args:resolved.append(args))
    h.lifecycle._recover()
    if execution_error is None:
        assert len(resolved)==1 and resolved[0][0]=='operation' and resolved[0][1].state=='completed'
        assert (h.home/'deliveries').exists()
    else:
        assert not resolved and not (h.home/'deliveries').exists()
        assert not h.validation.called and not h.verification.called
        # The blocked original still precedes a separate cache-only operation
        # in the next same scan. Only that independent operation may export,
        # and it must still pass the original card/receipt validation.
        h.records['later']={**h.record,'fetchFromCache':True}
        original=h.lifecycle.fetch_outcome
        h.lifecycle.fetch_outcome=Mock(wraps=original)
        h.lifecycle._recover()
        assert len(resolved)==1 and resolved[0][0]=='later' and resolved[0][1].state=='completed'
        assert [call.args[0] for call in h.lifecycle.fetch_outcome.call_args_list]==['later']
        h.validation.assert_called_once();h.verification.assert_called_once()
    h.protocol.fetch_delivery.assert_not_called()
