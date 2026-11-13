"""Binary file transport; credentials stay in the resident account session."""
from urllib.parse import quote
from a2c.codec import digest_bytes
from .protocol import RuntimeFault


class PrivateFiles:
    def __init__(self,account,actor):self.account,self.actor=account,actor

    def put(self,name,data,operation):
        sha=digest_bytes(data)
        response=self.account.http.post(self.account.base+'/api/v1/files/'+quote(self.actor,safe=''),
            params={'operationId':operation,'name':name,'digest':sha},content=data,
            headers={'Authorization':'Bearer '+self.account.bearer(),'Content-Type':'application/octet-stream'})
        response.raise_for_status();reference=response.json()
        if reference.get('name')!=name or reference.get('digest')!=sha or reference.get('size')!=len(data):
            raise RuntimeFault('protocol_error','Uploaded file receipt does not match the original bytes')
        return reference

    def get(self,reference):
        if not isinstance(reference,dict) or set(reference)!={'fileId','name','digest','size'} or not all(isinstance(reference[k],str) for k in ('fileId','name','digest')) or type(reference['size']) is not int or reference['size']<0:raise RuntimeFault('protocol_error','A complete original file reference is required')
        response=self.account.http.get(self.account.base+'/api/v1/files/'+quote(reference['fileId'],safe=''),
            params={'agentId':self.actor},headers={'Authorization':'Bearer '+self.account.bearer()})
        response.raise_for_status();data=response.content
        if len(data)!=reference['size'] or digest_bytes(data)!=reference['digest']:
            raise RuntimeFault('protocol_error','Downloaded file failed its original digest and size check')
        return data
