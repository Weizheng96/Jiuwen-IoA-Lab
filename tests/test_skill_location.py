"""Native installation returns the actual entry; common role text has no path guesses."""
from pathlib import Path
import pytest
from ioalab_client.native_skills import install

ROOT=Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('host',['codex','claude_code','jiuwen'])
def test_role_entry_is_owned_by_native_installer_and_common_text_has_no_path_fallback(tmp_path,monkeypatch,host):
    monkeypatch.setattr(Path,'home',lambda:tmp_path)
    result=install(host,source=ROOT/'src/client/skills')
    page=(ROOT/'src/client/skills/_shared/references/location.md').read_text(encoding='utf-8')
    assert 'JIUWENSWARM_' not in page and '.agents/skills' not in page and '.claude/skills' not in page
    for item in result['skills']:
        assert item['entry']
        if item['installed']:
            assert (Path(item['path'])/'scripts/ioalab.py').is_file()
        else:
            assert item['instructions'] and item['newSessionVerified'] is False
