"""Current step receipts expose only authenticated objects and their A2C digests."""
from types import SimpleNamespace

import pytest
from a2c import objects
from a2c.codec import utcnow
from a2c.errors import A2CError
from a2c.jws import Signer
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from ioalab_sdk.assistant.fulfillment import Fulfillment
from ioalab_sdk.runtime.protocol import RuntimeFault


REFERENCE='sha256:'+'a'*64
PROVIDER='urn:test:provider'


@pytest.fixture
def step_status(monkeypatch):
    signer=Signer(PROVIDER,Ed25519PrivateKey.generate(),kid='provider-key')
    request=objects.signed(objects.new('step-request',cardDigest=REFERENCE,period=1,step='approval'),signer,utcnow())
    task=SimpleNamespace(state='auth-required',status={'stepRequest':request},artifacts=[])
    card={'provider':PROVIDER,'signatures':{'5':[],'7':[]},'derived':{'periods':[{'amount':1}]},'fulfillment':{}}
    record={'card':card,'task':'original-task','state':'started'}
    client=SimpleNamespace(store=SimpleNamespace(get=lambda *_:record),
        transport=SimpleNamespace(get_task=lambda *_:task),keys=SimpleNamespace(jwk=lambda *_:signer.jwk))
    role=SimpleNamespace(runtime=object(),store=SimpleNamespace(messages=lambda *_:[]),
        identity=SimpleNamespace(agent_id='urn:test:assistant'),
        termination=SimpleNamespace(facts=lambda *_:{'terminated':True}))
    owner=Fulfillment(role)
    monkeypatch.setattr(owner,'client',lambda _: (client,{'result':{'formed':True},'interactionId':'original-interaction'}))
    return SimpleNamespace(owner=owner,client=client,task=task,card=card,record=record,request=request,signer=signer)


def test_current_step_receipt_uses_official_object_digest(step_status):
    data=step_status.owner.status(REFERENCE)['data']
    assert data['stepRequest']==step_status.request
    assert data['stepRequestDigest']==objects.object_digest(step_status.request)
    assert data['stepRequestDigest']!=REFERENCE


@pytest.mark.parametrize('state',['working','completed','auth-required'])
def test_no_current_step_has_no_digest(step_status,state):
    step_status.task.state=state
    step_status.task.status={}
    data=step_status.owner.status(REFERENCE)['data']
    assert data['stepRequest'] is None and data['stepRequestDigest'] is None


def test_terminated_contract_has_no_current_step_digest(step_status):
    step_status.card['fulfillment']['termination']={'original':'termination'}
    data=step_status.owner.status(REFERENCE)['data']
    assert data['terminationStatus']['terminated']
    assert data['stepRequest'] is None and data['stepRequestDigest'] is None


@pytest.mark.parametrize('boundary',['transport','verification_key'])
def test_unknown_step_read_or_verification_does_not_expose_candidate(step_status,monkeypatch,boundary):
    def unavailable(*_):raise RuntimeFault('unknown_result','Original lookup outcome is unknown')
    target,method=(step_status.client.transport,'get_task') if boundary=='transport' else (step_status.client.keys,'jwk')
    monkeypatch.setattr(target,method,unavailable)
    data=step_status.owner.status(REFERENCE)['data']
    assert data['stepRequest'] is None and data['stepRequestDigest'] is None


def test_tampered_step_is_rejected_instead_of_exposing_a_digest(step_status):
    step_status.request['step']='tampered'
    with pytest.raises(A2CError):step_status.owner.status(REFERENCE)


def test_authenticated_foreign_contract_step_is_rejected(step_status):
    foreign=objects.new('step-request',cardDigest='sha256:'+'b'*64,period=1,step='approval')
    step_status.task.status={'stepRequest':objects.signed(foreign,step_status.signer,utcnow())}
    with pytest.raises(RuntimeFault,match='another card') as error:step_status.owner.status(REFERENCE)
    assert error.value.code=='protocol_error'
