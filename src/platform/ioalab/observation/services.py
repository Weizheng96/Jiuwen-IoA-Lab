"""Public declarations and current execution availability; no production or indexing."""
import json
import time
from a2c.codec import parse_time


class ServiceViews:
    def __init__(self,db,*,clock=time.time):self.db,self.clock=db,clock

    def list(self,owner=None,*,include_unpublished=False):
        where="a.role='service'";values=[]
        if owner is not None:where+=' AND a.owner_id=?';values.append(owner)
        rows=self.db.rows('''SELECT a.id,a.owner_id,a.name,a.status AS identity_status,a.meta,p.revision,p.body,p.templates,
            x.revision AS indexed,l.connection,l.expires,
            policy.maximum,policy.accepting FROM agents a
            LEFT JOIN registry_publications p ON p.actor=a.id AND p.revision=(SELECT MAX(revision) FROM registry_publications WHERE actor=a.id)
            LEFT JOIN registry_projection x ON x.actor=a.id
            LEFT JOIN delivery_leases l ON l.actor=a.id
            LEFT JOIN admission_policies policy ON policy.service=a.id WHERE '''+where+' ORDER BY a.name',*values)
        result=[];now=self.clock()
        for row in rows:
            declaration=json.loads(row['body'])['declaration'] if row['body'] else None
            if owner is None and not include_unpublished and (not declaration or row['identity_status']=='disabled'):continue
            templates=json.loads(row['templates']) if row['templates'] else []
            valid=any(parse_time(t['validUntil']).timestamp()>now for t in templates)
            indexed=bool(declaration and row['indexed']==row['revision'])
            connected=bool(row['connection'] and row['expires'] and row['expires']>now)
            accepting=bool(row['accepting']);maximum=row['maximum'] or 0
            active=self.db.one("SELECT COUNT(*) AS n FROM delegation_admissions WHERE service=? AND (state='committed' OR state='held' AND hold_until>?)",row['id'],now)['n']
            status='listed'
            if indexed:
                status='offline' if not connected else 'paused' if not accepting else 'busy' if active>=maximum else 'online'
                if not valid:status='unavailable'
            if row['identity_status']=='disabled':status='disabled'
            meta=json.loads(row['meta'])
            value={'id':row['id'],'name':declaration['name'] if declaration else row['name'],
                'description':declaration.get('description','') if declaration else '',
                'category':declaration['categoryId'] if declaration else None,
                'url':declaration['protocolId'] if declaration else meta.get('protocolId'),
                'status':status,'published':bool(declaration),'indexReady':indexed,'connected':connected,
                'templateAvailable':valid,'accepting':accepting,'activeDelegations':active,'maxActiveDelegations':maximum}
            if owner is not None or include_unpublished:value['owner_id']=row['owner_id']
            if include_unpublished:
                formations=sorted({t['formation'] for t in templates})
                value.update(tags=declaration.get('tags',[]) if declaration else [],formations=formations,
                    formation=formations[0] if len(formations)==1 else 'multiple' if formations else None,
                    price=None,capacity=maximum,runnable=status=='online')
            result.append(value)
        return result
