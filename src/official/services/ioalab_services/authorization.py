"""Explicit initial deployment authorization; longrunning participants never receive this token."""
import httpx
import uuid
from urllib.parse import urlsplit
from ioalab_sdk.provider import publication_terms
from ioalab_sdk.runtime import private_json,read_json
from ioalab_sdk.runtime import RuntimeFault


def administrator_session(deployment,username,password):
    if not isinstance(username,str) or not username:raise RuntimeFault('forbidden','The configured administrator username is unavailable')
    if not isinstance(password,str) or not password:raise RuntimeFault('forbidden','The configured deployment administrator password is unavailable')
    with httpx.Client(base_url=deployment.value['platform_url'].rstrip('/'),trust_env=False,timeout=30) as http:
        response=http.post('/api/admin/login',json={'username':username,'password':password});response.raise_for_status()
        cookie='__Host-ioalab_session' if urlsplit(deployment.value['platform_url']).scheme=='https' else 'ioalab_session'
        token=response.cookies.get(cookie)
        if not token:raise RuntimeFault('protocol_error','The existing administrator login did not create its web session')
        return token


def authorize(deployment,token):
    if not isinstance(token,str) or not token.strip():raise RuntimeFault('forbidden','An existing web administrator token is required')
    with deployment.lock():
        state=deployment.state(prepared=True);path=deployment.home/'authorization.json'
        changes=[]
        for entry in state['accounts'].values():
            for identity in entry['identities']:
                service=entry['services'][identity['profile']]['service']
                changes.append({'agentId':identity['agentId'],'expectedRevision':0,
                    'termsDigest':publication_terms(identity['protocolId'],service),'maxTemplateSeconds':30*86400,'enabled':True})
        with httpx.Client(base_url=deployment.value['platform_url'].rstrip('/'),trust_env=False,timeout=120,
                          headers={'Authorization':'Bearer '+token}) as http:
            if path.exists():
                request=read_json(path)
                if request['binding']!=deployment.binding:raise RuntimeFault('configuration_changed','Original deployment authorization belongs to another configuration')
            else:
                existing=http.get('/api/admin/publication-authorizations');existing.raise_for_status()
                current={r['agentId']:r for r in existing.json()['currentAuthorizations']}
                present=[current.get(c['agentId']) for c in changes]
                if any(present):
                    if not all(p and p['enabled'] and p['termsDigest']==c['termsDigest'] and p['maxTemplateSeconds']>=c['maxTemplateSeconds'] for p,c in zip(present,changes)):
                        raise RuntimeFault('configuration_changed','Existing publication scopes require explicit administrator configuration; they will not be reenabled')
                    return {'status':'completed','authorizationReceipt':None,'currentAuthorizations':present,'existing':True}
                request={'binding':deployment.binding,'body':{'operationId':str(uuid.uuid4()),'changes':changes}}
                private_json(path,request)
            response=http.post('/api/admin/publication-authorizations',json=request['body']);response.raise_for_status()
            result=response.json()
            current={p['agentId']:p for p in result['currentAuthorizations'] if p}
            if len(current)!=len(changes) or any(not current.get(c['agentId']) or not current[c['agentId']]['enabled'] or current[c['agentId']]['termsDigest']!=c['termsDigest'] or current[c['agentId']]['maxTemplateSeconds']<c['maxTemplateSeconds'] for c in changes):
                raise RuntimeFault('forbidden','The original authorization receipt does not reenable currently revoked scopes','configure_publication')
            return {'status':'completed',**result}
