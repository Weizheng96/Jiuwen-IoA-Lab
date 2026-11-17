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


def _parse(root=ROOT):
    """Parse every test module once: (diagnostic module -> path, path -> AST)."""
    root = Path(root)
    modules, trees = {}, {}
    for path in sorted((root / 'tests').rglob('test_*.py')):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError('Test sources must stay inside the repository: ' + relative)
        tree = ast.parse(path.read_text(encoding='utf-8-sig'), filename=relative)
        if not any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and
                   n.name.startswith('test_') for n in ast.walk(tree)):
            raise ValueError('No test definitions in ' + relative)
        parts = path.relative_to(root / 'tests').with_suffix('').parts
        modules['.'.join((*parts[:-1], parts[-1].removeprefix('test_')))] = relative
        trees[relative] = tree
    if not modules:
        raise ValueError('No test modules found; coverage cannot be established')
    return modules, trees


def inventory(root=ROOT):
    return _parse(root)[0]


def _functions(trees):
    result = {}
    for relative, tree in trees.items():
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


def test_functions(root=ROOT):
    """Test file -> test function names; class methods are written Class::method."""
    return _functions(_parse(root)[1])

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


# Whole-chain functions moved from fast to chain by measured timings (fast-slim B1/B2).
# Each names the fast tests that still hold the same assertion seam; a guard must exist
# and stay fast (moved_guard_problems), so moving a guard later fails the repository test.
_RM = 'tests/test_recovery_matrix.py::'
_HSR = 'tests/test_host_submission_roles.py::'
_HS = 'tests/test_host_submission.py::'
_NEI = 'tests/test_native_event_integration.py::'
_NE = 'tests/test_native_events.py::'
_FE = 'tests/test_frontend.py::'
_WB = 'tests/test_web_browser.py::'
_REQ = 'tests/a2c_protocol/test_request_resume.py::'
_WEB_GUARDS = [_FE + 'test_actual_login_cookie_logout_and_admin_permissions',
               _FE + 'test_public_pages_and_server_side_workbench_gates',
               _FE + 'test_login_return_target_rejects_foreign_and_unrecognized_routes',
               _FE + 'test_all_html_assets_are_stamped_and_stale_changes_fail']

MOVED_TO_CHAIN = {
    _RM + 'test_acceptance_survives_a_socket_drop_during_the_acceptance_notice': {
        'owner': '33', 'seam': 'outbox resends one dropped message.send a2c.request exactly once; the two-period chain itself is already chain (resident_multiperiod)',
        'guards': [_RM + 'test_termination_survives_a_socket_drop_during_the_signed_notice',
                   _RM + 'test_every_stage_and_fault_names_existing_recovery_tests',
                   'tests/test_runtime_delivery.py::test_operation_payload_is_saved_before_send_and_cannot_change']},
    _RM + 'test_contract_choice_waiting_for_the_user_survives_an_assistant_restart': {
        'owner': '33', 'seam': 'a waiting user confirmation survives an assistant restart with the original reference and a single effect',
        'guards': [_RM + 'test_authorization_waiting_for_the_user_survives_an_assistant_restart',
                   'tests/test_user_confirmations.py::test_confirmation_is_immutable_and_bound_to_actual_preview_and_operation',
                   'tests/test_delegation_scopes.py::test_select_reserves_full_price_before_slot1_and_never_selects_two_cards']},
    _HSR + 'test_a2c_not_submitted_preserves_working_task_card_fee_and_original_host_across_restart': {
        'owner': '27/33', 'seam': 'proven not_submitted keeps the working task, fee and original host across restart and retries the same operation',
        'guards': [_HSR + 'test_not_submitted_private_request_waits_and_restarts_on_original_host_without_failure_or_replay',
                   _HS + 'test_proven_not_submitted_retries_same_operation_with_new_attempt',
                   _REQ + 'test_resume_after_restart_preserves_original_request_materials_fee_and_task']},
    _HSR + 'test_accepted_native_request_is_not_retried_and_late_sdk_reply_then_machine_end_converges': {
        'owner': '27/33', 'seam': 'an accepted native request is never retried; late reply and machine end converge on the original facts',
        'guards': [_HS + 'test_accepted_request_cannot_be_washed_into_not_submitted',
                   _HS + 'test_claim_survives_transport_unknown_without_second_submission',
                   _HS + 'test_duplicate_and_late_events_preserve_committed_facts']},
    _HSR + 'test_real_role_rpc_uses_original_binding_and_frozen_tool_after_default_changes': {
        'owner': '27/33', 'seam': 'an existing business keeps its original host binding and frozen tools after the default changes',
        'guards': ['tests/test_host_registry.py::test_default_changes_only_new_business_and_binding_survives_restart',
                   'tests/test_host_registry.py::test_assistant_resumes_original_rule_tools_after_restart_with_other_default']},
    _NEI + 'test_native_flow_leaf_dedup_and_post_formation_step_result': {
        'owner': '27', 'seam': 'each business operation produces exactly one native notice across repeated observes',
        'guards': [_NE + 'test_inactive_flow_receipt_recovers_after_callback_interruption_without_history_scan',
                   _NE + 'test_permits_survive_restart_and_origin_target_does_not_become_iid',
                   _NEI + 'test_private_reply_returns_to_original_native_session_once']},
    _NEI + 'test_native_provider_sync_result_and_async_decision_terminal': {
        'owner': '27', 'seam': 'an async decision reaches one terminal native notice, including expiry, without inventing success',
        'guards': [_NEI + 'test_async_flow_rejection_is_not_lost_after_real_confirmation',
                   _NE + 'test_machine_failure_releases_notice_permit_without_claiming_handled_or_business_success',
                   'tests/test_confirmation_deadlines.py::test_explicit_object_bound_confirmation_is_not_an_implicit_empty_deadline']},
    'tests/test_quote_admission_rejection.py::test_late_reservation_recovers_original_prepared_quote_once': {
        'owner': '33', 'seam': 'a prepared quote recovers only from original proof, consumes resume once and charges at most once',
        'guards': ['tests/test_quote_admission_rejection.py::test_prepared_recovery_requires_original_proof_and_consumes_resume_once',
                   'tests/test_quote_admission_rejection.py::test_original_negative_receipt_and_absent_acceptance_are_both_required']},
    'tests/test_resident_fulfillment.py::test_early_step_answer_queues_original_job_until_actual_native_terminal_and_drain': {
        'owner': '33', 'seam': 'the original job keeps its permit until the actual native terminal; drain preserves active and unknown writers',
        'guards': [_HS + 'test_unknown_job_retains_one_permit_until_machine_and_actual_callbacks_end',
                   _HS + 'test_completed_business_keeps_native_sid_and_pending_admission_until_original_machine_end',
                   'tests/test_runtime_scheduling.py::test_stopping_pending_business_checks_all_jobs_and_preserves_active_and_unknown_writers']},
    _WB + 'test_public_pages_load_without_errors_and_private_pages_ask_for_login': {
        'owner': 'Web', 'seam': 'public pages render and private pages require login', 'guards': _WEB_GUARDS},
    _WB + 'test_admin_opens_every_tab_without_errors': {
        'owner': 'Web', 'seam': 'admin UI rendering; admin permission held by the HTTP test', 'guards': _WEB_GUARDS},
    _WB + 'test_register_validation_login_logout_through_the_real_forms': {
        'owner': 'Web', 'seam': 'registration/login/logout through forms; cookie and status codes held by the HTTP test', 'guards': _WEB_GUARDS},
    _WB + 'test_home_language_navigation_and_real_public_stats': {
        'owner': 'Web', 'seam': 'home navigation rendering', 'guards': _WEB_GUARDS},
    _WB + 'test_ordinary_account_cannot_open_the_admin_page_or_its_data': {
        'owner': 'Web', 'seam': 'an ordinary account gets 403 on admin', 'guards': _WEB_GUARDS},
}


def manifest(root=ROOT, known=None):
    """The chain-layer manifest, validated against the real test functions; fails closed."""
    path = Path(root) / MANIFEST
    if not path.is_file():
        raise ValueError('Missing layer manifest: ' + MANIFEST)
    data = json.loads(path.read_text(encoding='utf-8'))
    chain, threshold = data.get('chain'), data.get('thresholdSeconds')
    if not isinstance(chain, dict) or isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or threshold <= 0:
        raise ValueError(MANIFEST + ' must map test files to function names and give a positive thresholdSeconds')
    known = test_functions(root) if known is None else known
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


def moved_guard_problems(root=ROOT, known=None):
    """Moved functions must be listed chain; every named guard must exist and stay fast."""
    layer = manifest(root, known)
    known = test_functions(root) if known is None else known
    problems = []
    for moved, entry in MOVED_TO_CHAIN.items():
        file, name = moved.split('::', 1)
        if name not in layer['chain'].get(file, ()):
            problems.append('moved function is not in the chain manifest: ' + moved)
        if not entry.get('guards'):
            problems.append('moved function names no fast guard: ' + moved)
        for guard in entry.get('guards', ()):
            gfile, gname = guard.split('::', 1)
            if gname not in known.get(gfile, ()):
                problems.append('guard does not exist: ' + guard)
            elif gname in layer['chain'].get(gfile, ()):
                problems.append('guard is not in the fast layer: ' + guard)
    return problems


def plan(*, layer=None, module=None, root=ROOT):
    modules, trees = _parse(root)
    if module is not None and module not in modules:
        raise ValueError('Unknown diagnostic module: ' + module)
    if layer is not None and layer not in LAYERS:
        raise ValueError('Unknown layer: ' + str(layer))
    result = {'replacements': dict(APPROVED_REPLACEMENTS), 'replacementCoverage': REPLACEMENT_COVERAGE,
              'retiredChain': RETIRED_CHAIN, 'movedToChain': MOVED_TO_CHAIN,
              'diagnostics': {name: path for name, path in modules.items()},
              'coverage': 'All effective coverage retained, including reviewed replacement mappings; no representative sampling.',
              'limits': 'Layers select by the measured manifest only. Environment-gated tests remain gated; '
                        'stage selfcheck and real deployment evidence are separate.'}
    if module is not None:
        result.update(layer='diagnose', tests=[modules[module]], pytestArgs=[])
    elif layer is not None:
        listed = manifest(root, _functions(trees))['chain']
        result.update(layer=layer, tests=['tests'], pytestArgs=list(LAYERS[layer]),
                      manifest={'files': len(listed), 'functions': sum(len(names) for names in listed.values())})
    else:
        result.update(layer='all', tests=list(modules.values()), pytestArgs=[])
    return result


def audit(report, root=ROOT):
    """Compare real per-test timings with the manifest. Advisory output, no manifest edits."""
    known = test_functions(root)
    layer = manifest(root, known)
    threshold = layer['threshold']
    dotted = {path[:-3].replace('/', '.'): path for path in known}
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
