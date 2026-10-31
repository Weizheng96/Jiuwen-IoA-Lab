"""Common configuration only; role/host namespaces are validated by their owners."""
from dataclasses import dataclass,field
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

from .files import read_json
from .protocol import RuntimeFault
from a2c.codec import digest


@dataclass(frozen=True)
class RuntimeConfig:
    platform: str
    username: str
    password: str=field(repr=False)
    home: Path
    identities: tuple
    max_model_runs: int=2
    max_pending_requests: int=128
    assistant: dict=field(default_factory=dict)
    provider: dict=field(default_factory=dict)
    host: dict=field(default_factory=dict)

    def public_summary(self):
        value={'platform':self.platform,'username':self.username,'dataDir':str(self.home),'identities':list(self.identities),
               'maxModelRuns':self.max_model_runs,'maxPendingRequests':self.max_pending_requests,
               'assistant':self.assistant,'provider':self.provider,'host':self.host}
        return {'configurationDigest':digest(value),'dataDir':str(self.home),'identities':list(self.identities)}

    @classmethod
    def load(cls,path):
        path=Path(path).resolve()
        value=read_json(path)
        allowed={'platform_url','username','password','password_env','data_dir','identities',
                 'max_model_runs','max_pending_requests','assistant','provider','host'}
        def invalid(message):raise RuntimeFault('invalid_input',message,'configure')
        if not isinstance(value,dict) or set(value)-allowed:invalid('Unknown common runtime configuration fields')
        for key in ('platform_url','username','data_dir'):
            if not isinstance(value.get(key),str) or not value[key].strip():invalid('Missing configuration field: '+key)
        platform=value['platform_url'].rstrip('/')
        url=urlsplit(platform)
        if url.scheme not in ('http','https') or not url.hostname or url.username or url.password or url.query or url.fragment:
            invalid('Configure a platform HTTP(S) address without embedded credentials')
        if 'password' in value and 'password_env' in value:invalid('Choose password or password_env, not both')
        if 'password_env' in value:
            key=value['password_env']
            if not isinstance(key,str) or not re.fullmatch('[A-Za-z_][A-Za-z0-9_]*',key):invalid('Invalid password environment reference')
            password=os.environ.get(key)
        else:password=value.get('password')
        if not isinstance(password,str) or not password:invalid('Configure local account credentials')
        home=Path(value['data_dir']).expanduser()
        if not home.is_absolute():home=path.parent/home
        profiles=value.get('identities')
        if not isinstance(profiles,list) or not 1<=len(profiles)<=64:invalid('Configure one to 64 local identity profiles')
        keys=set();assistants=0
        for profile in profiles:
            if not isinstance(profile,dict) or set(profile)!={'key','role','name'}:invalid('Identity profiles require key, role and name')
            key=profile['key']
            if not isinstance(key,str) or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,63}',key) or key in keys:
                invalid('Identity profile keys must be distinct local names')
            keys.add(key)
            if profile['role'] not in ('assistant','service'):invalid('Unsupported identity role')
            if not isinstance(profile['name'],str) or not 1<=len(profile['name'])<=120:invalid('A bounded identity display name is required')
            assistants+=profile['role']=='assistant'
        if assistants>1:invalid('An account has one logical personal assistant')
        for key,default,limit in [('max_model_runs',2,64),('max_pending_requests',128,10000)]:
            number=value.get(key,default)
            if type(number) is not int or not 1<=number<=limit:invalid('Invalid '+key)
        for key in ('assistant','provider','host'):
            if not isinstance(value.get(key,{}),dict):invalid(key+' configuration must be an object')
        return cls(platform,value['username'].strip(),password,home.resolve(),tuple(dict(p) for p in profiles),
                   value.get('max_model_runs',2),value.get('max_pending_requests',128),
                   value.get('assistant',{}),value.get('provider',{}),value.get('host',{}))
