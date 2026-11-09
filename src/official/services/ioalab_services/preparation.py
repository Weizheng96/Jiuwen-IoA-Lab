"""One resumable preparation recipe; no publication, model or private SDK database access."""
from a2c.codec import digest
from ioalab_sdk.provider import ProviderRole
from ioalab_sdk.runtime import RuntimeConfig
from ioalab_sdk.runtime import private_json,read_json
from ioalab_sdk.runtime import LocalClient
from ioalab_sdk.runtime import RuntimeFault
from ioalab_sdk.runtime import ResidentRuntime
from .deployment import operation,call
from .profiles import prepare as service_configuration


def prepare(deployment):
    with deployment.lock():
        state=deployment.state()
        for domain in deployment.domains:
            account=domain['account'];path=deployment.config_path(account)
            entry=state['accounts'].setdefault(account,{'phase':'preparing','services':{}})
            if entry['phase']=='prepared':
                # Existing user edits are never overwritten by rerunning prepare.
                RuntimeConfig.load(path)
                continue
            base=deployment.base(domain)
            deployment.save(state)
            # A crash after the final atomic config write needs no control-runtime takeover.
            if entry.get('finalConfigDigest') and path.exists() and digest(read_json(path))==entry['finalConfigDigest']:
                entry['phase']='prepared';deployment.save(state);continue
            if path.exists():
                if read_json(path)!=base:raise RuntimeFault('configuration_changed','Incomplete preparation configuration was edited; preserve it and inspect the original preparation')
            else:private_json(path,base)
            runtime=ResidentRuntime(RuntimeConfig.load(path),modules=(ProviderRole(),))
            try:
                runtime.start()
                ipc=LocalClient(runtime.config.home)
                identities=call(ipc,'identity.status',{},operation_id=operation(account,'identities'))
                entry['identities']=identities;deployment.save(state)
                pay=runtime.account.config['pay']
                payment={'id':pay,'jwksUri':pay+'/.well-known/jwks.json'}
                for definition in domain['services']:
                    profile=definition['id']
                    item=entry['services'].get(profile)
                    if item and 'service' in item:continue
                    verifier=definition['verifier']
                    args={'profile':profile,'verifierId':verifier['id'],'source':verifier['source'],'reads':verifier.get('reads',[])}
                    if 'kind' in verifier:args['kind']=verifier['kind']
                    item=entry['services'].setdefault(profile,{'verifierOperation':operation(deployment.binding,account,profile,args)})
                    deployment.save(state)
                    ref=call(ipc,'provider.verifier.publish',args,operation_id=item['verifierOperation'])
                    configured=service_configuration(profile,profile=profile,verifier_ref=ref,payment_provider=payment)
                    preview=call(ipc,'provider.preview',{'service':configured['service']},operation_id=operation(deployment.binding,account,profile,'preview'))
                    configured['service']=preview['service']
                    configured['execution']['native_tools']=dict(deployment.tools)
                    item.update(configured,verifierRef=ref);deployment.save(state)
            finally:runtime.close()
            complete={**base,'host':deployment.host,'provider':{'require_publication_authorization':True,
                       'services':{key:item['execution'] for key,item in entry['services'].items()}}}
            RuntimeConfig.from_mapping(path,complete)
            entry['finalConfigDigest']=digest(complete);deployment.save(state)
            private_json(path,complete)
            entry['phase']='prepared';deployment.save(state)
        deployment.state(prepared=True)
        return {'status':'completed','accounts':len(state['accounts']),
                'services':sum(len(a['services']) for a in state['accounts'].values()),'publishedDuringPrepare':False,'modelStarted':False}
