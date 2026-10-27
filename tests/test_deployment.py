"""Local deployment config must preserve path escaping and avoid exposing secrets."""
import importlib.util
from pathlib import Path
import pytest

def test_administrator_skill_install_preserves_local_account_and_rejects_traversal(tmp_path):
    import json,zipfile
    path=Path(__file__).resolve().parents[1]/'deploy/configure_assistant.py'
    spec=importlib.util.spec_from_file_location('assistant_config',path);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    root=tmp_path/'app';(root/'dist').mkdir(parents=True);swarm=tmp_path/'swarm'
    bundle=root/'dist/ioalab-skill.zip'
    with zipfile.ZipFile(bundle,'w') as z:z.writestr('ioalab-delegate/SKILL.md','skill');z.writestr('ioalab-delegate/assets/runtime.json','{}')
    values={'IOALAB_BASE_URL':'http://127.0.0.1:8750','IOALAB_USERNAME':'ISC_001','IOALAB_PASSWORD':'local-test-only','IOALAB_HOME':str(tmp_path/'assistant')}
    mod.install_skill(root,swarm,values)
    config=swarm/'agent/workspace/skills/ioalab-delegate/settings.local.json'
    assert json.loads(config.read_text())==values
    values['IOALAB_USERNAME']='preserve-existing-account'
    mod.install_skill(root,swarm,values)
    assert json.loads(config.read_text())['IOALAB_USERNAME']=='ISC_001'
    with zipfile.ZipFile(bundle,'w') as z:z.writestr('ioalab-delegate/../../escape.txt','unsafe')
    with pytest.raises(ValueError):mod.install_skill(root,swarm,values)
    assert not (swarm/'agent/workspace/escape.txt').exists()

def test_environment_parser_preserves_windows_paths_and_json(tmp_path):
    path=Path(__file__).resolve().parents[1]/'deploy/configure_assistant.py'
    spec=importlib.util.spec_from_file_location('assistant_config',path);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    env=tmp_path/'assistant.env';env.write_text('IOALAB_HOME="D:\\\\demo\\\\assistant"\nIOALAB_ALLOWED_SENDERS=["a","b"]\nIOALAB_SMTP_PASSWORD=local-test-only\n',encoding='utf-8')
    result=mod.read_env(env)
    assert result['IOALAB_HOME']=='D:\\demo\\assistant'
    assert result['IOALAB_ALLOWED_SENDERS']=='["a","b"]'

def test_trial_administrator_password_is_generated_not_default(tmp_path):
    from ioalab.config import Settings
    from ioalab.platform import Platform
    from ioalab.seed.accounts import ensure_accounts
    from a2c.errors import A2CError
    pf=Platform(Settings(data=tmp_path,mode='trial',admin_password='test-long-admin-password'))
    try:
        ensure_accounts(pf)
        password=(tmp_path/'assistant.secret').read_text().strip()
        assert len(password)>=12 and password!='001'
        u=pf.auth.login('ISC_001',password)
        assert pf.auth.is_admin(u['id'])
        with pytest.raises(A2CError):pf.auth.login('ISC_001','001')
    finally:
        for transport in pf.transports:transport.close()
