"""Opt-in real installed SDK takeover; host/user drivers are explicit test doubles."""
import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest
from ioalab_sdk.runtime.files import private_json
from ioalab_sdk.runtime.ipc import LocalClient
from conftest import wait_for
from test_resident_contracts import candidate,confirm,status
from test_resident_delegation import participants


def test_installed_release_restarts_with_original_identity_contract_authorization_and_fee(platform,tmp_path):
    python=os.environ.get('IOALAB_INSTALLED_ACCESS_PYTHON')
    if not python:pytest.skip('requires a clean product environment from tools/check_bootstrap.py')
    environment={key:value for key,value in os.environ.items() if key not in ('PYTHONPATH','PYTHONHOME','PYTHONUSERBASE')}
    environment['PYTHONUTF8']='1'
    def command(*args):
        result=subprocess.run([python,'-I','-m','ioalab_sdk.runtime',*args],cwd=tmp_path,env=environment,
                              capture_output=True,timeout=30)
        return result.returncode,json.loads(result.stdout)
    with participants(platform,tmp_path) as (provider,assistant,host,template,task):
        quote=candidate(assistant,provider,template,task);formed,_,_=confirm(assistant,quote);reference=formed['data']['contractRef']
        wait_for(lambda:status(assistant,reference))
        original=status(assistant,reference)['data'];assert original['formed']
        rpc=LocalClient(assistant.config.home);before=rpc.call('runtime.status')['data']
        authorization=rpc.call('assistant.delegation.status',{'taskRef':task})['data']
        account_id=assistant.account.account['id'];balance=platform.wallets.balance(account_id)
        key_file=assistant.config.home/'identities/personal/keys.json';key_hash=hashlib.sha256(key_file.read_bytes()).hexdigest()
        config=tmp_path/'assistant/config.json';assert config.is_file()
        code,result=command('start','--config',str(config))
        assert code==1 and result['error']['code']=='installation_changed'
        assert rpc.call('runtime.status')['data']['bootId']==before['bootId']
        args=tmp_path/'status.json';private_json(args,{'contractRef':reference})
        code,result=command('call','assistant.delegation.status','--home',str(assistant.config.home),'--args-file',str(args))
        assert code==1 and result['error']['code']=='installation_changed'
        stop=tmp_path/'stop-old.json';private_json(stop,{})
        code,result=command('call','runtime.stop','--home',str(assistant.config.home),'--args-file',str(stop))
        assert code==0 and result['data']['bootId']==before['bootId']
        assistant.close()
        # Keep an actual child handle throughout the new installed process lifecycle.
        with (tmp_path/'installed-runtime.log').open('w',encoding='utf-8') as log:
            child=subprocess.Popen([python,'-I','-m','ioalab_sdk.runtime','serve','--config',str(config)],
                cwd=tmp_path,env=environment,stdin=subprocess.DEVNULL,stdout=log,stderr=log,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            try:
                seen=[]
                def ready():
                    if child.poll() is not None:raise AssertionError('Installed runtime exited; inspect its scoped test log')
                    try:code,value=command('call','runtime.status','--home',str(assistant.config.home))
                    except (ValueError,subprocess.TimeoutExpired):return False
                    if code==0 and value['data']['platformConnected']:seen.append(value['data']);return True
                    return False
                wait_for(ready,timeout=40);after=seen[0]
                assert after['bootId']!=before['bootId'] and after['runtimeId']==before['runtimeId']
                assert after['identities']==before['identities']
                assert after['installation']['releaseId'] and after['installation']['environment']!=before['installation']['environment']
                code,reused=command('start','--config',str(config));assert code==0 and reused['data']['bootId']==after['bootId']
                code,restored=command('call','assistant.delegation.status','--home',str(assistant.config.home),'--args-file',str(args))
                assert code==0 and restored['data']['formed'] and restored['data']['card']==original['card']
                assert restored['data']['interactionId']==original['interactionId'] and restored['data']['admissionId']==original['admissionId']
                auth_args=tmp_path/'authorization.json';private_json(auth_args,{'taskRef':task})
                code,current=command('call','assistant.delegation.status','--home',str(assistant.config.home),'--args-file',str(auth_args))
                assert code==0 and current['data']['authorization']==authorization['authorization']
                assert hashlib.sha256(key_file.read_bytes()).hexdigest()==key_hash
                assert platform.wallets.balance(account_id)==balance and len(host.turns)==1
            finally:
                if child.poll() is None:
                    stop=tmp_path/'stop-installed.json';private_json(stop,{})
                    code,stopped=command('call','runtime.stop','--home',str(assistant.config.home),'--args-file',str(stop))
                    assert code==0,stopped
                    child.wait(timeout=20)
                assert child.returncode==0
