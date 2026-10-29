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


def test_skill_discovery_setup_preserves_other_config_and_original_backup(tmp_path):
    import yaml
    path=Path(__file__).resolve().parents[1]/'deploy/configure_assistant.py'
    spec=importlib.util.spec_from_file_location('assistant_config_discovery',path);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    config=tmp_path/'config/config.yaml';config.parent.mkdir()
    original='react:\n  skill_mode: all\n  agent_name: preserved-agent\nprogressive_tool_enabled: true\nunchanged_setting: keep-me\n'
    config.write_text(original,encoding='utf-8')
    mod.configure_skill_discovery(tmp_path)
    changed=yaml.safe_load(config.read_text(encoding='utf-8'))
    assert changed['react']=={'skill_mode':'all','agent_name':'preserved-agent'}
    assert changed['progressive_tool_enabled'] is False and changed['unchanged_setting']=='keep-me'
    backup=config.with_name('config.before-ioa-discovery.yaml')
    assert backup.read_text(encoding='utf-8')==original
    after=config.read_bytes();mod.configure_skill_discovery(tmp_path)
    assert config.read_bytes()==after and backup.read_text(encoding='utf-8')==original


def test_personal_instruction_block_preserves_owner_content_and_backup(tmp_path):
    source=Path(__file__).resolve().parents[1]/'deploy/configure_assistant.py'
    spec=importlib.util.spec_from_file_location('assistant_config_instructions',source);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    configure_ioa_instructions=mod.configure_ioa_instructions
    path=tmp_path/'agent/workspace/AGENT.md';path.parent.mkdir(parents=True)
    path.write_text('# Owner instructions\nKeep this setting.\n',encoding='utf-8')
    configure_ioa_instructions(tmp_path)
    first=path.read_text(encoding='utf-8')
    assert 'Keep this setting.' in first and first.count('<!-- ioalab:begin -->')==1
    assert 'skill_tool' in first and 'SKILL.md' in first
    assert path.with_name('AGENT.before-ioa.md').read_text(encoding='utf-8')=='# Owner instructions\nKeep this setting.\n'
    configure_ioa_instructions(tmp_path)
    assert path.read_text(encoding='utf-8')==first
