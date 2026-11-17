"""Local files crossing the SDK/native-host boundary retain actual bytes and scope."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from ioalab_sdk.provider.material_files import artifact_directory, material_files
from ioalab_sdk.runtime import AgentDefinition, RuntimeFault, execution_snapshot


def test_business_files_preserve_binary_bytes_and_original_labels(tmp_path):
    content = b'\x00\xff\x01binary\r\n'
    records = material_files(tmp_path / 'business', {'card': 'original', 'period': 2},
                             {'../input.bin': content, 'notes.txt': b'abc'})
    assert records[0]['name'] == '../input.bin'
    assert Path(records[0]['path']).read_bytes() == content
    assert Path(records[0]['path']).is_relative_to(tmp_path / 'business' / 'materials')
    assert Path(records[0]['path']).suffix == '.bin'
    assert records[0]['size'] == len(content)
    assert records[1]['contentDigest'] == 'sha256:ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad'
    assert material_files(tmp_path / 'business', {'card': 'original', 'period': 2},
                          {'../input.bin': content, 'notes.txt': b'abc'}) == records
    artifacts = artifact_directory(tmp_path / 'business')
    assert artifacts.is_dir() and artifacts == tmp_path / 'business' / 'artifacts'


def test_changed_cached_material_is_preserved_and_never_passed_to_host(tmp_path):
    record, = material_files(tmp_path, {'period': 1}, {'original.bin': b'original'})
    target = Path(record['path'])
    target.write_bytes(b'changed')
    with pytest.raises(RuntimeFault, match='Original material file changed'):
        material_files(tmp_path, {'period': 1}, {'original.bin': b'original'})
    assert target.read_bytes() == b'changed'


def test_business_files_reject_linked_workspace_and_do_not_modify_target(tmp_path):
    real, linked = tmp_path / 'real', tmp_path / 'linked'
    real.mkdir()
    try:
        linked.symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip('This Windows user cannot create symbolic links')
    with pytest.raises(RuntimeFault):
        material_files(linked, {'period': 1}, {'input.bin': b'original'})
    assert list(real.iterdir()) == []


def test_host_skill_names_do_not_require_ioa_professional_distribution():
    definition = AgentDefinition('service', 'original business instructions', (), ('already-installed',),
                                 native_tools=(('write_file', 'deny'),))
    snapshot = execution_snapshot(SimpleNamespace(agent_id='actor', profile='provider'),
                                  'interaction', 'job', definition, definition_digest='original-digest', version='1')
    assert snapshot.definition.instructions == 'original business instructions'
    assert snapshot.definition.digest == 'original-digest'
    assert snapshot.definition.resources == ()
    assert snapshot.authorization['nativeToolsEnabled'] is True
    assert snapshot.authorization['nativeTools'] == {}
    assert definition.native_tools == (('write_file', 'deny'),)


def test_service_with_host_skill_reference_reaches_sdk_quote_without_ioa_distribution(platform, tmp_path):
    import json
    from test_resident_contracts import candidate
    from test_resident_delegation import QuoteHost, participants

    class Host(QuoteHost):
        def execute(self, turn):
            assert json.loads(turn.input)['capabilityReferences'] == ['host-owned-skill']
            return super().execute(turn)

    with participants(platform, tmp_path, agent_host=Host(),
                      business={'skills': ['host-owned-skill']}) as (provider, assistant, host, template, task):
        result = candidate(assistant, provider, template, task)
        assert result['card']['signatures']['2']
        state = provider.modules[0].execution.status('service')
        assert state['executionReady'] and state['unavailableSkills'] == []
        assert state['uncheckedSkills'] == ['host-owned-skill']
