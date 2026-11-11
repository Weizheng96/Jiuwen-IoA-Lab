"""Private deployment recipes; participant state remains exclusively owned by the SDK."""
from copy import deepcopy
from pathlib import Path
import os
import re
import uuid

from a2c.codec import digest
from ioalab_sdk.runtime import Lease
from ioalab_sdk.runtime import RuntimeConfig
from ioalab_sdk.runtime import private_json,read_json,restrict
from ioalab_sdk.runtime import RuntimeFault
from .catalog import load_domains


class Deployment:
    def __init__(self,path,*,services=None):
        self.path=Path(path).resolve();self.value=read_json(self.path)
        required={'platform_url','data_dir','host'}
        if not isinstance(self.value,dict) or not required<=set(self.value) or set(self.value)-required-{'accounts','max_model_runs','native_tools'}:
            raise RuntimeFault('invalid_input','Use the documented official deployment configuration')
        value=self.value;host=deepcopy(value['host'])
        if not isinstance(host,dict) or set(host)-{'command','data_dir','model','skill_directories'} or not {'command','data_dir'}<=set(host):
            raise RuntimeFault('invalid_input','Configure the original Jiuwen process host')
        if not isinstance(host['command'],list) or not host['command'] or any(not isinstance(x,str) or not x for x in host['command']):
            raise RuntimeFault('invalid_input','Host command must be a nonempty argv list')
        self.host=host;self.host['data_dir']=str(self.resolve(host['data_dir']))
        if host.get('model') is not None and (not isinstance(host['model'],str) or not host['model']):
            raise RuntimeFault('invalid_input','Host model must be an existing model selection key')
        skills=host.get('skill_directories',{})
        if not isinstance(skills,dict) or len(skills)>64 or any(not isinstance(k,str) or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]{0,63}',k) or not isinstance(v,str) or not Path(v).expanduser().is_absolute() for k,v in skills.items()):
            raise RuntimeFault('invalid_input','Business skills require exact names and absolute source directories')
        tools=value.get('native_tools',{'read_file':'allow','write_file':'allow'})
        if not isinstance(tools,dict) or len(tools)>64 or any(not isinstance(k,str) or not re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,63}',k) or k=='ask_user' or v not in ('allow','ask','deny') for k,v in tools.items()):
            raise RuntimeFault('invalid_input','Use explicit native tool names and permissions')
        self.tools=dict(tools);self.home=self.resolve(value['data_dir'])
        self.domains=restricted(load_domains(),services)
        self.accounts=[{'account':self.domains[0]['account'],'services':[s for d in self.domains for s in d['services']]}]
        accounts=value.get('accounts',{})
        if not isinstance(accounts,dict) or set(accounts)-{d['account'] for d in self.accounts}:
            raise RuntimeFault('invalid_input','Account overrides must name configured internal ISP accounts')
        for item in accounts.values():
            if not isinstance(item,dict) or set(item) not in ({'password'},{'password_env'}):
                raise RuntimeFault('invalid_input','Each account override requires password or password_env')
        if len({name.casefold() for name in skills})!=len(skills):raise RuntimeFault('invalid_input','Business skill source names must be distinct ignoring case')
        self.binding=digest({**value,'data_dir':str(self.home),'host':self.host,'catalogDigest':digest({'domains':self.domains})})
        # Validate all RuntimeConfig-owned input before creating any local state.
        for account in self.accounts:RuntimeConfig.from_mapping(self.config_path(account['account']),self.base(account))

    def resolve(self,value):
        if not isinstance(value,str) or not value.strip():raise RuntimeFault('invalid_input','An explicit local directory is required')
        path=Path(value).expanduser()
        return (path if path.is_absolute() else self.path.parent/path).resolve()

    def lock(self):
        allowed={'deployment.lock','accounts','state.json','authorization.json','publications.json'}
        if self.home.exists() and any(p.name not in allowed and not p.name.startswith(('.state.json-','.authorization.json-','.publications.json-')) for p in self.home.iterdir()):
            raise RuntimeFault('invalid_input','Use a dedicated official deployment data directory')
        self.home.mkdir(mode=0o700,parents=True,exist_ok=True);restrict(self.home,directory=True)
        return Lease(self.home/'deployment.lock',timeout=0)

    def environment(self):
        allowed={'PATH','SYSTEMROOT','WINDIR','COMSPEC','PATHEXT','TEMP','TMP','USERPROFILE','HOMEDRIVE','HOMEPATH',
                 'PROCESSOR_ARCHITECTURE','PROCESSOR_ARCHITEW6432','NUMBER_OF_PROCESSORS',
                 'APPDATA','LOCALAPPDATA','PROGRAMDATA','HOME','USERNAME','USER','LOGNAME','LANG','LC_ALL','A2C_CACHE_DIR','SSL_CERT_FILE','SSL_CERT_DIR',
                 'REQUESTS_CA_BUNDLE','HTTP_PROXY','HTTPS_PROXY','NO_PROXY'}
        for domain in self.accounts:
            value=read_json(self.config_path(domain['account'])) if self.config_path(domain['account']).exists() else self.base(domain)
            if value.get('password_env'):allowed.add(value['password_env'].upper())
        return {**{key:value for key,value in os.environ.items() if key.upper() in allowed},'PYTHONUTF8':'1'}

    def config_path(self,account):return self.home/'accounts'/account/'config.json'

    def base(self,domain):
        account=domain['account']
        return {'platform_url':self.value['platform_url'],'username':account,
                **self.value.get('accounts',{}).get(account,{'password':account[-3:]}),
                'data_dir':'./resident','identities':[{'key':s['id'],'role':'service','name':s['name']} for s in domain['services']],
                'max_model_runs':self.value.get('max_model_runs',4),
                'provider':{'require_publication_authorization':True,'services':{}}}

    def state(self,*,prepared=False):
        path=self.home/'state.json'
        result=read_json(path) if path.exists() else {'schema':1,'binding':self.binding,'accounts':{}}
        if result.get('schema')!=1 or result.get('binding')!=self.binding:
            raise RuntimeFault('configuration_changed','Deployment configuration changed; preserve existing state and configure deliberately')
        for domain in self.accounts:
            entry=result['accounts'].get(domain['account'])
            if entry and entry.get('phase')=='prepared':
                profiles={s['id'] for s in domain['services']};identities=entry.get('identities',[])
                if (not isinstance(entry.get('services'),dict) or set(entry['services'])!=profiles or
                    not isinstance(identities,list) or len(identities)!=len(profiles) or
                    any(not isinstance(p,dict) or p.get('role')!='service' or not p.get('agentId') or not p.get('protocolId') for p in identities) or
                    {p.get('profile') for p in identities}!=profiles or len({p['agentId'] for p in identities})!=len(profiles) or
                    any(not isinstance(v,dict) or not isinstance(v.get('service'),dict) or v['service'].get('profile')!=key or not isinstance(v.get('execution'),dict) for key,v in entry['services'].items())):
                    raise RuntimeFault('not_ready','Original completed preparation is incomplete; preserve it and inspect the deployment state')
        if prepared and (set(result['accounts'])!={d['account'] for d in self.accounts} or any(a.get('phase')!='prepared' for a in result['accounts'].values())):
            raise RuntimeFault('not_ready','Complete official prepare before authorizing or serving','prepare')
        return result

    def save(self,state):private_json(self.home/'state.json',state)


def restricted(domains,services):
    """All domains, or only the domains and services named by an explicit service id list."""
    if services is None:return domains
    known={s['id'] for d in domains for s in d['services']}
    if (isinstance(services,(str,bytes,dict)) or not isinstance(services,(list,tuple,set,frozenset)) or not services
            or any(not isinstance(item,str) for item in services) or set(services)-known):
        raise RuntimeFault('invalid_input','Restrict the deployment only to known official service ids')
    wanted=set(services)
    return [{**d,'services':[s for s in d['services'] if s['id'] in wanted]} for d in domains
            if any(s['id'] in wanted for s in d['services'])]


def operation(*parts):return str(uuid.uuid5(uuid.NAMESPACE_URL,digest({'parts':list(parts)})))


def call(client,name,args,*,operation_id):
    result=client.call(name,args,operation_id=operation_id)
    if result['status']!='completed':
        error=result.get('error') or {}
        raise RuntimeFault(error.get('code','not_ready'),error.get('message','The original SDK operation is not complete'),
                           error.get('recovery','inspect_original_operation'),operation_id=operation_id)
    return result['data']
