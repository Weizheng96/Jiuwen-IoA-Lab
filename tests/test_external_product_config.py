"""Actual configuration validation survives native replacement of product files."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace

from ioalab_sdk.runtime.files import private_json
from test_access_products import bootstrap


def test_both_roles_share_external_default_after_skill_replacement_and_explicit_override(tmp_path,monkeypatch,capsys):
    profile=tmp_path/'profile';shared=profile/'.ioalab/settings.local.json';home=tmp_path/'durable-state'
    value={'platform_url':'http://127.0.0.1:8000','username':'owner-account','password':'PRIVATE-CONFIG-VALUE',
           'data_dir':str(home),'identities':[{'key':'personal','role':'assistant','name':'Owner'},
                                            {'key':'service','role':'service','name':'Service'}]}
    private_json(shared,value);original=shared.read_bytes()
    class UserPaths(type(Path())):
        @classmethod
        def home(cls):return cls(profile)
    def product(role):
        module=bootstrap();root=tmp_path/('skill-'+role);(root/'assets').mkdir(parents=True)
        (root/'assets/skill.json').write_text(json.dumps({'role':role}),encoding='utf-8')
        # A stale file in the replaceable package is not an alternative configuration source.
        (root/'settings.local.json').write_text('{"username":"wrong-source"}',encoding='utf-8')
        module.__file__=str(root/'scripts/ioalab.py')
        monkeypatch.setattr(module,'Path',UserPaths)
        monkeypatch.setattr(module,'installation',lambda:SimpleNamespace(prepare=lambda *a,**k:
            {'status':'completed','data':{'python':sys.executable},'error':None}))
        return module,root
    def validate(module,args=()):
        assert module.main(['runtime.validate',*args])==0
        text=capsys.readouterr().out
        assert 'PRIVATE-CONFIG-VALUE' not in text and 'password' not in text
        result=json.loads(text);assert result['status']=='completed'
        return result['data']
    assistant,root=product('assistant');first=validate(assistant)
    archived=(tmp_path/'replaced-skill-assistant').resolve()
    assert root.resolve().is_relative_to(tmp_path.resolve()) and archived.is_relative_to(tmp_path.resolve())
    root.rename(archived)
    assistant,_=product('assistant');assert validate(assistant)==first
    provider,_=product('service');assert validate(provider)==first
    assert shared.read_bytes()==original and not home.exists()
    other=tmp_path/'other-platform.json'
    private_json(other,{**value,'username':'other-account'})
    changed=validate(provider,['--config',str(other)])
    assert changed['configurationDigest']!=first['configurationDigest']
    assert shared.read_bytes()==original and not home.exists()
