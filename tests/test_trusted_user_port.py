"""Remote confirmation transport is closed and never becomes model RPC."""
import uuid

import httpx
import pytest

from ioalab_sdk.runtime import RuntimeStore, RuntimeFault
from ioalab_sdk.runtime.confirmations import Confirmations
from ioalab_sdk.runtime.trusted_user import TrustedUserPort, TrustedUserServer


def test_trusted_transport_authenticates_epoch_actor_and_revocation(tmp_path):
    store=RuntimeStore(tmp_path/'runtime.sqlite','http://local',['a','b'],owner_id='owner')
    manager=Confirmations(store)
    def pending(actor):return manager.ensure({'actor':actor,'operationId':str(uuid.uuid4()),'subject':{},
        'stage':'delegation.contract','action':'card.confirm','preview':{'budget':'10.00'}})
    first,other=pending('a'),pending('b');active=[True]
    port=TrustedUserPort(manager,actor='a',generation='epoch-1',authorize=lambda context,generation:active[0] and context=={'generation':generation})
    server=TrustedUserServer(port,key='k'*40).start()
    try:
        with httpx.Client(trust_env=False) as client:
            def call(body,key='k'*40):return client.post(server.address,headers={'Authorization':'Bearer '+key},json=body)
            assert call({'generation':'epoch-1','method':'list'},key='wrong').status_code==403
            assert client.post(server.address,headers={'Authorization':'Bearer '+'k'*40,'Origin':'http://untrusted'},
                json={'generation':'epoch-1','method':'list'}).status_code==403
            response=call({'generation':'epoch-1','method':'list'}).json()['data']
            assert [x['reference'] for x in response]==[first['reference']]
            assert manager.user_channel() not in str(response)
            assert call({'generation':'epoch-1','method':'get','reference':other['reference']}).status_code==403
            body={'generation':'epoch-1','method':'decide','reference':first['reference'],
                'bindingDigest':first['bindingDigest'],'decision':'approve','eventId':'browser-event'}
            assert call({**body,'generation':'old'}).status_code==403
            assert call({**body,'bindingDigest':'changed'}).status_code==409
            assert manager.get(first['reference'])['status']=='pending'
            assert call(body).json()['data']['status']=='approved'
            assert call({'generation':'epoch-1','method':'call','operation':'arbitrary'}).status_code!=200
            active[0]=False
            assert call({'generation':'epoch-1','method':'list'}).status_code==403
        port.close()
        with pytest.raises(RuntimeFault):port.get({'generation':'epoch-1'},first['reference'])
    finally:server.close();store.close()
