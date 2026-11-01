"""Root-owned recipe and original preparation receipts, separate from participant data."""
import ipaddress
import os
from pathlib import Path
import stat
from urllib.parse import parse_qs, urlsplit
import uuid

from a2c.codec import digest
from ioalab_sdk.lease import Lease
from ioalab_sdk.runtime.config import RuntimeConfig
from ioalab_sdk.runtime.files import private_json, read_json
from ioalab_sdk.runtime.protocol import RuntimeFault
from .presets import load


def operator():
    if os.name != 'posix' or os.geteuid() != 0:
        raise RuntimeFault('forbidden', 'Managed administrator preparation requires the Linux deployment root process')


def safe_path(value):
    path = Path(value)
    if not path.is_absolute() or path.absolute() != path.resolve():
        raise RuntimeFault('invalid_input', 'Use absolute deployment paths without symbolic links')
    return path


def owned(path, *, private=False):
    path = safe_path(path)
    value = path.stat()
    if value.st_uid != 0 or stat.S_IMODE(value.st_mode) & (0o077 if private else 0o022):
        raise RuntimeFault('forbidden', 'Deployment preparation inputs must remain owned and protected by root')


class Deployment:
    def __init__(self, path):
        self.path = safe_path(path)
        owned(self.path, private=True)
        if self.path.name != 'config.json' or self.path.parent.name != 'admin-assistant' or self.path.parent.parent.name != '.deployment':
            raise RuntimeFault('invalid_input', 'Use the installer-owned .deployment/admin-assistant/config.json recipe')
        self.private = self.path.parent
        self.state_root = self.private.parent.parent
        owned(self.private, private=True); owned(self.private.parent, private=True)
        marker = self.state_root / '.ioalab-deployment.json'
        owned(marker)
        identity = read_json(marker)
        if not isinstance(identity, dict) or set(identity) != {'format', 'root', 'state', 'config', 'users', 'deploymentId'} or identity['format'] != 'ioalab.deployment-layout/1':
            raise RuntimeFault('identity_conflict', 'Restore the original deployment ownership record')
        try:
            if uuid.UUID(identity['deploymentId']).hex != identity['deploymentId']: raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise RuntimeFault('identity_conflict', 'Invalid deployment ownership identifier') from None
        roots = [safe_path(identity[key]) for key in ('root', 'state', 'config')]
        if roots[1] != self.state_root or any(a.is_relative_to(b) or b.is_relative_to(a) for i,a in enumerate(roots) for b in roots[i+1:]):
            raise RuntimeFault('identity_conflict', 'Preparation paths differ from the deployment ownership record')
        for root in roots:
            owned(root); owned(root / '.ioalab-deployment.json')
            if read_json(root / '.ioalab-deployment.json') != identity:
                raise RuntimeFault('identity_conflict', 'Deployment roots belong to different instances')
        self.value = read_json(self.path)
        required = {'deployment_id', 'platform_url', 'password', 'host', 'web_url', 'recipient'}
        if not isinstance(self.value, dict) or not required <= set(self.value) or set(self.value)-required-{'notification', 'max_model_runs'}:
            raise RuntimeFault('invalid_input', 'Use the fixed administrator preparation recipe')
        if self.value['deployment_id'] != identity['deploymentId']:
            raise RuntimeFault('identity_conflict', 'Administrator recipe belongs to another deployment')
        self.home = safe_path(self.state_root / 'agents/admin-assistant')
        self.workspace = safe_path(self.state_root / 'agents/swarm-admin')
        self.runtime_path = self.home / 'config.json'
        self.schedule = self.home / 'schedule.json'
        self.record_path = self.private / 'state.json'
        host = self.value['host']
        if (not isinstance(host,dict) or set(host)-{'command','data_dir','model','skill_directories'} or
                not {'command','data_dir','model'} <= set(host) or safe_path(host['data_dir']) != self.workspace or
                not isinstance(host['model'],str) or not host['model']):
            raise RuntimeFault('invalid_input', 'Select the owned administrator Swarm workspace and a real model key')
        from ioalab_jiuwen.process import ProcessHost
        ProcessHost.from_config(host, base_dir=self.private)
        url = urlsplit(self.value['web_url'])
        try: loopback = ipaddress.ip_address(url.hostname).is_loopback
        except (ValueError, TypeError): loopback = False
        if (url.scheme != 'ws' or not loopback or url.username or url.password or url.fragment or url.path != '/ws' or
                parse_qs(url.query) != {'user_id':['ioa-admin']}):
            raise RuntimeFault('invalid_input', 'Use the loopback native web target with fixed user_id=ioa-admin')
        from ioalab_tools.calendar import address
        address(self.value['recipient'])
        self.resource_digest, self.presets = load(meetings=self.value.get('notification') is not None)
        self.binding = digest({'recipe': self.value, 'ownership': identity, 'presetDigest': self.resource_digest})
        RuntimeConfig.from_mapping(self.runtime_path, self.base())

    def base(self):
        return {'platform_url': self.value['platform_url'], 'username':'ISC_001', 'password':self.value['password'],
                'data_dir':str(self.home/'resident'), 'identities':[{'key':'admin','role':'assistant','name':'管理员的个人助手'}],
                'max_model_runs':self.value.get('max_model_runs',2)}

    def complete(self):
        web = {'factory':'jiuwen-web','profile':'admin','options':{
            'url':self.value['web_url'],'user_id':'ioa-admin','secret_env':'IOALAB_ADMIN_WEB_SECRET',
            'credentials_file':str(self.home/'web-notification.env')}}
        tools = [web]
        if self.value.get('notification') is not None:
            tools.append({'factory':'calendar','profile':'admin','options':{
                'calendar':{'backend':'local','schedule_file':str(self.schedule)},
                'notification':self.value['notification'],'recipient':self.value['recipient']}})
        return {**self.base(),'host':{**self.value['host'],'business_tools':tools}}

    def state(self):
        if not self.record_path.exists():
            if self.home.exists():
                raise RuntimeFault('identity_conflict','An unregistered administrator instance will not be adopted')
            return {'schema':1,'binding':self.binding,'phase':'preparing','presets':{}}
        owned(self.record_path, private=True)
        value = read_json(self.record_path)
        if value.get('schema') != 1 or value.get('binding') != self.binding or value.get('phase') not in ('preparing','finishing','prepared'):
            raise RuntimeFault('configuration_changed','Restore the original administrator preparation recipe and receipts')
        return value

    def initial_owner(self):
        if self.home.exists():
            for path in (self.home, *self.home.rglob('*')):
                owned(path)

    def save(self, value): private_json(self.record_path,value)

    def lock(self): return Lease(self.private/'prepare.lock',timeout=0)

    def prepared(self, value):
        config = RuntimeConfig.load(self.runtime_path)
        if (config.username != 'ISC_001' or config.home != self.home/'resident' or config.platform != self.value['platform_url'].rstrip('/') or
                [(p['key'],p['role']) for p in config.identities] != [('admin','assistant')] or not (config.home/'identity-index.json').is_file()):
            raise RuntimeFault('identity_conflict','Restore the original administrator configuration and identity')
        index = read_json(config.home/'identity-index.json')
        if set(index) != {'admin'} or index['admin'] != value['identityIndex']:
            raise RuntimeFault('identity_conflict','Original administrator identity mapping differs')
        if not (config.home/'identities/admin/keys.json').is_file() or not (config.home/'runtime.sqlite').is_file():
            raise RuntimeFault('not_ready','Restore the original administrator keys and runtime data')
        if value.get('scheduleInitialized'):
            from ioalab_tools.schedule import LocalSchedule
            LocalSchedule(self.schedule).read()
        if set(value['presets']) != {'message.forward','meeting.request'} or any(not p.get('result') for p in value['presets'].values()):
            raise RuntimeFault('not_ready','Original preset publication receipts are incomplete')
        return config


def operation(*values):
    return str(uuid.uuid5(uuid.NAMESPACE_URL,digest({'administratorPreset':list(values)})))
