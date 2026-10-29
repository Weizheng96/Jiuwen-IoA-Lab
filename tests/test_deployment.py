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


def test_install_never_changes_host_config_or_owner_instructions(tmp_path):
    import zipfile
    path=Path(__file__).resolve().parents[1]/'deploy/configure_assistant.py'
    spec=importlib.util.spec_from_file_location('assistant_config_pure',path);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    root=tmp_path/'app';(root/'dist').mkdir(parents=True)
    with zipfile.ZipFile(root/'dist/ioalab-skill.zip','w') as z:z.writestr('ioalab-delegate/SKILL.md','skill')
    swarm=tmp_path/'swarm';config=swarm/'config/config.yaml';config.parent.mkdir(parents=True)
    agent=swarm/'agent/workspace/AGENT.md';agent.parent.mkdir(parents=True)
    config.write_bytes(b'react:\n  skill_mode: auto_list\nprogressive_tool_enabled: true\n')
    agent.write_bytes(b'# Owner instructions\nDo not change.\n')
    before=(config.read_bytes(),agent.read_bytes())
    values={k:'test-only' for k in ('IOALAB_BASE_URL','IOALAB_USERNAME','IOALAB_PASSWORD','IOALAB_HOME')}
    mod.install_skill(root,swarm,values)
    assert before==(config.read_bytes(),agent.read_bytes())
    assert not config.with_name('config.before-ioa-discovery.yaml').exists()


def test_override_removal_preserves_owner_changes_and_is_idempotent(tmp_path):
    import yaml
    source=Path(__file__).resolve().parents[1]/'deploy/restore_swarm_overrides.py'
    spec=importlib.util.spec_from_file_location('restore_overrides',source);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    config=tmp_path/'config/config.yaml';config.parent.mkdir()
    config.write_text('react:\n  skill_mode: all\n  owner: keep\nprogressive_tool_enabled: false\nother: new-owner-value\n',encoding='utf-8')
    backup=config.with_name('config.before-ioa-discovery.yaml')
    backup.write_text('react:\n  skill_mode: auto_list\nprogressive_tool_enabled: true\nother: old\n',encoding='utf-8')
    agent=tmp_path/'agent/workspace/AGENT.md';agent.parent.mkdir(parents=True)
    agent.write_text('Owner before\n<!-- ioalab:begin -->old instruction<!-- ioalab:end -->\nOwner after\n',encoding='utf-8')
    assert mod.restore(tmp_path)['restartRequired']
    now=yaml.safe_load(config.read_text(encoding='utf-8'))
    assert now['react']=={'skill_mode':'auto_list','owner':'keep'} and now['progressive_tool_enabled'] is True
    assert now['other']=='new-owner-value' and backup.exists()
    assert agent.read_text(encoding='utf-8')=='Owner before\n\nOwner after\n'
    before=(config.read_bytes(),agent.read_bytes())
    assert not mod.restore(tmp_path)['restartRequired']
    assert before==(config.read_bytes(),agent.read_bytes())
