"""Local key ownership for a configured role; no business or host implementation."""
import base64
from dataclasses import dataclass,field
from pathlib import Path
import uuid

from a2c.jws import Signer
from a2c.payment.ap2 import EcKey
from cryptography.hazmat.primitives.asymmetric import ed25519,ec
from cryptography.hazmat.primitives.serialization import Encoding,PrivateFormat,NoEncryption,load_pem_private_key

from .files import private_json,read_json
from .identity import IdentityClient
from .protocol import RuntimeFault


@dataclass(frozen=True)
class LocalIdentity:
    profile: str
    agent_id: str
    protocol_id: str
    role: str
    name: str
    signer: Signer=field(repr=False)
    payment: EcKey=field(repr=False)
    user: Signer|None=field(default=None,repr=False)

    def public(self):
        return {'profile':self.profile,'agentId':self.agent_id,'protocolId':self.protocol_id,'role':self.role,'name':self.name,'bound':True}


def load_identity(session,home,profile):
    path=Path(home)/'identities'/profile['key']/'keys.json'
    index_path=Path(home)/'identity-index.json'
    index=read_json(index_path) if index_path.exists() else {}
    expected={'owner':session.account['id'],'platform':session.base,'role':profile['role']}
    if path.exists():
        saved=read_json(path)
        if any(saved.get(key)!=value for key,value in expected.items()):
            raise RuntimeFault('identity_conflict','Identity profile belongs to another owner, platform or role','migrate')
    else:
        if profile['key'] in index:
            raise RuntimeFault('identity_conflict','Previously registered identity keys are missing','restore_identity')
        def raw():
            return base64.b64encode(ed25519.Ed25519PrivateKey.generate().private_bytes(Encoding.Raw,PrivateFormat.Raw,NoEncryption())).decode()
        aid=('urn:ioalab:assistant:' if profile['role']=='assistant' else 'svc-')+str(uuid.uuid4())
        saved={**expected,'agentId':aid,'agent':raw(),
               'payment':ec.generate_private_key(ec.SECP256R1()).private_bytes(Encoding.PEM,PrivateFormat.PKCS8,NoEncryption()).decode()}
        if profile['role']=='assistant':saved['user']=raw()
        private_json(path,saved)
    aid=saved['agentId']
    if profile['key'] in index and index[profile['key']]!={'agentId':aid,'role':profile['role']}:
        raise RuntimeFault('identity_conflict','Identity profile mapping changed','migrate')
    index[profile['key']]={'agentId':aid,'role':profile['role']}
    private_json(index_path,index)
    protocol=IdentityClient(session).protocol_id(profile['role'],aid)
    if saved.get('protocolId',protocol)!=protocol:
        raise RuntimeFault('identity_conflict','The original protocol address needs explicit migration','migrate')
    signer=Signer(protocol,ed25519.Ed25519PrivateKey.from_private_bytes(base64.b64decode(saved['agent'])),
                  'agent1' if profile['role']=='service' else None)
    payer=EcKey('payee' if profile['role']=='service' else 'payer',load_pem_private_key(saved['payment'].encode(),None))
    user=None
    keys={'agent':signer.jwk}
    if profile['role']=='assistant':
        user=Signer(aid+':user',ed25519.Ed25519PrivateKey.from_private_bytes(base64.b64decode(saved['user'])))
        keys.update(user=user.jwk,payer=payer.jwk)
    else:keys['payee']=payer.jwk
    IdentityClient(session).enroll(profile['role'],aid,signer,keys,name=profile['name'],
                                   source='client' if profile['role']=='assistant' else 'provider')
    if 'protocolId' not in saved:private_json(path,{**saved,'protocolId':protocol})
    return LocalIdentity(profile['key'],aid,protocol,profile['role'],profile['name'],signer,payer,user)
