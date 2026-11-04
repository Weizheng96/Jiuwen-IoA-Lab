"""Search reuses public directory facts without changing index or admission."""
from copy import deepcopy
from datetime import datetime
import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from ioalab.observation.services import ServiceViews
from ioalab.operations import DiscoveryOperations
from ioalab.web.api_v1 import build_router


class DirectoryDB:
    def __init__(self, rows, active=0):
        self.values=rows
        self.active=active
        self.reads=[]

    def rows(self, sql, *args):
        self.reads.append((sql,args))
        return deepcopy(self.values)

    def one(self, sql, *args):
        self.reads.append((sql,args))
        return {'n':self.active} if 'COUNT(*)' in sql else None

    def run(self, *args, **kwargs):
        raise AssertionError('Reading search must not mutate service or admission state')


def service_row(**updates):
    row={'id':'service-current','owner_id':'PRIVATE-OWNER','name':'索引旧名称',
         'identity_status':'listed','meta':json.dumps({'private':'PRIVATE-CONFIG'}),
         'revision':1,'indexed':1,'connection':'PRIVATE-CONNECTION','expires':200,
         'maximum':2,'accepting':True,
         'body':json.dumps({'declaration':{'name':'同名服务','description':'公开说明',
                            'categoryId':'cat_dev','protocolId':'https://example.test/a2a'}}),
         'templates':json.dumps([{'validUntil':'2030-01-01T00:00:00Z'}])}
    return {**row,**updates}


def platform_for(rows, *, active=0):
    db=DirectoryDB(rows,active)
    cards=[{'id':sid,'name':'同名服务','description':'原始索引',
            'card':{'status':'INDEX-STATUS','proof':{'signature':'ORIGINAL-SIGNATURE'}}}
           for sid in ('service-unknown','service-current')]
    calls=[]
    def search(query, top_k):
        calls.append((query,top_k))
        return cards
    platform=SimpleNamespace()
    platform.db=db
    platform.discovery=SimpleNamespace(search=search,last_mode='a2x-tree')
    platform.service_views=ServiceViews(db,clock=lambda:100)
    events=[]
    platform.bus=SimpleNamespace(emit=lambda *args,**kwargs:events.append((args,kwargs)))
    platform.market_id='market'
    platform.discovery_operations=DiscoveryOperations(platform.discovery,platform.service_views,platform.bus.emit,platform.market_id)
    return platform,cards,calls,events


@pytest.mark.parametrize(('changes','active','expected'),[
    ({},0,'online'),
    ({'expires':100},0,'offline'),
    ({'accepting':False},0,'paused'),
    ({},2,'busy'),
    ({'indexed':None},0,'listed'),
    ({'templates':json.dumps([{'validUntil':'1970-01-01T00:00:50Z'}])},0,'unavailable'),
])
def test_search_preserves_original_hits_and_uses_current_public_projection(changes,active,expected):
    platform,original,calls,events=platform_for([service_row(**changes)],active=active)
    before=deepcopy(original)
    rows=deepcopy(platform.db.values)
    result=platform.discovery_operations.search('自然业务需求',8,by='assistant')
    assert calls==[('自然业务需求',8)]
    assert [r['id'] for r in result]==[r['id'] for r in original]
    assert result[0]['availability'] is None
    snapshot=result[1]['availability']
    assert snapshot['status']==expected
    assert snapshot['activeDelegations']==active and snapshot['maxActiveDelegations']==2
    assert set(snapshot)=={'observedAt','status','activeDelegations','maxActiveDelegations'}
    assert datetime.fromisoformat(snapshot['observedAt'].replace('Z','+00:00')).utcoffset().total_seconds()==0
    assert snapshot['observedAt'].endswith('Z')
    assert [{k:v for k,v in hit.items() if k!='availability'} for hit in result]==before
    assert all(a is not b and a['card'] is b['card'] for a,b in zip(result,original))
    assert original==before and platform.db.values==rows
    assert len(events)==1 and events[0][1]['sender']=='assistant'
    public=json.dumps(result)
    assert all(secret not in public for secret in ('PRIVATE-OWNER','PRIVATE-CONNECTION','PRIVATE-CONFIG'))


def test_search_http_transmits_null_or_public_snapshot_without_dropping_hits():
    platform,original,calls,_=platform_for([service_row(),service_row(id='service-second')])
    original.append({**deepcopy(original[1]),'id':'service-second'})
    app=FastAPI()
    app.include_router(build_router(platform,lambda:'authorized-account'))
    with TestClient(app) as http:
        response=http.post('/api/v1/search',json={'query':'自然业务需求','top_k':8})
    assert response.status_code==200
    payload=response.json()
    assert payload['mode']=='a2x-tree' and calls==[('自然业务需求',8)]
    assert [h['id'] for h in payload['hits']]==[h['id'] for h in original]
    assert payload['hits'][0]['availability'] is None
    assert payload['hits'][1]['availability']['status']=='online'
    assert payload['hits'][1]['card']==original[1]['card']
    snapshots=[h['availability'] for h in payload['hits'] if h['availability']]
    assert len(snapshots)==2 and len({s['observedAt'] for s in snapshots})==1


def test_search_with_no_public_projection_never_invents_online_state():
    platform,original,_,_=platform_for([])
    result=platform.discovery_operations.search('仍保留搜索命中',8)
    assert [h['id'] for h in result]==[h['id'] for h in original]
    assert all(hit['availability'] is None for hit in result)
    assert all('availability' not in hit for hit in original)
