"""The platform's private configuration authority and revisioned hot policy."""
from copy import deepcopy
from dataclasses import fields
from decimal import Decimal, InvalidOperation
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import tempfile
import threading
from urllib.parse import urlsplit

from .config import Settings, DEFAULT_LLM_BASE
from .configuration_permissions import private, protect_new
from .errors import LabError

FORMAT='ioalab.platform-config/1'
EDITABLE={'authentication':{'max_failures','window_seconds','registration_limit','registration_window_seconds','local_unlimited'},
          'credits':{'initial','login','repeat_daily'}}
_LOCKS={}
_LOCK=threading.Lock()
SPECIAL={'llm','agent_model','a2x_model','embedding_model','llm_concurrency','round1_attempts','arbitration_models',
         'admin_username','admin_password','initial_credit','login_credit','repeat_daily_credit','auth_max_failures',
         'auth_window_seconds','registration_limit','registration_window_seconds','local_unlimited','local_addresses',
         'trusted_proxy_addresses','_configuration'}
RUNTIME={f.name for f in fields(Settings)}-SPECIAL-{'currency','sandbox_concurrency','sandbox_fuel'}


def invalid():
    raise LabError('invalid_input','Invalid platform configuration fields or values')


def defaults():
    settings=Settings()
    runtime={name:getattr(settings,name) for name in RUNTIME}
    runtime={name:str(value) if isinstance(value,Path) else value for name,value in runtime.items()}
    runtime['mode']='trial'
    runtime['public_url']=runtime.pop('public_url_env')
    return {'format':FORMAT,'revision':1,'runtime':runtime,
        'admin':{'username':'admin','password':'admin1234'},
        'llm':{'api_key':'','base_url':DEFAULT_LLM_BASE,'fallback_base':'','model':settings.agent_model,
               'discovery_model':settings.a2x_model,'embedding_model':settings.embedding_model,
               'concurrency':settings.llm_concurrency,'round1_attempts':settings.round1_attempts,'arbitration_models':{}},
        'authentication':{'max_failures':40,'window_seconds':60,'registration_limit':5,'registration_window_seconds':3600,
                          'local_unlimited':True,'local_addresses':['127.0.0.1','::1'],'trusted_proxy_addresses':[]},
        'credits':{'initial':'500.00','login':'100.00','repeat_daily':False,'day_timezone':'Asia/Shanghai'}}


def validate(value):
    result=deepcopy(value)
    template=defaults()
    if not isinstance(result,dict) or set(result)!=set(template):invalid()
    if result['format']!=FORMAT or type(result['revision']) is not int or result['revision']<1:invalid()
    for group in ('runtime','admin','llm','authentication','credits'):
        if not isinstance(result[group],dict) or set(result[group])!=set(template[group]):invalid()
    for key,default in template['runtime'].items():
        current=result['runtime'][key]
        if default is None:
            if current is not None and not isinstance(current,str):invalid()
        elif type(current) is not type(default):invalid()
    runtime=result['runtime']
    if runtime['mode'] not in ('dev','trial') or runtime['registration'] not in ('open','invite','closed') or runtime['traffic']!='off':invalid()
    if not 1<=runtime['port']<=65535 or runtime['isc_count']<1:invalid()
    if not 1<=runtime['message_ttl_days']<=runtime['message_retention_days']<=runtime['message_metadata_days']:invalid()
    if min(runtime['message_pending_limit'],runtime['message_rate'])<1 or runtime['quota_needs_per_day']<0:invalid()
    if not 1<=runtime['connection_ticket_seconds']<=300 or runtime['ws_heartbeat_seconds']<1 or runtime['ws_lease_seconds']<3*runtime['ws_heartbeat_seconds']:invalid()
    try:ipaddress.ip_address(runtime['bind'])
    except (ValueError,TypeError):invalid()
    for key in ('public_url','trial_url','trial_confirm_url'):
        if not runtime[key]:continue
        try:
            url=urlsplit(runtime[key]);port=url.port
            if url.scheme not in ('http','https') or not url.hostname or url.username or url.password or url.query or url.fragment:invalid()
            runtime[key]=runtime[key].rstrip('/')
        except ValueError:invalid()
    admin=result['admin']
    if any(not isinstance(v,str) or not v for v in admin.values()):invalid()
    if len(admin['username'])>128 or len(admin['password'])>4096:invalid()
    model=result['llm']
    for key,default in template['llm'].items():
        if type(model[key]) is not type(default):invalid()
    for key in ('base_url','fallback_base'):
        if not model[key]:continue
        try:
            url=urlsplit(model[key]);port=url.port
            if url.scheme not in ('http','https') or not url.hostname or url.username or url.password or url.query or url.fragment:invalid()
        except ValueError:invalid()
    if not model['model'] or not model['discovery_model'] or not 1<=model['concurrency']<=64 or not 1<=model['round1_attempts']<=100:invalid()
    auth=result['authentication']
    for name,maximum in (('max_failures',10000),('window_seconds',86400),('registration_limit',10000),('registration_window_seconds',86400)):
        if type(auth[name]) is not int or not 1<=auth[name]<=maximum:invalid()
    if type(auth['local_unlimited']) is not bool:invalid()
    for name in ('local_addresses','trusted_proxy_addresses'):
        if not isinstance(auth[name],list) or len(auth[name])>32:invalid()
        try:auth[name]=[str(ipaddress.ip_address(x)) for x in auth[name]]
        except (ValueError,TypeError):invalid()
        if len(auth[name])!=len(set(auth[name])):invalid()
    if set(auth['local_addresses'])&set(auth['trusted_proxy_addresses']):invalid()
    for name in ('initial','login'):
        if not isinstance(result['credits'][name],str):invalid()
        try:amount=Decimal(result['credits'][name])
        except InvalidOperation:invalid()
        if not amount.is_finite() or amount<0 or amount.as_tuple().exponent < -2:invalid()
        try:result['credits'][name]=format(amount.quantize(Decimal('.01')),'f')
        except InvalidOperation:invalid()
    if type(result['credits']['repeat_daily']) is not bool or result['credits']['day_timezone']!='Asia/Shanghai':invalid()
    return result


def _digest(document):
    return hashlib.sha256(json.dumps(document,sort_keys=True,ensure_ascii=False,allow_nan=False).encode()).digest()


class PlatformConfiguration:
    def __init__(self,path):
        self.path=Path(path).absolute()
        with _LOCK:self.lock=_LOCKS.setdefault(self.path,threading.RLock())
        self._document=self._read()
        self._fingerprint=_digest(self._document)
        self.audit=None

    def _read(self):
        private(self.path.parent);private(self.path)
        if self.path.stat().st_size>1024*1024:invalid()
        try:return validate(json.loads(self.path.read_text('utf-8-sig')))
        except (UnicodeError,json.JSONDecodeError):invalid()

    @classmethod
    def create(cls,path,document):
        path=Path(path).absolute();private(path.parent)
        document=validate(document)
        # Exclusive creation never repairs/adopts an existing input.
        with path.open('x',encoding='utf-8') as stream:
            protect_new(path)
            json.dump(document,stream,ensure_ascii=False,sort_keys=True,allow_nan=False)
            stream.flush();os.fsync(stream.fileno())
        return cls(path)

    def policy(self):
        document=self._document
        return {key:deepcopy(document[key]) for key in ('authentication','credits')}

    def view(self):
        document=self._document
        return {'revision':document['revision'],'editable':{
            group:{key:deepcopy(document[group][key]) for key in keys} for group,keys in EDITABLE.items()},
            'restart':{'adminUsername':document['admin']['username'],'adminPasswordConfigured':bool(document['admin']['password']),
                       'llmKeyConfigured':bool(document['llm']['api_key']),
                       'publicUrl':document['runtime']['public_url']}}

    def update(self,expected_revision,changes,*,actor=''):
        if type(expected_revision) is not int or not isinstance(changes,dict) or not changes:invalid()
        for group,values in changes.items():
            if group not in EDITABLE or not isinstance(values,dict) or not values or set(values)-EDITABLE[group]:invalid()
        with self.lock:
            disk=self._read()
            if disk['revision']!=expected_revision or _digest(disk)!=self._fingerprint:
                raise LabError('id_conflict','Platform configuration revision changed',status=409)
            candidate=deepcopy(disk)
            for group,values in changes.items():candidate[group].update(values)
            candidate['revision']+=1;candidate=validate(candidate)
            fd,name=tempfile.mkstemp(prefix='.platform-',suffix='.json',dir=self.path.parent)
            try:
                with os.fdopen(fd,'w',encoding='utf-8') as stream:
                    protect_new(Path(name));json.dump(candidate,stream,ensure_ascii=False,sort_keys=True,allow_nan=False)
                    stream.flush();os.fsync(stream.fileno())
                private(self.path);os.replace(name,self.path)
            except OSError:
                raise LabError('configuration_write_failed','Platform configuration could not be saved',status=503) from None
            finally:
                if os.path.exists(name):os.unlink(name)
            self._document=candidate;self._fingerprint=_digest(candidate)
            if self.audit:
                self.audit(actor,expected_revision,candidate['revision'],sorted(group+'.'+key for group,values in changes.items() for key in values))
            return self.view()

    def settings(self):
        document=self._document
        runtime=deepcopy(document['runtime'])
        runtime['public_url_env']=runtime.pop('public_url')
        for name in ('data','bundles_dir','seed_file'):
            if runtime[name] is not None:
                path=Path(runtime[name]).expanduser()
                runtime[name]=(path if path.is_absolute() else self.path.parent/path).resolve()
        model=document['llm'];auth=document['authentication'];credits=document['credits']
        runtime.update(admin_username=document['admin']['username'],admin_password=document['admin']['password'],
            llm=({'api_key':model['api_key'],'api_base':model['base_url'].rstrip('/'),'fallback_base':model['fallback_base'].rstrip('/')} if model['api_key'] else {}),
            agent_model=model['model'],a2x_model=model['discovery_model'],embedding_model=model['embedding_model'],
            llm_concurrency=model['concurrency'],round1_attempts=model['round1_attempts'],arbitration_models=deepcopy(model['arbitration_models']),
            login_credit=credits['login'],initial_credit=credits['initial'],repeat_daily_credit=credits['repeat_daily'],
            auth_max_failures=auth['max_failures'],auth_window_seconds=auth['window_seconds'],
            registration_limit=auth['registration_limit'],registration_window_seconds=auth['registration_window_seconds'],
            local_unlimited=auth['local_unlimited'],local_addresses=tuple(auth['local_addresses']),
            trusted_proxy_addresses=tuple(auth['trusted_proxy_addresses']),_configuration=self)
        return Settings(**runtime)
