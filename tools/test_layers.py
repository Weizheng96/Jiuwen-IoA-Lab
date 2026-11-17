"""Two measured test layers; execution stays in the repository check runner.

fast: every test not listed in tests/chain_layer.json. chain: the listed whole-chain,
real-process and opt-in host/model functions. Module diagnostics expand whole files,
never representative samples. The manifest is maintained from real pytest.xml timings
with --audit; it is advisory and never edits the manifest itself.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = 'tests/chain_layer.json'
LAYERS = {'fast': ['-m', 'not chain'], 'chain': ['-m', 'chain']}

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


def test_functions(root=ROOT):
    """Test file -> test function names; class methods are written Class::method."""
    root = Path(root)
    result = {}
    for relative in inventory(root).values():
        tree = ast.parse((root / relative).read_text(encoding='utf-8-sig'), filename=relative)
        names = set()

        def visit(body, prefix=''):
            for node in body:
                if isinstance(node, ast.ClassDef):
                    visit(node.body, prefix + node.name + '::')
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith('test_'):
                    names.add(prefix + node.name)

        visit(tree.body)
        result[relative] = names
    return result

# Chain entries retired by the explicit Codex S01 migration (ticket 31): the old
# run/probe/HostTurn functions were rewritten, not renamed. Each old rule keeps a
# named S01 home; the new functions measure under thresholdSeconds and stay fast.
_CODEX = 'tests/test_codex_host.py::'
RETIRED_CHAIN = {
    _CODEX+'test_bound_tool_round_and_new_host_resume_keep_the_original_session': {
        'targets': [_CODEX+'test_bound_tool_round_has_actual_turn_and_prior_p28_ack'],
        'rule': 'SDK tool callback only after P28 ACK of the actual turn; a second submit with the original session resumes it',
        'fails': 'callback before ACK, session/request mismatch, or the resumed submit changes session_id'},
    _CODEX+'test_lost_result_keeps_session_and_never_retries': {
        'targets': [_CODEX+'test_lost_result_keeps_machine_id_and_same_operation_does_not_replay'],
        'rule': 'Lost terminal stays unknown with the actual session/request; the same operation never replays',
        'fails': 'state other than unknown, missing machine IDs, or a second dispatch'},
    _CODEX+'test_changed_definition_and_missing_original_binding_are_rejected': {
        'targets': [_CODEX+'test_original_scope_cannot_change_or_bind_another_session',
                    _CODEX+'test_missing_p28_ack_cannot_activate_sdk_tools',
                    _CODEX+'test_execution_cannot_override_sdk_attempt_identity'],
        'rule': 'Original scope/attempt is immutable and a missing binding ACK blocks SDK tools',
        'fails': 'another session bound, changed scope accepted, or tools reachable without ACK'},
    _CODEX+'test_cancel_returns_actual_terminal_and_cannot_claim_no_effect': {
        'targets': [_CODEX+'test_cancel_ack_does_not_replace_actual_terminal'],
        'rule': 'Cancel acceptance never replaces the actual machine terminal or claims no effect',
        'fails': 'cancel acknowledgement reported as terminal or not_submitted'},
    _CODEX+'test_native_tools_and_unprobed_rounds_fail_before_start': {
        'targets': [_CODEX+'test_sdk_native_permissions_reject_before_any_native_submission',
                    _CODEX+'test_true_native_phase_is_not_ignored_with_empty_native_requirements',
                    _CODEX+'test_mutated_non_boolean_native_phase_is_rejected_before_dispatch',
                    _CODEX+'test_static_registration_and_check_are_not_submit_gate'],
        'rule': 'Unfulfillable native permissions are not_submitted before any process; design 196 removed the probe gate',
        'fails': 'native permission true/invalid reaches the host process, or check becomes a submit gate'},
    _CODEX+'test_selected_skills_are_frozen_and_cannot_be_replaced_on_resume': {
        'targets': [_CODEX+'test_resources_preserve_original_reference_and_fail_before_dispatch_until_p26_glue'],
        'rule': 'Frozen professional resources keep the original reference; until P26 glue they fail before dispatch',
        'fails': 'a latest skill directory is read or a resource submission is dispatched',
        'gap': 'positive frozen-skill injection and resume non-replacement return with the P26 reader (ticket 31/33)'},
    _CODEX+'test_unbound_namespace_cannot_reach_the_role_callback': {
        'targets': [_CODEX+'test_unbound_namespace_never_enters_sdk_callback'],
        'rule': 'Tool calls outside the bound namespace never enter the SDK callback',
        'fails': 'any foreign namespace reaches on_tool'},
    _CODEX+'test_unresponsive_child_cannot_block_the_turn_deadline_while_writing': {
        'targets': [_CODEX+'test_bounded_turn_deadline_keeps_unknown_and_original_session'],
        'rule': 'A child that stops reading 1 MiB input cannot outlive the original deadline; result stays unknown',
        'fails': 'deadline exceeded, terminal claimed, or session lost'},
    # Ticket 27 S01 slice: design 196 removed host capabilities/probe, so provider status can no
    # longer learn a host deny policy. The resident function was renamed (same frozen-manifest
    # body, still chain); the status-time deny block retired to each adapter's submission check.
    'tests/test_resident_contracts.py::test_original_native_tool_manifest_is_frozen_and_denied_tools_block_new_service': {
        'targets': ['tests/test_resident_contracts.py::test_original_native_tool_manifest_is_frozen_and_native_requirements_are_checked_at_submission',
                    'tests/test_codex_host.py::test_sdk_native_permissions_reject_before_any_native_submission',
                    'tests/test_claude_code_host.py::test_unsupported_native_permission_is_honestly_not_dispatched',
                    'tests/test_jiuwen_host.py::test_invalid_or_unavailable_native_tool_does_not_start_a_process'],
        'rule': 'The original native tool manifest is frozen per business; status never probes the host and reports unconfirmed tools as unchecked; a native requirement the adapter cannot honor fails before any native dispatch',
        'fails': 'a later config changes the frozen manifest or digest, status probes or blocks on a host policy, or an unfulfillable native requirement reaches a native process',
        'gap': 'no resident end-to-end case yet shows a provider business submission with an adapter-unfulfillable native tool ending not_submitted without quote/effect; adapter unit cases cover the S01 rejection (tickets 27/33, 30-32)'},
}


def manifest(root=ROOT):
    """The chain-layer manifest, validated against the real test functions; fails closed."""
    path = Path(root) / MANIFEST
    if not path.is_file():
        raise ValueError('Missing layer manifest: ' + MANIFEST)
    data = json.loads(path.read_text(encoding='utf-8'))
    chain, threshold = data.get('chain'), data.get('thresholdSeconds')
    if not isinstance(chain, dict) or isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or threshold <= 0:
        raise ValueError(MANIFEST + ' must map test files to function names and give a positive thresholdSeconds')
    known = test_functions(root)
    unknown = []
    for file, names in chain.items():
        if not isinstance(names, list) or not names or not all(isinstance(name, str) for name in names):
            unknown.append(file)
            continue
        unknown.extend(file + '::' + name for name in names if name not in known.get(file, ()))
    if unknown:
        shown = sorted(unknown)
        raise ValueError(MANIFEST + ' lists tests that do not exist: ' + ', '.join(shown[:8]) +
                         (f' and {len(shown) - 8} more' if len(shown) > 8 else ''))
    return {'threshold': float(threshold), 'chain': {file: set(names) for file, names in chain.items()}}


def plan(*, layer=None, module=None, root=ROOT):
    modules = inventory(root)
    if module is not None and module not in modules:
        raise ValueError('Unknown diagnostic module: ' + module)
    if layer is not None and layer not in LAYERS:
        raise ValueError('Unknown layer: ' + str(layer))
    result = {'replacements': dict(APPROVED_REPLACEMENTS), 'replacementCoverage': REPLACEMENT_COVERAGE,
              'retiredChain': RETIRED_CHAIN,
              'diagnostics': {name: path for name, path in modules.items()},
              'coverage': 'All effective coverage retained, including reviewed replacement mappings; no representative sampling.',
              'limits': 'Layers select by the measured manifest only. Environment-gated tests remain gated; '
                        'stage selfcheck and real deployment evidence are separate.'}
    if module is not None:
        result.update(layer='diagnose', tests=[modules[module]], pytestArgs=[])
    elif layer is not None:
        listed = manifest(root)['chain']
        result.update(layer=layer, tests=['tests'], pytestArgs=list(LAYERS[layer]),
                      manifest={'files': len(listed), 'functions': sum(len(names) for names in listed.values())})
    else:
        result.update(layer='all', tests=list(modules.values()), pytestArgs=[])
    return result


def audit(report, root=ROOT):
    """Compare real per-test timings with the manifest. Advisory output, no manifest edits."""
    layer = manifest(root)
    threshold = layer['threshold']
    dotted = {path[:-3].replace('/', '.'): path for path in test_functions(root)}
    measured, skipped = {}, set()
    for case in ET.parse(report).iter('testcase'):
        classname, name = case.get('classname', ''), case.get('name', '').split('[')[0]
        for prefix, path in dotted.items():
            if classname == prefix or classname.startswith(prefix + '.'):
                classes = classname[len(prefix) + 1:].split('.') if classname != prefix else []
                key = (path, '::'.join([*classes, name]))
                break
        else:
            continue
        if case.find('skipped') is not None:
            skipped.add(key)
            continue
        measured[key] = max(measured.get(key, 0.0), float(case.get('time') or 0))
    listed = {(file, name) for file, names in layer['chain'].items() for name in names}
    return {
        'threshold': threshold,
        'unlistedSlow': sorted(f'{path}::{name} {seconds:.2f}s' for (path, name), seconds in measured.items()
                               if seconds >= threshold and (path, name) not in listed),
        'listedFast': sorted(f'{path}::{name} {measured[(path, name)]:.2f}s' for path, name in listed
                             if (path, name) in measured and measured[(path, name)] < threshold / 2),
        'unmeasured': sorted(f'{path}::{name}' for path, name in listed
                             if (path, name) not in measured and (path, name) not in skipped),
        'skipped': sorted(f'{path}::{name}' for path, name in listed if (path, name) in skipped),
    }


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
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--layer', choices=sorted(LAYERS))
    group.add_argument('--diagnose', metavar='MODULE')
    group.add_argument('--audit', metavar='PYTEST_XML', help='compare real timings with the manifest')
    args = parser.parse_args()
    try:
        result = audit(args.audit) if args.audit else plan(layer=args.layer, module=args.diagnose)
    except (ValueError, SyntaxError, OSError, ET.ParseError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
