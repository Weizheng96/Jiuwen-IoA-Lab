"""Public seed, ordinary account and read-only sample-budget readiness."""
import json
from decimal import Decimal

import httpx
import pytest

from ioalab_trial.bootstrap import public_seed,core_environment,register_assistant
from ioalab_trial.probe import sample_budget


IDS=['code-miniapp-reliable','code-miniapp-basic','code-website','code-android']


def test_core_seed_has_only_four_public_summaries():
    value=public_seed(IDS)
    assert len(value['domains'])==1
    assert {item['id'] for item in value['domains'][0]['services']}==set(IDS)
    def inspect(node):
        if isinstance(node,dict):
            assert not set(node)&{'sample','generator','verifier','sourceCode','instructions','skills'}
            for child in node.values():inspect(child)
        elif isinstance(node,list):
            for child in node:inspect(child)
    inspect(value)


def test_seed_projection_drops_private_fields_even_if_input_contains_them():
    services=[{'id':name,'name':name,'description':'public','generator':{'secret':'not public'},
               'verifier':{'source':'private'},'sample':{'private':True}} for name in IDS]
    source={'domains':[{'account':'ISP_008','label':'编程','category':{'id':'cat_dev','name':'编程'},
                        'services':services,'generator':{'secret':True}}]}
    projected=public_seed(IDS,source)
    assert 'secret' not in json.dumps(projected) and 'generator' not in json.dumps(projected)
    assert 'verifier' not in json.dumps(projected) and 'sample' not in json.dumps(projected)
    with pytest.raises(ValueError):public_seed([*IDS[:3],'missing'],source)
    source['domains'][0]['category']['id']='cat_other'
    with pytest.raises(ValueError):public_seed(IDS,source)


def test_trial_credit_is_explicit_and_does_not_copy_formal_override(monkeypatch):
    monkeypatch.setenv('IOALAB_LOGIN_CREDIT','3')
    model={'name':'trial-model','base_url':'http://model-egress:8081/v1','api_key':'epoch-only-token'}
    config=core_environment(model,'private-admin')
    assert config['IOALAB_LOGIN_CREDIT']=='1000' and config['IOALAB_MODE']=='trial'
    assert config['IOALAB_DATA']=='/epoch/core/data' and config['IOALAB_REGISTRATION']=='open'
    assert config['IOALAB_LLM_API_KEY']=='epoch-only-token'
    assert config['IOALAB_ASSISTANT_PASSWORD']!='private-admin'


@pytest.mark.parametrize('role,cookie,valid',[('user',True,True),('admin',True,False),('user',False,False)])
def test_registration_uses_stable_public_operation_and_requires_ordinary_role(role,cookie,valid):
    calls=[]
    def api(request):
        calls.append(request)
        if request.url.path=='/api/auth/register':
            value=json.loads(request.content)
            assert value['username']=='trial-user' and value['operationId']=='trial-register-epoch'
            return httpx.Response(200,json={'id':'new-user'},headers={'Set-Cookie':'ioalab_session=original; Path=/'} if cookie else {})
        assert request.url.path=='/api/session'
        return httpx.Response(200,json={'role':role})
    with httpx.Client(base_url='http://local',transport=httpx.MockTransport(api)) as client:
        if valid:assert register_assistant(client,'private-password','epoch')=={'cookie':'original','username':'trial-user'}
        else:
            with pytest.raises(RuntimeError):register_assistant(client,'private-password','epoch')
    assert calls[0].method=='POST'


@pytest.mark.parametrize('amount,expected',[('1000',True),('500',True),('499.99',False),('NaN',False),('Infinity',False)])
def test_probe_reuses_original_registration_cookie_and_never_logs_in(amount,expected):
    calls=[]
    def api(request):
        calls.append(request)
        assert request.method=='GET' and request.url.path=='/api/wallet'
        assert 'ioalab_session=original-cookie' in request.headers['Cookie']
        return httpx.Response(200,json={'balance':amount,'log':[]})
    with httpx.Client(base_url='http://local',transport=httpx.MockTransport(api)) as client:
        assert sample_budget(client,{'cookie':'original-cookie'}) is expected
        assert sample_budget(client,{'cookie':'original-cookie'}) is expected
    assert len(calls)==2 and all(request.method=='GET' for request in calls)


def test_registration_failure_does_not_fall_back_to_admin_or_login():
    calls=[]
    def api(request):
        calls.append(request)
        return httpx.Response(400,json={'error':'explicit registration rejection'})
    with httpx.Client(base_url='http://local',transport=httpx.MockTransport(api)) as client:
        with pytest.raises(httpx.HTTPStatusError):register_assistant(client,'private','epoch')
    assert len(calls)==1 and calls[0].url.path=='/api/auth/register'


@pytest.mark.parametrize('code',[401,503])
def test_budget_probe_failure_stays_unready_without_login(code):
    calls=[]
    def api(request):
        calls.append(request)
        return httpx.Response(code)
    with httpx.Client(base_url='http://local',transport=httpx.MockTransport(api)) as client:
        with pytest.raises(httpx.HTTPStatusError):sample_budget(client,{'cookie':'original'})
    assert len(calls)==1 and calls[0].method=='GET' and calls[0].url.path=='/api/wallet'
