"""Durable role jobs, with interaction/session exclusion and a separate model limit."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
import json
import threading
import time
from typing import Callable
import uuid

from .protocol import RuntimeFault,encode

CURRENT_JOB=ContextVar('ioa-current-job',default=None)


SCHEMA='''CREATE TABLE IF NOT EXISTS runtime_jobs(
 id TEXT PRIMARY KEY,actor TEXT NOT NULL,interaction TEXT NOT NULL,session TEXT,
 handler TEXT NOT NULL,revision TEXT NOT NULL,payload TEXT NOT NULL,fingerprint TEXT NOT NULL,
 state TEXT NOT NULL,created REAL NOT NULL,deadline REAL,result TEXT,continuation TEXT);
CREATE INDEX IF NOT EXISTS runtime_jobs_state ON runtime_jobs(state,created);
CREATE TABLE IF NOT EXISTS runtime_job_resumptions(
 actor TEXT NOT NULL,id TEXT NOT NULL,job TEXT NOT NULL,continuation TEXT NOT NULL,receipt TEXT NOT NULL,
 PRIMARY KEY(actor,id));'''


@dataclass(frozen=True)
class JobOutcome:
    state: str
    data: dict
    session: str|None=None


@dataclass(frozen=True)
class JobHandler:
    run: Callable
    revision: str
    uses_model: bool=True
    ready: Callable|None=None
    control: bool=False


class Scheduler:
    def __init__(self,store,handlers,*,max_models=2,max_pending=128,max_workers=None,can_dispatch=lambda:True):
        max_workers=max_workers if max_workers is not None else max(8,max_models+2)
        if any(type(n) is not int or n<1 for n in (max_models,max_pending,max_workers)):
            raise ValueError('Positive scheduler limits are required')
        if any(not isinstance(name,str) or not isinstance(h,JobHandler) or not h.revision or not callable(h.run)
               or h.control and h.uses_model for name,h in handlers.items()):raise ValueError('Register explicit local handlers; control handlers cannot run models')
        self.store,self.handlers=store,dict(handlers)
        self.max_models,self.max_pending,self.max_workers=max_models,max_pending,max_workers
        self.can_dispatch=can_dispatch
        self._condition=threading.Condition(threading.RLock())
        self._active={}
        self._observation_locks={}
        self._stop=threading.Event()
        self._draining=False
        self._thread=None
        self.error=None
        self._pool=ThreadPoolExecutor(max_workers=max_workers,thread_name_prefix='ioa-role')
        with store.lock:store.db.executescript(SCHEMA)
        with store.transaction() as conn:
            if 'model_waiting' not in {row[1] for row in conn.execute('PRAGMA table_info(runtime_jobs)')}:
                conn.execute('ALTER TABLE runtime_jobs ADD COLUMN model_waiting INTEGER NOT NULL DEFAULT 0')
            # A process boundary is not evidence that an external action failed.
            conn.execute("UPDATE runtime_jobs SET state='unknown',result=? WHERE state='running'",
                         (encode({'error':{'code':'unknown_result','recovery':'reconcile_role'}}),))

    def start(self):
        if self._thread:raise RuntimeError('Scheduler is already started')
        self._thread=threading.Thread(target=self._loop,name='ioa-dispatch',daemon=True)
        self._thread.start()
        return self

    def submit(self,actor,interaction,handler,payload,*,operation_id,session=None,deadline=None,admitted=False):
        if handler not in self.handlers:raise RuntimeFault('not_ready','Role handler is unavailable','configure_role')
        if not isinstance(interaction,str) or not interaction or not isinstance(operation_id,str) or not 1<=len(operation_id)<=128:
            raise RuntimeFault('invalid_input','A stable interaction and operation are required')
        if not isinstance(payload,dict) or (session is not None and (not isinstance(session,str) or not session)):
            raise RuntimeFault('invalid_input','Invalid role job data')
        if deadline is not None and (type(deadline) not in (int,float) or not 0<deadline<float('inf')):
            raise RuntimeFault('invalid_input','Invalid local execution deadline')
        revision=self.handlers[handler].revision
        if type(admitted) is not bool:raise ValueError('Admission is an internal role decision')
        original=encode({'actor':actor,'interaction':interaction,'handler':handler,'revision':revision,
                         'payload':payload,'session':session,'deadline':deadline,**({'admitted':True} if admitted else {})})
        fingerprint=hashlib.sha256(original.encode()).hexdigest()
        job=self.reference(actor,handler,operation_id)
        with self.store.transaction() as conn:
            row=conn.execute('SELECT * FROM runtime_jobs WHERE id=?',(job,)).fetchone()
            if row:
                if row['fingerprint']!=fingerprint:raise RuntimeFault('id_conflict','Original job parameters changed')
                return self._public(row)
            if self._draining:raise RuntimeFault('closed','Runtime is draining; no new jobs are accepted')
            if not conn.execute('SELECT 1 FROM runtime_cursors WHERE actor=?',(actor,)).fetchone():
                raise RuntimeFault('forbidden','Job identity is outside this instance')
            count=conn.execute("SELECT COUNT(*) FROM runtime_jobs WHERE state IN ('queued','running','waiting','unknown')").fetchone()[0]
            if count>=self.max_pending and not admitted:raise RuntimeFault('quota_exceeded','Local pending queue is full','wait_for_capacity')
            conn.execute('INSERT INTO runtime_jobs(id,actor,interaction,session,handler,revision,payload,fingerprint,state,created,deadline,result,continuation) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                         (job,actor,interaction,session,handler,revision,encode(payload),fingerprint,
                          'queued',time.time(),deadline,None,None))
            result=self._public(conn.execute('SELECT * FROM runtime_jobs WHERE id=?',(job,)).fetchone())
        with self._condition:self._condition.notify_all()
        return result

    @staticmethod
    def reference(actor,handler,operation):
        return str(uuid.uuid5(uuid.NAMESPACE_URL,encode(['ioa-job',actor,handler,operation])))

    def bind_session(self,job,session):
        if not isinstance(session,str) or not session:raise RuntimeFault('protocol_error','A real host session is required')
        with self.store.transaction() as conn:
            row=conn.execute('SELECT session,state,interaction,handler FROM runtime_jobs WHERE id=?',(job,)).fetchone()
            if not row or row['state']!='running':raise RuntimeFault('not_ready','Job is not currently executing')
            if self.handlers[row['handler']].control:raise RuntimeFault('forbidden','A protocol control handler cannot bind or execute a host session')
            if row['session'] and row['session']!=session:raise RuntimeFault('identity_conflict','Host session changed')
            conflicts=conn.execute("SELECT handler,interaction FROM runtime_jobs WHERE id!=? AND session=? AND state IN ('running','waiting','unknown')",(job,session)).fetchall()
            if any(other['interaction']!=row['interaction'] or other['handler'] not in self.handlers or not self.handlers[other['handler']].control for other in conflicts):
                raise RuntimeFault('not_ready','Another unresolved job owns this host session')
            conn.execute('UPDATE runtime_jobs SET session=? WHERE id=?',(session,job))

    @staticmethod
    def _public(row):
        return {'reference':row['id'],'agentId':row['actor'],'interactionId':row['interaction'],
                'sessionId':row['session'],'status':row['state'],'data':json.loads(row['result']) if row['result'] else None}

    def get(self,job):
        with self.store.lock:
            row=self.store.db.execute('SELECT * FROM runtime_jobs WHERE id=?',(job,)).fetchone()
            if not row:raise RuntimeFault('not_ready','Local job does not exist')
            return self._public(row)

    def records(self,actor,handlers,*,reference=None):
        """Private role-owned job snapshots; IPC callers never select arbitrary handlers."""
        handlers=tuple(handlers)
        if not handlers or any(name not in self.handlers for name in handlers):raise ValueError('Installed handlers are required')
        sql='SELECT * FROM runtime_jobs WHERE actor=? AND handler IN ('+','.join('?' for _ in handlers)+')'
        arguments=[actor,*handlers]
        if reference is not None:sql+=' AND id=?';arguments.append(reference)
        with self.store.lock:rows=self.store.db.execute(sql+' ORDER BY created DESC,id DESC LIMIT 100',arguments).fetchall()
        return [{**dict(row),'payload':json.loads(row['payload']),
                 'continuation':json.loads(row['continuation']) if row['continuation'] else None} for row in rows]

    def resumption(self,actor,operation,job):
        with self.store.lock:
            row=self.store.db.execute('SELECT job,receipt FROM runtime_job_resumptions WHERE actor=? AND id=?',(actor,operation)).fetchone()
        if row and row['job']!=job:raise RuntimeFault('id_conflict','Original resume operation targets another task')
        return json.loads(row['receipt']) if row else None

    def requeue(self,job,operation,continuation):
        """Only the installed role may call after reconciling the original effect scope."""
        if not isinstance(continuation,dict) or not isinstance(operation,str) or not 1<=len(operation)<=128:
            raise RuntimeFault('invalid_input','A verified original continuation and operation are required')
        encoded=encode(continuation)
        with self._condition:
            with self.store.transaction() as conn:
                row=conn.execute('SELECT * FROM runtime_jobs WHERE id=?',(job,)).fetchone()
                if not row:raise RuntimeFault('not_ready','Original job is unavailable')
                existing=conn.execute('SELECT job,receipt FROM runtime_job_resumptions WHERE actor=? AND id=?',(row['actor'],operation)).fetchone()
                if existing:
                    if existing['job']!=job:raise RuntimeFault('id_conflict','Original resume operation targets another task')
                    return json.loads(existing['receipt'])
                if self._draining or job in self._active:raise RuntimeFault('not_ready','Original executor has not released this job')
                handler=self.handlers.get(row['handler'])
                if row['state']!='unknown' or not handler or row['revision']!=handler.revision:
                    raise RuntimeFault('id_conflict','Only the original unknown job can be continued')
                if row['deadline'] is not None and row['deadline']<=time.time():raise RuntimeFault('expired','Original job deadline passed')
                receipt={'taskRef':job,'operationId':operation,'queuedAt':time.time()}
                conn.execute('INSERT INTO runtime_job_resumptions VALUES(?,?,?,?,?)',(row['actor'],operation,job,encoded,encode(receipt)))
                conn.execute("UPDATE runtime_jobs SET state='queued',continuation=?,model_waiting=0 WHERE id=?",(encoded,job))
            self._condition.notify_all()
        return receipt

    def status(self):
        with self.store.lock:
            counts=dict(self.store.db.execute('SELECT state,COUNT(*) FROM runtime_jobs GROUP BY state').fetchall())
            user_waits=self.store.db.execute("SELECT COUNT(*) FROM runtime_jobs WHERE state='running' AND model_waiting=1").fetchone()[0]
        with self._condition:
            return {'states':counts,'active':len(self._active),'draining':self._draining,'maxModelRuns':self.max_models,
                    'maxPendingRequests':self.max_pending,'waitingForUser':user_waits,'error':self.error}

    def require_model(self,actor,interaction):
        current=CURRENT_JOB.get()
        if not current or current[0] is not self:raise RuntimeFault('forbidden','Model observations require the current local model job')
        with self.store.lock:row=self.store.db.execute('SELECT actor,interaction,handler,state,model_waiting FROM runtime_jobs WHERE id=?',(current[1],)).fetchone()
        handler=self.handlers.get(row['handler']) if row else None
        if not row or row['actor']!=actor or row['interaction']!=interaction or row['state']!='running' or row['model_waiting'] or not handler or not handler.uses_model or handler.control:
            raise RuntimeFault('forbidden','This job has no model permit for the requested identity and interaction')
        return current[1]

    @contextmanager
    def observation(self,actor,interaction):
        job=self.require_model(actor,interaction)
        with self._condition:lock=self._observation_locks.setdefault(job,threading.Lock())
        with lock:
            self.require_model(actor,interaction)
            yield job

    @contextmanager
    def user_wait(self,job,*,cancelled=lambda:False):
        """Keep the original child/session alive while yielding only its model slot."""
        with self._condition,self.store.transaction() as conn:
            row=conn.execute('SELECT state,model_waiting FROM runtime_jobs WHERE id=?',(job,)).fetchone()
            if not row or row['state']!='running' or row['model_waiting']:
                raise RuntimeFault('id_conflict','Only the current running job can await its native user')
            conn.execute('UPDATE runtime_jobs SET model_waiting=1 WHERE id=?',(job,))
            self._condition.notify_all()
        try:yield
        except BaseException:
            # The original child is aborting, so there is no model round to resume.
            # Its executing handler still owns the interaction until its outcome.
            raise
        else:
            # Answering a question does not grant extra model concurrency or a stale lease.
            with self._condition:
                while True:
                    if self._stop.is_set() or self._draining or cancelled():
                        raise RuntimeFault('closed','Runtime stopped while the original user question was waiting')
                    with self.store.transaction() as conn:
                        running=conn.execute("SELECT handler FROM runtime_jobs WHERE state='running' AND model_waiting=0").fetchall()
                        models=sum(self.handlers[row['handler']].uses_model for row in running if row['handler'] in self.handlers)
                        if self.can_dispatch() and models<self.max_models:
                            conn.execute('UPDATE runtime_jobs SET model_waiting=0 WHERE id=?',(job,))
                            self._condition.notify_all()
                            break
                    self._condition.wait(.1)

    def resume(self,job,continuation):
        """Only a role/host owner calls this after validating its real continuation."""
        if not isinstance(continuation,dict):raise ValueError('A validated continuation is required')
        with self.store.transaction() as conn:
            row=conn.execute('SELECT state,continuation FROM runtime_jobs WHERE id=?',(job,)).fetchone()
            if not row:raise RuntimeFault('not_ready','Original job is unavailable')
            if row['state']!='waiting':raise RuntimeFault('id_conflict','Only a verified waiting job can resume')
            conn.execute("UPDATE runtime_jobs SET state='queued',continuation=? WHERE id=?",(encode(continuation),job))
        with self._condition:self._condition.notify_all()

    def resolve(self,job,outcome):
        """The role reconciles original effects; untrusted IPC cannot invent a result."""
        self._validate_outcome(outcome)
        with self.store.transaction() as conn:
            row=conn.execute('SELECT * FROM runtime_jobs WHERE id=?',(job,)).fetchone()
            if not row or row['state']!='unknown':raise RuntimeFault('id_conflict','Job is not awaiting reconciliation')
            self._save_outcome(conn,row,outcome)
        with self._condition:self._condition.notify_all()

    def reject_wait(self,job,data):
        """A local owner has verified rejection/expiry of its pending approval."""
        with self.store.transaction() as conn:
            row=conn.execute('SELECT * FROM runtime_jobs WHERE id=?',(job,)).fetchone()
            if not row or row['state']!='waiting':raise RuntimeFault('id_conflict','Only the original waiting job can be rejected')
            self._save_outcome(conn,row,JobOutcome('rejected',data))
        with self._condition:self._condition.notify_all()

    @staticmethod
    def _validate_outcome(value):
        if not isinstance(value,JobOutcome) or value.state not in ('waiting','completed','rejected','failed','unknown') or not isinstance(value.data,dict):
            raise ValueError('Role handler must return an explicit execution outcome')
        if value.session is not None and (not isinstance(value.session,str) or not value.session):raise ValueError('Invalid host session')
        encode(value.data)

    def _save_outcome(self,conn,row,outcome):
        # The real session can be learned by an event callback during this turn.
        current=conn.execute('SELECT session FROM runtime_jobs WHERE id=?',(row['id'],)).fetchone()
        row={**dict(row),'session':current['session']}
        if row['session'] and outcome.session and row['session']!=outcome.session:
            raise RuntimeFault('identity_conflict','Execution changed its fixed host session')
        conn.execute('UPDATE runtime_jobs SET state=?,result=?,session=?,model_waiting=0 WHERE id=?',
                     (outcome.state,encode(outcome.data),outcome.session or row['session'],row['id']))

    def _next(self):
        with self.store.transaction() as conn:
            held=conn.execute("SELECT id,interaction,session,handler,state,model_waiting FROM runtime_jobs WHERE state IN ('running','waiting','unknown')").fetchall()
            models=sum(self.handlers[r['handler']].uses_model for r in held
                       if r['state']=='running' and not r['model_waiting'] and r['handler'] in self.handlers)
            business_threads=sum(not self.handlers[r['handler']].control for r in held if r['id'] in self._active and r['handler'] in self.handlers)
            for row in conn.execute("SELECT * FROM runtime_jobs WHERE state='queued' ORDER BY created,id").fetchall():
                handler=self.handlers.get(row['handler'])
                if row['deadline'] is not None and row['deadline']<=time.time():
                    conn.execute("UPDATE runtime_jobs SET state='failed',result=? WHERE id=?",(encode({'error':{'code':'expired'}}),row['id']))
                    continue
                if not handler or row['revision']!=handler.revision or handler.ready and not handler.ready():continue
                blocked=False
                for other in held:
                    if other['interaction']!=row['interaction'] and not (row['session'] and row['session']==other['session']):continue
                    prior=self.handlers.get(other['handler'])
                    # Only installed non-model controls for this same interaction
                    # may receive facts while its business execution is unresolved.
                    # Controls still serialize with controls; business never bypasses.
                    if handler.control and prior and not prior.control and other['interaction']==row['interaction']:continue
                    blocked=True
                    break
                if blocked:continue
                if handler.uses_model and models>=self.max_models:continue
                if not handler.control and business_threads>=max(1,self.max_workers-2):continue
                conn.execute("UPDATE runtime_jobs SET state='running' WHERE id=?",(row['id'],))
                return dict(row)
        return None

    def _loop(self):
        try:self._dispatch()
        except Exception:
            self.error={'code':'not_ready','recovery':'inspect_local_storage'}

    def _dispatch(self):
        while not self._stop.is_set():
            with self._condition:
                if self._draining or len(self._active)>=self.max_workers or not self.can_dispatch():
                    self._condition.wait(.1)
                    continue
                row=self._next()
                if row:
                    future=self._pool.submit(self._execute,row)
                    self._active[row['id']]=future
                    future.add_done_callback(lambda _,job=row['id']:self._finished(job))
                else:self._condition.wait(.1)

    def _execute(self,row):
        token=CURRENT_JOB.set((self,row['id']))
        try:self._run_job(row)
        finally:CURRENT_JOB.reset(token)

    def _run_job(self,row):
        job={**row,'payload':json.loads(row['payload']),
             'continuation':json.loads(row['continuation']) if row['continuation'] else None,
             'previous':json.loads(row['result']) if row['result'] else None}
        try:
            outcome=self.handlers[row['handler']].run(job)
            self._validate_outcome(outcome)
            with self.store.transaction() as conn:self._save_outcome(conn,row,outcome)
        except Exception as exc:
            # Even an exception after a tool call cannot prove that its effect failed.
            with self.store.transaction() as conn:
                conn.execute("UPDATE runtime_jobs SET state='unknown',result=? WHERE id=?",
                             (encode({'error':{'code':'unknown_result','type':type(exc).__name__,'recovery':'reconcile_role'}}),row['id']))

    def _finished(self,job):
        with self._condition:
            self._active.pop(job,None)
            self._observation_locks.pop(job,None)
            self._condition.notify_all()

    def drain(self):
        with self._condition:
            self._draining=True
            self._condition.notify_all()

    @property
    def idle(self):
        with self._condition:return not self._active

    def close(self):
        self._stop.set()
        with self._condition:self._condition.notify_all()
        if self._thread:self._thread.join(5)
        self._pool.shutdown(wait=True)
