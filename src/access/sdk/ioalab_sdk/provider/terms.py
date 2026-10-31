"""Canonical public terms, excluding only the bound identity and expiring signatures."""
from a2c.codec import digest,parse_time


def publication_terms(protocol_id,service):
    if not isinstance(protocol_id,str) or not protocol_id or not isinstance(service,dict):
        raise ValueError('A bound protocol identity and complete public service are required')
    templates=[{k:v for k,v in t.items() if k not in ('provider','templateDigest','signatures','validUntil')}
               for t in service['templates']]
    return digest({'protocolId':protocol_id,**{k:service[k] for k in ('name','description','tags','categoryId')},'templates':templates})


def covers(policy,identity,service,now):
    if (not isinstance(policy,dict) or policy.get('agentId')!=identity.agent_id or policy.get('enabled') is not True or
        type(policy.get('revision')) is not int or policy['revision']<1 or
        type(policy.get('maxTemplateSeconds')) is not int or not 60<=policy['maxTemplateSeconds']<=31622400 or
        policy.get('termsDigest')!=publication_terms(identity.protocol_id,service)):
        return False
    return all(now.timestamp()<parse_time(t['validUntil']).timestamp()<=now.timestamp()+policy['maxTemplateSeconds'] for t in service['templates'])
