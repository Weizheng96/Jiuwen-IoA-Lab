"""Select bounded quick feedback; full regression remains an explicit stage gate."""
from __future__ import annotations

import argparse
import ast
import fnmatch
import io
import json
from pathlib import Path
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]


def case(module, name):
    return f'tests/test_{module}.py::{name}'


# Representative invariant checks, not all matrices in every domain.
CRITICAL = (
    case('auth', 'test_register_login_me_and_each_login_credit'),
    case('authentication_recovery', 'test_lost_response_recovers_same_operation_without_second_credit'),
    case('contract_binding', 'test_formation_is_bound_before_any_work_or_payment_and_cannot_change_participants[proposal]'),
    case('core_request_lifecycle', 'test_canceled_public_caller_keeps_original_writer_and_data_owned'),
    case('payment_atomicity', 'test_concurrent_replay_restart_and_original_receipt'),
    case('payment_atomicity', 'test_expired_and_mismatched_signed_checkout_are_rejected'),
    case('payment_rules', 'test_insufficient_then_pay_installment'),
    case('protocol_relay', 'test_countersign_delivers_slot_one_before_review_and_commits_before_slot_two'),
    case('agent_delivery', 'test_offline_open_uuid_recovery_conflict_then_retry_same_interaction'),
    case('lease', 'test_fresh_lease_serializes_actual_independent_processes'),
    case('resident_runtime', 'test_control_remains_responsive_and_stop_drains_actual_running_work'),
    'tests/test_user_confirmations.py',
    case('termination_control', 'test_original_termination_before_work_uses_actual_deposit_and_closes_without_fake_anchor[zero-proposal-False]'),
)
# B samples expensive matrices; --full still runs every scenario/formation.
QUICK_CASES = {
    'resident_fulfillment': (
        case('resident_fulfillment', 'test_full_contract_with_actual_user_step_delivery_wasm_payment_close_and_reputation[False-proposal]'),
        case('resident_fulfillment', 'test_actual_bad_delivery_fails_wasm_and_never_signs_acceptance_or_pays'),
    ),
    'resident_multiperiod': (
        case('resident_multiperiod', 'test_two_periods_keep_original_context_each_user_gate_files_and_actual_payments[normal-proposal]'),
        case('resident_multiperiod', 'test_two_periods_keep_original_context_each_user_gate_files_and_actual_payments[lost-first-payment-countersign]'),
    ),
    'package_reads': (
        case('package_reads', 'test_package_disconnect_does_not_abort_original_two_period_production[proposal]'),
        case('package_reads', 'test_package_digest_mismatch_is_not_retried'),
    ),
    'termination_control': (CRITICAL[-1],),
}
# These shared harness entry points are exercised by explicit original
# success/failure/recovery consumers, rather than every unrelated business
# importing the harness. New/unmapped helper definitions still propagate.
HELPER_CASES = {
    'tests/test_resident_fulfillment.py': (
        {'operation', 'invoke', 'accept_preview'},
        ('resident_fulfillment', 'resident_multiperiod', 'package_reads'),
    ),
    'tests/test_resident_multiperiod.py': (
        {'approve_step'}, ('resident_multiperiod', 'package_reads'),
    ),
}
# Exact owners override directory fallbacks; directory rules do not accumulate.
FILE_RULES = {
    'tools/container_images.py': ('container_images',),
    'tools/check_docs.py': ('check_docs',),
    'tests/chain_layer.json': ('test_layers',),
    'src/platform/ioalab/payments/scopes.py': ('payment_*', 'delegation_scopes', 'delegation_admission', 'protocol_relay', 'rule_boundaries'),
    'src/platform/ioalab/delegation/admission.py': ('delegation_*', 'protocol_relay', 'provider_admission', 'arbitration*', 'payment_rejection', 'rule_boundaries'),
    'src/access/sdk/ioalab_sdk/adapters/arbitration.py': ('arbitration_review', 'runtime_delivery', 'rule_boundaries'),
    'src/access/sdk/ioalab_sdk/provider/policy.py': ('provider_*', 'service_publication', 'task_*', 'resident_fulfillment', 'rule_boundaries'),
    'deploy/lifecycle.py': ('deployment_lifecycle', 'linux_service_management', 'trial_leases', 'trial_gateway', 'trial_sandbox'),
    'deploy/units.py': ('deployment_lifecycle', 'linux_service_management', 'deployment_configs'),
    'deploy/repair.py': ('deployment_repair', 'deployment_jobs', 'deployment_entry', 'deployment_layout', 'deployment_activation'),
    'deploy/jobs.py': ('deployment_repair', 'deployment_jobs', 'deployment_entry'),
    'deploy/__main__.py': ('deployment_repair', 'deployment_entry', 'deployment_jobs'),
    'deploy/installation.py': ('deployment_repair', 'deployment_entry', 'deployment_upgrade_flow'),
    'src/platform/ioalab/config.py': ('platform_configuration','configuration_web','account_rewards','auth','auth_failures','domain_migrations'),
    'src/platform/ioalab/readiness.py': ('core_readiness', 'deployment_lifecycle', 'linux_service_management', 'trial_processes'),
    'src/platform/ioalab/configuration.py': ('platform_configuration','configuration_web','configuration_migration','deployment_configs'),
    'src/platform/ioalab/configuration_permissions.py': ('platform_configuration','configuration_web'),
    'src/platform/ioalab/web/source.py': ('configuration_web','auth_failures'),
    'src/platform/ioalab/web/configuration.py': ('configuration_web','admin_api'),
    'src/platform/ioalab/identity/auth.py': ('auth','auth_failures','configuration_web','authentication_recovery'),
    'src/platform/ioalab/payments/ledger.py': ('account_rewards','payment_*','authentication_recovery','delegation_scopes'),
    'src/platform/ioalab/payments/migrations.py': ('account_rewards','domain_migrations','protocol_operations','auth','core_ownership'),
    'deploy/configure.py': ('deployment_configs','configuration_migration'),
    'deploy/migrate_configuration.py': ('configuration_migration','deployment_configs'),
    'deploy/linux.py': ('linux_service_management', 'deployment_layout'),
    'deploy/windows_local.py': ('windows_local', 'windows_local_owner', 'deployment_configs', 'owned_workspace'),
    'src/official/admin-assistant/ioalab_admin_assistant/deployment.py': ('admin_assistant_preparation', 'windows_local_owner'),
    'src/official/admin-assistant/ioalab_admin_assistant/local_owner.py': ('admin_assistant_preparation', 'windows_local_owner'),
    'tools/verification/trial_resources.py': ('trial_resources',),
    'src/access/sdk/ioalab_sdk/assistant/flow.py': ('assistant_flow_guidance', 'resident_delegation', 'user_confirmations'),
    'src/access/sdk/ioalab_sdk/runtime/confirmation_view.py': ('confirmation_view', 'user_confirmation_view', 'user_confirmations'),
    'src/access/sdk/ioalab_sdk/runtime/trusted_user.py': ('trusted_user_port', 'user_confirmations', 'trial_gateway'),
    'src/hosts/jiuwen/ioalab_jiuwen/a2ui.py': ('jiuwen_a2ui_forms', 'access_products'),
    'src/platform/ioalab/storage.py': ('domain_migrations', 'protocol_operations', 'core_*', 'arbitration*', 'proposal_expiry', 'quote_*', 'provider_admission'),
    'src/platform/ioalab/observation/accounts.py': ('account_views', 'frontend', 'observation_views'),
    'src/platform/ioalab/observation/workbench.py': ('account_views', 'workbench_*', 'observation_views'),
    'src/platform/ioalab/identity/trial.py': ('trial_admission', 'frontend'),
    'src/platform/ioalab/web/trial.py': ('trial_admission', 'frontend'),
    'tests/test_nail_flow.py': ('programming_services',),
    'tools/media/delegation-provider/src/scenario/definitions.py': ('programming_services', 'nail_scene', 'booking_demo'),
    'src/official/services/ioalab_services/programming.py': ('programming_services', 'nail_scene', 'booking_demo', 'official_resources'),
    'src/official/services/ioalab_services/assets/verifiers/programming.py': ('programming_services', 'nail_scene', 'booking_demo', 'official_resources'),
    'src/official/services/ioalab_services/assets/domains/08-dev.json': ('official_*', 'business_tools', 'appointment_source', 'agentmail_*', 'programming_services', 'nail_scene', 'booking_demo'),
    'tools/llm.py': ('llm', 'import_model_source', 'approved_voice'),
    'tools/import_model_source.py': ('import_model_source', 'llm'),
    'tools/test_layers.py': ('test_layers', 'check_workflow'),
    'tools/check_frontend.cjs': ('frontend',),
    'tools/stamp_static.py': ('frontend',),
    'src/access/sdk/ioalab_sdk/assistant/fulfillment.py': ('fulfillment_status', 'resident_fulfillment', 'resident_multiperiod', 'user_confirmations', 'payment_atomicity', 'fulfillment_payment_guards', 'resident_payment_recovery'),
    'src/access/sdk/ioalab_sdk/assistant/trading.py': ('assistant_role', 'resident_input_boundaries', 'resident_contracts', 'payment_transport_recovery'),
    'src/access/sdk/ioalab_sdk/adapters/payments.py': ('runtime_delivery', 'runtime_scheduling', 'installation_identity', 'resident_input_boundaries', 'payment_transport_recovery'),
    'src/access/sdk/ioalab_sdk/adapters/transport.py': ('runtime_delivery', 'runtime_scheduling', 'installation_identity', 'resident_input_boundaries', 'payment_transport_recovery'),
    'src/access/sdk/ioalab_sdk/runtime/store.py': ('runtime_delivery', 'runtime_scheduling', 'installation_identity', 'resident_input_boundaries', 'payment_transport_recovery'),
    'src/platform/ioalab/operations.py': ('core_*', 'arbitration*', 'proposal_expiry', 'quote_*', 'provider_admission', 'payment_rejection','account_rewards','auth','authentication_recovery'),
    'src/access/sdk/ioalab_sdk/assistant/contracts.py': ('resident_contracts', 'contract_binding', 'delegation_scopes'),
    'src/access/sdk/ioalab_sdk/assistant/candidates.py': ('candidate_evidence', 'rule_boundaries'),
    'src/access/sdk/ioalab_sdk/assistant/delegation.py': ('resident_delegation', 'delegation_admission', 'delegation_scopes'),
    'src/access/sdk/ioalab_sdk/assistant/rules.py': ('inbox_rules', 'user_confirmations'),
    'src/access/sdk/ioalab_sdk/assistant/termination.py': ('resident_termination', 'termination_control'),
    'src/access/sdk/ioalab_sdk/assistant/role.py': ('assistant_role', 'runtime_delivery', 'runtime_scheduling'),
    'src/access/sdk/ioalab_sdk/runtime/web/user.html': ('user_confirmation_view', 'user_confirmations'),
    'src/access/sdk/ioalab_sdk/runtime/user_ui.py': ('user_confirmation_view', 'user_confirmations'),
    'src/access/sdk/ioalab_sdk/runtime/confirmations.py': ('user_confirmation_view', 'user_confirmations'),
    'src/access/sdk/ioalab_sdk/runtime/authentication.py': ('authentication_recovery', 'auth'),
    'src/access/sdk/ioalab_sdk/runtime/host.py': ('tool_output_contract', 'host_tool_names', 'jiuwen_host'),
    'src/access/sdk/ioalab_sdk/runtime/connection.py': ('runtime_delivery', 'runtime_signals'),
    'src/access/sdk/ioalab_sdk/runtime/scheduling.py': ('runtime_scheduling', 'runtime_delivery'),
    'src/access/sdk/ioalab_sdk/runtime/config.py': ('configuration_boundary', 'configuration_edit', 'installation_identity'),
    'src/access/sdk/ioalab_sdk/runtime/configuration.py': ('configuration_boundary', 'configuration_edit', 'installation_identity'),
    'src/access/sdk/ioalab_sdk/runtime/installation.py': ('installation_identity', 'access_products'),
    'src/access/sdk/ioalab_sdk/runtime/__main__.py': ('configuration_boundary', 'cli_encoding', 'fulfillment_status'),
    'src/access/skills/assistant/references/delegation.md': ('business_skills', 'access_products', 'resident_delegation'),
    'src/access/skills/_shared/references/calling.md': ('access_products', 'business_skills', 'cli_encoding'),
    'src/access/sdk/ioalab_sdk/templates.py': ('template_renewal', 'service_publication', 'provider_authoring'),
    'src/access/sdk/ioalab_sdk/runtime/lease.py': ('lease', 'resident_runtime', 'runtime_signals'),
    'src/access/sdk/ioalab_sdk/runtime/account_store.py': ('account_enrollment', 'authentication_recovery'),
    'src/official/tools/ioalab_tools/agentmail_setup.py': ('agentmail_setup', 'business_tools'),
    'src/official/tools/ioalab_tools/schedule_cli.py': ('local_schedule', 'appointment_source'),
    'tools/build_requirements.py': ('access_products', 'package_installation', 'package_manifest_boundary'),
    'tools/media/script_documents.py': ('review_story', 'demo_artifacts'),
    'tools/media/review_story.py': ('review_story', 'demo_artifacts'),
    'tools/media/build_voice.py': ('approved_voice', 'demo_artifacts'),
    'tools/media/approved_voice.py': ('approved_voice', 'demo_artifacts'),
}
RULES = (
    (('src/trial/', 'deploy/trial/'), ('trial_*',)),
    (('src/platform/ioalab/migrations/', 'src/platform/ioalab/identity/migrations.py',
      'src/platform/ioalab/payments/migrations.py', 'src/platform/ioalab/registry/migrations.py',
      'src/platform/ioalab/delegation/migrations.py', 'src/platform/ioalab/messaging/migrations.py',
      'src/platform/ioalab/observation/migrations.py'), ('domain_migrations', 'protocol_operations', 'auth', 'core_ownership')),
    (('.agents/', '.github/', 'tools/check_scope.py'), ('check_workflow', 'check_scope')),
    (('tests/js/user_confirmation_view.cjs',), ('user_confirmation_view',)),
    (('src/access/examples/',), ('external_provider', 'resident_runtime', 'configuration_boundary')),
    (('src/access/skills/',), ('access_products', 'business_skills', 'skill_*', 'cli_encoding', 'private_files')),
    (('src/access/sdk/ioalab_sdk/assistant/',), ('assistant_role', 'resident_input_boundaries', 'resident_contracts')),
    (('src/access/sdk/ioalab_sdk/provider/',), ('provider_*', 'service_publication', 'publication_authorizations', 'task_*', 'resident_fulfillment')),
    (('src/access/sdk/',), ('runtime_delivery', 'runtime_scheduling', 'installation_identity', 'resident_input_boundaries')),
    (('src/hosts/',), ('jiuwen_host', 'host_tool_names', 'native_*', 'web_*', 'runtime_delivery')),
    (('src/platform/ioalab/web/',), ('admin_api', 'observation_views', 'protocol_operations', 'workbench_*', 'user_browser', 'frontend')),
    (('src/platform/ioalab/registry/',), ('category_preview', 'search_availability', 'service_publication', 'publication_authorizations')),
    (('src/platform/ioalab/delegation/',), ('delegation_*', 'protocol_relay', 'provider_admission', 'arbitration*', 'payment_rejection')),
    (('src/platform/ioalab/payments/',), ('payment_*', 'delegation_scopes')),
    (('src/platform/ioalab/messaging/',), ('agent_delivery', 'message_boundaries', 'protocol_relay', 'runtime_delivery')),
    (('src/platform/ioalab/identity/',), ('auth', 'authentication_recovery')),
    (('src/platform/ioalab/',), ('core_*', 'arbitration*', 'proposal_expiry', 'quote_*', 'provider_admission')),
    (('src/official/',), ('official_*', 'business_tools', 'appointment_source', 'agentmail_*')),
    (('deploy/', 'vendor/'), ('deployment*', 'vendor_deployment', 'owned_workspace', 'production_release', 'sync_a2c')),
    (('tools/media/', 'tools/verification/'), ('demo_artifacts', 'booking_demo', 'appointment_source', 'seed_manifest')),
)
CODE = ('.py', '.sh', '.cjs', '.js', '.html', '.toml', '.lock', '.whl', '.json', '.yml', '.yaml', '.example')
SHARED_INPUTS = ('requirements.txt', 'pyproject.toml', 'tests/conftest.py', 'tests/_support.py')


def revision(base, root):
    return subprocess.check_output(['git', 'rev-parse', '--verify', '--end-of-options', base + '^{commit}'],
                                   cwd=root, stderr=subprocess.PIPE).decode().strip()


def test_sources(root, ref=None):
    if ref is None:
        return {p.relative_to(root).as_posix(): p.read_text(encoding='utf-8') for p in (root / 'tests').glob('*.py')}
    # Committed plans ignore worktree edits/untracked files. Read blobs in one
    # process, without extracting archives or repeatedly spawning git per file.
    data = subprocess.check_output(['git', 'archive', '--format=tar', ref, '--', 'tests'], cwd=root)
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        return {entry.name: archive.extractfile(entry).read().decode('utf-8') for entry in archive
                if entry.isfile() and entry.name.startswith('tests/') and entry.name.count('/') == 1 and entry.name.endswith('.py')}


def definitions(tree):
    result = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            fixture = any('fixture' in ast.unparse(value) for value in getattr(node, 'decorator_list', []))
            if not node.name.startswith(('test_', 'Test')) or fixture:
                result[node.name] = node
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.Import, ast.ImportFrom)):
            names = ([alias.asname or alias.name.split('.')[0] for alias in node.names]
                     if isinstance(node, (ast.Import, ast.ImportFrom)) else
                     [value.id for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
                      for value in ast.walk(target) if isinstance(value, ast.Name)])
            result.update((name, node) for name in names)
        elif not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)):
            # Dynamic module initialization cannot be narrowed safely.
            result['*'] = tree
    return result


def affected_exports(tree, names):
    exports = definitions(tree)
    if '*' in names:
        return set(exports) | {'*'}
    names = set(names)
    while True:
        extra = {name for name, node in exports.items() if name not in names and
                 any(isinstance(value, ast.Name) and isinstance(value.ctx, ast.Load) and value.id in names for value in ast.walk(node))}
        if not extra:
            return names
        names.update(extra)


def changed_exports(path, sources, old_sources):
    new = definitions(ast.parse(sources[path], filename=path)) if path in sources else {}
    old = definitions(ast.parse(old_sources[path], filename=path)) if path in old_sources else {}
    names = set(new) | set(old)
    if old_sources:
        names = {name for name in names if ast.dump(new.get(name, ast.Pass())) != ast.dump(old.get(name, ast.Pass()))}
    return names


def dependents(paths, sources, old_sources):
    if not paths:
        return set()
    trees = {path: ast.parse(text, filename=path) for path, text in sources.items()}
    affected, selected = {}, set()
    for path in paths:
        if path not in sources and path not in old_sources:
            continue
        changed = changed_exports(path, sources, old_sources)
        if changed:
            affected[Path(path).stem] = affected_exports(trees[path], changed) if path in trees else changed
    while True:
        grew = False
        for path, tree in trees.items():
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    names = affected.get((node.module or '').split('.')[-1], set())
                    imported.update(alias.asname or alias.name for alias in node.names if names and ('*' in names or alias.name in names or alias.name == '*'))
                elif isinstance(node, ast.Import):
                    imported.update(alias.asname or alias.name.split('.')[0] for alias in node.names if affected.get(alias.name.split('.')[-1]))
            if imported:
                selected.add(path)
                module = Path(path).stem
                names = affected_exports(tree, imported)
                if not names.issubset(affected.get(module, set())):
                    affected.setdefault(module, set()).update(names); grew = True
        if not grew:
            return selected


def select_tests(paths, root=ROOT, *, base=None):
    paths = sorted({raw.replace('\\', '/') for raw in paths})
    sources = test_sources(root, revision('HEAD', root) if base else None)
    old_sources = test_sources(root, revision(base, root)) if base else {}
    available = {path: Path(path).stem[5:] for path in sources if Path(path).name.startswith('test_')}
    selected, reasons, warnings = set(), [], []

    def add(patterns):
        for pattern in patterns:
            if pattern.startswith('tests/'):
                if pattern.split('::')[0] in available:
                    selected.add(pattern)
                continue
            for path, stem in available.items():
                if fnmatch.fnmatchcase(stem, pattern):
                    if stem not in QUICK_CASES:
                        selected.add(path)
                        continue
                    sampled = QUICK_CASES[stem]
                    selected.update(sampled)
                    tree = ast.parse(sources[path], filename=path)
                    functions = {node.name for node in tree.body
                                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith('test_')}
                    covered = {target.split('::')[1].split('[')[0] for target in sampled}
                    # Sampling a matrix must not omit new test functions.
                    selected.update(path + '::' + name for name in functions - covered)
                    # A whole class target includes new methods, nested tests,
                    # async methods and every class/method parametrization.
                    selected.update(path + '::' + node.name for node in tree.body
                                    if isinstance(node, ast.ClassDef) and node.name.startswith('Test'))

    for path in paths:
        if path.startswith('历史备份/'):
            continue
        code = path.endswith(CODE)
        shared = path in SHARED_INPUTS
        # Shared inputs must be handled before extension/prose early returns:
        # requirements.txt is executable dependency input, not prose.
        if shared:
            add(CRITICAL)
            reasons.append(path + ': shared dependency/fixture critical boundaries')
            warnings.append(path + ': shared dependency/fixture; quick sample does not replace full stage gate')
        if path in available:
            add((available[path],)); reasons.append(path + ': changed test (quick integration matrix where declared)')
            continue
        if not code and not path.startswith('src/access/skills/'):
            continue
        if path in FILE_RULES:
            add(FILE_RULES[path]); reasons.append(path + ': exact owner and adjacent invariants')
        else:
            match = next((tests for prefixes, tests in RULES if path.startswith(prefixes)), None)
            if match:
                add(match); reasons.append(path + ': most-specific directory scope')
            elif not path.startswith('tests/') and not shared:
                add(CRITICAL); warnings.append(path + ': unmapped executable; representative critical checks only, review affected coverage')
        if code and path.startswith(('src/', 'vendor/', 'deploy/')):
            add(CRITICAL)
    changed_tests = [p for p in paths if p.startswith('tests/') and p.endswith('.py')]
    for path in tuple(changed_tests):
        if path in HELPER_CASES and path in sources and old_sources:
            names = changed_exports(path, sources, old_sources)
            allowed, patterns = HELPER_CASES[path]
            if names and names <= allowed:
                add(patterns); changed_tests.remove(path)
                reasons.append(path + ': changed harness entry has explicit success/failure/recovery consumers')
    for path in sorted(dependents(changed_tests, sources, old_sources)):
        if path in available:
            add((available[path],)); reasons.append(path + ': imports affected shared test code')
    # Whole files subsume nodeids; do not duplicate collection/execution.
    selected = {target for target in selected if '::' not in target or target.split('::')[0] not in selected}
    return {'level': 'B' if selected else 'A', 'tests': sorted(selected), 'reasons': reasons, 'warnings': warnings,
            'limits': 'Quick representative feedback only; --tests expands affected invariants and --full retains every test and parametrization for stage delivery.'}


def changed_paths(base, root=ROOT):
    resolved = revision(base, root)
    listing = subprocess.check_output(['git', 'diff', '--name-only', '--no-renames', '-z', resolved, 'HEAD'], cwd=root)
    return [name for name in listing.decode('utf-8').split('\0') if name]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', required=True, help='committed base revision; working changes are not included')
    parser.add_argument('--github-output', type=Path)
    args = parser.parse_args()
    try:
        result = select_tests(changed_paths(args.base), base=args.base)
    except (subprocess.CalledProcessError, SyntaxError) as exc:
        parser.error('Cannot plan committed scope: ' + str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.github_output:
        with args.github_output.open('a', encoding='utf-8') as output:
            output.write('tests=' + json.dumps(result['tests']) + '\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
