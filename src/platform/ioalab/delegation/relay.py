"""Validate participant protocol envelopes; original A2C execution stays local."""
import hashlib
import json
from datetime import datetime,timezone

from a2c import objects
from a2c.errors import A2CError
from a2c.jws import check_fresh,header_of
from ..errors import LabError


SCHEMA='''CREATE TABLE IF NOT EXISTS delegation_protocol_calls(
 message TEXT PRIMARY KEY,interaction TEXT NOT NULL,admission TEXT NOT NULL,op TEXT NOT NULL,
 state TEXT NOT NULL,last_result TEXT,last_hash TEXT,task TEXT);'''


class ProtocolRelay:
    def __init__(self,db,identities,admissions,scopes,files,*,clock):
        self.db,self.identities,self.admissions,self.scopes,self.files,self.clock=db,identities,admissions,scopes,files,clock
        with db.lock:db.conn.executescript(SCHEMA)
        self._migrate_calls()

    def _migrate_calls(self):
        # Older releases allowed one method per interaction, which loses later
        # periods. Preserve every original call before replacing that constraint.
        with self.db.tx() as conn:
            indexes=conn.execute('PRAGMA index_list(delegation_protocol_calls)').fetchall()
            obsolete=set()
            for index in indexes:
                quoted='"'+index['name'].replace('"','""')+'"'
                columns=[row['name'] for row in conn.execute('PRAGMA index_info('+quoted+')')]
                if index['unique'] and columns==['interaction','op']:obsolete.add(index['name'])
            if not obsolete:return
            expected=['message','interaction','admission','op','state','last_result','last_hash','task']
            if [row['name'] for row in conn.execute('PRAGMA table_info(delegation_protocol_calls)')]!=expected:
                raise LabError('not_ready','Restore or migrate the unknown original call schema before startup',status=409)
            definitions=[row['sql'] for row in conn.execute("SELECT name,type,sql FROM sqlite_master WHERE tbl_name='delegation_protocol_calls' AND type IN ('index','trigger') AND sql IS NOT NULL") if row['type']!='index' or row['name'] not in obsolete]
            conn.execute(SCHEMA.replace('IF NOT EXISTS delegation_protocol_calls','delegation_protocol_calls_upgraded'))
            conn.execute('INSERT INTO delegation_protocol_calls_upgraded SELECT * FROM delegation_protocol_calls')
            count=conn.execute('SELECT COUNT(*) FROM delegation_protocol_calls').fetchone()[0]
            if conn.execute('SELECT COUNT(*) FROM delegation_protocol_calls_upgraded').fetchone()[0]!=count:raise RuntimeError('Original call migration did not preserve every row')
            conn.execute('DROP TABLE delegation_protocol_calls')
            conn.execute('ALTER TABLE delegation_protocol_calls_upgraded RENAME TO delegation_protocol_calls')
            for definition in definitions:conn.execute(definition)

    def check_request_in(self,conn,value):
        payload=value['body']
        if set(payload)-{'op','body','files','admissionId','taskId'} or not {'op','body','files','admissionId'}.issubset(payload) or not isinstance(payload['op'],str) or not isinstance(payload['body'],dict):
            raise LabError('invalid_input','Use an original A2C body, method, files and admission context')
        row,original=self.admissions.initial_protocol_in(conn,value['sender'],payload['admissionId'],payload['op'],payload['body'])
        if (row['service'],row['interaction'])!=(value['recipient'],value.get('interactionId')):
            raise LabError('forbidden','Protocol envelope changed its original receiver or interaction',status=403)
        self._files(conn,value,original)
        return row

    def _files(self,conn,value,original):
        payload=value['body'];references=payload['files']
        if not isinstance(references,dict):raise LabError('invalid_input','Protocol files must be a reference map')
        if not references:return
        if payload['op']=='ap2.checkout':raise LabError('invalid_input','A checkout request does not contain materials')
        subject=payload['body'].get('request') or payload['body'].get('card')
        review={item['name'] for item in original['template'].get('inputs',[]) if item.get('reviewBeforeSigning')}
        if not set(references).issubset(review):raise LabError('forbidden','Only authorized pre-signing review material may be attached at this stage',status=403)
        for name,reference in references.items():
            if not isinstance(reference,dict) or subject.get('inputs',{}).get(name,{}).get('digest')!=reference.get('digest'):
                raise LabError('forbidden','Material file digest differs from the signed original object',status=403)
        owner=self.identities.agent(value['sender'])['owner_id']
        authority=self.scopes.authority_in(conn,owner,value['sender'],original['body']['scopeDigest'])
        auth=payload['body'].get('informationAuthorization')
        try:
            objects.verify_signed(auth,authority['jwk'],signer=authority['id'])
            check_fresh(header_of(auth['signature']),datetime.fromtimestamp(self.clock(),timezone.utc))
            inline={key:item for key,item in authority['jwk'].items() if key!='kid'}
            if auth.get('kind')!='information-authorization' or auth.get('jwk')!=inline or set(auth.get('inputs',[]))!=set(references):raise ValueError()
            key='requestDigest' if original['body']['formation']=='proposal' else 'cardDigest'
            if auth.get(key)!=original['body']['bindingDigest']:raise ValueError()
        except (A2CError,KeyError,TypeError,ValueError):raise LabError('forbidden','Original material authorization is required before granting access',status=403) from None

    def request_in(self,conn,value):
        self.check_request_in(conn,value)
        if value['body']['op'] in ('request.submit','card.countersign') and conn.execute('SELECT 1 FROM delegation_protocol_calls WHERE interaction=? AND op=?',(value['interactionId'],value['body']['op'])).fetchone():
            raise LabError('id_conflict','Resume the original protocol message instead of signing a replacement',status=409)
        self.files.grant_in(conn,value['sender'],value['recipient'],value['body']['files'])
        if value['body']['op']=='request.submit':
            self.admissions.signed_request_in(conn,value['body']['admissionId'],value['messageId'],value['body']['body']['request'])
        conn.execute("INSERT INTO delegation_protocol_calls(message,interaction,admission,op,state) VALUES(?,?,?,?,'pending')",
                     (value['messageId'],value['interactionId'],value['body']['admissionId'],value['body']['op']))

    def start_in(self,conn,value):
        self.check_request_in(conn,value)
        if value['body']['op']=='request.submit':
            self.admissions.signed_request_in(conn,value['body']['admissionId'],value['messageId'],value['body']['body']['request'],started=True)
        conn.execute("UPDATE delegation_protocol_calls SET state='processing' WHERE message=?",(value['messageId'],))

    def result_in(self,conn,value,original):
        if (value['sender'],value['recipient'])!=(original['recipient'],original['sender']) or value.get('interactionId')!=original['interaction']:
            raise LabError('forbidden','A2C result must return to the original requester and interaction',status=403)
        call=conn.execute('SELECT * FROM delegation_protocol_calls WHERE message=?',(original['id'],)).fetchone()
        if not call or original['started'] is None:raise LabError('forbidden','An A2C result requires its original start permit',status=403)
        payload=value['body']
        if set(payload)-{'reply','files','taskFiles','executionError'} or not {'reply','files'}.issubset(payload) or not isinstance(payload['reply'],dict) or not isinstance(payload['files'],dict) or not isinstance(payload.get('taskFiles',{}),dict):raise LabError('invalid_input','Preserve the original A2C reply and file maps')
        reply=payload['reply']
        if set(reply)-{'body','state','taskId','task'} or not isinstance(reply.get('body'),dict) or reply['body'].get('op')!=call['op']:
            raise LabError('invalid_input','A2C reply must preserve its original protocol method')
        state=reply.get('state')
        if state is None:state='completed'
        if state not in ('working','auth-required','completed','failed','rejected','canceled'):raise LabError('invalid_input','Use an original A2C task state')
        if 'executionError' in payload:
            error=payload['executionError']
            if state not in ('working','auth-required') or not isinstance(error,dict) or set(error)!={'code','recovery'} or error['code']!='unknown_result' or error['recovery']!='reconcile_original':
                raise LabError('invalid_input','Unknown execution preserves a nonterminal original task')
        task=reply.get('taskId')
        if task is not None and (not isinstance(task,str) or not 1<=len(task)<=256):raise LabError('invalid_input','Use the original A2C task ID')
        snapshot=reply.get('task')
        if snapshot is not None:
            if not isinstance(snapshot,dict) or set(snapshot)!={'id','op','state','artifacts','status'} or (snapshot['id'],snapshot['op'],snapshot['state'])!=(task,call['op'],state) or not isinstance(snapshot['artifacts'],list) or (snapshot['status'] is not None and not isinstance(snapshot['status'],dict)):
                raise LabError('invalid_input','Task snapshot must preserve the original reply identity, method and state')
        elif payload.get('taskFiles'):raise LabError('invalid_input','Task files require the original task snapshot')
        if call['task'] and task!=call['task']:raise LabError('id_conflict','A2C progress changed its original task ID',status=409)
        fingerprint=hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
        if call['state'] in ('completed','failed','rejected','canceled'):
            if call['last_hash']==fingerprint:return call['last_result']
            raise LabError('id_conflict','Original A2C terminal result cannot be replaced',status=409)
        if call['op']=='card.countersign' and 'card' in reply['body']:
            request=json.loads(original['envelope'])['body']['body']['card']
            self.admissions.countersign_result_in(conn,call['admission'],reply['body']['card'],request['signatures']['1'])
        self.files.grant_in(conn,value['sender'],value['recipient'],payload['files'])
        self.files.grant_in(conn,value['sender'],value['recipient'],payload.get('taskFiles',{}))
        conn.execute('UPDATE delegation_protocol_calls SET state=?,last_result=?,last_hash=?,task=? WHERE message=?',
                     (state,value['messageId'],fingerprint,task,original['id']))
        return None
