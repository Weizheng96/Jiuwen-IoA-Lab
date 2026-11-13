"""Durable ordinary-input forms; roles own previews/effects, Confirmations owns approval."""
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import threading
from typing import Callable
import uuid

from jsonschema import Draft202012Validator

from .ipc import public_error
from .protocol import RuntimeFault, encode


@dataclass(frozen=True)
class FormDefinition:
    kind: str
    title: str
    schema: dict
    preview: Callable
    submit: Callable


@dataclass(frozen=True)
class WorkflowDefinition(FormDefinition):
    """A role-owned sequence; callbacks also receive its saved ordinary context."""


def check_schema(schema):
    Draft202012Validator.check_schema(schema)
    def visit(value):
        if isinstance(value, dict):
            if any(key in value for key in ('$ref', '$dynamicRef', '$id')):
                raise RuntimeFault('invalid_input', 'Form schemas must be self-contained')
            for child in value.values(): visit(child)
        elif isinstance(value, list):
            for child in value: visit(child)
    visit(schema)


def draft_schema(schema):
    """Allow incomplete nested fields while preserving their types and local constraints."""
    if not isinstance(schema, dict): return schema
    result = deepcopy(schema)
    result.pop('required', None)
    result.pop('dependentRequired', None)
    for key in ('allOf', 'anyOf', 'oneOf', 'not', 'if', 'then', 'else', 'dependentSchemas', 'contains', 'minContains'):
        result.pop(key, None)
    for key in ('properties', 'patternProperties', 'dependentSchemas'):
        if isinstance(result.get(key), dict): result[key] = {name: draft_schema(value) for name, value in result[key].items()}
    for key in ('items', 'additionalProperties', 'contains', 'if', 'then', 'else', 'not'):
        if key in result: result[key] = draft_schema(result[key])
    for key in ('prefixItems', 'allOf', 'anyOf', 'oneOf'):
        if isinstance(result.get(key), list): result[key] = [draft_schema(value) for value in result[key]]
    return result


class InteractionForms:
    def __init__(self, store, definitions, *, can_advance=lambda: False, decision_ready=lambda _: False):
        self.store = store
        self.definitions = {}
        self._lock = threading.RLock()
        self.can_advance, self.decision_ready = can_advance, decision_ready
        self._stop = threading.Event()
        self._thread = None
        for definition in definitions:
            if definition.kind in self.definitions:
                raise ValueError('Duplicate form kind')
            Draft202012Validator.check_schema(definition.schema)
            # All form schemas are local assets, never a way to fetch a remote reference.
            def check(value):
                if isinstance(value, dict):
                    if any(key in value for key in ('$ref', '$dynamicRef', '$id')):
                        raise ValueError('Form schemas must be self-contained')
                    for child in value.values(): check(child)
                elif isinstance(value, list):
                    for child in value: check(child)
            check(definition.schema)
            self.definitions[definition.kind] = definition

    def start(self):
        self._thread = threading.Thread(target=self._advance, name='ioa-workflows', daemon=True)
        self._thread.start()

    def close(self):
        self._stop.set()
        if self._thread:
            self._thread.join(20)
            if self._thread.is_alive(): raise RuntimeError('Workflow work is still active; preserve client ownership')

    def _advance(self):
        while not self._stop.wait(1):
            if not self.can_advance(): continue
            with self._lock: records = list(self.store.checkpoints('forms').values())
            for record in records:
                if self._stop.is_set() or not self.can_advance(): break
                try:
                    confirmation = ((record.get('result') or {}).get('data') or {}).get('confirmationRef')
                    advancing = isinstance(self.definitions.get(record['kind']), WorkflowDefinition) and record['phase'] in ('accepted', 'running')
                    if not advancing and not (
                            record['phase'] == 'waiting' and confirmation and self.decision_ready(confirmation)): continue
                    self.submit(record['reference'], record['revision'])
                except RuntimeFault: pass  # The durable form keeps the actual denial/unknown result.
                except Exception: pass  # submit persisted unknown before entering the role.

    def _definition(self, kind):
        if not isinstance(kind, str) or kind not in self.definitions:
            raise RuntimeFault('not_ready', 'This instance does not provide the requested form', 'configure_role')
        return self.definitions[kind]

    @staticmethod
    def _digest(definition):
        return hashlib.sha256(encode({'kind': definition.kind, 'schema': definition.schema}).encode()).hexdigest()

    @staticmethod
    def _validate(definition, values, *, complete=False, schema=None):
        try:
            valid_size = isinstance(values, dict) and len(encode(values).encode()) <= 96 * 1024
        except (TypeError, ValueError, RecursionError):
            valid_size = False
        if not valid_size:
            raise RuntimeFault('invalid_input', 'Provide a bounded object of ordinary form fields')
        schema = schema or definition.schema
        schema = schema if complete else draft_schema(schema)
        errors = list(Draft202012Validator(schema).iter_errors(values))
        if errors:
            # ValidationError.message can quote user input. Report only field location and rule.
            issue = errors[0]
            field = '.'.join(str(part) for part in issue.absolute_path) or 'form'
            raise RuntimeFault('invalid_input', f'Check {field}: {issue.validator}', 'edit_form')

    def _read(self, reference):
        if not isinstance(reference, str) or len(reference) > 64:
            raise RuntimeFault('invalid_input', 'Use the original form reference')
        record = self.store.checkpoint('forms', reference)
        if record is None:
            raise RuntimeFault('not_ready', 'The original form is not stored in this instance')
        return record

    def _view(self, record):
        definition = self._definition(record['kind'])
        return {**{key: deepcopy(value) for key, value in record.items() if key not in ('initialDigest', 'definitionDigest', 'context', 'history')},
                'completedSteps': len(record.get('history', [])),
                'title': record.get('title', definition.title), 'schema': deepcopy(record.get('schema', definition.schema)),
                'readOnly': record['phase'] not in ('draft', 'previewed')}

    def _current(self, reference, revision):
        record = self._read(reference)
        if type(revision) is not int or record['revision'] != revision:
            raise RuntimeFault('id_conflict', 'Form revision changed; reload the original form', 'reload_form')
        definition = self._definition(record['kind'])
        if self._digest(definition) != record['definitionDigest']:
            raise RuntimeFault('id_conflict', 'Form definition changed; review the saved input in a new form', 'review_form')
        return record, definition

    def list(self):
        with self._lock:
            return {'definitions': [{'kind': item.kind, 'title': item.title, 'schema': deepcopy(item.schema)}
                                    for item in self.definitions.values()],
                    'forms': [{'reference': item['reference'], 'kind': item['kind'],
                               'revision': item['revision'], 'phase': item['phase']}
                              for item in self.store.checkpoints('forms').values()]}

    def open(self, operation_id, kind, values):
        definition = self._definition(kind)
        self._validate(definition, values)
        reference = str(uuid.uuid5(uuid.NAMESPACE_URL, encode(['ioa-form', self.store.runtime_id, operation_id])))
        fingerprint = hashlib.sha256(encode({'kind': kind, 'values': values}).encode()).hexdigest()
        with self._lock:
            record = self.store.checkpoint('forms', reference)
            if record and record['initialDigest'] != fingerprint:
                raise RuntimeFault('id_conflict', 'Original form opening parameters changed')
            if record is None:
                record = {'reference': reference, 'kind': kind, 'revision': 0, 'values': deepcopy(values),
                          'initialDigest': fingerprint, 'definitionDigest': self._digest(definition),
                          'phase': 'draft', 'preview': None, 'result': None}
                self.store.checkpoint_set('forms', reference, record)
            return self._view(record)

    def get(self, reference):
        with self._lock: return self._view(self._read(reference))

    def update(self, reference, revision, values):
        with self._lock:
            record, definition = self._current(reference, revision)
            if record['phase'] not in ('draft', 'previewed'):
                raise RuntimeFault('id_conflict', 'Submitted input is frozen; recover this operation or start a new draft')
            self._validate(definition, values, schema=record.get('schema'))
            record = {**record, 'revision': revision + 1, 'values': deepcopy(values),
                      'phase': 'draft', 'preview': None, 'result': None}
            self.store.checkpoint_set('forms', reference, record)
            return self._view(record)

    @staticmethod
    def _operation(record):
        return str(uuid.uuid5(uuid.NAMESPACE_URL, encode(['ioa-form-revision', record['reference'], record['revision']])))

    def preview(self, reference, revision):
        with self._lock:
            old = self._read(reference)
            if any(item['revision'] == revision for item in old.get('history', [])): return self._view(old)
            record, definition = self._current(reference, revision)
            if record['preview'] is not None: return self._view(record)
            self._validate(definition, record['values'], complete=True, schema=record.get('schema'))
            arguments = [self._operation(record), deepcopy(record['values'])]
            if isinstance(definition, WorkflowDefinition): arguments.append(deepcopy(record.get('context', {})))
            prepared = definition.preview(*arguments)
            record = {**record, 'phase': 'previewed', 'preview': prepared}
            self.store.checkpoint_set('forms', reference, record)
            return self._view(record)

    def submit(self, reference, revision):
        with self._lock:
            old = self._read(reference)
            if any(item['revision'] == revision for item in old.get('history', [])): return self._view(old)
            record, definition = self._current(reference, revision)
            if record['preview'] is None:
                raise RuntimeFault('invalid_input', 'Preview the current form before submitting', 'preview_form')
            if record['phase'] in ('completed', 'rejected', 'failed'):
                return self._view(record)
            record = {**record, 'phase': 'unknown'}
            self.store.checkpoint_set('forms', reference, record)
            try:
                arguments = [self._operation(record), deepcopy(record['preview'])]
                if isinstance(definition, WorkflowDefinition): arguments.append(deepcopy(record.get('context', {})))
                result = definition.submit(*arguments)
            except RuntimeFault as exc:
                phase = ('unknown' if exc.code in ('unknown_result', 'result_unknown') else
                         'waiting' if exc.code == 'not_ready' else 'rejected')
                record = {**record, 'phase': phase,
                          'result': {'status': phase, 'data': None, 'error': public_error(exc)}}
                self.store.checkpoint_set('forms', reference, record)
                raise
            if not isinstance(result, dict) or result.get('status') not in ('accepted', 'running', 'waiting', 'completed', 'rejected', 'failed', 'unknown'):
                raise RuntimeFault('protocol_error', 'Form handler did not return a real operation result')
            following = result.get('next') if isinstance(definition, WorkflowDefinition) else None
            result = {key: value for key, value in result.items() if key != 'next'}
            record = {**record, 'phase': result['status'], 'result': result}
            if following is not None:
                if result['status'] != 'completed' or not isinstance(following, dict):
                    raise RuntimeFault('protocol_error', 'Only a completed step can advance a workflow')
                check_schema(following['schema'])
                self._validate(definition, following.get('values', {}), schema=following['schema'])
                record = {**record, 'revision': revision + 1, 'phase': 'draft', 'preview': None,
                          'schema': deepcopy(following['schema']), 'values': deepcopy(following.get('values', {})),
                          'context': deepcopy(following['context']), 'title': following['title'],
                          'evidence': deepcopy(following.get('evidence', {})),
                          'history': [*record.get('history', []), {'revision': revision, 'result': result}]}
            self.store.checkpoint_set('forms', reference, record)
            return self._view(record)
