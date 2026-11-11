"""Finite publication renewal over the ordinary resident SDK, without a business execution loop."""
from copy import deepcopy
from datetime import datetime,timedelta,timezone
import time
import uuid
import sys

from a2c.codec import digest,fmt_time,parse_time
from ioalab_sdk.provider import publication_terms
from ioalab_sdk.runtime import start,require_configuration
from ioalab_sdk.runtime import RuntimeConfig
from ioalab_sdk.runtime import private_json,read_json
from ioalab_sdk.runtime import LocalClient,public_error
from ioalab_sdk.runtime import RuntimeFault


def connected(deployment,account,*,launch):
    path=deployment.config_path(account);config=RuntimeConfig.load(path)
    if config.platform!=deployment.value['platform_url'].rstrip('/') or config.username!=account or config.provider.get('require_publication_authorization') is not True:
        raise RuntimeFault('configuration_changed','Restore the intended strict official runtime configuration')
    state=start(path,command=[sys.executable,'-m','ioalab_jiuwen'],assembly='ioalab-jiuwen',environment=deployment.environment()) if launch else LocalClient(config.home,timeout=2).call('runtime.status')
    if state['status']!='completed':raise RuntimeFault('not_ready','The original SDK runtime is still starting','runtime.status')
    require_configuration(state['data'],config,assembly='ioalab-jiuwen')
    return LocalClient(config.home,boot_id=state['data']['bootId']),state['data']


def public_reply(reply):
    data=reply.get('data') or {};receipt=data.get('publicationReceipt') or {};current=data.get('currentPublication') or {}
    return {'status':reply['status'],'operationId':reply['operationId'],'acceptedRevision':receipt.get('acceptedRevision'),
            'currentRevision':current.get('revision'),'indexReady':current.get('indexReady'),'error':reply.get('error')}


def tick(deployment,*,launch=True,clock=time.time,stop_event=None):
    state=deployment.state(prepared=True)
    path=deployment.home/'publications.json'
    publications=read_json(path) if path.exists() else {}
    accounts=[]
    for account,entry in state['accounts'].items():
        if stop_event is not None and stop_event.is_set():return {'status':'waiting','stopping':True,'accounts':accounts,'allServicesReady':False}
        row={'account':account,'services':[]};accounts.append(row)
        try:
            ipc,live=connected(deployment,account,launch=launch)
            actual={p['profile']:(p['agentId'],p['protocolId']) for p in live['identities']}
            expected={p['profile']:(p['agentId'],p['protocolId']) for p in entry['identities']}
            if actual!=expected:raise RuntimeFault('identity_conflict','Runtime identities differ from original official preparation')
            row.update(connected=live['platformConnected'],hostAvailable=bool(live['host'].get('available')))
            if not row['connected'] or not row['hostAvailable'] or live['stopping']:
                raise RuntimeFault('not_ready','Restore the original host and platform connection')
        except RuntimeFault as exc:row['error']=public_error(exc);continue
        for identity in entry['identities']:
            if stop_event is not None and stop_event.is_set():return {'status':'waiting','stopping':True,'accounts':accounts,'allServicesReady':False}
            profile=identity['profile'];actor=identity['agentId'];item={'profile':profile,'agentId':actor};row['services'].append(item)
            try:
                status=ipc.call('provider.status',{'profile':profile})
                if status['status']!='completed':raise RuntimeFault('not_ready','The service status query is incomplete')
                status=status['data'];item['current']=status
                if stop_event is not None and stop_event.is_set():return {'status':'waiting','stopping':True,'accounts':accounts,'allServicesReady':False}
                original=publications.get(actor)
                if original and original['status'] in ('unknown','accepted','running'):
                    reply=ipc.call('provider.publish',original['args'],operation_id=original['operationId'])
                    original.update(status=reply['status'],result=public_reply(reply));private_json(path,publications)
                    item['publication']=public_reply(reply);continue
                service=entry['services'][profile]['service']
                terms=publication_terms(identity['protocolId'],service);policy=status.get('publicationAuthorization')
                if status['published'] and status.get('publishedTermsDigest')!=terms:
                    raise RuntimeFault('configuration_changed','Published terms changed; preserve the owner publication','configure_publication')
                if not policy or not policy['enabled'] or policy['termsDigest']!=terms or policy['maxTemplateSeconds']<30*86400:
                    raise RuntimeFault('forbidden','Configure a current matching deployment publication authorization','configure_publication')
                basis=digest({'authorization':policy,'revision':status['revision'],'terms':status.get('publishedTermsDigest')})
                if original and original['status'] in ('rejected','failed','waiting') and original['basis']==basis:
                    item['publication']=original['result'];continue
                expiry=parse_time(status['templateExpiresAt']).timestamp() if status.get('templateExpiresAt') else 0
                if status['published'] and expiry-clock()>=7*86400:
                    if not status['indexReady'] and original and original['status']=='completed':
                        reply=ipc.call('provider.publish',original['args'],operation_id=original['operationId'])
                        original.update(status=reply['status'],result=public_reply(reply));private_json(path,publications);item['publication']=public_reply(reply)
                    else:item['status']='current'
                    continue
                renewed=deepcopy(service)
                for template in renewed['templates']:template['validUntil']=fmt_time(datetime.fromtimestamp(clock(),timezone.utc)+timedelta(days=30))
                preview=ipc.call('provider.preview',{'service':renewed})
                if preview['status']!='completed':raise RuntimeFault('not_ready','The original SDK publication preview is incomplete')
                if stop_event is not None and stop_event.is_set():return {'status':'waiting','stopping':True,'accounts':accounts,'allServicesReady':False}
                args={k:preview['data'][k] for k in ('service','contentDigest','expectedRevision')}
                original={'operationId':str(uuid.uuid4()),'args':args,'status':'unknown','basis':basis}
                publications[actor]=original;private_json(path,publications)
                reply=ipc.call('provider.publish',args,operation_id=original['operationId'])
                original.update(status=reply['status'],result=public_reply(reply));private_json(path,publications);item['publication']=public_reply(reply)
            except RuntimeFault as exc:item['error']=public_error(exc)
    return {'status':'completed','accounts':accounts,'allServicesReady':bool(accounts) and all(not a.get('error') and all(not i.get('error') and i.get('current',{}).get('localExecution',{}).get('executionReady') and i.get('current',{}).get('admissionPolicy',{}).get('accepting') and i.get('current',{}).get('indexReady') and i.get('current',{}).get('templateReady') and i.get('publication',{}).get('status','completed')=='completed' for i in a['services']) for a in accounts)}


def status(deployment):
    state=deployment.state(prepared=True);rows=[]
    for account in state['accounts']:
        try:
            ipc,live=connected(deployment,account,launch=False)
            services=[]
            for identity in state['accounts'][account]['identities']:
                item={'profile':identity['profile'],'agentId':identity['agentId']};services.append(item)
                if not live['platformConnected']:
                    item['error']={'code':'not_ready','recovery':'restore_connection'};continue
                try:
                    result=ipc.call('provider.status',{'profile':identity['profile']})
                    if result['status']=='completed':item['current']=result['data']
                    else:item['error']=result.get('error') or {'code':'not_ready'}
                except RuntimeFault as exc:item['error']=public_error(exc)
            rows.append({'account':account,'runtime':live,'services':services})
        except RuntimeFault as exc:rows.append({'account':account,'error':public_error(exc)})
    return {'status':'completed','accounts':rows}
