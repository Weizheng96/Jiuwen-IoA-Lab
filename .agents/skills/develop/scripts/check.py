"""Run lightweight checks by default; select focused tests or full gates explicitly."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[4]


def selector():
    spec = importlib.util.spec_from_file_location('ioalab_check_scope', ROOT / 'tools/check_scope.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument('--tests', nargs='+', help='focused pytest paths or nodeids')
    scope.add_argument('--full', action='store_true', help='explicitly run all four repository gates')
    scope.add_argument('--changed-from', help='quick CI scope from a committed base revision to HEAD')
    scope.add_argument('--layer', choices=['fast', 'chain'],
                       help='fast: every test outside tests/chain_layer.json; chain: the listed whole-chain and opt-in host tests')
    scope.add_argument('--module', metavar='NAME',
                       help="union of one module's interface-page test column, e.g. --module 支付中心")
    scope.add_argument('--diagnose', metavar='MODULE', help='whole test module from the diagnostic map')
    parser.add_argument('--report-dir', type=Path, help='new directory for stage logs and report.json')
    args = parser.parse_args()
    helper = ROOT / '.agents/skills/design/scripts/check.py'
    if not helper.is_file() or not (ROOT / 'AGENTS.md').is_file():
        parser.error('run this helper from its repository skill location')
    spec = importlib.util.spec_from_file_location('ioalab_repository_checks', helper)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    steps = runner.base_steps()
    layer_module = None
    layer_plan = None
    if args.layer or args.diagnose:
        layer_spec = importlib.util.spec_from_file_location('ioalab_test_layers', ROOT / 'tools/test_layers.py')
        layer_module = importlib.util.module_from_spec(layer_spec)
        layer_spec.loader.exec_module(layer_module)
        try:
            layer_plan = layer_module.plan(layer=args.layer, module=args.diagnose, root=ROOT)
        except (ValueError, SyntaxError, OSError) as exc:
            parser.error(str(exc))
        summary = {key: layer_plan[key] for key in ('layer', 'tests', 'pytestArgs')} | layer_plan.get('manifest', {})
        print('Test layer: ' + json.dumps(summary, ensure_ascii=False), flush=True)
        steps.append(runner.test_step([*layer_plan['pytestArgs'], *layer_plan['tests']]))
    elif args.full:
        steps.extend(runner.full_steps())
    elif args.tests:
        steps.append(runner.test_step(args.tests))
    elif args.module:
        try:
            targets = selector().module_targets(args.module)
        except ValueError as exc:
            parser.error(str(exc))
        print('Module tests: ' + json.dumps({'module': args.module, 'tests': targets}, ensure_ascii=False), flush=True)
        steps.append(runner.test_step(targets))
    elif args.changed_from:
        scope_module = selector()
        try:
            plan = scope_module.select_tests(scope_module.changed_paths(args.changed_from), base=args.changed_from)
        except scope_module.subprocess.CalledProcessError:
            parser.error('cannot resolve/diff --changed-from; fetch the base revision first')
        print('Quick scope: ' + str(plan), flush=True)
        if plan['tests']:
            steps.append(runner.test_step(plan['tests']))
    report_dir = args.report_dir
    if layer_plan and report_dir is None:
        report_dir = ROOT / 'temp/checks' / ('layer-' + uuid.uuid4().hex)
    result = runner.run_checks(steps, report_dir)
    if result and layer_plan:
        failed = layer_module.failed_modules(report_dir, layer_plan['diagnostics'])
        if failed:
            for module in failed:
                print('Diagnose: python .agents/skills/develop/scripts/check.py --diagnose ' + module, flush=True)
        else:
            print('No mapped pytest failure record; inspect stage exit codes and logs before diagnosing. '
                  'Module targets are in the Test layer plan; no tests were retried.', flush=True)
    return result


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace', newline='')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace', newline='')
    raise SystemExit(main())
