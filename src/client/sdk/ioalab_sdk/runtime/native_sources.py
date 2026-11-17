"""P10 source ingress. Only a bound private native transport can issue entry refs."""
import hashlib
import hmac
from pathlib import Path
import secrets
import time
import copy
import threading

from .files import private_json
from .host_submission import HostRegistration
from .native_entry_bindings import NativeEntryBindings,EntryBindingSource,EntryBindingAuthority
from .protocol import RuntimeFault,encode


def native_source_descriptor(home,host_ref,profile):
    """P07 non-secret location metadata; never reads a capability or account."""
    if not all(isinstance(value,str) and 1<=len(value)<=128 for value in (host_ref,profile)):
        raise RuntimeFault('invalid_input','Use installed host/profile bindings')
    name=hashlib.sha256(encode([host_ref,profile]).encode()).hexdigest()+'.json'
    return Path(home)/'roles'/'.native-sources'/name


class NativeSources:
    def __init__(self,store,registry,identities,installation,account_ref,home,*,clock=time.time,accept_setup=lambda:True):
        self.store,self.registry,self.clock=store,registry,clock
        self.identities={item.profile:item.agent_id for item in identities}
        self.installation_ref=hashlib.sha256(encode(installation).encode()).hexdigest()
        self.account_ref=account_ref
        self.home=Path(home)
        self.boot_id=None
        self.endpoint=None
        self._grants={}
        self.entry_bindings=None
        self.accept_setup=accept_setup
        self._control_slots=threading.BoundedSemaphore(32)

    def attach(self,descriptor):
        self.boot_id=descriptor['bootId'];self.endpoint=descriptor['url']+'/v1/native-source'
        self.control_endpoint=descriptor['url']+'/v1/native-control'
        self.entry_bindings=NativeEntryBindings(self.store,boot_id=self.boot_id,
            installation_ref=self.installation_ref,account_ref=self.account_ref,clock=lambda:self.clock())

    def provision(self,host_ref,profile):
        """Internal assembly/resource binding only; never a P14 operation or export."""
        host=self.registry.hosts.get(host_ref) if self.registry else None
        registration=getattr(host,'registration',None)
        if not self.boot_id or not isinstance(registration,HostRegistration) or profile not in self.identities:
            raise RuntimeFault('not_ready','Bind an installed native entry to the current account instance')
        key=(host_ref,profile)
        if key not in self._grants:
            token=secrets.token_urlsafe(48)
            scope={'hostRef':host_ref,'adapterId':registration.adapter_id,'profile':profile,
                   'actor':self.identities[profile],'installationRef':self.installation_ref,
                   'accountBindingRef':self.account_ref,'bootId':self.boot_id}
            self._grants[key]=(token,scope)
        token,scope=self._grants[key]
        # Retry a failed descriptor write with the SAME in-memory capability/scope.
        # Runtime capability stays private, never Skill/export content.
        path=native_source_descriptor(self.home,host_ref,profile)
        directory=path.parent
        if directory.exists() and (directory.is_symlink() or not directory.resolve().is_relative_to(self.home.resolve())):
            raise RuntimeFault('forbidden','Native entry descriptor is outside the owned client directory')
        directory.mkdir(parents=True,exist_ok=True)
        private_json(path,{'url':self.endpoint,'controlUrl':self.control_endpoint,'bootId':self.boot_id,'grant':token,**scope})
        def issue(body,authorization):
            if self.authenticate(authorization)!=scope:
                raise RuntimeFault('identity_conflict','Native issuer belongs to another entry binding')
            return self.register(authorization,body)
        return issue

    def authenticate(self,authorization):
        if not isinstance(authorization,str) or not authorization.startswith('Bearer '):raise RuntimeFault('authenticate','Native source transport is not authenticated')
        token=authorization.removeprefix('Bearer ')
        for expected,scope in self._grants.values():
            if hmac.compare_digest(token.encode(),expected.encode()):
                self._current(scope)
                return copy.deepcopy(scope)
        raise RuntimeFault('authenticate','Use the original private native entry binding')

    def _current(self,source):
        host=self.registry.hosts.get(source['hostRef']) if self.registry else None
        registration=getattr(host,'registration',None)
        if (source['bootId']!=self.boot_id or source['installationRef']!=self.installation_ref or
            source['accountBindingRef']!=self.account_ref or not isinstance(registration,HostRegistration) or
            source['adapterId']!=registration.adapter_id or self.identities.get(source['profile'])!=source['actor']):
            raise RuntimeFault('identity_conflict','The source belongs to another native installation or account binding')

    def exchange(self,authorization,body,handlers):
        """Same passive IPC transport; SDK setup purpose never dispatches a model."""
        scope=self.authenticate(authorization)
        if not isinstance(body,dict) or len(encode(body).encode())>128*1024:
            raise RuntimeFault('invalid_input','Use a bounded native control packet')
        if not self._control_slots.acquire(blocking=False):raise RuntimeFault('busy','Native control calls are still executing')
        try:
            if body.get('purpose')=='sdk.entry':result=self._entry_control(scope,body)
            else:
                handler=handlers.get(scope['hostRef'])
                if not callable(handler):raise RuntimeFault('not_ready','The bound native control resource is unavailable')
                result=handler(copy.deepcopy(scope),copy.deepcopy(body))
            if len(encode(result).encode())>128*1024:raise RuntimeFault('invalid_input','Native control response exceeds its bound')
            return result
        finally:self._control_slots.release()

    def entry_control_for(self,host_ref):
        """SDK-only resource capability; fixed handler supplies authenticated scope."""
        def control(scope,payload):
            if not isinstance(scope,dict) or scope.get('hostRef')!=host_ref:
                raise RuntimeFault('identity_conflict','Native setup resource belongs to another host')
            original=self._grants.get((host_ref,scope.get('profile')))
            if original is None or original[1]!=scope:
                raise RuntimeFault('identity_conflict','Restore the originally installed native control binding')
            self._current(scope)
            if not isinstance(payload,dict) or payload.get('purpose')!='sdk.entry' or len(encode(payload).encode())>128*1024:
                raise RuntimeFault('invalid_input','Use the fixed SDK entry permission envelope')
            return self._entry_control(copy.deepcopy(scope),copy.deepcopy(payload))
        return control

    def _entry_control(self,scope,body):
        if self.entry_bindings is None:raise RuntimeFault('not_ready','The original native setup authority is unavailable')
        action=body.get('action')
        fields={'pending':{'purpose','action','entryRef'},
                'claim':{'purpose','action','entryRef','intentRef','targetDigest'},
                'complete':{'purpose','action','claimRef','targetDigest','savedReceiptRef'}}
        if not isinstance(action,str) or action not in fields or set(body)!=fields[action]:
            raise RuntimeFault('invalid_input','Use the fixed native setup control envelope')
        if action=='complete':
            authority=EntryBindingAuthority(scope['hostRef'],scope['adapterId'],scope['actor'],scope['profile'],
                scope['installationRef'],scope['accountBindingRef'],scope['bootId'])
            return self.entry_bindings.complete(body['claimRef'],body['targetDigest'],body['savedReceiptRef'],scope=authority)
        if not self.accept_setup():raise RuntimeFault('closed','Native setup no longer accepts a new save obligation')
        source=self.setup_source({'entryRef':body['entryRef']},authority=scope)
        if action=='pending':return self.entry_bindings.pending(source)
        return self.entry_bindings.claim(body['intentRef'],body['targetDigest'],scope=source)

    def setup_source(self,reference,*,authority=None):
        """SDK-only capture snapshot; a normal source does not itself authorize setup."""
        record=self._entry_record(reference);source=record['source']
        if authority is not None and any(source[key]!=authority[key] for key in
                ('hostRef','adapterId','actor','profile','installationRef','accountBindingRef','bootId')):
            raise RuntimeFault('identity_conflict','The setup capture belongs to another native entry')
        fingerprint=hashlib.sha256(encode({'source':source}).encode()).hexdigest()
        return EntryBindingSource(source['hostRef'],source['adapterId'],source['actor'],source['profile'],
            source['installationRef'],source['accountBindingRef'],source['bootId'],fingerprint,record['expiresAt'])

    def register(self,authorization,body):
        scope=self.authenticate(authorization)
        if not isinstance(body,dict) or set(body)!={'sessionId','workspace','captureId'}:
            raise RuntimeFault('invalid_input','Native ingress accepts only captured session, workspace and capture ID')
        if any(not isinstance(value,str) or not 1<=len(value)<=8192 for value in body.values()) or not Path(body['workspace']).is_absolute():
            raise RuntimeFault('invalid_input','A real native session and absolute captured workspace are required')
        key=encode([self.boot_id,scope['hostRef'],scope['profile'],body['captureId']])
        # Source capture dedup is separate from P18 business effect identity.
        with self.store.transaction() as conn:
            old=conn.execute('SELECT value FROM runtime_checkpoints WHERE namespace=? AND key=?',('native-source-captures',key)).fetchone()
            if old:
                import json
                saved=json.loads(old['value'])
                if saved['source']!={**scope,**body}:raise RuntimeFault('identity_conflict','The captured native source changed')
                if saved['expiresAt']<self.clock():raise RuntimeFault('not_ready','Capture a new native entry after its short source reference expires','connect_native_context')
                return {'entryRef':saved['entryRef']}
            entry=secrets.token_urlsafe(32)
            record={'entryRef':entry,'source':{**scope,**body},'expiresAt':self.clock()+300}
            conn.execute('INSERT INTO runtime_checkpoints VALUES(?,?,?)',('native-source-captures',key,encode(record)))
            conn.execute('INSERT INTO runtime_checkpoints VALUES(?,?,?)',('native-source-entries',entry,encode(record)))
        return {'entryRef':entry}

    def _entry_record(self,reference):
        if not isinstance(reference,dict) or set(reference)!={'entryRef'} or not isinstance(reference['entryRef'],str):
            raise RuntimeFault('invalid_input','Use a source entry reference from the bound native wrapper')
        record=self.store.checkpoint('native-source-entries',reference['entryRef'])
        if not record or record['expiresAt']<self.clock():raise RuntimeFault('not_ready','Capture the current source through the native wrapper','connect_native_context')
        self._current(record['source'])
        return record

    def lookup(self,reference):
        source=self._entry_record(reference)['source']
        from .host_registry import HostContext
        return HostContext(source['hostRef'],source['sessionId'],source['workspace'],source['actor'],source['profile'])
