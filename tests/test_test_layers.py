"""Two measured test layers, manifest validation, audit and failure diagnostics without running a slow suite."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from tools.test_layers import ROOT, audit, failed_modules, manifest, plan

MANIFEST = {'thresholdSeconds': 1.0, 'chain': {'tests/test_payments.py': ['test_recovery'],
                                               'tests/integration/test_process.py': ['TestOwner::test_release']}}


def sources(root, chain=MANIFEST):
    tests = root / 'tests'
    tests.mkdir(parents=True)
    # Same rootdir rule as the repository: nodeids start at the tree root, not at tests/.
    (root / 'pyproject.toml').write_text('[tool.pytest.ini_options]\ntestpaths = ["tests"]\n', encoding='utf-8')
    (tests / 'test_payments.py').write_text(
        "import pytest\n@pytest.mark.parametrize('fault', ['lost', 'rollback'])\n"
        "def test_recovery(fault): assert fault\n\ndef test_quick(): assert True\n", encoding='utf-8')
    nested = tests / 'integration'
    nested.mkdir()
    (nested / 'test_process.py').write_text(
        'class TestOwner:\n    async def test_release(self): pass\n', encoding='utf-8')
    if chain is not None:
        (tests / 'chain_layer.json').write_text(json.dumps(chain), encoding='utf-8')


def test_layers_select_by_marker_and_diagnostics_expand_whole_files(tmp_path):
    sources(tmp_path)
    fast = plan(layer='fast', root=tmp_path)
    assert fast['tests'] == ['tests'] and fast['pytestArgs'] == ['-m', 'not chain']
    assert fast['manifest'] == {'files': 2, 'functions': 2}
    chain = plan(layer='chain', root=tmp_path)
    assert chain['tests'] == ['tests'] and chain['pytestArgs'] == ['-m', 'chain']
    everything = plan(root=tmp_path)
    assert set(everything['tests']) == {'tests/test_payments.py', 'tests/integration/test_process.py'}
    assert everything['diagnostics'] == {
        'integration.process': 'tests/integration/test_process.py', 'payments': 'tests/test_payments.py'}
    diagnosis = plan(module='payments', root=tmp_path)
    assert diagnosis['tests'] == ['tests/test_payments.py'] and diagnosis['pytestArgs'] == []
    assert all('::' not in target for target in fast['tests'] + chain['tests'] + everything['tests'] + diagnosis['tests'])


@pytest.mark.parametrize('broken,message', [
    ({'thresholdSeconds': 1.0, 'chain': {'tests/test_payments.py': ['test_missing']}}, 'do not exist'),
    ({'thresholdSeconds': 1.0, 'chain': {'tests/test_gone.py': ['test_recovery']}}, 'do not exist'),
    ({'thresholdSeconds': 1.0, 'chain': {'tests/test_payments.py': []}}, 'do not exist'),
    ({'thresholdSeconds': 0, 'chain': {}}, 'positive thresholdSeconds'),
    ({'thresholdSeconds': True, 'chain': {}}, 'positive thresholdSeconds'),
    ({'thresholdSeconds': 1.0, 'chain': ['tests/test_payments.py']}, 'map test files'),
])
def test_manifest_must_name_existing_tests_and_a_positive_threshold(tmp_path, broken, message):
    sources(tmp_path, chain=broken)
    with pytest.raises(ValueError, match=message):
        plan(layer='fast', root=tmp_path)
    (tmp_path / 'tests/chain_layer.json').unlink()
    with pytest.raises(ValueError, match='Missing layer manifest'):
        manifest(tmp_path)
    with pytest.raises(ValueError, match='Unknown layer'):
        plan(layer='slow', root=tmp_path)
    # Diagnostics and the complete plan never depend on the manifest.
    assert plan(module='payments', root=tmp_path)['tests'] == ['tests/test_payments.py']
    assert len(plan(root=tmp_path)['tests']) == 2


def test_audit_reports_unlisted_slow_listed_fast_skipped_and_unmeasured(tmp_path):
    sources(tmp_path, chain={'thresholdSeconds': 1.0, 'chain': {
        'tests/test_payments.py': ['test_recovery'], 'tests/integration/test_process.py': ['TestOwner::test_release']}})
    report = tmp_path / 'pytest.xml'
    report.write_text(
        '<testsuites><testsuite>'
        '<testcase classname="tests.test_payments" name="test_recovery[lost]" time="0.10"/>'
        '<testcase classname="tests.test_payments" name="test_recovery[rollback]" time="0.30"/>'
        '<testcase classname="tests.test_payments" name="test_quick" time="2.50"/>'
        '<testcase classname="tests.integration.test_process.TestOwner" name="test_release" time="0.00"><skipped/></testcase>'
        '<testcase classname="tests.unrelated" name="test_other" time="9.00"/>'
        '</testsuite></testsuites>', encoding='utf-8')
    assert audit(report, root=tmp_path) == {
        'threshold': 1.0,
        'unlistedSlow': ['tests/test_payments.py::test_quick 2.50s'],
        'listedFast': ['tests/test_payments.py::test_recovery 0.30s'],
        'unmeasured': [],
        'skipped': ['tests/integration/test_process.py::TestOwner::test_release']}
    report.write_text('<testsuites><testsuite/></testsuites>', encoding='utf-8')
    assert audit(report, root=tmp_path)['unmeasured'] == [
        'tests/integration/test_process.py::TestOwner::test_release', 'tests/test_payments.py::test_recovery']


def test_conftest_marks_manifest_functions_so_layers_select_by_marker(tmp_path):
    sources(tmp_path)
    shutil.copy2(ROOT / 'tests/conftest.py', tmp_path / 'tests/conftest.py')

    def collected(expression):
        result = subprocess.run([sys.executable, '-B', '-m', 'pytest', 'tests', '-m', expression, '--collect-only', '-q',
                                 '-p', 'no:cacheprovider'], cwd=tmp_path, capture_output=True, text=True,
                                encoding='utf-8', timeout=60, env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
        assert result.returncode == 0, result.stdout + result.stderr
        assert 'PytestUnknownMarkWarning' not in result.stdout + result.stderr
        return {line for line in result.stdout.splitlines() if '::' in line}

    assert collected('chain') == {'tests/test_payments.py::test_recovery[lost]', 'tests/test_payments.py::test_recovery[rollback]',
                                  'tests/integration/test_process.py::TestOwner::test_release'}
    assert collected('not chain') == {'tests/test_payments.py::test_quick'}


def test_repository_manifest_lists_existing_functions_only():
    layer = manifest()
    assert layer['threshold'] == 1.0 and layer['chain']
    assert all((ROOT / path).is_file() for path in layer['chain'])
    everything = plan(layer='chain')
    assert everything['manifest']['functions'] == sum(len(names) for names in layer['chain'].values())


def test_removed_scene_cases_and_asset_changes_select_their_real_coverage_owner():
    from test_programming_services import SCENE_REGRESSIONS
    from tools.check_scope import select_tests
    import ast
    from tools.test_layers import APPROVED_REPLACEMENTS, REPLACEMENT_COVERAGE
    assert SCENE_REGRESSIONS == {'code-website': 'kuaida', 'code-miniapp-reliable': 'mashangfei'}
    assert len(APPROVED_REPLACEMENTS) == 2
    assert all(Path(ROOT / target.split('::')[0]).is_file() for target in APPROVED_REPLACEMENTS.values())
    for old, target in APPROVED_REPLACEMENTS.items():
        source, case = target.split('::')
        function, parameter = case.rstrip(']').split('[')
        tree = ast.parse((ROOT / source).read_text(encoding='utf-8'))
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == function)
        assert any(isinstance(value, ast.Constant) and value.value == parameter
                   for decorator in node.decorator_list for value in ast.walk(decorator))
        coverage = REPLACEMENT_COVERAGE[old]
        assert coverage['target'] == target and len(coverage['boundaries']) == 4
        assert {'open mismatch: check[0] fails', 'close mismatch: check[0] fails',
                'service name mismatch: check[0] fails', 'service minutes mismatch: check[0] fails',
                'missing engineSource fails'} <= set(coverage['checks'])
        expected = (['invalid miniProgram app.json: check[4] fails', 'missing miniProgram page: check[4] fails']
                    if '[mashangfei]' in old else ['missing website app.js: check[4] fails'])
        assert set(expected) <= set(coverage['checks'])
    assert plan()['replacementCoverage'] == REPLACEMENT_COVERAGE
    paths = ['tests/test_nail_flow.py',
             'tools/media/delegation-provider/src/scenario/definitions.py',
             'src/official/services/ioalab_services/programming.py',
             'src/official/services/ioalab_services/assets/verifiers/programming.py',
             'src/official/services/ioalab_services/assets/domains/08-dev.json']
    for path in paths:
        selected = select_tests([path])['tests']
        assert 'tests/test_programming_services.py' in selected, path
        assert 'tests/test_nail_flow.py' not in selected, path
    for path, owner in {
        'src/platform/ioalab/storage.py':'domain_migrations',
        'src/platform/ioalab/migrations/__init__.py':'domain_migrations',
        'src/platform/ioalab/identity/migrations.py':'domain_migrations',
        'src/client/sdk/ioalab_sdk/assistant/flow.py':'assistant_flow_guidance',
        'src/client/sdk/ioalab_sdk/runtime/confirmation_view.py':'confirmation_view',
        'src/client/hosts/jiuwen/ioalab_jiuwen/a2ui.py':'jiuwen_a2ui_forms',
        'src/platform/ioalab/web/trial.py':'trial_admission',
        'tests/chain_layer.json':'test_layers',
        'src/platform/ioalab/payments/scopes.py':'rule_boundaries',
        'src/client/sdk/ioalab_sdk/adapters/arbitration.py':'rule_boundaries',
    }.items():
        assert 'tests/test_'+owner+'.py' in select_tests([path])['tests'], path


def test_missing_module_empty_inventory_and_uncollectable_source_fail_closed(tmp_path):
    with pytest.raises(ValueError, match='No test modules'):
        plan(root=tmp_path)
    sources(tmp_path)
    for invalid in ('unknown', '../payments', 'payments::test_recovery[lost]'):
        with pytest.raises(ValueError, match='Unknown diagnostic module'):
            plan(module=invalid, root=tmp_path)
    (tmp_path / 'tests/test_broken.py').write_text('def test_broken(:', encoding='utf-8')
    with pytest.raises(SyntaxError):
        plan(root=tmp_path)
    (tmp_path / 'tests/test_broken.py').write_text('VALUE=1', encoding='utf-8')
    with pytest.raises(ValueError, match='No test definitions'):
        plan(root=tmp_path)


def test_failure_map_uses_actual_failures_and_includes_class_methods(tmp_path):
    sources(tmp_path)
    modules = plan(root=tmp_path)['diagnostics']
    assert failed_modules(tmp_path, modules) == []
    (tmp_path / 'pytest.xml').write_text(
        '<testsuites><testsuite>'
        '<testcase classname="tests.test_payments" name="test_recovery[lost]"/>'
        '<testcase classname="tests.integration.test_process.TestOwner" name="test_release"><error/></testcase>'
        '<testcase classname="tests.unmapped" name="other"><failure/></testcase>'
        '</testsuite></testsuites>', encoding='utf-8')
    assert failed_modules(tmp_path, modules) == ['integration.process']
    (tmp_path / 'pytest.xml').write_text('<unfinished', encoding='utf-8')
    assert failed_modules(tmp_path, modules) == []


@pytest.mark.parametrize('exit_code', [0, 7])
def test_layer_entry_reuses_runner_preserves_exit_and_reports_diagnosis(tmp_path, exit_code):
    sources(tmp_path)
    develop = tmp_path / '.agents/skills/develop/scripts/check.py'
    develop.parent.mkdir(parents=True)
    shutil.copy2(ROOT / '.agents/skills/develop/scripts/check.py', develop)
    helper = tmp_path / '.agents/skills/design/scripts/check.py'
    helper.parent.mkdir(parents=True)
    # Exercise command orchestration using a runner substitute, never pytest recursively.
    helper.write_text(
        'import json\nfrom pathlib import Path\n'
        'def base_steps(): return [("base", [])]\n'
        'def test_step(targets): return ("Tests", targets)\n'
        'def run_checks(steps, report_dir):\n'
        '    Path("selected.json").write_text(json.dumps(steps))\n'
        f'    return {exit_code}\n', encoding='utf-8')
    (tmp_path / 'AGENTS.md').write_text('test', encoding='utf-8')
    (tmp_path / 'tools').mkdir()
    shutil.copy2(ROOT / 'tools/test_layers.py', tmp_path / 'tools/test_layers.py')
    report = tmp_path / 'report'
    report.mkdir()
    (report / 'pytest.xml').write_text(
        '<testsuites><testsuite><testcase classname="tests.test_payments" name="test_recovery">'
        '<failure/></testcase></testsuite></testsuites>', encoding='utf-8')
    env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}
    for layer in ('fast', 'chain'):
        command = [sys.executable, '-B', str(develop), '--layer', layer, '--report-dir', str(report)]
        result = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, encoding='utf-8', timeout=20, env=env)
        assert result.returncode == exit_code, result.stdout + result.stderr
        selected = json.loads((tmp_path / 'selected.json').read_text())
        assert selected == [['base', []], ['Tests', ['-m', 'not chain' if layer == 'fast' else 'chain', 'tests']]]
        assert ('--diagnose payments' in result.stdout) is bool(exit_code)
        (tmp_path / 'selected.json').unlink()
    for invalid in (['--diagnose', 'missing'], ['--layer', 'first']):
        result = subprocess.run([sys.executable, '-B', str(develop), *invalid], cwd=tmp_path,
                                capture_output=True, text=True, timeout=20, env=env)
        assert result.returncode != 0 and not (tmp_path / 'selected.json').exists()
