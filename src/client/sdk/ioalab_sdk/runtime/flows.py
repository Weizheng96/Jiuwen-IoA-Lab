"""Durable role-owned flow journal; business sequencing stays in each role."""
import copy
import threading
import uuid

from a2c.codec import digest
from .catalog import CATALOG
from .host_registry import native_source, pending_source, source_scope, source_value, source_context
from .protocol import RuntimeFault, encode


def flow_key(*parts):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, encode(list(parts))))


class FlowJournal:
    def __init__(self, runtime, actor, kind, advance, *, on_async_receipt=None):
        self.runtime, self.actor, self.kind, self.advance = runtime, actor, kind, advance
        self.store = runtime.store
        self.namespace = 'flow:' + kind + ':' + actor
        self.lock = threading.RLock()
        self.cursor = ''
        self.notice_cursor = ''
        self.on_async_receipt=on_async_receipt

    def save(self, record):
        pending=(self.on_async_receipt and record.get('nativeNoticeWatch')==record.get('operation')
                 and record.get('receipt') and record.get('nativeNoticeBaseline')!=digest(record['receipt']))
        # The original receipt and a lightweight recovery pointer commit together.
        # An inactive business receipt must survive a crash before its projection callback.
        with self.store.transaction() as conn:
            conn.execute('INSERT OR REPLACE INTO runtime_checkpoints VALUES(?,?,?)',
                         (self.namespace,record['flowRef'],encode(record)))
            if pending:
                conn.execute('INSERT OR REPLACE INTO runtime_checkpoints VALUES(?,?,?)',
                             (self.namespace+':native-pending',record['flowRef'],encode({'flowRef':record['flowRef']})))
            else:
                conn.execute('DELETE FROM runtime_checkpoints WHERE namespace=? AND key=?',
                             (self.namespace+':native-pending',record['flowRef']))

    def call(self, operation, args, actions):
        action = args.get('action', 'observe')
        value = args.get('input', {})
        if action not in (*actions, 'observe', 'resume') or not isinstance(value, dict):
            raise RuntimeFault('invalid_input', 'Use a documented business action and object input')
        if action in ('observe', 'resume') and value:
            raise RuntimeFault('invalid_input', 'Observation and recovery use only the original flowRef')
        ref = args.get('flowRef')
        if ref is not None and (not isinstance(ref, str) or not ref):
            raise RuntimeFault('invalid_input', 'Use an original flowRef')
        if ref is not None:
            try:
                if str(uuid.UUID(ref)) != ref:
                    raise ValueError()
            except ValueError:
                raise RuntimeFault('invalid_input', 'Use the exact original flowRef') from None
        with self.lock:
            request_key = 'request:' + operation
            original = self.store.checkpoint(self.namespace, request_key)
            if original and original['fingerprint'] != digest(args):
                raise RuntimeFault('id_conflict', 'Original flow invocation changed')
            if original:
                record = self.store.checkpoint(self.namespace, original['flowRef'])
            elif ref:
                record = self.store.checkpoint(self.namespace, ref)
                if not record:
                    raise RuntimeFault('forbidden', 'Flow belongs to another local role or identity')
            else:
                if action in ('observe', 'resume'):
                    raise RuntimeFault('invalid_input', 'An original flowRef is required')
                ref = flow_key(self.kind, self.actor, operation)
                record = {'flowRef': ref, 'context': {}, 'action': None, 'results': {}, 'active': False, 'nativeSource': None, 'sourcePending': getattr(self.runtime,'native_entry_required',False)}
            if not original and action not in ('observe', 'resume'):
                if record['active']:
                    raise RuntimeFault('not_ready', 'Resume the pending action before supplying a new business decision')
                record.update(action=action, input=copy.deepcopy(value), operation=operation,
                              results={}, active=True, stage=0)
            # Invocation binding and its initial state commit together, before effects.
            self.store.checkpoint_update(self.namespace, {record['flowRef']: record, request_key: {'fingerprint': digest(args), 'flowRef': record['flowRef']}})
            return self.drive(record,notify=False)

    def drive(self, record, advance=None, *, notify=True):
        try:
            with source_scope(source_context(record.get('nativeSource'),actor=self.actor) or pending_source()):
                result=self._drive(record,advance)
        except RuntimeFault:
            result=record.get('receipt')
            if notify and result and result['status']=='rejected' and not record.get('active'):
                self._notify(record,result)
            raise
        if not notify:
            record['nativeNoticeBaseline']=digest(result)
            record['nativeNoticeWatch']=record['operation'] if record.get('active') else None
        else:self._notify(record,result)
        self.save(record)
        return result

    def _notify(self,record,result):
        baseline=digest(result)
        if (record.get('nativeNoticeWatch')==record.get('operation') and
            record.get('nativeNoticeBaseline')!=baseline and self.on_async_receipt):
            self.on_async_receipt(self,record,result)
            record['nativeNoticeBaseline']=baseline
            self.save(record)

    def _drive(self, record, advance=None):
        if not record['active'] and record.get('receipt'):
            return {**copy.deepcopy(record['receipt']), 'data': {**copy.deepcopy(record['receipt']['data']), **copy.deepcopy(record['context'])}}
        try:
            result = (advance or self.advance)(self, record)
        except FlowWait as exc:
            result = exc.receipt
        except RuntimeFault as exc:
            if exc.code not in ('unknown_result', 'result_unknown', 'not_ready', 'closed'):
                record['active'] = False
                record['receipt'] = self.receipt(record,'rejected',exc.code,error={'code':exc.code,'message':str(exc),'recovery':exc.recovery})
                self.save(record)
                raise
            if exc.code == 'closed':
                record['active'] = False
            result = self.receipt(record, 'unknown' if exc.code in ('unknown_result', 'result_unknown') else 'waiting',
                                  exc.code, error={'code': exc.code, 'message': str(exc), 'recovery': exc.recovery})
        record['receipt'] = result
        self.save(record)
        return copy.deepcopy(result)

    def step(self, record, index, name, args, invoke):
        """Persist the exact child call, then consume its actual job if asynchronous."""
        required, optional = CATALOG[name].inputs[0]
        if not set(required) <= set(args) or set(args) - set(required) - set(optional):
            raise RuntimeFault('invalid_input', 'Use documented input fields for ' + name)
        key = str(index) + ':' + name
        child = flow_key(record['flowRef'], record['operation'], key)
        entry = record['results'].get(key)
        if entry is None:
            if getattr(self.runtime,'native_entry_required',False) and CATALOG[name].effect != 'read' and not record.get('nativeSource'):
                if not record.get('sourcePending'):
                    raise RuntimeFault('not_ready','Explicitly recover the original historical source before a new effect','migrate_host')
                verified=native_source(self.actor)
                if verified is None:raise RuntimeFault('not_ready','Verified native source context is required for a new local effect','connect_native_context')
                record['nativeSource']=source_value(verified);record['sourcePending']=False
                self.save(record)
            entry = {'operationId': child, 'name': name, 'args': copy.deepcopy(args)}
            record['results'][key] = entry
            self.save(record)
        elif entry['args'] != args:
            raise RuntimeFault('id_conflict', 'Original flow step changed')
        if record.get('nativeSource'):
            owner={'kind':'flow','namespace':self.namespace,'flowRef':record['flowRef'],'operation':record['operation']}
            owner_key=encode([self.actor,child])
            with self.store.transaction() as conn:
                row=conn.execute('SELECT value FROM runtime_checkpoints WHERE namespace=? AND key=?',('native-notice-owners',owner_key)).fetchone()
                if row and row['value']!=encode(owner):raise RuntimeFault('identity_conflict','The original notice event owner changed')
                conn.execute('INSERT OR IGNORE INTO runtime_checkpoints VALUES(?,?,?)',('native-notice-owners',owner_key,encode(owner)))
        result = entry.get('result')
        if not result or result['status'] not in ('completed', 'rejected', 'failed', 'unknown'):
            with source_scope(source_context(record.get("nativeSource"),actor=self.actor) or pending_source()):
                result = invoke(name, child, args)
            data = result.get('data') or {}
            job = data.get('job') if isinstance(data, dict) else None
            reference = data.get('reference') if isinstance(data, dict) else None
            job_ref = job.get('reference') if isinstance(job, dict) else None
            if isinstance(reference, dict) and reference.get('kind') == 'job':
                job_ref = reference['id']
            if job_ref:
                actual = self.runtime.scheduler.get(job_ref)
                result = {'status': actual['status'], 'data': {**data, **(actual.get('data') or {})}}
            elif name == 'assistant.messaging.send' and result['status'] == 'accepted':
                result = {**result, 'status': 'completed'}
            elif isinstance(data, dict) and data.get('operationRef'):
                actual = self.runtime._delivery_operation({'reference': data['operationRef']})
                result = {'status':actual['status'], 'data':{**data, 'delivery':actual}}
            entry['result'] = result
            self.save(record)
        # Unknown jobs are reconciled by their existing owner, never requeued here.
        if result['status'] == 'unknown':
            data = result.get('data') or {}
            job = data.get('job') or {}
            if job.get('reference'):
                actual = self.runtime.scheduler.get(job['reference'])
                if actual['status'] != 'unknown':
                    result = entry['result'] = {'status': actual['status'], 'data': {**data, **(actual.get('data') or {})}}
        if result['status'] == 'completed':
            record['stage'] = max(record['stage'], index + 1)
            self.save(record)
            return result.get('data')
        if result['status'] in ('rejected', 'failed'):
            record['active'] = False
        reason = ('confirmation' if isinstance(result.get('data'), dict) and result['data'].get('confirmationRef') else
                  'unknown_result' if result['status'] == 'unknown' else 'external_processing')
        # The sole background reminder opens the page for every new pending row;
        # old entry uiAttempted/uiOpened fields stay as history only.
        raise FlowWait(self.receipt(record, result['status'] if result['status'] in ('rejected', 'failed', 'unknown') else 'waiting', reason,
                            evidence=result.get('data')))

    def receipt(self, record, status='waiting', reason='business_input', *, required=(), **data):
        return {'status': status, 'data': {'flowRef': record['flowRef'], 'action': record['action'],
                'reason': reason, 'required': list(required), 'continue': {'action': 'resume', 'flowRef': record['flowRef']},
                **copy.deepcopy(record['context']), **data}, 'nextActions': []}

    def scan(self):
        with self.lock:
            page=self.store.checkpoint_page(self.namespace,after=self.cursor,active_only=True)
            if not page:page=self.store.checkpoint_page(self.namespace,active_only=True)
            if page:self.cursor=page[-1][0]
            pending=self.store.checkpoint_page(self.namespace+':native-pending',after=self.notice_cursor)
            if not pending:pending=self.store.checkpoint_page(self.namespace+':native-pending')
            if pending:self.notice_cursor=pending[-1][0]
            selected=dict(page)
            for key,_ in pending:
                record=self.store.checkpoint(self.namespace,key)
                if record:selected[key]=record
            for key,record in selected.items():
                if key.startswith(('request:','delivery:')):continue
                try:self.drive(record)
                except RuntimeFault:continue


class FlowWait(Exception):
    def __init__(self, receipt):
        self.receipt = receipt
