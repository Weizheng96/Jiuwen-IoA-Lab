"""Check scope, real child output and failure propagation in isolated repositories."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import xml.etree.ElementTree as ET

import pytest

ROOT = Path(__file__).resolve().parents[1]


def repository(path, *, design_exit=0):
    for skill in ('design', 'develop'):
        target = path / '.agents/skills' / skill / 'scripts/check.py'
        target.parent.mkdir(parents=True)
        shutil.copy2(ROOT / '.agents/skills' / skill / 'scripts/check.py', target)
    (path / 'tools').mkdir(exist_ok=True)
    shutil.copy2(ROOT / 'tools/check_scope.py', path / 'tools/check_scope.py')
    (path / 'AGENTS.md').write_text('Isolated runner test\n')
    for name, marker, code in [('设计/check.py', 'design', design_exit),
                               ('tools/check_docs.py', 'docs', 0), ('tools/selfcheck.py', 'selfcheck', 0)]:
        script = path / name
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(f'print("{marker}", flush=True)\nraise SystemExit({code})\n', encoding='utf-8')
    (path / 'tests').mkdir()
    for name in ('selected', 'other'):
        (path / f'tests/test_{name}.py').write_text(f'def test_{name}():\n    assert True\n', encoding='utf-8')
    return path


def command(path, *arguments, skill='develop'):
    return [sys.executable, '-X', 'utf8', str(path / '.agents/skills' / skill / 'scripts/check.py'),
            *arguments, '--report-dir', str(path / 'result')]


def run(path, *arguments, skill='develop'):
    result = subprocess.run(command(path, *arguments, skill=skill), cwd=path,
                            capture_output=True, text=True, encoding='utf-8', timeout=30)
    return result, json.loads((path / 'result/report.json').read_text(encoding='utf-8'))


@pytest.mark.parametrize('skill', ['design', 'develop'])
def test_default_check_does_not_launch_pytest_or_selfcheck(tmp_path, skill):
    path = repository(tmp_path)
    (path / 'tests/test_selected.py').write_text('def test_selected():\n    assert False\n', encoding='utf-8')
    result, report = run(path, skill=skill)
    assert result.returncode == 0, result.stdout + result.stderr
    assert report['status'] == 'passed' and len(report['steps']) == 2
    assert all(step['exitCode'] == 0 for step in report['steps'])
    assert 'selfcheck' not in result.stdout and 'test session starts' not in result.stdout


def test_focused_check_runs_only_the_selected_test(tmp_path):
    path = repository(tmp_path)
    (path / 'tests/test_other.py').write_text('def test_other():\n    assert False\n', encoding='utf-8')
    result, report = run(path, '--tests', 'tests/test_selected.py::test_selected')
    assert result.returncode == 0, result.stdout + result.stderr
    assert report['status'] == 'passed' and len(report['steps']) == 3
    assert '1 passed' in result.stdout and 'selfcheck' not in result.stdout
    assert 'test_selected.py::test_selected' in (path / 'result/03.log').read_text(encoding='utf-8')


def test_explicit_full_check_runs_all_four_gates(tmp_path):
    path = repository(tmp_path)
    result, report = run(path, '--full')
    assert result.returncode == 0, result.stdout + result.stderr
    assert report['status'] == 'passed' and len(report['steps']) == 4
    assert '2 passed' in result.stdout and 'selfcheck' in result.stdout
    assert all(step['exitCode'] == 0 and step['seconds'] >= 0 for step in report['steps'])
    assert '--durations=20' in report['steps'][2]['command']
    cases = ET.parse(path / 'result/pytest.xml').findall('.//testcase')
    assert len(cases) == 2 and all(float(case.attrib['time']) >= 0 for case in cases)
    assert '--basetemp=' + str(path / 'result/pytest-work') in report['steps'][2]['command']


def test_failure_preserves_real_exit_output_and_stops_later_gates(tmp_path):
    path = repository(tmp_path, design_exit=7)
    result, report = run(path, '--full')
    assert result.returncode == 7, result.stdout + result.stderr
    assert report['status'] == 'failed' and len(report['steps']) == 1
    assert report['steps'][0]['exitCode'] == 7
    assert (path / 'result/01.log').read_text(encoding='utf-8').strip() == 'design'
    assert 'Running: Documentation' not in result.stdout and 'selfcheck' not in result.stdout


def test_output_and_running_report_are_visible_before_child_exits(tmp_path):
    path = repository(tmp_path)
    (path / '设计/check.py').write_text(
        'from pathlib import Path\nimport time\nprint("live-marker", flush=True)\n'
        'while not Path("release").exists(): time.sleep(.02)\n', encoding='utf-8')
    seen = threading.Event()
    with subprocess.Popen(command(path), cwd=path, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, encoding='utf-8') as process:
        def read():
            for line in process.stdout:
                if 'live-marker' in line:
                    seen.set()
        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        try:
            assert seen.wait(5), 'Child output was buffered until exit'
            report = json.loads((path / 'result/report.json').read_text(encoding='utf-8'))
            assert process.poll() is None and report['status'] == report['steps'][0]['status'] == 'running'
            assert 'exitCode' not in report['steps'][0]
            assert 'live-marker' in (path / 'result/01.log').read_text(encoding='utf-8')
        finally:
            (path / 'release').touch()
            process.wait(timeout=10)
            reader.join(timeout=5)
    assert process.returncode == 0


def test_failed_pytest_stops_full_selfcheck_and_preserves_failure_details(tmp_path):
    path = repository(tmp_path)
    (path / 'tests/test_selected.py').write_text('def test_selected():\n    assert False, "failure-detail"\n', encoding='utf-8')
    result, report = run(path, '--full')
    assert result.returncode == 1 and report['status'] == 'failed'
    assert len(report['steps']) == 3 and report['steps'][-1]['exitCode'] == 1
    assert report['finishedAt'] and 'failure-detail' in (path / 'result/03.log').read_text(encoding='utf-8')
    assert ET.parse(path / 'result/pytest.xml').find('.//failure') is not None
    assert 'Running: Repository selfcheck' not in result.stdout


def test_conflicting_scope_flags_fail_before_starting_checks(tmp_path):
    path = repository(tmp_path)
    result = subprocess.run(command(path, '--full', '--tests', 'tests'), capture_output=True, text=True)
    assert result.returncode == 2 and not (path / 'result').exists()


def test_existing_report_is_preserved_instead_of_overwritten(tmp_path):
    path = repository(tmp_path)
    result, _ = run(path)
    assert result.returncode == 0
    before = (path / 'result/report.json').read_bytes()
    second = subprocess.run(command(path), capture_output=True, text=True)
    assert second.returncode != 0 and (path / 'result/report.json').read_bytes() == before


def test_invalid_committed_scope_fails_without_reporting_success(tmp_path):
    path = repository(tmp_path)
    result = subprocess.run(command(path, '--changed-from', 'missing-base'), capture_output=True, text=True)
    assert result.returncode == 2 and not (path / 'result').exists()
