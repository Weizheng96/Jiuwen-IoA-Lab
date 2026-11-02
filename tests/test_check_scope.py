"""Fast CI selection and separation from explicitly triggered validation."""
import json
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

from tools.check_scope import CRITICAL, ROOT, changed_paths, select_tests


@pytest.mark.parametrize('paths', [[], ['README.md'], ['设计/流程/助手通信.md'],
                                  ['.agents/skills/develop/SKILL.md']])
def test_prose_changes_keep_lightweight_scope(paths):
    assert select_tests(paths)['tests'] == []


def test_maintenance_scope_does_not_pull_in_business_or_installation_tests():
    selected = select_tests(['.github/workflows/ci.yml', 'tools/check_scope.py'])['tests']
    assert selected == ['tests/test_check_scope.py', 'tests/test_check_workflow.py']


@pytest.mark.parametrize('path,expected', [
    ('src/access/sdk/ioalab_sdk/assistant/role.py', 'tests/test_assistant_role.py'),
    ('src/access/sdk/ioalab_sdk/provider/production.py', 'tests/test_provider_task_recovery.py'),
    ('src/platform/ioalab/payment.py', 'tests/test_payment_atomicity.py'),
    ('deploy/deployment.json.example', 'tests/test_vendor_deployment.py'),
    ('src/access/skills/assistant/SKILL.md', 'tests/test_skill_flows.py'),
])
def test_runtime_scope_keeps_related_and_critical_boundaries(path, expected):
    selected = select_tests([path])['tests']
    assert expected in selected
    if not path.endswith('.md'):
        assert all('tests/test_' + name + '.py' in selected for name in CRITICAL)
    assert 'tests/test_package_installation.py' not in selected


def test_changed_test_includes_transitive_consumers_without_unrelated_tests(tmp_path):
    tests = tmp_path / 'tests'; tests.mkdir()
    for name, text in [('base', 'def test_base(): pass'), ('middle', 'from test_base import fixture'),
                       ('consumer', 'import test_middle'), ('unrelated', 'def test_other(): pass')]:
        (tests / f'test_{name}.py').write_text(text, encoding='utf-8')
    assert select_tests(['tests/test_base.py'], tmp_path)['tests'] == [
        'tests/test_base.py', 'tests/test_consumer.py', 'tests/test_middle.py']


def test_unmapped_executable_change_still_selects_critical_boundaries():
    selected = select_tests(['tools/new_runtime_tool.py'])['tests']
    assert selected and 'tests/test_payment_atomicity.py' in selected


def test_committed_base_selection_and_cli_output(tmp_path):
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=tmp_path, stderr=subprocess.PIPE).decode().strip()
    git('init'); git('config', 'user.email', 'fixture@invalid.test'); git('config', 'user.name', 'Fixture')
    (tmp_path / 'README.md').write_text('first')
    git('add', 'README.md'); git('commit', '-m', 'first'); base = git('rev-parse', 'HEAD')
    (tmp_path / 'README.md').write_text('second')
    git('add', 'README.md'); git('commit', '-m', 'second')
    assert changed_paths(base, tmp_path) == ['README.md']
    (tmp_path / 'uncommitted.py').write_text('not a committed input')
    assert changed_paths(base, tmp_path) == ['README.md']
    output = tmp_path / 'output'
    result = subprocess.run([sys.executable, str(ROOT / 'tools/check_scope.py'), '--base', 'HEAD',
                             '--github-output', str(output)], capture_output=True, text=True, encoding='utf-8')
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['tests'] == [] and output.read_text().strip() == 'tests=[]'


def test_ci_uses_local_entrypoint_and_separates_explicit_validation():
    ci = yaml.load((ROOT / '.github/workflows/ci.yml').read_text(encoding='utf-8'), Loader=yaml.BaseLoader)
    validation = yaml.load((ROOT / '.github/workflows/validation.yml').read_text(encoding='utf-8'), Loader=yaml.BaseLoader)
    assert set(ci['on']) == {'push', 'pull_request'}
    assert set(ci['jobs']) == {'check'}
    steps = ci['jobs']['check']['steps']
    commands = [step.get('run', '') for step in steps]
    assert sum('pip install' in command for command in commands) == 1
    assert any('scripts/check.py --changed-from' in command for command in commands)
    assert not any('--full' in command or 'verify_deployment' in command for command in commands)
    assert steps[-1]['if'] == 'always()'
    assert set(steps[-1]['with']['path'].split()) == {
        'temp/checks/ci/report.json', 'temp/checks/ci/*.log', 'temp/checks/ci/pytest.xml'}
    assert set(validation['on']) == {'workflow_dispatch', 'schedule'}
    for name in ('full', 'deployment'):
        assert 'inputs.suite' in validation['jobs'][name]['if']
    assert any('scripts/check.py --full' in step.get('run', '') for step in validation['jobs']['full']['steps'])
