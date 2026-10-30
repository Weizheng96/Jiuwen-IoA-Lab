"""Public declarations and frozen schema checks owned by the message center."""
import json
import re
from fastapi import HTTPException
from ..errors import LabError
from . import check_schema,validate


class PublicInterfaces:
    def __init__(self,db,identities):
        self.db,self.identities=db,identities

    @staticmethod
    def check(schema,value=None,*,schema_only=False):
        try:
            check_schema(schema)
            if not schema_only:validate(schema,value)
        except HTTPException as exc:
            raise LabError('invalid_input',str(exc.detail)) from exc

    def publish(self,conn,owner,payload):
        name,definition,expected=payload.get('name'),payload.get('definition'),payload.get('expectedRevision')
        if not isinstance(name,str) or not re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_.-]{0,63}',name):
            raise LabError('invalid_input','Invalid interface name')
        if not isinstance(definition,dict) or set(definition)-{'description','inputSchema','outputSchema','allowedSenders','enabled','contentDigest'}:
            raise LabError('invalid_input','Invalid public interface definition')
        if not isinstance(expected,int) or isinstance(expected,bool) or expected<0:
            raise LabError('invalid_input','expectedRevision must be a nonnegative integer')
        digest=definition.get('contentDigest','')
        if not isinstance(digest,str) or not re.fullmatch('sha256:[0-9a-f]{64}',digest):
            raise LabError('invalid_input','A local rule contentDigest is required')
        for key in ('inputSchema','outputSchema'):
            schema=definition.get(key)
            if not isinstance(schema,dict) or schema.get('type')!='object':
                raise LabError('invalid_input','Explicit object input/output schemas of at most 16 KiB are required')
            self.check(schema,schema_only=True)
            if len(json.dumps(schema).encode())>16384:
                raise LabError('invalid_input','Schema exceeds 16 KiB')
        enabled=definition.get('enabled',True)
        if not isinstance(enabled,bool):raise LabError('invalid_input','enabled must be boolean')
        allow=definition.get('allowedSenders')
        ids=None
        if allow is not None:
            if not isinstance(allow,list) or len(allow)>100 or not all(isinstance(a,str) for a in allow):
                raise LabError('invalid_input','Invalid allowedSenders')
            ids=[]
            for username in allow:
                row=conn.execute('SELECT id FROM users WHERE username=?',(username,)).fetchone()
                if not row:raise LabError('invalid_input','An allowed account does not exist')
                ids.append(row['id'])
        old=conn.execute('SELECT revision FROM message_interfaces WHERE owner=? AND name=?',(owner,name)).fetchone()
        revision=old['revision'] if old else 0
        if revision!=expected:raise LabError('revision_conflict','Read the current interface before publishing',status=409)
        value={**definition,'description':str(definition.get('description',''))[:1000],
               'allowedIds':None if ids is None else sorted(set(ids)),'allowedSenders':allow}
        encoded=json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
        revision+=1
        conn.execute('INSERT INTO message_versions VALUES(?,?,?,?)',(owner,name,revision,encoded))
        conn.execute('INSERT OR REPLACE INTO message_interfaces VALUES(?,?,?,?,?,?)',(owner,name,revision,encoded,int(enabled),__import__('time').time()))
        return {'name':name,'revision':revision,'agentId':self.identities.assistant(owner),**value,'enabled':enabled}

    def current(self,conn,recipient,sender,name):
        target=self.identities.agent(recipient)
        source=self.identities.agent(sender)
        if target['role']!='assistant' or source['role']!='assistant':raise LabError('forbidden','Personal messages connect personal assistants',status=403)
        row=conn.execute('SELECT * FROM message_interfaces WHERE owner=? AND name=?',(target['owner_id'],name)).fetchone()
        if not row:raise LabError('forbidden','Interface is unavailable',status=403)
        definition=json.loads(row['definition'])
        if not row['enabled'] or (definition.get('allowedIds') is not None and source['owner_id'] not in definition['allowedIds']):
            raise LabError('forbidden','Interface is unavailable',status=403)
        return row

    def version(self,conn,recipient,name,revision):
        owner=self.identities.agent(recipient)['owner_id']
        row=conn.execute('SELECT definition FROM message_versions WHERE owner=? AND name=? AND revision=?',(owner,name,revision)).fetchone()
        if not row:raise LabError('revision_conflict','Original interface revision is unavailable',status=409)
        definition=json.loads(row['definition'])
        if any(not isinstance(definition.get(k),dict) or definition[k].get('type')!='object' for k in ('inputSchema','outputSchema')):
            raise LabError('not_ready','Interface needs explicit object schemas',status=409)
        return definition
