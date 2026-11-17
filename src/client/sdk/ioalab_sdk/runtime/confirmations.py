"""Typed user decisions, bound to actual objects and one original operation."""
import hashlib
import base64
import binascii
import hmac
import json
import math
import secrets
import time
import uuid
from dataclasses import dataclass

from .protocol import RuntimeFault,encode,stamp


SCHEMA='''CREATE TABLE IF NOT EXISTS runtime_confirmations(
 id TEXT PRIMARY KEY,actor TEXT NOT NULL,operation TEXT NOT NULL,kind TEXT NOT NULL,
 binding TEXT NOT NULL,fingerprint TEXT NOT NULL,expires REAL,state TEXT NOT NULL,
 answer TEXT,source TEXT,used_by TEXT,created REAL NOT NULL);'''


@dataclass(frozen=True)
class ConfirmationDeadline:
    """Neutral trusted-owner deadline; protocol due time need not be expiry."""
    expires_at: float|None = None
    response_due_at: float|None = None
    object_bound: bool = False

    def __post_init__(self):
        if type(self.object_bound) is not bool or (self.object_bound and (self.expires_at is not None or self.response_due_at is not None)):
            raise RuntimeFault('invalid_input','Object-bound confirmation cannot override a time deadline')
        for value in (self.expires_at,self.response_due_at):
            if value is not None and (type(value) not in (int,float) or not math.isfinite(value)):
                raise RuntimeFault('invalid_input','An absolute finite deadline is required')
            if value is not None:
                try:stamp(value)
                except (OverflowError,OSError,ValueError):
                    raise RuntimeFault('invalid_input','An absolute deadline must be representable') from None
        if self.expires_at is None and self.response_due_at is None and not self.object_bound:
            raise RuntimeFault('invalid_input','An original expiry or response deadline is required')

    def value(self):
        return {'expiresAt':self.expires_at,'responseDueAt':self.response_due_at,
                **({'objectBound':True} if self.object_bound else {})}


class Confirmations:
    def __init__(self,store,*,clock=time.time):
        self.store,self.clock=store,clock
        self._authority=secrets.token_urlsafe(32)
        with store.transaction() as conn:
            conn.execute(SCHEMA)
            fields=conn.execute('PRAGMA table_info(runtime_confirmations)').fetchall()
            if any(row['name']=='expires' and row['notnull'] for row in fields):
                # All twelve original columns survive byte-for-byte in one
                # transaction; no business decision is migrated here.
                conn.execute('ALTER TABLE runtime_confirmations RENAME TO runtime_confirmations_legacy')
                conn.execute(SCHEMA)
                columns='id,actor,operation,kind,binding,fingerprint,expires,state,answer,source,used_by,created'
                conn.execute('INSERT INTO runtime_confirmations ('+columns+') SELECT '+columns+' FROM runtime_confirmations_legacy')
                if (conn.execute('SELECT '+columns+' FROM runtime_confirmations_legacy EXCEPT SELECT '+columns+' FROM runtime_confirmations').fetchone()
                    or conn.execute('SELECT '+columns+' FROM runtime_confirmations EXCEPT SELECT '+columns+' FROM runtime_confirmations_legacy').fetchone()):
                    raise RuntimeFault('identity_conflict','Original confirmation migration did not preserve every field')
                conn.execute('DROP TABLE runtime_confirmations_legacy')

    def ensure(self,binding,*,ttl=900,kind='approval',questions=None,deadline=None):
        return self._request(binding,ttl=ttl,kind=kind,questions=questions,create=True,deadline=deadline)

    def find(self,binding,*,kind='approval',questions=None):
        """Find the same original binding without guessing its former TTL.

        Lets its trusted role inspect the original result before choosing
        ensure or a separately authorized pending-only migration.
        """
        if kind not in ('approval','question'):
            raise RuntimeFault('invalid_input','Invalid confirmation kind')
        cid=str(uuid.uuid5(uuid.NAMESPACE_URL,encode(['ioa-confirm',binding['actor'],binding['operationId'],binding['stage'],binding['action'],kind])))
        with self.store.lock:
            row=self.store.db.execute('SELECT * FROM runtime_confirmations WHERE id=?',(cid,)).fetchone()
            if row is None:return None
            value=json.loads(row['binding'])
            if encode(value['binding'])!=encode(binding) or value['questions']!=(questions or []):
                raise RuntimeFault('id_conflict','The confirmed object or preview changed')
            return self._public(row)

    def _request(self,binding,*,ttl,kind,questions,create,deadline=None):
        fields={'actor','operationId','subject','stage','action','preview'}
        if not isinstance(binding,dict) or set(binding)!=fields or not isinstance(binding['subject'],dict) or not isinstance(binding['preview'],dict):
            raise RuntimeFault('invalid_input','A complete confirmation binding is required')
        for field in ('actor','operationId','stage','action'):
            if not isinstance(binding[field],str) or not 1<=len(binding[field])<=256:raise RuntimeFault('invalid_input','Invalid confirmation identity')
        if (kind not in ('approval','question') or
            (deadline is None and (type(ttl) not in (int,float) or not 0<ttl<=86400)) or
            (deadline is not None and (not isinstance(deadline,ConfirmationDeadline) or ttl!=900))):
            raise RuntimeFault('invalid_input','Invalid confirmation kind or deadline')
        questions=[] if questions is None else questions
        if not isinstance(questions,list) or (kind=='question' and not questions):raise RuntimeFault('invalid_input','Native questions are required')
        for question in questions:
            if not isinstance(question,dict) or type(question.get('allow_custom_input',True)) is not bool:
                raise RuntimeFault('invalid_input','Question allow_custom_input must be a boolean')
            if question.get('allow_custom_input') is False:
                options=question.get('options')
                if (not isinstance(options,list) or not options or
                    any(not isinstance(option,dict) or not isinstance(option.get('value'),str) or
                        not option['value'].strip() for option in options)):
                    raise RuntimeFault('invalid_input','A closed question requires displayed option objects with nonempty string values')
                if len({option['value'] for option in options})!=len(options):
                    raise RuntimeFault('invalid_input','Closed question option values must not repeat')
        payload={'binding':binding,'questions':questions,
                 **({'deadline':deadline.value()} if deadline is not None else {'ttl':ttl})}
        serialized=encode(payload)
        if len(serialized.encode())>96*1024:raise RuntimeFault('invalid_input','Confirmation preview is too large')
        fingerprint=hashlib.sha256(serialized.encode()).hexdigest()
        cid=str(uuid.uuid5(uuid.NAMESPACE_URL,encode(['ioa-confirm',binding['actor'],binding['operationId'],binding['stage'],binding['action'],kind])))
        with self.store.transaction() as conn:
            if not conn.execute('SELECT 1 FROM runtime_cursors WHERE actor=?',(binding['actor'],)).fetchone():
                raise RuntimeFault('forbidden','Confirmation identity is outside this instance')
            row=conn.execute('SELECT * FROM runtime_confirmations WHERE id=?',(cid,)).fetchone()
            if row:
                if row['fingerprint']!=fingerprint:raise RuntimeFault('id_conflict','The confirmed object or preview changed')
            else:
                if not create:return None
                now=self.clock()
                expiry=deadline.expires_at if deadline is not None else now+ttl
                if expiry is not None and expiry<=now:
                    raise RuntimeFault('expired','Original business object has expired')
                conn.execute('INSERT INTO runtime_confirmations VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                             (cid,binding['actor'],binding['operationId'],kind,serialized,fingerprint,expiry,'pending',None,None,None,now))
                row=conn.execute('SELECT * FROM runtime_confirmations WHERE id=?',(cid,)).fetchone()
            return self._public(row)

    def _public(self,row,*,detail=False):
        value=json.loads(row['binding'])
        state='expired' if self._expired(row) and row['state'] in ('pending','approved','answered') else row['state']
        result={'reference':row['id'],'status':state,'kind':row['kind'],'bindingDigest':row['fingerprint'],
                'agentId':row['actor'],'operationId':row['operation'],'expiresAt':stamp(row['expires']) if row['expires'] is not None else None,
                'stage':value['binding']['stage'],'action':value['binding']['action'],'source':row['source']}
        if detail:result.update(binding=value['binding'],questions=value['questions'])
        if 'deadline' in value:
            due=value['deadline']['responseDueAt']
            result.update(responseDueAt=stamp(due) if due is not None else None,
                          responseOverdue=due is not None and self.clock()>due)
            if value['deadline'].get('objectBound'):result['objectBound']=True
        return result

    def _expired(self,row):
        return row['expires'] is not None and row['expires']<=self.clock()

    def migrate_pending(self,reference,binding_digest,binding,*,deadline):
        """Trusted role only, after revalidating its real unchanged live object.

        No RPC operation exposes this. A role must not call after its job/flow
        became terminal or when another operation superseded this stage.
        Previously decided/used records never qualify, even if now expired.
        """
        if not isinstance(deadline,ConfirmationDeadline):
            raise RuntimeFault('invalid_input','A verified original deadline is required')
        with self.store.transaction() as conn:
            row=conn.execute('SELECT * FROM runtime_confirmations WHERE id=?',(reference,)).fetchone()
            if not row or row['fingerprint']!=binding_digest:
                raise RuntimeFault('id_conflict','The displayed confirmation object changed')
            payload=json.loads(row['binding'])
            if encode(payload['binding'])!=encode(binding):
                raise RuntimeFault('forbidden','Original confirmation object is required')
            if 'deadline' in payload:
                if payload['deadline']!=deadline.value():
                    raise RuntimeFault('id_conflict','The original deadline changed')
                return self._public(row)
            if (row['state']!='pending' or any(row[key] is not None for key in ('answer','source','used_by'))
                    or type(payload.get('ttl')) not in (int,float) or not 0<payload['ttl']<=900):
                raise RuntimeFault('forbidden','Only an undecided unused legacy pending confirmation can migrate')
            if deadline.expires_at is not None and deadline.expires_at<=max(self.clock(),row['expires']):
                raise RuntimeFault('expired','No still-valid longer original expiry exists')
            payload.pop('ttl')
            payload['deadline']=deadline.value()
            serialized=encode(payload)
            fingerprint=hashlib.sha256(serialized.encode()).hexdigest()
            conn.execute('UPDATE runtime_confirmations SET binding=?,fingerprint=?,expires=? WHERE id=?',
                         (serialized,fingerprint,deadline.expires_at,reference))
            return self._public(conn.execute('SELECT * FROM runtime_confirmations WHERE id=?',(reference,)).fetchone())

    def get(self,reference,*,detail=False):
        with self.store.lock:
            row=self.store.db.execute('SELECT * FROM runtime_confirmations WHERE id=?',(reference,)).fetchone()
            if not row:raise RuntimeFault('not_ready','Original confirmation is unavailable')
            return self._public(row,detail=detail)

    def list(self):
        with self.store.lock:
            return [self._public(row,detail=True) for row in self.store.db.execute('SELECT * FROM runtime_confirmations ORDER BY created DESC LIMIT 100').fetchall()]

    def page(self,*,view='pending',cursor=None):
        """Bounded browser projection, never a new decision or expiry source."""
        if view not in ('pending','history'):
            raise RuntimeFault('invalid_input','Choose pending or history')
        after=None
        if cursor is not None:
            try:
                if not isinstance(cursor,str) or not 1<=len(cursor)<=1024:raise ValueError()
                raw=base64.b64decode(cursor+'='*(-len(cursor)%4),altchars=b'-_',validate=True)
                value=json.loads(raw)
                if (not isinstance(value,list) or len(value)!=3 or value[0]!=view
                    or type(value[1]) not in (int,float) or not math.isfinite(value[1])
                    or not isinstance(value[2],str) or not 1<=len(value[2])<=256):raise ValueError()
                after=value[1:]
            except (ValueError,TypeError,binascii.Error,UnicodeError,RecursionError):
                raise RuntimeFault('invalid_input','Invalid confirmation page cursor') from None
        now=self.clock()
        pending="state='pending' AND (expires IS NULL OR expires>?)"
        where=pending if view=='pending' else 'NOT ('+pending+')'
        with self.store.lock:
            count=self.store.db.execute('SELECT COUNT(*) FROM runtime_confirmations WHERE '+where,(now,)).fetchone()[0]
            pending_count=self.store.db.execute('SELECT COUNT(*) FROM runtime_confirmations WHERE '+pending,(now,)).fetchone()[0]
            args=[now]
            if after is not None:
                where='('+where+') AND (created>? OR (created=? AND id>?))'
                args.extend((after[0],after[0],after[1]))
            rows=self.store.db.execute('SELECT * FROM runtime_confirmations WHERE '+where+' ORDER BY created,id LIMIT 51',args).fetchall()
            selected=rows[:50]
            next_cursor=None
            if len(rows)>50:
                last=selected[-1]
                next_cursor=base64.urlsafe_b64encode(encode([view,last['created'],last['id']]).encode()).decode().rstrip('=')
            return {'confirmations':[self._public(row,detail=True) for row in selected],
                    'page':{'view':view,'nextCursor':next_cursor,'total':count,'pendingCount':pending_count,'size':50}}

    def user_channel(self):
        """Only the local UI controller holds this authority; never put it in RPC."""
        return self._authority

    def decide(self,reference,binding_digest,decision,*,authority,source,answers=None):
        if not isinstance(authority,str) or not hmac.compare_digest(authority,self._authority):
            raise RuntimeFault('forbidden','A trusted user channel is required')
        if not isinstance(source,str) or not source:raise RuntimeFault('invalid_input','A real user event reference is required')
        with self.store.transaction() as conn:
            row=conn.execute('SELECT * FROM runtime_confirmations WHERE id=?',(reference,)).fetchone()
            if not row or row['fingerprint']!=binding_digest:raise RuntimeFault('id_conflict','The displayed confirmation object changed')
            if self._expired(row):raise RuntimeFault('expired','Confirmation expired')
            if row['kind']=='approval':
                if decision not in ('approve','reject') or answers is not None:raise RuntimeFault('invalid_input','Choose approve or reject for this SDK action')
                state='approved' if decision=='approve' else 'rejected'
                answer=None
            else:
                questions=json.loads(row['binding'])['questions']
                if decision!='answer' or not isinstance(answers,list) or len(answers)!=len(questions):
                    raise RuntimeFault('invalid_input','Answer the original native questions')
                for question,item in zip(questions,answers):
                    if not isinstance(item,dict) or set(item)-{'selected_options','custom_input','question','card_id'}:
                        raise RuntimeFault('invalid_input','Invalid native answer fields')
                    selected=item.get('selected_options',[])
                    options={option.get('value') for option in question.get('options',[]) if isinstance(option,dict)}
                    if not isinstance(selected,list) or any(not isinstance(value,str) or value not in options for value in selected):
                        raise RuntimeFault('invalid_input','Answer does not match the displayed options')
                    if len(set(selected))!=len(selected) or (not question.get('multi_select',False) and len(selected)>1):
                        raise RuntimeFault('invalid_input','Choose the allowed number of displayed options')
                    if not isinstance(item.get('custom_input',''),str) or len(item.get('custom_input',''))>8000:
                        raise RuntimeFault('invalid_input','Invalid native custom answer')
                    if question.get('allow_custom_input') is False and item.get('custom_input',''):
                        raise RuntimeFault('invalid_input','This question only accepts displayed options; remove supplemental text')
                    if not selected and not item.get('custom_input','').strip():raise RuntimeFault('invalid_input','A real nonempty user answer is required')
                    if 'question' in item and item['question']!=question.get('question'):
                        raise RuntimeFault('id_conflict','Answer must refer to the displayed native question')
                state,answer='answered',encode(answers)
            if row['state']!='pending':
                if row['state']!=state or row['answer']!=answer:raise RuntimeFault('id_conflict','The user decision is already final')
            else:
                conn.execute('UPDATE runtime_confirmations SET state=?,answer=?,source=? WHERE id=?',(state,answer,source,reference))
            return self._public(conn.execute('SELECT * FROM runtime_confirmations WHERE id=?',(reference,)).fetchone())

    def claim(self,reference,binding):
        with self.store.transaction() as conn:
            row=conn.execute('SELECT * FROM runtime_confirmations WHERE id=?',(reference,)).fetchone()
            if not row or row['kind']!='approval' or encode(json.loads(row['binding'])['binding'])!=encode(binding):
                raise RuntimeFault('forbidden','Approval does not cover the actual object, preview and operation')
            if self._expired(row):raise RuntimeFault('expired','Approval expired before this action')
            if row['state']!='approved':raise RuntimeFault('forbidden','This action has no user approval','wait_for_user')
            if row['used_by'] not in (None,binding['operationId']):raise RuntimeFault('id_conflict','Approval is already assigned')
            conn.execute('UPDATE runtime_confirmations SET used_by=? WHERE id=?',(binding['operationId'],reference))

    def native_answer(self,reference):
        with self.store.lock:
            row=self.store.db.execute('SELECT * FROM runtime_confirmations WHERE id=?',(reference,)).fetchone()
            if not row or row['kind']!='question':raise RuntimeFault('forbidden','This is not the original native question')
            if self._expired(row):raise RuntimeFault('expired','Native user question expired')
            return json.loads(row['answer']) if row['state']=='answered' else None

    def cancel_question(self,reference):
        with self.store.transaction() as conn:
            conn.execute("UPDATE runtime_confirmations SET state='cancelled' WHERE id=? AND kind='question' AND state='pending'",(reference,))
