"""An external owner assembles the ordinary resident SDK and completes public IPC flow."""
import importlib.util
from pathlib import Path
import httpx
from a2c.binding import A2ATransport
from a2c.codec import digest
from a2c.common import Directory
from a2c.jws import verify
from a2c.slots import verify_template
from ioalab_sdk.runtime.ipc import LocalClient
from _resident_payment import ready_for_accept
from test_assistant_role import user_decision
from test_resident_contracts import status
from test_resident_fulfillment import operation
from conftest import wait_for


def test_third_party_resident_provider_uses_public_publication_and_completes_contract(platform,client,tmp_path):
    source=Path(__file__).resolve().parents[1]/'src/access/examples/provider.py'
    spec=importlib.util.spec_from_file_location('external_resident_example',source)
    example=importlib.util.module_from_spec(spec);spec.loader.exec_module(example)
    created=[]
    def factory(config,host,tools=None):
        runtime=example.create_runtime(config,host,tools);created.append(runtime);return runtime
    with ready_for_accept(platform,tmp_path,provider_factory=factory) as (provider,assistant,ref,key,pending):
        assert created and provider.identities[0].agent_id==created[0].identities[0].agent_id
        identity=provider.identities[0]
        published=client.get('/a2a/agents/'+identity.agent_id+'/publication')
        assert published.status_code==200
        document=published.json();declaration=document['declaration']
        assert declaration['agentId']==identity.agent_id and declaration['protocolId']==identity.protocol_id
        assert document['declarationDigest']==digest(declaration)
        verify(document['proof'],digest(declaration),identity.signer.jwk,
               slot='ioa-service',signer=identity.protocol_id,expect_kid=True)
        transport=A2ATransport(http=lambda:httpx.AsyncClient(timeout=15,trust_env=False))
        try:
            templates=transport.send(identity.protocol_id,'template.get',{}).body['templates']
            assert templates and {t['templateDigest'] for t in templates}==set(declaration['templateDigests'])
            for template in templates:
                assert template['provider']==identity.protocol_id and template['signatures']['provider']
                verify_template(template,Directory(fetch=transport.jwks))
        finally:transport.close()
        assert provider.connection.connected and not hasattr(provider,'public_http_server')
        user_decision(assistant,pending['data']['confirmationRef'])
        LocalClient(assistant.config.home).call('assistant.delegation.accept',{'contractRef':ref,'period':1},operation_id=key)
        wait_for(lambda:(operation(assistant,ref,key) or {}).get('status')=='completed')
        assert operation(assistant,ref,key)['data']['payment']=='paid'
        wait_for(lambda:status(assistant,ref)['data']['closed'])
        assert len(platform.db.rows("SELECT * FROM transfers WHERE ref=? AND purpose='installment'",ref))==1
        assert platform.market.reputation(identity.protocol_id)['completed']>=1
