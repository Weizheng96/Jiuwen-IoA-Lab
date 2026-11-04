"""Coverage-preserving test plans; execution stays in the repository check runner.

First keeps every existing matrix until a reviewed equivalent replaces it.
Module diagnostics expand whole files, never QUICK_CASES representative samples.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]

# Reviewed replacements preserve each old scene's spec, original values and
# negative assertions while sharing the identical immutable verifier package.
APPROVED_REPLACEMENTS = {
    'tests/test_nail_flow.py::test_scene_verifier_rejects_mismatched_spec_and_broken_source_structure[kuaida]':
        'tests/test_programming_services.py::test_owned_carriers_accept_real_core_and_reject_wrong_carrier_or_broken_source[code-website]',
    'tests/test_nail_flow.py::test_scene_verifier_rejects_mismatched_spec_and_broken_source_structure[mashangfei]':
        'tests/test_programming_services.py::test_owned_carriers_accept_real_core_and_reject_wrong_carrier_or_broken_source[code-miniapp-reliable]',
}
REPLACEMENT_COVERAGE = {
    old: {
        'target': target,
        'reason': 'Reuse the identical owned verifier package; preserve actual Demo sample/spec values and all WASM assertions.',
        'boundaries': ['verifier identity/source/reads/criteria equality', 'booking.json', 'target=booking', 'period=1'],
        'checks': ['scene good passes and criteria count matches', 'open mismatch: check[0] fails',
                   'close mismatch: check[0] fails', 'service name mismatch: check[0] fails',
                   'service minutes mismatch: check[0] fails', 'missing engineSource fails'] + (
                   ['invalid miniProgram app.json: check[4] fails', 'missing miniProgram page: check[4] fails']
                   if '[mashangfei]' in old else ['missing website app.js: check[4] fails']),
    }
    for old, target in APPROVED_REPLACEMENTS.items()
}


def inventory(root=ROOT):
    root = Path(root)
    modules = {}
    for path in sorted((root / 'tests').rglob('test_*.py')):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError('Test sources must stay inside the repository: ' + relative)
        tree = ast.parse(path.read_text(encoding='utf-8-sig'), filename=relative)
        if not any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and
                   n.name.startswith('test_') for n in ast.walk(tree)):
            raise ValueError('No test definitions in ' + relative)
        parts = path.relative_to(root / 'tests').with_suffix('').parts
        module = '.'.join((*parts[:-1], parts[-1].removeprefix('test_')))
        modules[module] = relative
    if not modules:
        raise ValueError('No test modules found; coverage cannot be established')
    return modules


def plan(*, module=None, root=ROOT):
    modules = inventory(root)
    if module is not None and module not in modules:
        raise ValueError('Unknown diagnostic module: ' + module)
    targets = [modules[module]] if module is not None else list(modules.values())
    return {'layer': 'diagnose' if module is not None else 'first',
            'tests': targets,
            'replacements': dict(APPROVED_REPLACEMENTS),
            'replacementCoverage': REPLACEMENT_COVERAGE,
            'diagnostics': {name: path for name, path in modules.items()},
            'coverage': 'All effective coverage retained, including reviewed replacement mappings; no representative sampling.',
            'limits': 'First has no unproven matrix reductions. Environment-gated tests remain gated; '
                      'stage selfcheck and real deployment evidence are separate.'}


def failed_modules(report_dir, modules):
    """Resolve only actual pytest failure records; never auto-run another suite."""
    path = Path(report_dir) / 'pytest.xml'
    if not path.is_file():
        return []
    failed = set()
    try:
        tree = ET.parse(path)
    except (ET.ParseError, OSError):
        return []  # A damaged report must not replace the original stage exit code.
    for case in tree.iter('testcase'):
        if not any(case.find(tag) is not None for tag in ('failure', 'error')):
            continue
        classname = case.get('classname', '')
        for name, source in modules.items():
            dotted = source.removesuffix('.py').replace('/', '.')
            if classname == dotted or classname.startswith(dotted + '.'):
                failed.add(name)
    return sorted(failed)


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--diagnose', metavar='MODULE')
    args = parser.parse_args()
    try:
        result = plan(module=args.diagnose)
    except (ValueError, SyntaxError, OSError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
