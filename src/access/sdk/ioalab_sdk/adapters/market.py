"""Read and verify platform-issued reputation; never submit self-reported scores."""
import math
from urllib.parse import quote

import httpx
from a2c.common import Directory
from a2c.errors import A2CError
from a2c.jws import verify,header_of,check_window
from a2c.objects import object_digest
from a2c.codec import utcnow,parse_time
from ..runtime.protocol import RuntimeFault


def read_reputation(account,public,provider,*,keys=None):
    value=account.api('GET','/providers/reputation?provider='+quote(provider,safe=''))
    fields={'kind','provider','completed','successRate','issuedAt','signature'}
    if (not isinstance(value,dict) or set(value)!=fields or value['kind']!='reputation'
            or value['provider']!=provider or type(value['completed']) is not int or value['completed']<0):
        raise RuntimeFault('protocol_error','Reputation does not describe this service')
    rate=value['successRate']
    if ((rate is not None and (type(rate) not in (int,float) or not math.isfinite(rate) or not 0<=rate<=1))
            or (value['completed']==0)!=(rate is None)):
        raise RuntimeFault('protocol_error','Invalid reputation statistics')
    market=account.config['market'];header=header_of(value['signature'])
    directory=keys or Directory(fetch=public.jwks)
    verify(value['signature'],object_digest(value),directory.jwk(market,header.get('kid')),
           slot='market',signer=market,expect_kid=True)
    check_window(header,utcnow());parse_time(value['issuedAt'])
    return value


def reputation_evidence(account,public,provider,*,keys=None):
    try:return {'status':'verified','record':read_reputation(account,public,provider,keys=keys)}
    except (A2CError,RuntimeFault,ValueError,TypeError,KeyError):
        return {'status':'invalid','error':{'code':'invalid_evidence'}}
    except httpx.HTTPError:
        return {'status':'unavailable','error':{'code':'read_unavailable'}}
