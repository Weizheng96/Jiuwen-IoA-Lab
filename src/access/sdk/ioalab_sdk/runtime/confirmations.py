"""Typed user decisions, bound to actual objects and one original operation."""
import hashlib
import hmac
import json
import secrets
import time
import uuid

from .protocol import RuntimeFault,encode,stamp


SCHEMA='''CREATE TABLE IF NOT EXISTS runtime_confirmations(
 id TEXT PRIMARY KEY,actor TEXT NOT NULL,operation TEXT NOT NULL,kind TEXT NOT NULL,
 binding TEXT NOT NULL,fingerprint TEXT NOT NULL,expires REAL NOT NULL,state TEXT NOT NULL,
 answer TEXT,source TEXT,used_by TEXT,created REAL NOT NULL);'''


class Confirmations:
    def __init__(self,store,*,clock=time.time):
        self.store,self.clock=store,clock
        self._authority=secrets.token_urlsafe(32)
        with store.lock:store.db.executescript(SCHEMA)

    def ensure(self,binding,*,ttl=900,kind='approval',questions=None):
        fields={'actor','operationId','subject','stage','action','preview'}
        if not isinstance(binding,dict) or set(binding)!=fields or not isinstance(binding['subject'],dict) or not isinstance(binding['preview'],dict):
            raise RuntimeFault('invalid_input','A complete confirmation binding is required')
        for field in ('actor','operationId','stage','action'):
            if not isinstance(binding[field],str) or not 1<=len(binding[field])<=256:raise RuntimeFault('invalid_input','Invalid confirmation identity')
        if kind not in ('approval','question') or type(ttl) not in (int,float) or not 0<ttl<=86400:
            raise RuntimeFault('invalid_input','Invalid confirmation kind or deadline')
        questions=[] if questions is None else questions
        if not isinstance(questions,list) or (kind=='question' and not questions):raise RuntimeFault('invalid_input','Native questions are required')
        payload={'binding':binding,'questions':questions,'ttl':ttl}
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
                now=self.clock()
                conn.execute('INSERT INTO runtime_confirmations VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                             (cid,binding['actor'],binding['operationId'],kind,serialized,fingerprint,now+ttl,'pending',None,None,None,now))
                row=conn.execute('SELECT * FROM runtime_confirmations WHERE id=?',(cid,)).fetchone()
            return self._public(row)

    def _public(self,row,*,detail=False):
        value=json.loads(row['binding'])
        state='expired' if row['expires']<=self.clock() and row['state'] in ('pending','approved','answered') else row['state']
        result={'reference':row['id'],'status':state,'kind':row['kind'],'bindingDigest':row['fingerprint'],
                'agentId':row['actor'],'operationId':row['operation'],'expiresAt':stamp(row['expires']),
                'stage':value['binding']['stage'],'action':value['binding']['action'],'source':row['source']}
        if detail:result.update(binding=value['binding'],questions=value['questions'])
        return result

    def get(self,reference,*,detail=False):
        with self.store.lock:
            row=self.store.db.execute('SELECT * FROM runtime_confirmations WHERE id=?',(reference,)).fetchone()
            if not row:raise RuntimeFault('not_ready','Original confirmation is unavailable')
            return self._public(row,detail=detail)

    def list(self):
        with self.store.lock:
            return [self._public(row,detail=True) for row in self.store.db.execute('SELECT * FROM runtime_confirmations ORDER BY created DESC LIMIT 100').fetchall()]

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
            if row['expires']<=self.clock():raise RuntimeFault('expired','Confirmation expired')
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
            if row['expires']<=self.clock():raise RuntimeFault('expired','Approval expired before this action')
            if row['state']!='approved':raise RuntimeFault('forbidden','This action has no user approval','wait_for_user')
            if row['used_by'] not in (None,binding['operationId']):raise RuntimeFault('id_conflict','Approval is already assigned')
            conn.execute('UPDATE runtime_confirmations SET used_by=? WHERE id=?',(binding['operationId'],reference))

    def native_answer(self,reference):
        with self.store.lock:
            row=self.store.db.execute('SELECT * FROM runtime_confirmations WHERE id=?',(reference,)).fetchone()
            if not row or row['kind']!='question':raise RuntimeFault('forbidden','This is not the original native question')
            if row['expires']<=self.clock():raise RuntimeFault('expired','Native user question expired')
            return json.loads(row['answer']) if row['state']=='answered' else None

    def cancel_question(self,reference):
        with self.store.transaction() as conn:
            conn.execute("UPDATE runtime_confirmations SET state='cancelled' WHERE id=? AND kind='question' AND state='pending'",(reference,))
