"""Editable local rules, immutable confirmed versions and recoverable publication."""
import copy
import json
from pathlib import Path
import re
import threading
from urllib.parse import quote,urlencode
import uuid

from a2c.codec import digest,digest_bytes
import httpx
from jsonschema import Draft202012Validator
import yaml

from ..runtime.files import private_text
from ..runtime.protocol import RuntimeFault,encode


FIELDS={'name','description','inputSchema','outputSchema','allowedSenders','enabled','tools'}
KEYWORDS={'type','properties','required','additionalProperties','items','enum','minimum','maximum','minLength','maxLength','minItems','maxItems','description','format'}


def invalid(message):raise RuntimeFault('invalid_input',message,'edit_rule')


def schema_check(schema,*,root=True,depth=0):
    if depth>8 or not isinstance(schema,(dict,bool)):invalid('Invalid or overly deep schema')
    if isinstance(schema,bool):
        if root:invalid('Input and output need explicit object schemas')
        return
    if set(schema)-KEYWORDS:invalid('Unsupported schema keyword')
    if root and schema.get('type')!='object':invalid('Input and output need explicit object schemas')
    if schema.get('type') not in (None,'object','array','string','number','integer','boolean','null'):invalid('Unsupported schema type')
    if schema.get('format') not in (None,'email','date-time'):invalid('Unsupported schema format')
    if not isinstance(schema.get('properties',{}),dict):invalid('Schema properties must be an object')
    for value in schema.get('properties',{}).values():schema_check(value,root=False,depth=depth+1)
    for key in ('items','additionalProperties'):
        if key in schema:schema_check(schema[key],root=False,depth=depth+1)
    try:Draft202012Validator.check_schema(schema)
    except Exception:invalid('Invalid JSON Schema')
    if root and len(encode(schema).encode())>16384:invalid('Schema exceeds 16 KiB')


class _Dumper(yaml.SafeDumper):
    def ignore_aliases(self,data):return True


def render(declaration,instructions):
    return '---\n'+yaml.dump(declaration,Dumper=_Dumper,allow_unicode=True,sort_keys=True)+'---\n'+instructions.strip()+'\n'


def parse(text):
    if len(text.encode())>65536 or not text.startswith('---\n') or '\n---\n' not in text:
        invalid('Expected a Markdown rule with bounded YAML front matter')
    header,body=text[4:].split('\n---\n',1)
    try:
        for event in yaml.parse(header):
            if isinstance(event,yaml.events.AliasEvent) or getattr(event,'anchor',None):invalid('YAML aliases and anchors are not supported')
            tag=getattr(event,'tag',None)
            if tag and not tag.startswith('tag:yaml.org,2002:'):invalid('Custom YAML tags are not supported')
        value=yaml.safe_load(header)
    except yaml.YAMLError:invalid('Invalid YAML declaration')
    if not isinstance(value,dict) or set(value)-FIELDS:invalid('Unknown rule declaration fields')
    def json_value(item,depth=0):
        if depth>12:invalid('Rule declaration is too deeply nested')
        if isinstance(item,dict):
            if any(not isinstance(key,str) for key in item):invalid('Declaration keys must be strings')
            for child in item.values():json_value(child,depth+1)
        elif isinstance(item,list):
            for child in item:json_value(child,depth+1)
        elif item is not None and type(item) not in (str,int,float,bool):invalid('Declaration must contain JSON values')
    json_value(value)
    try:encode(value)
    except (TypeError,ValueError):invalid('Declaration contains invalid JSON values')
    if not isinstance(value.get('name'),str) or not re.fullmatch('[A-Za-z][A-Za-z0-9_.-]{0,63}',value['name']):invalid('Invalid interface name')
    if not isinstance(value.get('description'),str) or not 1<=len(value['description'])<=1000:invalid('Describe the interface purpose')
    for key in ('inputSchema','outputSchema'):schema_check(value.get(key))
    allow=value.get('allowedSenders')
    if allow is not None and (not isinstance(allow,list) or len(allow)>100 or any(not isinstance(name,str) or not name for name in allow) or len(set(allow))!=len(allow)):
        invalid('allowedSenders must be distinct exact account names')
    if type(value.get('enabled',True)) is not bool:invalid('enabled must be boolean')
    tools=value.get('tools',[])
    if not isinstance(tools,list) or len(tools)>32 or any(not isinstance(name,str) or not name for name in tools) or len(set(tools))!=len(tools):invalid('Invalid local tool references')
    if not body.strip() or len(body.encode())>16384:invalid('Provide bounded local processing instructions')
    normalized={**value,'enabled':value.get('enabled',True),'allowedSenders':allow,'tools':tools}
    return {'declaration':normalized,'instructions':body.strip(),'contentDigest':digest_bytes(text.encode())}


class Rules:
    def __init__(self,runtime,identity,tools):
        self.runtime,self.identity,self.tools=runtime,identity,dict(tools)
        self.store,self.account=runtime.store,runtime.account
        self.root=runtime.config.home/'roles'/identity.profile/'rules'
        self.lock=threading.RLock()
        self.namespace='rules:'+identity.agent_id

    def _path(self,reference):
        try:
            if str(uuid.UUID(reference))!=reference:raise ValueError()
        except (TypeError,ValueError,AttributeError):invalid('Use the original draft reference')
        return self.root/'drafts'/(reference+'.md')

    def draft(self,operation,declaration,instructions):
        if not isinstance(declaration,dict) or not isinstance(instructions,str):invalid('A declaration and local processing text are required')
        text=render(copy.deepcopy(declaration),instructions)
        # Structural validation before writing; account/tool checks remain a pure preflight.
        parse(text)
        fingerprint=digest({'declaration':declaration,'instructions':instructions})
        reference=str(uuid.uuid5(uuid.NAMESPACE_URL,encode(['ioa-rule-draft',self.identity.agent_id,operation])))
        key='draft:'+operation
        with self.lock:
            old=self.store.checkpoint(self.namespace,key)
            if old and old['fingerprint']!=fingerprint:raise RuntimeFault('id_conflict','Draft operation parameters changed')
            path=self._path(reference)
            if not old:
                if path.exists() and digest_bytes(path.read_bytes())!=digest_bytes(text.encode()):raise RuntimeFault('id_conflict','Uncommitted draft differs; preserve and reconcile it')
                private_text(path,text)
                self.store.checkpoint_set(self.namespace,key,{'fingerprint':fingerprint,'reference':reference})
        return {'draftRef':reference,'path':str(path),'published':False}

    def read(self,reference):
        with self.lock:
            path=self._path(reference)
            if not path.exists():raise RuntimeFault('not_ready','Local rule draft is missing')
            with path.open(encoding='utf-8') as stream:return parse(stream.read(65537))

    def validate(self,reference):
        value=self.read(reference)
        declaration=value['declaration']
        missing=[name for name in declaration['tools'] if name not in self.tools]
        if missing and declaration['enabled']:raise RuntimeFault('not_ready','Configure the declared local tools: '+', '.join(missing),'configure_tools')
        for name in declaration['allowedSenders'] or []:
            self.account.api('GET','/messaging/accounts/'+quote(name,safe='')+'/interfaces')
        return {**value,'draftRef':reference,'valid':True}

    def preview(self,reference):
        value=self.validate(reference)
        decl=value['declaration']
        versions=[item for item in self.store.checkpoints(self.namespace).values() if item.get('type')=='version' and item['name']==decl['name']]
        previous=max(versions,key=lambda item:item['revision']) if versions else None
        return {**value,'preview':{'title':('发布' if decl['enabled'] else '停用')+'收信接口：'+decl['name'],
                'account':self.account.username,'assistant':self.identity.name,'description':decl['description'],
                'allowedSenders':'所有已登录账号' if decl['allowedSenders'] is None else decl['allowedSenders'],
                'inputSchema':decl['inputSchema'],'outputSchema':decl['outputSchema'],'processing':value['instructions'],
                'tools':[self.tools[name].policy() if name in self.tools else {'name':name,'description':'未配置','effect':'unavailable'} for name in decl['tools']],
                'enabled':decl['enabled'],'previousRevision':previous['revision'] if previous else 0,
                'previousDigest':previous['contentDigest'] if previous else None,'newDigest':value['contentDigest']}}

    def _finish(self,operation,record,result):
        if result.get('agentId')!=self.identity.agent_id or result.get('name')!=record['name'] or result.get('contentDigest')!=record['contentDigest']:
            raise RuntimeFault('protocol_error','Published rule result differs from the original local content')
        version={'type':'version','name':record['name'],'revision':result['revision'],'contentDigest':record['contentDigest'],'tools':record['tools']}
        with self.lock,self.store.transaction() as conn:
            conn.execute('INSERT OR REPLACE INTO runtime_checkpoints VALUES(?,?,?)',(self.namespace,'version:'+record['name']+':'+str(result['revision']),encode(version)))
            record={**record,'result':result}
            conn.execute('INSERT OR REPLACE INTO runtime_checkpoints VALUES(?,?,?)',(self.namespace,'publish:'+operation,encode(record)))
        return {'status':'completed','data':version}

    def publish(self,operation,reference,content_digest,expected_revision,confirmation=None):
        if type(expected_revision) is not int or expected_revision<0:invalid('expectedRevision must be a nonnegative integer')
        parameters={'draftRef':reference,'contentDigest':content_digest,'expectedRevision':expected_revision}
        fingerprint=digest(parameters)
        key='publish:'+operation
        with self.lock:record=self.store.checkpoint(self.namespace,key)
        if record:
            if record['fingerprint']!=fingerprint:raise RuntimeFault('id_conflict','Original publication parameters changed')
            if record.get('result'):return {'status':'completed','data':{'type':'version','name':record['name'],'revision':record['result']['revision'],'contentDigest':record['contentDigest'],'tools':record['tools']}}
            try:
                result=self.account.api('GET','/operations/'+quote(operation,safe='')+'?'+urlencode({'agentId':self.identity.agent_id,'operation':'interface.publish'}))
                return self._finish(operation,record,result)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code!=404:raise
        value=self.preview(reference)
        if value['contentDigest']!=content_digest:raise RuntimeFault('id_conflict','Draft changed after preview; check and confirm the current content')
        binding={'actor':self.identity.agent_id,'operationId':operation,'subject':parameters,
                 'stage':'rules.publish','action':'interface.publish','preview':value['preview']}
        decision=self.runtime.confirmations.ensure(binding)
        if decision['status']!='approved':
            return {'status':'waiting' if decision['status']=='pending' else 'rejected',
                    'data':{'confirmationRef':decision['reference'],'confirmationStatus':decision['status'],'preview':value['preview']}}
        if confirmation not in (None,decision['reference']):raise RuntimeFault('id_conflict','Use the original confirmation reference')
        self.runtime.confirmations.claim(decision['reference'],binding)
        decl=value['declaration']
        payload={'name':decl['name'],'expectedRevision':expected_revision,
                 'definition':{**{name:item for name,item in decl.items() if name not in ('name','tools')},'contentDigest':content_digest}}
        record={'fingerprint':fingerprint,'name':decl['name'],'contentDigest':content_digest,'payload':payload,'tools':value['preview']['tools']}
        with self.lock:
            path=self.root/'versions'/(content_digest.removeprefix('sha256:')+'.md')
            with self._path(reference).open(encoding='utf-8') as stream:text=stream.read(65537)
            if digest_bytes(text.encode())!=content_digest:raise RuntimeFault('id_conflict','Draft changed while publication was prepared')
            if path.exists() and digest_bytes(path.read_bytes())!=content_digest:raise RuntimeFault('protocol_error','Immutable local rule version is damaged')
            if not path.exists():private_text(path,text)
            self.store.checkpoint_set(self.namespace,key,record)
        result=self.account.api('POST','/messaging/interfaces',{'operationId':operation,**payload})
        return self._finish(operation,record,result)

    def version(self,name,revision,*,validate_tools=True):
        record=self.store.checkpoint(self.namespace,'version:'+name+':'+str(revision))
        if not record:raise RuntimeFault('not_ready','The original local rule revision is unavailable','recover_publication')
        path=self.root/'versions'/(record['contentDigest'].removeprefix('sha256:')+'.md')
        if not path.exists() or path.stat().st_size>65536 or digest_bytes(path.read_bytes())!=record['contentDigest']:
            raise RuntimeFault('protocol_error','Original immutable rule content is missing or damaged','restore_rule')
        value={**parse(path.read_text(encoding='utf-8')),**record}
        if validate_tools and value['declaration']['enabled']:
            current=[self.tools[name].policy() if name in self.tools else None for name in value['declaration']['tools']]
            if current!=record['tools']:raise RuntimeFault('not_ready','Restore the confirmed tool definitions or publish a new rule revision','restore_tools')
        return value

    def list(self):
        records=self.store.checkpoints(self.namespace)
        return {'versions':[value for value in records.values() if value.get('type')=='version'],
                'pending':[key.removeprefix('publish:') for key,value in records.items() if key.startswith('publish:') and not value.get('result')]}
