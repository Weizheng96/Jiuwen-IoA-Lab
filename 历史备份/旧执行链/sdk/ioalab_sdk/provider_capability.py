"""A participant-owned, single-job SDK capability. Credentials never enter model arguments."""
import httpx

class ProviderJob:
    OPERATIONS=frozenset(('inspect','quote','selftest','deliver'))
    def __init__(self,url,token):self.url,self.token=url,token

    def call(self,operation,body=None):
        if operation not in self.OPERATIONS:raise ValueError('Unknown job capability')
        with httpx.Client(timeout=120,trust_env=False) as client:
            headers={'Authorization':'Bearer '+self.token}
            response=client.get(self.url,headers=headers) if operation=='inspect' else client.post(self.url+'/'+operation,json=body or {},headers=headers)
            response.raise_for_status();return response.json()
