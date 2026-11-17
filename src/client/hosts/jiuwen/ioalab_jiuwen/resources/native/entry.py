"""Native foreground wrapper. Standard library only; SDK is not installed here."""
import json
from pathlib import Path
import subprocess
import uuid
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise ValueError('native_endpoint_redirect')


def _run(command,body=None):
    response=subprocess.run(command,input=None if body is None else json.dumps(body),
        capture_output=True,text=True,encoding='utf-8',shell=False)
    if len(response.stdout.encode())>128*1024:raise ValueError('native_receipt_too_large')
    value=json.loads(response.stdout)
    if not isinstance(value,dict):raise ValueError('native_receipt_invalid')
    return response.returncode,value


def _post(descriptor,body,*,control=False):
    url=descriptor['controlUrl' if control else 'url'];parsed=urlsplit(url)
    if (parsed.scheme!='http' or parsed.hostname!='127.0.0.1' or parsed.path!=('/v1/native-control' if control else '/v1/native-source') or
        parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError('native_endpoint_invalid')
    request=Request(url,data=json.dumps(body).encode(),method='POST',headers={
        'Content-Type':'application/json','Authorization':'Bearer '+descriptor['grant'],
        'X-Ioa-Instance':descriptor['bootId']})
    with build_opener(_NoRedirect()).open(request,timeout=15) as response:
        encoded=response.read(128*1024+1)
    if len(encoded)>128*1024:raise ValueError('native_source_receipt_too_large')
    return json.loads(encoded)


class Entry:
    def __init__(self,root):
        self.root=Path(root)

    def requires_source(self,operation):
        binding=json.loads((self.root/'entry-binding.json').read_text(encoding='utf-8'))
        return operation in binding['sourceOperations'] or operation==binding['setupOperation']

    def invoke(self,role,input,*,capture,connect=None):
        if (not isinstance(input,dict) or set(input)!={'operation','operationId','arguments'} or
            not isinstance(input['operation'],str) or not isinstance(input['operationId'],str) or
            not 1<=len(input['operationId'])<=128 or not isinstance(input['arguments'],dict)):
            return {'status':'rejected','error':{'code':'invalid_input'}}
        try:
            binding=json.loads((self.root/'entry-binding.json').read_text(encoding='utf-8'))
            selected=binding['profiles'][role]
            if input['operation']==binding['setupOperation'] and input['arguments']:
                return {'operationId':input['operationId'],'status':'rejected','error':{'code':'invalid_input','phase':'setup_arguments','dispatch':'not_submitted'}}
            source=input['operation'] in binding['sourceOperations'] or input['operation']==binding['setupOperation']
            context=None
            if source:
                code,ready=_run(selected['ensureCommand'])
                if code or ready.get('status')!='completed':
                    return {'operationId':input['operationId'],'status':'waiting',
                        'error':{'code':'not_ready','phase':'ensure_client','dispatch':'not_submitted'}}
                # Read a fresh SDK-provisioned capability after every foreground
                # ensure. It never enters model arguments/results or resources.
                descriptor=json.loads(Path(selected['descriptorFile']).read_text(encoding='utf-8'))
                if (descriptor.get('adapterId')!='jiuwen' or descriptor.get('hostRef')!=binding['hostRef'] or
                    descriptor.get('profile')!=selected['profile'] or not descriptor.get('grant') or not descriptor.get('bootId')):
                    raise ValueError('native_binding_mismatch')
                actual=capture()
                receipt=_post(descriptor,{'sessionId':actual['sessionId'],'workspace':actual['workspace'],
                                         'captureId':str(uuid.uuid4())})
                reference=receipt.get('data',{}).get('entryRef')
                if not isinstance(reference,str) or not reference:raise ValueError('native_source_unverified')
                if not callable(connect):raise ValueError('native_runtime_bridge_missing')
                connect(descriptor,reference,actual)
                context={'entryRef':reference}
        except Exception:
            return {'operationId':input['operationId'],'status':'rejected',
                'error':{'code':'native_entry_unavailable','phase':'source','dispatch':'not_submitted'}}
        try:
            _,result=_run(selected['callerCommand'],{**input,'contextRef':context})
            if result.get('operationId')!=input['operationId']:raise ValueError('caller_receipt_mismatch')
            return result
        except Exception:
            return {'operationId':input['operationId'],'status':'unknown',
                'error':{'code':'native_caller_unknown','phase':'caller','dispatch':'unknown'}}
