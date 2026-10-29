"""Account preferences govern comparison, never service names or business specifications."""
import pytest


def candidate(c,d,url,sid):
    template=c.templates(url)[0]
    materials={i['name']:(b'x'*10,d.get('sample_inputs',{}).get(i['name'],{})) for i in d.get('inputs',[])}
    return c.draft(template['templateDigest'],{d['deliverable']['name']:d.get('sample_spec',{})},materials=materials,budget=100,scope_id=sid)


def test_policy_counts_distinct_services_and_blocks_early_sign(providers,local_client,monkeypatch):
    c=local_client;monkeypatch.setenv('IOALAB_MIN_QUOTES','2')
    scope=c.authorize('Compare eligible professional services',100,minimum_quotes=1,confirmed=True)
    assert scope['minimumQuotes']==2
    first,second=providers.selected[:2]
    url=lambda d:providers.public_url+'/a2a/providers/svc-'+d['id']
    with pytest.raises(ValueError,match='scope_id'):candidate(c,first,url(first),None)
    a=candidate(c,first,url(first),scope['scopeId'])
    duplicate=candidate(c,first,url(first),scope['scopeId'])
    assert duplicate['cardDigest']!=a['cardDigest']
    assert duplicate['quoteProgress']['receivedQuotes']==1 and not duplicate['quoteProgress']['readyForConfirmation']
    with pytest.raises(ValueError,match='Comparison policy'):c.sign(a['cardDigest'],confirmed=True)
    assert not c.client.card(a['cardDigest'])['signatures']['1']
    b=candidate(c,second,url(second),scope['scopeId'])
    assert b['quoteProgress']['receivedQuotes']==2 and b['quoteProgress']['readyForConfirmation']
    state=c.scope(scope['scopeId']);state['requests']['unknown-payment']={'fee':'1','state':'pending'}
    c.store.put('scopes',state['scopeId'],state)
    assert c.quote_progress(state['scopeId'])['nextAction']=='reconcile'
    with pytest.raises(ValueError,match='Comparison policy'):c.sign(a['cardDigest'],confirmed=True)
    del state['requests']['unknown-payment'];c.store.put('scopes',state['scopeId'],state)
    c.sign(a['cardDigest'],confirmed=True)
    assert c.scope(state['scopeId'])['selected']==a['cardDigest']
    assert len(c.scope(state['scopeId'])['comparisonCards'])==2


def test_legacy_scope_is_not_retroactively_restricted(providers,local_client,monkeypatch):
    c=local_client;scope=c.authorize('Existing approved scope',100,confirmed=True)
    scope.pop('minimumQuotes');c.store.put('scopes',scope['scopeId'],scope)
    monkeypatch.setenv('IOALAB_MIN_QUOTES','3')
    d=providers.selected[0];url=providers.public_url+'/a2a/providers/svc-'+d['id']
    result=candidate(c,d,url,scope['scopeId'])
    assert result['quoteProgress']['minimumQuotes']==1 and result['quoteProgress']['readyForConfirmation']
    c.sign(result['cardDigest'],confirmed=True)


def test_invalid_comparison_preferences_fail_explicitly(local_client,monkeypatch):
    for value in ('0','9','three'):
        monkeypatch.setenv('IOALAB_MIN_QUOTES',value)
        with pytest.raises(ValueError,match='IOALAB_MIN_QUOTES'):local_client.delegation_policy()
    monkeypatch.delenv('IOALAB_MIN_QUOTES')
    assert local_client.delegation_policy()=={'minimumQuotes':1}
    for value in (0,9,True,1.5,'2'):
        with pytest.raises(ValueError,match='minimum_quotes'):local_client.authorize('Test explicit preference',100,minimum_quotes=value,confirmed=True)
