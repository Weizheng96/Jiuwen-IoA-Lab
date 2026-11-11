"""Resumable fixed setup, with no model, receiver loop or business effects."""
import secrets

from a2c.codec import digest, digest_bytes
from ioalab_sdk.assistant import render
from ioalab_sdk.runtime import Lease
from ioalab_sdk.assistant import AssistantRole
from ioalab_sdk.runtime import require_configuration
from ioalab_sdk.runtime import RuntimeConfig
from ioalab_sdk.runtime import private_json, private_text, read_json
from ioalab_sdk.runtime import LocalClient, public_error
from ioalab_sdk.runtime import RuntimeFault
from ioalab_sdk.runtime import ResidentRuntime
from .deployment import operator, owned, operation


def result(deployment, state, runtime=None):
    return {'status':'completed' if state['phase']=='prepared' else 'waiting', 'phase':state['phase'],
            'account':'admin','runtime':runtime or {'state':'not_queried'},'modelStarted':False,
            'originalPublications':{name:item.get('result') for name,item in state['presets'].items()},
            'currentInterfacesChecked':False,'meetingInitiallyEnabled':deployment.value.get('notification') is not None}


def released(home):
    # A free OS lock is the only evidence of no owner; a descriptor file is not.
    lock=home/'client.lock'
    if lock.is_symlink(): return False
    if not lock.is_file(): return True
    try: Lease(lock,timeout=0).close()
    except (RuntimeError,OSError): return False
    return True


def runtime_state(deployment, state):
    """Only the authenticated local answer proves a running instance; nothing here starts or writes."""
    home=deployment.home/'resident'
    try:
        reply=LocalClient(home,timeout=2).call('runtime.status')
        if reply['status']!='completed': raise RuntimeFault('not_ready','The runtime status query is incomplete','runtime.status')
        if state['phase']!='prepared': raise RuntimeFault('not_ready','A runtime answered before preparation finished','prepare')
        live=reply['data']
        require_configuration(live,RuntimeConfig.load(deployment.runtime_path),assembly='ioalab-jiuwen')
        if [item['agentId'] for item in live['identities']]!=[state['identity']['agentId']]:
            raise RuntimeFault('identity_conflict','Running instance differs from the original managed identity')
    except RuntimeFault as exc:
        if exc.code in ('not_ready','unknown_result') and released(home): return {'state':'not_running'}
        return {'state':'not_queried','error':public_error(exc)}
    ready=live['identities'][0]['executionReady']
    return {'state':'ready' if ready else 'not_ready','bootId':live['bootId'],'executionReady':ready,'stopping':live['stopping'],
            'platformConnected':live['platformConnected'],'hostAvailable':bool(live['host'].get('available'))}


def status(deployment):
    state = deployment.state()
    if state['phase']=='prepared': deployment.prepared(state)
    return result(deployment,state,runtime_state(deployment,state))


def assets(deployment, state):
    # The original random credential is recorded before writing its private file.
    if 'webSecret' not in state:
        state['webSecret']=secrets.token_urlsafe(32); deployment.save(state)
    credential=deployment.home/'web-notification.env'
    text='IOALAB_ADMIN_WEB_SECRET='+state['webSecret']+'\n'
    if credential.exists():
        if credential.read_text(encoding='utf-8')!=text:
            raise RuntimeFault('configuration_changed','Preserve the original native notification credential')
    else: private_text(credential,text)
    if deployment.value.get('notification') is not None:
        from ioalab_tools.schedule import LocalSchedule
        schedule=LocalSchedule(deployment.schedule)
        if not state.get('schedulePlanned'):
            if deployment.schedule.exists():
                raise RuntimeFault('identity_conflict','An unregistered existing schedule will not be adopted')
            state['schedulePlanned']=True; deployment.save(state)
        if state.get('scheduleInitialized'):
            schedule.read()  # A missing original schedule is never initialized empty.
        elif deployment.schedule.exists():
            schedule.read(); state['scheduleInitialized']=True; deployment.save(state)
        else:
            schedule.init(); state['scheduleInitialized']=True; deployment.save(state)
    complete=deployment.complete()
    control=deployment.private/'host-preview.json'
    if control.exists() and read_json(control)!=complete:
        raise RuntimeFault('configuration_changed','Original host preparation input differs')
    if not control.exists(): private_json(control,complete)
    arguments=deployment.private/'web-arguments.json'
    if arguments.exists() and read_json(arguments)!={'profile':'admin'}:
        raise RuntimeFault('configuration_changed','Original web preparation profile differs')
    if not arguments.exists(): private_json(arguments,{'profile':'admin'})
    from ioalab_jiuwen import prepare_extension
    prepare_extension(control,arguments,'assistant')
    return complete


def publish(deployment, state, role, preset):
    name=preset['declaration']['name']
    entry=state['presets'].setdefault(name,{'draftOperation':operation(deployment.binding,name,'draft'),
                                         'operationId':operation(deployment.binding,name,'publish')})
    if entry.get('result'): return
    deployment.save(state)
    rules=role.rules
    draft=rules.draft(entry['draftOperation'],preset['declaration'],preset['instructions'])
    preview=rules.preview(draft['draftRef'])
    if preview['contentDigest']!=digest_bytes(render(preset['declaration'],preset['instructions']).encode('utf-8')):
        raise RuntimeFault('configuration_changed','The initial draft differs from the packaged fixed preset')
    if not preview.get('valid'): raise RuntimeFault('not_ready','A fixed administrator preset failed SDK validation')
    expected={'draftRef':draft['draftRef'],'contentDigest':preview['contentDigest'],'expectedRevision':0}
    if 'parameters' in entry and entry['parameters']!=expected:
        raise RuntimeFault('configuration_changed','The original fixed preset draft changed')
    entry.update(parameters=expected,preview=preview['preview']); deployment.save(state)
    outcome=rules.publish(entry['operationId'],**{
        'reference':expected['draftRef'],'content_digest':expected['contentDigest'],'expected_revision':0})
    if outcome['status']=='waiting':
        reference=outcome['data']['confirmationRef']
        manager=role.runtime.confirmations
        confirmation=manager.get(reference,detail=True)
        binding=confirmation['binding']
        if binding!={'actor':role.identity.agent_id,'operationId':entry['operationId'],'subject':expected,
                     'stage':'rules.publish','action':'interface.publish','preview':preview['preview']}:
            raise RuntimeFault('forbidden','Initial permission does not match the packaged fixed preset')
        if entry.get('confirmation') not in (None,{'reference':reference,'bindingDigest':confirmation['bindingDigest']}):
            raise RuntimeFault('identity_conflict','Original preset confirmation differs')
        entry['confirmation']={'reference':reference,'bindingDigest':confirmation['bindingDigest']}; deployment.save(state)
        source='deployment-preset:'+deployment.value['deployment_id']+':'+deployment.resource_digest
        manager.decide(reference,confirmation['bindingDigest'],'approve',authority=manager.user_channel(),source=source)
        outcome=rules.publish(entry['operationId'],expected['draftRef'],expected['contentDigest'],0,reference)
    if outcome['status']!='completed':
        raise RuntimeFault('not_ready','Original fixed preset was not approved or its confirmation expired; inspect the original preparation')
    entry['result']=outcome['data']; deployment.save(state)


def prepare(deployment):
    operator()
    with deployment.lock():
        state=deployment.state()
        if state['phase']=='prepared':
            deployment.prepared(state); return result(deployment,state)
        if state['phase']=='finishing':
            if not deployment.runtime_path.exists():
                raise RuntimeFault('not_ready','Restore the original complete administrator configuration')
            current=read_json(deployment.runtime_path)
            if current==deployment.base():
                deployment.initial_owner()
                with Lease(deployment.home/'resident/client.lock',timeout=0):
                    private_json(deployment.runtime_path,deployment.complete())
            elif digest(current)!=state['finalConfigDigest']:
                raise RuntimeFault('configuration_changed','Original administrator configuration changed before preparation completed')
            state['phase']='prepared'; deployment.save(state)
            deployment.prepared(state); return result(deployment,state)
        deployment.save(state)
        deployment.initial_owner()
        base=deployment.base()
        if deployment.runtime_path.exists():
            owned(deployment.runtime_path,private=True)
            if read_json(deployment.runtime_path)!=base:
                raise RuntimeFault('configuration_changed','Incomplete administrator configuration was edited')
        else: private_json(deployment.runtime_path,base)
        complete=assets(deployment,state)
        from ioalab_jiuwen import ConfiguredTools
        tools=ConfiguredTools(complete['host']['business_tools'],base['identities'],home=deployment.home/'resident',base_dir=deployment.home)
        runtime=None
        try:
            role=AssistantRole(tools=tools.for_profile('admin'))
            # No start(): no host, receiver, scheduler, model or business tool is run.
            runtime=ResidentRuntime(RuntimeConfig.load(deployment.runtime_path),modules=(role,))
            identity=runtime.identities[0].public()
            if state.get('identity') not in (None,identity):
                raise RuntimeFault('identity_conflict','Original managed administrator identity differs')
            state['identity']=identity
            state['identityIndex']=read_json(runtime.config.home/'identity-index.json')['admin']
            deployment.save(state)
            for preset in deployment.presets: publish(deployment,state,role,preset)
        finally:
            if runtime: runtime.close()
            tools.close()
        state.update(phase='finishing',finalConfigDigest=digest(complete)); deployment.save(state)
        private_json(deployment.runtime_path,complete)
        state['phase']='prepared'; deployment.save(state)
        deployment.prepared(state)
        return result(deployment,state)
