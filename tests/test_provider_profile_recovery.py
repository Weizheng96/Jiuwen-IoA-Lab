"""A successful real publication clears only its superseded profile error."""
from ioalab_sdk.provider import ProviderRole
from ioalab_sdk.runtime.config import RuntimeConfig
from ioalab_sdk.runtime.resident import ResidentRuntime
from ioalab_sdk.runtime.files import read_json,private_json
from ioalab_sdk.runtime.ipc import LocalClient
from test_resident_runtime import configuration
from test_resident_delegation import QuoteHost,uid
from test_service_publication import draft
from test_assistant_role import user_decision
from conftest import wait_for


def test_unpublished_error_clears_after_publish_without_losing_original_request_error(platform,tmp_path):
    config,_=configuration(platform,tmp_path)
    value=read_json(config)
    value['identities']=[value['identities'][1]]
    value['provider']={'services':{'service':{'instructions':'Use configured business tools.','skills':[],'max_active_delegations':2}}}
    private_json(config,value)
    host=QuoteHost();role=ProviderRole(host)
    runtime=ResidentRuntime(RuntimeConfig.load(config),modules=(role,),host_status=host.capabilities).start()
    try:
        wait_for(lambda:runtime.connection.connected and 'profile:service' in role.execution.errors)
        role.execution.errors['original-unknown-request']={'code':'unknown_result'}
        service={'profile':'service','name':'Configured service','description':'Software development',
                 'tags':['development'],'categoryId':platform.discovery.categories()[0]['id'],
                 'templates':[draft(platform)]}
        client=LocalClient(runtime.config.home)
        preview=client.call('provider.preview',{'service':service})
        assert preview['status']=='completed',preview
        args={k:preview['data'][k] for k in ('service','contentDigest','expectedRevision')}
        operation=uid();pending=client.call('provider.publish',args,operation_id=operation)
        assert pending['status']=='waiting',pending
        user_decision(runtime,pending['data']['confirmationRef'])
        published=client.call('provider.publish',args,operation_id=operation)
        assert published['status']=='completed',published
        wait_for(lambda:role.ready('service') and 'profile:service' not in role.execution.errors)
        current=client.call('provider.status',{'profile':'service'})['data']
        assert current['published'] and current['indexReady'] and current['localExecution']['executionReady']
        assert current['localExecution']['errors'] is None
        assert current['localExecution']['blockedRequests']==1
        assert role.execution.errors['original-unknown-request']=={'code':'unknown_result'}
    finally:runtime.close()
