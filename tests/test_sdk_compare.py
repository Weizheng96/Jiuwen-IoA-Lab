"""Read signed templates over HTTP and bind a reported choice to its actual contract."""
import pytest


def test_comparison_uses_signed_templates_and_matches_contract(platform,providers,local_client,client):
    definitions=providers.selected[:2]
    urls=[providers.public_url+'/a2a/providers/svc-'+d['id'] for d in definitions]
    t=local_client.templates(urls[0])[0]
    draft=local_client.draft(t['templateDigest'],definitions[0]['sample_spec'])
    with pytest.raises(ValueError,match='does not match'):
        local_client.compare(urls,'wrong selection',urls[1],draft['cardDigest'])
    result=local_client.compare(urls,'选择覆盖本单需求的模板',urls[0],draft['cardDigest'])
    assert result['trust']=='reported'
    assert all(t['signatures']['provider'] for c in result['candidates'] for t in c['templates'])
    view=client.get('/api/workbench',headers={'Authorization':'Bearer '+local_client.token}).json()
    event=next(e for e in view['events'] if e['op']=='discovery.compared')
    assert event['card_digest']==draft['cardDigest'] and event['trust']=='reported'
