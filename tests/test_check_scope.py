"""Fast CI selection, committed-input isolation and explicit complete validation."""
import ast
import fnmatch
import json
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

from tools.check_scope import CRITICAL, FILE_RULES, HELPER_CASES, QUICK_CASES, ROOT, changed_paths, select_tests


def has_target(selected, target):
    return target in selected or target.split('::')[0] in selected


@pytest.mark.parametrize('paths', [[], ['README.md'], ['设计/流程/助手通信.md'],
                                  ['.agents/skills/develop/SKILL.md']])
def test_prose_changes_keep_lightweight_scope(paths):
    assert select_tests(paths)['tests'] == []


def test_maintenance_scope_does_not_pull_in_business_or_installation_tests():
    assert select_tests(['.github/workflows/ci.yml', 'tools/check_scope.py'])['tests'] == [
        'tests/test_check_scope.py', 'tests/test_check_workflow.py']


@pytest.mark.parametrize('path', ['demo/script_documents.py', 'demo/review_story.py'])
def test_review_draft_sources_select_generation_and_evidence_checks_only(path):
    assert select_tests([path])['tests'] == ['tests/test_demo_artifacts.py', 'tests/test_review_story.py']


@pytest.mark.parametrize('path', ['demo/build_voice.py', 'demo/approved_voice.py'])
def test_approved_voice_sources_select_cache_and_existing_evidence_checks_only(path):
    result = select_tests([path])
    assert result['level'] == 'B' and result['warnings'] == []
    assert result['tests'] == ['tests/test_approved_voice.py', 'tests/test_demo_artifacts.py']


def test_approved_voice_owner_does_not_spread_to_unrelated_demo_or_test_edits():
    assert 'tests/test_approved_voice.py' not in select_tests(['demo/unrelated_voice_example.py'])['tests']
    assert select_tests(['tests/test_approved_voice.py'])['tests'] == ['tests/test_approved_voice.py']


@pytest.mark.parametrize('path', ['requirements.txt', 'pyproject.toml', 'tests/conftest.py', 'tests/_support.py'])
def test_shared_inputs_cannot_be_filtered_as_prose_or_test_helpers(path):
    result = select_tests([path])
    assert result['level'] == 'B'
    assert all(has_target(result['tests'], target) for target in CRITICAL)
    assert any('shared dependency/fixture' in warning for warning in result['warnings'])


@pytest.mark.parametrize('path,expected', [
    ('src/access/sdk/ioalab_sdk/assistant/role.py', 'tests/test_assistant_role.py'),
    ('src/access/sdk/ioalab_sdk/provider/production.py', 'tests/test_provider_task_recovery.py'),
    ('src/platform/ioalab/payments/ledger.py', 'tests/test_payment_atomicity.py'),
    ('deploy/deployment.json.example', 'tests/test_vendor_deployment.py'),
    ('src/access/skills/assistant/SKILL.md', 'tests/test_skill_flows.py'),
])
def test_runtime_scope_keeps_related_and_critical_boundaries(path, expected):
    selected = select_tests([path])['tests']
    assert expected in selected
    if not path.endswith('.md'):
        assert all(has_target(selected, target) for target in CRITICAL)
    assert 'tests/test_package_installation.py' not in selected


def test_exact_fulfillment_owner_avoids_unrelated_domain_and_full_matrices():
    selected = select_tests(['src/access/sdk/ioalab_sdk/assistant/fulfillment.py'])['tests']
    assert 'tests/test_fulfillment_status.py' in selected
    assert all(target in selected for target in QUICK_CASES['resident_multiperiod'])
    assert 'tests/test_resident_multiperiod.py' not in selected
    assert 'tests/test_candidate_evidence.py' not in selected
    assert 'tests/test_inbox_rules.py' not in selected
    assert 'tests/test_installation_identity.py' not in selected
    assert not any('arbitration' in target for target in selected)


def test_payment_owners_add_recovery_boundaries_without_removing_existing_coverage(monkeypatch):
    owners = {
        'src/access/sdk/ioalab_sdk/assistant/fulfillment.py': (
            ('fulfillment_status', 'resident_fulfillment', 'resident_multiperiod', 'user_confirmations', 'payment_atomicity'),
            ('fulfillment_payment_guards', 'resident_payment_recovery')),
        'src/access/sdk/ioalab_sdk/assistant/trading.py': (
            ('assistant_role', 'sdk_inputs', 'resident_contracts'), ('payment_transport_recovery',)),
        'src/platform/ioalab/operations.py': (
            ('core_*', 'arbitration*', 'proposal_expiry', 'quote_*', 'provider_admission'), ('payment_rejection',)),
    }
    for name in ('adapters/payments.py', 'adapters/transport.py', 'runtime/store.py'):
        owners['src/access/sdk/ioalab_sdk/' + name] = (
            ('runtime_delivery', 'runtime_scheduling', 'installation_identity', 'sdk_inputs'), ('payment_transport_recovery',))
    for path, (original, additions) in owners.items():
        selected = set(select_tests([path])['tests'])
        with monkeypatch.context() as previous:
            previous.setitem(FILE_RULES, path, original)
            baseline = set(select_tests([path])['tests'])
        assert baseline <= selected
        assert selected - baseline == {'tests/test_' + name + '.py' for name in additions}


def test_payment_owners_do_not_expand_unrelated_roles_or_test_edits():
    payment_tests = {'tests/test_' + name + '.py' for name in
                     ('fulfillment_payment_guards', 'resident_payment_recovery', 'payment_rejection', 'payment_transport_recovery')}
    for path in ('src/access/sdk/ioalab_sdk/assistant/rules.py', 'src/access/sdk/ioalab_sdk/runtime/config.py'):
        assert not payment_tests.intersection(select_tests([path])['tests'])
    assert select_tests(['tests/test_payment_transport_recovery.py'])['tests'] == ['tests/test_payment_transport_recovery.py']


def test_owner_view_and_its_js_driver_select_behavior_and_security_checks():
    for path in ['src/access/sdk/ioalab_sdk/runtime/web/user.html', 'tests/js/user_confirmation_view.cjs']:
        selected = select_tests([path])['tests']
        assert 'tests/test_user_confirmation_view.py' in selected
        if path.startswith('src/'):
            assert 'tests/test_user_confirmations.py' in selected
        assert 'tests/test_resident_multiperiod.py' not in selected
        assert 'tests/test_resident_runtime.py' not in selected


def test_changed_test_imports_are_structural_transitive_and_ignore_strings(tmp_path):
    tests = tmp_path / 'tests'; tests.mkdir()
    files = {
        'base': 'def helper(): return 1\n\ndef test_base(): pass\n',
        'middle': 'from test_base import helper as original\n\ndef helper(): return original()\n',
        'consumer': 'import test_middle as source\n\ndef test_consumer(): assert source.helper()\n',
        'unrelated': '# import test_base\nexample = "test_base"\ndef test_other(): pass\n',
    }
    for name, text in files.items():
        (tests / f'test_{name}.py').write_text(text, encoding='utf-8')
    assert select_tests(['tests/test_base.py'], tmp_path)['tests'] == [
        'tests/test_base.py', 'tests/test_consumer.py', 'tests/test_middle.py']


@pytest.fixture
def git_repo(tmp_path):
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=tmp_path, stderr=subprocess.PIPE).decode().strip()
    git('init'); git('config', 'user.email', 'fixture@invalid.test'); git('config', 'user.name', 'Fixture')
    tests = tmp_path / 'tests'; tests.mkdir()
    (tests / 'test_base.py').write_text('def helper(): return 1\n\ndef test_base(): assert helper() == 1\n')
    (tests / 'test_consumer.py').write_text('from test_base import helper\n\ndef test_consumer(): assert helper() == 1\n')
    git('add', '.'); git('commit', '-m', 'base')
    return tmp_path, git, git('rev-parse', 'HEAD')


def test_committed_test_body_only_change_does_not_expand_to_helper_consumers(git_repo):
    root, git, base = git_repo
    (root / 'tests/test_base.py').write_text('def helper(): return 1\n\ndef test_base(): assert helper() > 0\n')
    git('add', '.'); git('commit', '-m', 'test assertion')
    assert select_tests(changed_paths(base, root), root, base=base)['tests'] == ['tests/test_base.py']
    # The committed selector must not parse or include these working edits.
    (root / 'tests/test_base.py').write_text('invalid python !')
    (root / 'tests/test_untracked.py').write_text('invalid python !')
    assert select_tests(changed_paths(base, root), root, base=base)['tests'] == ['tests/test_base.py']


def test_committed_helper_change_reaches_importers_through_local_wrapper(git_repo):
    root, git, base = git_repo
    (root / 'tests/test_base.py').write_text('def inner(): return 2\n\ndef helper(): return inner()\n\ndef test_base(): pass\n')
    git('add', '.'); git('commit', '-m', 'shared helper')
    assert select_tests(changed_paths(base, root), root, base=base)['tests'] == [
        'tests/test_base.py', 'tests/test_consumer.py']


def test_known_harness_entry_uses_explicit_consumers_but_new_helper_falls_back(git_repo, monkeypatch):
    root, git, base = git_repo
    (root / 'tests/test_extra.py').write_text('from test_base import helper\n\ndef test_extra(): assert helper()\n')
    git('add', '.'); git('commit', '-m', 'extra consumer'); base = git('rev-parse', 'HEAD')
    monkeypatch.setitem(HELPER_CASES, 'tests/test_base.py', ({'helper'}, ('consumer',)))
    (root / 'tests/test_base.py').write_text('def helper(): return 2\n\ndef test_base(): pass\n')
    git('add', '.'); git('commit', '-m', 'mapped helper')
    assert select_tests(changed_paths(base, root), root, base=base)['tests'] == [
        'tests/test_base.py', 'tests/test_consumer.py']
    (root / 'tests/test_base.py').write_text('def nested(): return 3\n\ndef helper(): return nested()\n\ndef test_base(): pass\n')
    git('add', '.'); git('commit', '-m', 'unmapped helper')
    assert 'tests/test_extra.py' in select_tests(changed_paths(base, root), root, base=base)['tests']


def test_quick_matrix_still_includes_new_test_functions(git_repo, monkeypatch):
    root, _, _ = git_repo
    monkeypatch.setitem(QUICK_CASES, 'base', ('tests/test_base.py::test_base[representative]',))
    with (root / 'tests/test_base.py').open('a') as stream:
        stream.write('\ndef test_new_boundary(): pass\n')
    selected = select_tests(['tests/test_base.py'], root)['tests']
    assert 'tests/test_base.py::test_base[representative]' in selected
    assert 'tests/test_base.py::test_new_boundary' in selected


def test_quick_matrix_collects_whole_new_class_and_new_parametrized_async_methods(git_repo, monkeypatch):
    root, _, _ = git_repo
    monkeypatch.setitem(QUICK_CASES, 'base', ('tests/test_base.py::test_base[1]',))
    source = '''import pytest
@pytest.mark.parametrize('value', [1, 2])
def test_base(value): pass
@pytest.mark.parametrize('value', [3, 4])
class TestAdded:
    def test_sync(self, value): pass
    @pytest.mark.parametrize('other', ['a', 'b'])
    async def test_async(self, value, other): pass
'''
    path = root / 'tests/test_base.py'
    path.write_text(source)
    selected = select_tests(['tests/test_base.py'], root)['tests']
    assert 'tests/test_base.py::TestAdded' in selected
    assert 'tests/test_base.py' not in selected
    # Actual pytest collection proves class and method parametrization survives;
    # no test body (including the async methods) executes in this subprocess.
    def collect():
        result = subprocess.run([sys.executable, '-m', 'pytest', '--collect-only', '-q', *selected],
                                cwd=root, capture_output=True, text=True, encoding='utf-8', timeout=10)
        assert result.returncode == 0, result.stdout + result.stderr
        return [line for line in result.stdout.splitlines() if '::' in line and '.py' in line]
    first = collect()
    assert len(first) == 7
    assert sum('::TestAdded::test_sync[' in node for node in first) == 2
    assert sum('::TestAdded::test_async[' in node for node in first) == 4
    path.write_text(source + '    def test_later(self, value): pass\n')
    assert select_tests(['tests/test_base.py'], root)['tests'] == selected
    assert sum('::TestAdded::test_later[' in node for node in collect()) == 2


def test_renamed_deleted_helper_keeps_old_and_new_paths_and_checks_consumer(git_repo):
    root, git, base = git_repo
    git('mv', 'tests/test_base.py', 'tests/test_replacement.py')
    (root / '说明.md').write_text('unicode path', encoding='utf-8')
    git('add', '.'); git('commit', '-m', 'rename helper')
    assert changed_paths(base, root) == ['tests/test_base.py', 'tests/test_replacement.py', '说明.md']
    selected = select_tests(changed_paths(base, root), root, base=base)['tests']
    assert selected == ['tests/test_consumer.py', 'tests/test_replacement.py']
    assert 'tests/test_base.py' not in selected


def test_unmapped_executable_keeps_critical_checks_and_explicit_coverage_warning():
    result = select_tests(['tools/new_runtime_tool.py'])
    assert all(has_target(result['tests'], target) for target in CRITICAL)
    assert result['warnings'] and 'unmapped executable' in result['warnings'][0]


def test_invalid_syntax_is_an_explicit_planning_failure(tmp_path):
    (tmp_path / 'tests').mkdir()
    (tmp_path / 'tests/test_broken.py').write_text('def broken(')
    with pytest.raises(SyntaxError):
        select_tests(['tests/test_broken.py'], tmp_path)


def test_selector_itself_and_runner_change_only_select_maintenance_checks():
    selected = select_tests(['tools/check_scope.py', 'tests/test_check_scope.py',
                             '.agents/skills/develop/scripts/check.py'])['tests']
    assert selected == ['tests/test_check_scope.py', 'tests/test_check_workflow.py']


def test_cli_output_and_invalid_revision_fail_without_emitting_success(tmp_path):
    script = str(ROOT / 'tools/check_scope.py')
    output = tmp_path / 'output'
    good = subprocess.run([sys.executable, script, '--base', 'HEAD', '--github-output', str(output)],
                          capture_output=True, text=True, encoding='utf-8', timeout=10)
    assert good.returncode == 0, good.stderr
    assert json.loads(good.stdout)['tests'] == []
    assert output.read_text().strip() == 'tests=[]'
    failed_output = tmp_path / 'failed-output'
    bad = subprocess.run([sys.executable, script, '--base=--not-a-revision',
                          '--github-output', str(failed_output)], capture_output=True, text=True, timeout=10)
    assert bad.returncode != 0 and not failed_output.exists()


def test_declared_quick_nodes_refer_to_existing_real_test_functions():
    targets = [*CRITICAL, *(target for values in QUICK_CASES.values() for target in values)]
    for target in targets:
        path, _, node = target.partition('::')
        tree = ast.parse((ROOT / path).read_text(encoding='utf-8'))
        if node:
            assert node.split('[')[0] in {value.name for value in tree.body if isinstance(value, (ast.FunctionDef, ast.AsyncFunctionDef))}, target
    available = {path.stem[5:] for path in (ROOT / 'tests').glob('test_*.py')}
    for patterns in FILE_RULES.values():
        assert all(any(fnmatch.fnmatchcase(stem, pattern) for stem in available) for pattern in patterns)


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
    # Inspect the actual runner function; do not run the slow complete gate here.
    runner = ROOT / '.agents/skills/design/scripts/check.py'
    tree = ast.parse(runner.read_text(encoding='utf-8'))
    full = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'full_steps')
    assert 'test_step(["tests"])' in ast.unparse(full) or "test_step(['tests'])" in ast.unparse(full)
    assert 'tools/selfcheck.py' in ast.unparse(full)
