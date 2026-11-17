"""Hard object expiry and soft protocol response deadlines through public APIs."""
import uuid

import pytest

from ioalab_sdk.runtime import ConfirmationDeadline, RuntimeFault, RuntimeStore
from ioalab_sdk.runtime.confirmations import Confirmations


def test_explicit_object_bound_confirmation_is_not_an_implicit_empty_deadline(tmp_path):
    for kwargs in ({},{'object_bound':1},{'object_bound':True,'expires_at':400000.}):
        with pytest.raises(RuntimeFault):ConfirmationDeadline(**kwargs)
    store=RuntimeStore(tmp_path/'runtime.sqlite','http://local',['actor'],owner_id='owner')
    clock=[1000.];manager=Confirmations(store,clock=lambda:clock[0])
    original={'actor':'actor','operationId':str(uuid.uuid4()),'subject':{'contractRef':'original'},
              'stage':'delegation.terminate','action':'terminate','preview':{'mode':'unilateral'}}
    try:
        value=manager.ensure(original,deadline=ConfirmationDeadline(object_bound=True))
        clock[0]=1000000.
        current=manager.find(original)
        assert current['status']=='pending' and current['expiresAt'] is None and current['objectBound'] is True
        assert current['bindingDigest']==value['bindingDigest']
        with pytest.raises(RuntimeFault,match='changed'):
            manager.ensure({**original,'subject':{'contractRef':'another'}},deadline=ConfirmationDeadline(object_bound=True))
    finally:store.close()


def test_template_deadlines_keep_distinct_hard_and_protocol_due_times(tmp_path):
    store = RuntimeStore(tmp_path/'runtime.sqlite', 'http://local', ['actor'], owner_id='owner')
    clock = [1000.]
    confirmations = Confirmations(store, clock=lambda: clock[0])
    def original(stage):
        return {'actor': 'actor', 'operationId': str(uuid.uuid4()),
                'subject': {'contractRef': 'real-card', 'period': 1},
                'stage': stage, 'action': stage,
                'preview': {'title': '原合同阶段'}}
    hard_binding, soft_binding = original('contract'), original('accept')
    try:
        hard = confirmations.ensure(hard_binding, deadline=ConfirmationDeadline(expires_at=400000.))
        soft = confirmations.ensure(soft_binding, deadline=ConfirmationDeadline(response_due_at=400000.))
        assert hard['expiresAt'] == '1970-01-05T15:06:40Z'
        assert soft['expiresAt'] is None
        assert soft['responseDueAt'] == '1970-01-05T15:06:40Z'
        # More than 24 hours pass without inventing a new operation or expiry.
        clock[0] = 100000.
        assert confirmations.ensure(hard_binding, deadline=ConfirmationDeadline(expires_at=400000.)) == hard
        assert confirmations.get(soft['reference'])['status'] == 'pending'
        clock[0] = 400001.
        assert confirmations.get(hard['reference'])['status'] == 'expired'
        assert confirmations.get(soft['reference'])['responseOverdue'] is True
        assert confirmations.get(soft['reference'])['status'] == 'pending'
        with pytest.raises(RuntimeFault, match='expired'):
            confirmations.decide(hard['reference'], hard['bindingDigest'], 'approve',
                authority=confirmations.user_channel(), source='actual-user-event')
        confirmations.decide(soft['reference'], soft['bindingDigest'], 'approve',
            authority=confirmations.user_channel(), source='actual-user-event')
        confirmations.claim(soft['reference'], soft_binding)
        assert confirmations.get(soft['reference'])['status'] == 'approved'
    finally:
        store.close()
