"""Signed service publications are authoritative; A2X is a rebuildable projection."""
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import time

from a2c.binding import agent_card
from a2c.codec import digest, parse_time
from a2c.jws import check_fresh, verify,header_of
from google.protobuf.json_format import MessageToDict

from ..errors import LabError


SCHEMA = '''
CREATE TABLE IF NOT EXISTS registry_publications(
 actor TEXT,revision INTEGER,digest TEXT NOT NULL,body TEXT NOT NULL,templates TEXT NOT NULL,
 created REAL NOT NULL,PRIMARY KEY(actor,revision));
CREATE TABLE IF NOT EXISTS registry_publication_operations(
 actor TEXT,operation TEXT,fingerprint TEXT NOT NULL,result TEXT NOT NULL,PRIMARY KEY(actor,operation));
CREATE TABLE IF NOT EXISTS registry_projection(
 actor TEXT PRIMARY KEY,revision INTEGER NOT NULL,category TEXT NOT NULL,indexed REAL NOT NULL);
'''


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


@dataclass(frozen=True)
class PreparedPublication:
    actor: str
    operation: str
    fingerprint: str
    body: str
    key: str


class ServicePublications:
    def __init__(self, db, identities, categories, *, clock=time.time):
        self.db, self.identities, self.categories, self.clock = db, identities, categories, clock
        with db.lock:
            db.conn.executescript(SCHEMA)

    def binding(self, owner, actor):
        row = self.identities.owned(owner, actor)
        if row['role'] != 'service':
            raise LabError('forbidden', 'A service identity is required', status=403)
        meta = json.loads(row['meta'])
        if not meta.get('providerJwk') or not meta.get('protocolId'):
            raise LabError('not_ready', 'Migrate the original service identity first', status=409)
        return row, meta

    def operation(self, owner, actor, operation):
        self.binding(owner, actor)
        row = self.db.one('SELECT result FROM registry_publication_operations WHERE actor=? AND operation=?', actor, operation)
        if not row:
            raise LabError('not_ready', 'Publication has no accepted result', status=404, recovery='retry_original')
        return json.loads(row['result'])

    def restore(self, owner, actor, body):
        self.binding(owner, actor)
        operation = body.get('operationId')
        if not isinstance(operation, str) or not 1 <= len(operation) <= 128:
            raise LabError('invalid_input', 'A stable publication operationId is required')
        row = self.db.one('SELECT * FROM registry_publication_operations WHERE actor=? AND operation=?', actor, operation)
        if row:
            if row['fingerprint'] != digest(body):
                raise LabError('id_conflict', 'Original publication parameters changed', status=409)
            return json.loads(row['result'])

    def category(self, value):
        available = {c['id']: c for c in self.categories()}
        if not isinstance(value,str) or value not in available:
            raise LabError('category_required', 'Choose an existing A2X leaf category', status=409, recovery='choose_category')
        return value

    def prepare(self, owner, actor, body):
        _, meta = self.binding(owner, actor)
        if set(body) != {'operationId', 'declaration', 'proof', 'templates'}:
            raise LabError('invalid_input', 'Use the documented publication fields')
        if len(encode(body).encode()) > 1024 * 1024:
            raise LabError('invalid_input', 'Service publication exceeds 1 MiB')
        declaration = body['declaration']
        fields = {'schema', 'agentId', 'protocolId', 'revision', 'name', 'description', 'tags', 'categoryId', 'templateDigests'}
        if not isinstance(declaration, dict) or set(declaration) != fields:
            raise LabError('invalid_input', 'Invalid service declaration fields')
        if (declaration['schema'], declaration['agentId'], declaration['protocolId']) != ('ioalab.service/1', actor, meta['protocolId']):
            raise LabError('forbidden', 'Declaration does not match the bound service address', status=403)
        if type(declaration['revision']) is not int or declaration['revision'] < 1:
            raise LabError('invalid_input', 'Publication revision must be a positive integer')
        for name, maximum in [('name', 120), ('description', 8000)]:
            value = declaration[name]
            if not isinstance(value, str) or not 1 <= len(value.strip()) <= maximum:
                raise LabError('invalid_input', 'A bounded service ' + name + ' is required')
        tags = declaration['tags']
        if not isinstance(tags, list) or len(tags) > 20 or any(not isinstance(t, str) or not 1 <= len(t) <= 80 for t in tags):
            raise LabError('invalid_input', 'Use at most 20 bounded service tags')
        self.category(declaration['categoryId'])
        templates = body['templates']
        if not isinstance(templates, list) or not 1 <= len(templates) <= 16 or not all(isinstance(t, dict) for t in templates):
            raise LabError('invalid_input', 'Provide one to 16 original signed templates')
        actual = [t.get('templateDigest') for t in templates]
        if actual != declaration['templateDigests'] or any(not isinstance(t, str) for t in actual) or len(set(actual)) != len(actual):
            raise LabError('invalid_input', 'Template references must match the uploaded originals in order')
        header = verify(body['proof'], digest(declaration), meta['providerJwk'], slot='ioa-service', signer=meta['protocolId'], expect_kid=True)
        check_fresh(header, datetime.fromtimestamp(self.clock(), timezone.utc))
        previous=self.db.one('SELECT MAX(revision) AS revision FROM registry_publications WHERE actor=?',actor)['revision'] or 0
        if declaration['revision']!=previous+1:
            raise LabError('revision_conflict','Read the current publication before changing it',status=409)
        return PreparedPublication(actor, body['operationId'], digest(body), encode(body), encode(meta['providerJwk']))

    def commit_in(self, conn, owner, prepared, templates):
        if conn is not self.db.conn or not conn.in_transaction:
            raise RuntimeError('Publication needs the owned application transaction')
        _, meta = self.binding(owner, prepared.actor)
        if encode(meta['providerJwk']) != prepared.key:
            raise LabError('forbidden', 'Bound service key changed before publication', status=403)
        body = json.loads(prepared.body)
        old = self.restore(owner, prepared.actor, body)
        if old:
            return old
        declaration = body['declaration']
        check_fresh(header_of(body['proof']),datetime.fromtimestamp(self.clock(),timezone.utc))
        self.category(declaration['categoryId'])
        previous = conn.execute('SELECT MAX(revision) AS revision FROM registry_publications WHERE actor=?', (prepared.actor,)).fetchone()['revision'] or 0
        if declaration['revision'] != previous + 1:
            raise LabError('revision_conflict', 'Read the current publication before changing it', status=409)
        if [t['templateDigest'] for t in templates] != declaration['templateDigests']:
            raise LabError('id_conflict', 'Endorsed templates differ from the original declaration', status=409)
        if any(parse_time(t['validUntil']).timestamp() <= self.clock() for t in templates):
            raise LabError('expired', 'A template expired before publication', status=409)
        declared_digest = digest(declaration)
        result = {'agentId': prepared.actor, 'acceptedRevision': declaration['revision'], 'declarationDigest': declared_digest,
                  'templateDigests': declaration['templateDigests'], 'templates': templates,
                  'acceptedAt': datetime.fromtimestamp(self.clock(), timezone.utc).isoformat().replace('+00:00', 'Z')}
        conn.execute('INSERT INTO registry_publications VALUES(?,?,?,?,?,?)',
                     (prepared.actor, declaration['revision'], declared_digest, prepared.body, encode(templates), self.clock()))
        conn.execute('INSERT INTO registry_publication_operations VALUES(?,?,?,?)',
                     (prepared.actor, prepared.operation, prepared.fingerprint, encode(result)))
        return result

    def current(self, actor):
        row = self.db.one('SELECT * FROM registry_publications WHERE actor=? ORDER BY revision DESC LIMIT 1', actor)
        if not row:
            raise LabError('not_ready', 'Service has not published its business declaration', status=409)
        return row

    def public(self, actor):
        row = self.current(actor)
        body = json.loads(row['body'])
        return {'declaration': body['declaration'], 'proof': body['proof'], 'declarationDigest': row['digest']}

    def card(self, actor):
        declaration = self.public(actor)['declaration']
        return MessageToDict(agent_card(name=declaration['name'], base_url=declaration['protocolId'], role='provider'))

    def templates(self, actor, template_digest=None):
        if template_digest:
            for row in self.db.rows('SELECT templates FROM registry_publications WHERE actor=? ORDER BY revision DESC', actor):
                for template in json.loads(row['templates']):
                    if template['templateDigest'] == template_digest:
                        return [template]
            raise LabError('not_ready', 'Template was not published by this service', status=404)
        return [t for t in json.loads(self.current(actor)['templates']) if parse_time(t['validUntil']).timestamp() > self.clock()]

    def status(self, owner, actor):
        self.binding(owner, actor)
        row = self.db.one('SELECT * FROM registry_publications WHERE actor=? ORDER BY revision DESC LIMIT 1', actor)
        projection = self.db.one('SELECT * FROM registry_projection WHERE actor=?', actor)
        declaration=json.loads(row['body'])['declaration'] if row else None
        active=self.templates(actor) if row else []
        return {'agentId': actor, 'published': bool(row), 'revision': row['revision'] if row else 0,
                'declarationDigest': row['digest'] if row else None,
                'indexedRevision': projection['revision'] if projection else 0,
                'indexReady': bool(row and projection and row['revision'] == projection['revision']),
                'categoryId': declaration['categoryId'] if declaration else None,
                'indexedCategoryId':projection['category'] if projection else None,
                'templateReady':bool(active),'activeTemplateDigests':[t['templateDigest'] for t in active],
                'executionStatus': 'not_reported'}

    def index_document(self, actor):
        row = self.current(actor)
        declaration = json.loads(row['body'])['declaration']
        templates = self.templates(actor)
        card = {'name': declaration['name'], 'description': declaration['description'], 'url': declaration['protocolId'],
                'version': str(declaration['revision']), 'status': 'listed',
                'skills': [{'id': str(i), 'name': tag, 'description': tag, 'tags': [tag]} for i, tag in enumerate(declaration['tags'])],
                'a2c': {'templates': [{'digest': t['templateDigest'], 'formation': t['formation'],
                                     'validUntil':t['validUntil'],
                                     'proposalFee': t['pricing'].get('proposalFee')} for t in templates]}}
        return declaration, card

    def indexed_in(self, conn, actor, declaration, actual_category):
        current = self.current(actor)
        if current['revision'] != declaration['revision'] or actual_category != declaration['categoryId']:
            raise LabError('revision_conflict', 'Index does not reflect the current publication', status=409)
        conn.execute('INSERT OR REPLACE INTO registry_projection VALUES(?,?,?,?)',
                     (actor, declaration['revision'], actual_category, self.clock()))
        definition = {'id': actor, 'name': declaration['name'], 'description': declaration['description'],
                      'tags': declaration['tags'], 'category': actual_category, 'url': declaration['protocolId'],
                      'external': True, 'runnable': False}
        conn.execute("UPDATE agents SET name=?,definition=?,status='listed' WHERE id=?",
                     (declaration['name'], encode(definition), actor))
