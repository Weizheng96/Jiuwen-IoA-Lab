"""Coverage-preserving plans and failure diagnostics without running a slow suite."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from tools.test_layers import ROOT, failed_modules, plan


def sources(root):
    tests = root / 'tests'
    tests.mkdir(parents=True)
    (tests / 'test_payments.py').write_text(
        "import pytest\n@pytest.mark.parametrize('fault', ['lost', 'rollback'])\n"
        "def test_recovery(fault): assert fault\n", encoding='utf-8')
    nested = tests / 'integration'
    nested.mkdir()
    (nested / 'test_process.py').write_text(
        'class TestOwner:\n    async def test_release(self): pass\n', encoding='utf-8')


def test_first_preserves_full_files_and_diagnostics_never_sample_matrices(tmp_path):
    sources(tmp_path)
    first = plan(root=tmp_path)
    assert set(first['tests']) == {'tests/test_payments.py', 'tests/integration/test_process.py'}
    assert first['diagnostics'] == {
        'integration.process': 'tests/integration/test_process.py', 'payments': 'tests/test_payments.py'}
    diagnosis = plan(module='payments', root=tmp_path)
    assert diagnosis['tests'] == ['tests/test_payments.py']
    assert all('::' not in target for target in first['tests'] + diagnosis['tests'])


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
             'demo/delegation-provider/src/scenario/definitions.py',
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
        'src/access/sdk/ioalab_sdk/assistant/flow.py':'assistant_flow_guidance',
        'src/access/sdk/ioalab_sdk/runtime/confirmation_view.py':'confirmation_view',
        'src/hosts/jiuwen/ioalab_jiuwen/a2ui.py':'jiuwen_a2ui_forms',
        'src/platform/ioalab/web/trial.py':'trial_admission',
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
    command = [sys.executable, '-B', str(develop), '--layer', 'first', '--report-dir', str(report)]
    result = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True,
                            encoding='utf-8', timeout=20, env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
    assert result.returncode == exit_code, result.stdout + result.stderr
    selected = json.loads((tmp_path / 'selected.json').read_text())
    assert selected == [['base', []], ['Tests', ['tests/integration/test_process.py', 'tests/test_payments.py']]]
    assert ('--diagnose payments' in result.stdout) is bool(exit_code)
    (tmp_path / 'selected.json').unlink()
    invalid = subprocess.run([sys.executable, '-B', str(develop), '--diagnose', 'missing'], cwd=tmp_path,
                             capture_output=True, text=True, timeout=20)
    assert invalid.returncode != 0 and not (tmp_path / 'selected.json').exists()
