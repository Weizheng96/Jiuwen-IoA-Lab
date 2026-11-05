"""One private initial input; generated runtime files are preserved after creation."""
import json
from pathlib import Path
import secrets

from .configuration import validate, platform
from .layout import environment_text, read_environment, write_initial
from .release import digest, read_json
from .workspace import path_without_links, save


FORMAT='ioalab.initial-configuration/1'


def local_url(bind,port):
    host={'0.0.0.0':'127.0.0.1','::':'::1'}.get(bind,bind)
    return 'http://'+('['+host+']' if ':' in host else host)+':'+str(port)


class Configuration:
    def __init__(self,layout,identity,value,*,assets=None):
        self.layout,self.identity=layout,identity
        self.assets = path_without_links(assets) if assets is not None else layout.environments/'current/release'
        self.options=validate(value)
        if Path(self.options['platform_config'])!=layout.config/'core/platform.json':
            raise ValueError('The unique platform configuration must be in this deployment core leaf')
        self.input={**self.options,**platform(self.options['platform_config'])}
        self.path=path_without_links(layout.private/'initial.json')
        self.owner={'format':FORMAT,'deploymentId':identity['deploymentId'],'inputDigest':digest(self.options)}
        if self.path.exists():
            self.record=read_json(self.path)
            if (any(self.record.get(k)!=v for k,v in self.owner.items()) or self.record.get('input')!=self.options
                    or not isinstance(self.record.get('files'),dict)):
                raise ValueError('Initial deployment input changed; edit the existing role configuration explicitly')

        else:
            if (layout.config/'agents/official.env').exists() or (layout.config/'core/core.env').exists() or (layout.config/'agents/official.json').exists():
                raise RuntimeError('Restore the missing original initial configuration before continuing')
            self.record={**self.owner,'input':self.options,'files':{}}
            self.persist()

    def persist(self):save(self.path,self.record)

    def file(self,path,value,*,environment=False):
        path=path_without_links(path)
        if not (path.is_relative_to(self.layout.config) or path.is_relative_to(self.layout.private)):
            raise ValueError('Generated configuration must stay inside this deployment')
        key=str(path);entry=self.record['files'].get(key)
        if entry and entry.get('phase')=='ready':
            if not path.is_file():raise RuntimeError('Restore the missing original configuration; it will not be regenerated')
            return path  # Completed operator edits are never replayed from initial JSON.
        content=(environment_text(value) if environment else json.dumps(value,ensure_ascii=False,sort_keys=True)+'\n').encode('utf-8')
        expected=digest({'content':content.hex()})
        if entry is None:
            entry={'phase':'writing','inputDigest':expected};self.record['files'][key]=entry;self.persist()
        if entry!={'phase':'writing','inputDigest':expected}:
            raise RuntimeError('Continue the original configuration write; do not replace its input')
        if path.is_relative_to(self.layout.private):path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        write_initial(path,content)
        entry['phase']='ready';self.persist()
        return path

    def base(self):
        layout,value=self.layout,self.input
        # Runtime reads this exact authority, never a generated per-field environment.
        self.record['files'].setdefault(str(layout.config/'core/platform.json'),{'phase':'ready'})
        self.persist()
        common={'HOME':str(layout.state/'agents'),'A2C_CACHE_DIR':str(layout.state/'cache/agents'),
                'PYTHONUTF8':'1','PYTHONNOUSERSITE':'1'}
        self.file(layout.config/'agents/official.env',common,environment=True)
        if value['admin_assistant']['enabled']:
            ports=value['swarm_ports'];workspace=layout.state/'agents/swarm-admin'
            self.file(layout.config/'agents/swarm-admin.env',{**common,
                'JIUWENSWARM_DATA_DIR':str(workspace),'JIUWENSWARM_CONFIG_DIR':str(workspace/'config'),
                'JIUWENSWARM_CLI_PORTS':'1',
                'JIUWENSWARM_A2UI_ENABLED':'true',
                'AGENT_SERVER_HOST':'127.0.0.1','AGENT_SERVER_PORT':str(ports['agent_server']),
                'GATEWAY_HOST':'127.0.0.1','GATEWAY_PORT':str(ports['gateway']),
                'WEB_HOST':'127.0.0.1','WEB_PORT':str(ports['web']),
                'FRONTEND_HOST':'127.0.0.1','FRONTEND_PORT':str(ports['frontend']),
                'GATEWAY_URL':'http://127.0.0.1:'+str(ports['web'])},environment=True)
            self.file(layout.config/'agents/assistant.env',{**common,**self.notification()[1]},environment=True)
        return self.core()

    def core(self):
        value=read_json(self.options['platform_config'])
        if value['runtime']['public_url']!=self.input['public_url']:
            raise ValueError('The established public platform identity cannot be changed by reinstalling')
        return value

    def host(self,workspace):
        if workspace.get('status')!='completed' or not workspace.get('model'):
            raise RuntimeError('A real original Swarm workspace must be prepared first')
        return {key:workspace[key] for key in ('command','data_dir','model')}

    def official(self,workspace):
        return self.file(self.layout.config/'agents/official.json',{
            'platform_url':self.input['public_url'],'data_dir':str(self.layout.state/'agents/official'),
            'host':self.host(workspace),'max_model_runs':self.input['max_model_runs']})

    def administrator(self,workspace):
        if not self.input['admin_assistant']['enabled']:return None
        options=self.input['admin_assistant'];notification,_=self.notification()
        return self.file(self.layout.private/'admin-assistant/config.json',{
            'deployment_id':self.identity['deploymentId'],'platform_url':self.input['public_url'],
            'password':self.core()['runtime']['assistant_password'],'host':self.host(workspace),
            'web_url':'ws://127.0.0.1:'+str(self.input['swarm_ports']['web'])+'/ws?user_id=ioa-admin',
            'recipient':options['recipient'],'notification':notification,'max_model_runs':self.input['max_model_runs']})

    def notification(self):
        notice=self.input['admin_assistant']['notification']
        notification=None
        variables={}
        if notice is not None:
            notification={key:value for key,value in notice.items() if key not in ('api_key','username','password')}
            for key in ('api_key','username','password'):
                if key in notice:
                    name='IOALAB_NOTIFICATION_'+key.upper();variables[name]=notice[key]
                    notification[key+'_env']=name
        return notification,variables

    def public(self):
        # Only this projection is suitable for CLI/status; record/input contain secrets.
        return {'deploymentId':self.identity['deploymentId'],'publicUrl':self.input['public_url'],
            'administratorEnabled':self.input['admin_assistant']['enabled'],
            'meetingConfigured':self.input['admin_assistant']['notification'] is not None,
            'configurationFiles':len(self.record['files'])}
