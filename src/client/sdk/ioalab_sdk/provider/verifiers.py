"""One verifier-authoring owner for local management and task-scoped proposals."""
from functools import lru_cache
import json
import threading

import httpx
from a2c.codec import digest
from a2c.errors import A2CError
from a2c.verifier.package import check_manifest,load
from a2c.verifier.sandbox import Sandbox,SandboxError
from jsonschema import Draft202012Validator
from wasmtime import WasmtimeError

from ..adapters import stable
from ..runtime import RuntimeFault


TASK_SOURCE_LIMIT=64*1024
SCHEMA={'type':'object','properties':{
    'verifierId':{'type':'string','pattern':r'^[a-z][a-z0-9_]*@\d+\.\d+(\.\d+)?$','maxLength':160},
    'source':{'type':'string','minLength':1,'description':'Python verify(ctx)。合同字段在ctx.values中，以完整reads路径作键，例如ctx.values["spec.result.topic"]；ctx.read(name)仅返回交付文件字节。没有ctx.spec或ctx.inputs。'},
    'reads':{'type':'array','description':'合同字段路径，仅spec.<deliverable>.<key>或inputs.<material>.<key>；不是文件名。仅ctx.read读取文件时省略或传[]。','maxItems':256,'items':{'type':'string','maxLength':256}},
    'kind':{'enum':['script','skill']}},'required':['verifierId','source'],'additionalProperties':False}
TASK_SCHEMA={**SCHEMA,'properties':{**SCHEMA['properties'],'source':{**SCHEMA['properties']['source'],'maxLength':TASK_SOURCE_LIMIT}}}


_COMPILE_SOURCE='''import ast,json,re,sys
def contract_context_check(tree,entry_name):
    # Diagnose statically known API mistakes, not arbitrary Python semantics.
    # Only functions reached with verify's actual Context are inspected.
    functions={n.name:n for n in tree.body if isinstance(n,ast.FunctionDef)}
    entry=functions.get(entry_name)
    if entry is None:return
    arguments=[*entry.args.posonlyargs,*entry.args.args]
    if not arguments:return
    contexts={entry.name:{arguments[0].arg}}
    def nodes(function):
        pending=list(function.body)
        while pending:
            node=pending.pop()
            if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef,ast.Lambda)):continue
            yield node
            pending.extend(ast.iter_child_nodes(node))
    changed=True
    while changed:
        changed=False
        for name,roots in list(contexts.items()):
            for node in nodes(functions[name]):
                if not isinstance(node,ast.Call) or not isinstance(node.func,ast.Name) or node.func.id not in functions:continue
                target=functions[node.func.id];arguments=[*target.args.posonlyargs,*target.args.args]
                pairs=list(zip([a.arg for a in arguments],node.args))+[(a.arg,a.value) for a in node.keywords]
                for parameter,value in pairs:
                    if isinstance(value,ast.Name) and value.id in roots and parameter not in contexts.get(target.name,set()):
                        contexts.setdefault(target.name,set()).add(parameter);changed=True
    assignments={}
    for node in ast.walk(tree):
        if isinstance(node,ast.Name) and isinstance(node.ctx,ast.Store):assignments.setdefault(node.id,[]).append(None)
        if isinstance(node,ast.Assign) and isinstance(node.value,ast.Constant) and isinstance(node.value.value,str):
            for target in node.targets:
                if isinstance(target,ast.Name):assignments.setdefault(target.id,[]).append(node.value.value)
    constants={name:next(v for v in values if v is not None) for name,values in assignments.items() if len(values)==2 and values.count(None)==1}
    def literal(node):
        if isinstance(node,ast.Constant):return node.value
        if isinstance(node,ast.Name):return constants.get(node.id)
    def is_context(node,roots):return isinstance(node,ast.Name) and node.id in roots
    for name,roots in contexts.items():
        for node in nodes(functions[name]):
            bad=isinstance(node,ast.Attribute) and is_context(node.value,roots) and node.attr in ("spec","inputs")
            if isinstance(node,ast.Call):
                fn=node.func
                if isinstance(fn,ast.Name) and fn.id=="getattr" and len(node.args)>=2 and is_context(node.args[0],roots):
                    bad=literal(node.args[1]) in ("spec","inputs")
                if isinstance(fn,ast.Attribute) and is_context(fn.value,roots) and fn.attr in ("read","file") and node.args:
                    path=literal(node.args[0])
                    bad=isinstance(path,str) and re.fullmatch(r"(?:spec|inputs)\\.[A-Za-z_][A-Za-z0-9_]*\\.[A-Za-z_][A-Za-z0-9_]*",path) is not None
            if bad:
                return {"ok":False,"line":node.lineno,"column":node.col_offset+1,
                        "message":"Contract fields use ctx.values[full_reads_path]; ctx.read/ctx.file take actual delivery filenames. Context has no ctx.spec or ctx.inputs."}
try:
    source=sys.stdin.buffer.read()
    compile(source, "verify.py", "exec", dont_inherit=True)
    diagnostic=contract_context_check(ast.parse(source),sys.argv[1])
except SyntaxError as exc:
    print(json.dumps({"ok":False,"line":exc.lineno,"column":exc.offset,"message":exc.msg}))
except (ValueError,MemoryError,RecursionError):
    print(json.dumps({"ok":False,"line":0,"column":0,"message":"Source exceeds parser limits"}))
else:
    print(json.dumps(diagnostic or {"ok":True}))
'''


@lru_cache(maxsize=64)
def _check_source(source,entry='verify'):
    """Compile without execution in the target grammar, independent of host Python."""
    try:
        result=Sandbox.get('cpython-3.13-wasi').run(
            ['python','-I','-S','-B','-c',_COMPILE_SOURCE,entry],source,{})
    except (OSError,SandboxError,WasmtimeError,ValueError,KeyError):
        raise RuntimeFault('not_ready','Verifier syntax sandbox is unavailable','inspect_runtime') from None
    if result.trap or result.exit_code:
        raise RuntimeFault('not_ready','Verifier syntax sandbox did not complete','inspect_runtime')
    try:parsed=json.loads(result.stdout)
    except (UnicodeError,ValueError):
        raise RuntimeFault('not_ready','Verifier syntax sandbox returned an invalid result','inspect_runtime') from None
    if parsed=={'ok':True}:return
    if (not isinstance(parsed,dict) or set(parsed)!={'ok','line','column','message'}
            or parsed['ok'] is not False or not isinstance(parsed['message'],str)):
        raise RuntimeFault('not_ready','Verifier syntax sandbox returned an invalid result','inspect_runtime')
    raise RuntimeFault('invalid_input',
        f"Verifier source validation failed at line {parsed['line']}, column {parsed['column']}: {parsed['message']}",
        'correct_input')


def platform_fault(exc):
    if isinstance(exc,A2CError):return RuntimeFault('invalid_input',exc.message,'correct_input')
    if isinstance(exc,httpx.HTTPStatusError):
        if exc.response.status_code>=500:return RuntimeFault('unknown_result','Recover the original provider operation','resume_original')
        try:problem=exc.response.json().get('error',{})
        except (ValueError,AttributeError):problem={}
        if (not isinstance(problem,dict) or not {'code','message','recovery'}<=set(problem)
                or not all(isinstance(problem[key],str) and problem[key] for key in ('code','message','recovery'))):
            return RuntimeFault('protocol_error','Platform returned an invalid error envelope','inspect_platform')
        return RuntimeFault(problem['code'],problem['message'],problem['recovery'])
    return RuntimeFault('unknown_result','Recover the original provider operation','resume_original')


class VerifierPublisher:
    def __init__(self,runtime):self.runtime=runtime;self.lock=threading.RLock()

    def check_reference(self,reference,kind):
        """Inspect the actual package before a new quote freezes its reference."""
        package=load(self.runtime.account.fetch_package(reference['uri']),reference,kind)
        module,_,function=package.manifest['entry'].partition(':')
        source=package.dir.joinpath(*module.split('.')).with_suffix('.py')
        if source.is_file():_check_source(source.read_bytes(),function)
        return package

    @staticmethod
    def validate(body,limit):
        problem=next(Draft202012Validator(SCHEMA).iter_errors(body),None)
        if problem:raise RuntimeFault('invalid_input',problem.message,'correct_input')
        try:bounded=len(body['source'].encode('utf-8'))<=limit
        except UnicodeError:bounded=False
        if not bounded:raise RuntimeFault('invalid_input','Verifier source exceeds the UTF-8 size bound','correct_input')
        try:check_manifest({'id':body['verifierId'],'kind':body.get('kind','script'),'runtime':'cpython-3.13-wasi',
                           'entry':'verify:verify','reads':body.get('reads',[])},{'id':body['verifierId']},body.get('kind','script'))
        except A2CError as exc:raise RuntimeFault('invalid_input',exc.message,'correct_input') from None
        _check_source(body['source'].encode('utf-8'))

    def publish(self,identity,operation,body,*,limit=256*1024):
        self.validate(body,limit)
        namespace='provider:'+identity.agent_id;key='verifier:'+operation;store=self.runtime.store
        fingerprint=digest({'profile':identity.profile,**body})
        with self.lock:
            record=store.checkpoint(namespace,key)
            if record and record['fingerprint']!=fingerprint:raise RuntimeFault('id_conflict','Original verifier upload changed')
            if not record:
                record={'fingerprint':fingerprint,'body':body};store.checkpoint_set(namespace,key,record)
            if 'result' in record:return record['result']
        try:
            result=self.runtime.account.api('POST','/provider/packages',record['body'])
            if (not isinstance(result,dict) or set(result)!={'id','uri','digest'} or result.get('id')!=body['verifierId']
                    or not isinstance(result.get('digest'),str) or result.get('uri')!='ioalab://pkg/'+result['digest']):
                raise RuntimeFault('protocol_error','Verifier upload returned a different reference','inspect_platform')
            try:load(self.runtime.account.fetch_package(result['uri']),result,body.get('kind','script'))
            except (A2CError,ValueError):raise RuntimeFault('protocol_error','Verifier package does not match its reference or kind','inspect_platform') from None
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (400,404):raise RuntimeFault('protocol_error','Returned verifier package is not readable','inspect_platform') from None
                raise
        except (A2CError,httpx.HTTPError) as exc:raise platform_fault(exc) from None
        with self.lock:
            saved=store.checkpoint(namespace,key)
            if 'result' not in saved:store.checkpoint_set(namespace,key,{**saved,'result':result})
            return store.checkpoint(namespace,key)['result']

    def task(self,identity,job,body):
        self.validate(body,TASK_SOURCE_LIMIT)
        namespace='provider:'+identity.agent_id;key='verifier-job:'+job;store=self.runtime.store;fingerprint=digest(body)
        with self.lock:
            state=store.checkpoint(namespace,key) or {'receipts':{}}
            if fingerprint in state['receipts']:return state['receipts'][fingerprint]['reference']
            pending=state.get('pending')
            if pending and pending['fingerprint']!=fingerprint:
                raise RuntimeFault('unknown_result','Resolve the original task verifier before preparing different content','resume_original')
            if len(state['receipts'])>=32:raise RuntimeFault('invalid_input','A quote may prepare at most 32 verifier packages','correct_input')
            if not pending:
                pending={'fingerprint':fingerprint,'operation':stable('task-verifier',job,fingerprint),'body':body}
                state={**state,'pending':pending};store.checkpoint_set(namespace,key,state)
        try:result=self.publish(identity,pending['operation'],pending['body'],limit=TASK_SOURCE_LIMIT)
        except RuntimeFault as exc:
            if exc.code not in ('unknown_result','result_unknown'):
                with self.lock:
                    current=store.checkpoint(namespace,key)
                    if current.get('pending')==pending:
                        current.pop('pending');store.checkpoint_set(namespace,key,current)
            raise
        with self.lock:
            current=store.checkpoint(namespace,key)
            current['receipts'][fingerprint]={'reference':result,'kind':body.get('kind','script')}
            current.pop('pending',None);store.checkpoint_set(namespace,key,current)
        return result

    def prepared(self,identity,job):
        namespace='provider:'+identity.agent_id;key='verifier-job:'+job
        state=self.runtime.store.checkpoint(namespace,key) or {'receipts':{}}
        if state.get('pending'):
            self.task(identity,job,state['pending']['body'])
            state=self.runtime.store.checkpoint(namespace,key)
        return list(state['receipts'].values())
