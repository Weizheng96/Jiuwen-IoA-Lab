"""Choose quick feedback tests; complete regression remains an explicit stage gate."""
from __future__ import annotations

import argparse
import fnmatch
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
CRITICAL = (
    'auth', 'authentication_recovery', 'contract_binding', 'core_request_lifecycle',
    'payment_atomicity', 'payment_rules', 'protocol_relay', 'messaging', 'lease',
    'recovery', 'user_confirmations', 'termination_control',
)
RULES = (
    (('.agents/', '.github/', 'tools/check_scope.py'), ('check_workflow', 'check_scope')),
    (('src/access/skills/', 'connector/'), ('access_products', 'business_skills', 'skill_*', 'cli_*', 'private_files')),
    (('src/access/sdk/ioalab_sdk/assistant/',), ('assistant*', 'inbox_rules', 'delegation_*', 'candidate_evidence', 'sdk_*', 'resident_contracts')),
    (('src/access/sdk/ioalab_sdk/provider/',), ('provider_*', 'service_publication', 'publication_authorizations', 'task_*', 'resident_fulfillment')),
    (('src/access/sdk/',), ('runtime_*', 'resident_runtime', 'installation_identity', 'local_execution', 'configuration_*', 'sdk_*')),
    (('src/hosts/',), ('jiuwen_host', 'host_tool_names', 'native_*', 'web_*', 'runtime_delivery')),
    (('src/platform/ioalab/web/',), ('api_v1', 'admin_api', 'workbench_*', 'user_browser')),
    (('src/platform/ioalab/',), ('core_*', 'arbitration*', 'proposal_expiry', 'quote_*', 'provider_admission')),
    (('src/official/', 'official-services/'), ('official_*', 'service_tools', 'business_tools')),
    (('deploy/', 'personal-assistant/', 'vendor/'), ('deployment*', 'vendor_deployment', 'owned_workspace', 'production_release', 'sync_a2c')),
    (('demo/', 'examples/', 'experiments/'), ('demo_artifacts', 'booking_demo', 'appointment_source', 'seed_manifest')),
)


def select_tests(paths, root=ROOT):
    available = {path.as_posix(): path.name[5:-3] for path in
                 (p.relative_to(root) for p in (root / 'tests').glob('test_*.py'))}
    selected, reasons = set(), []
    for raw in sorted(set(paths)):
        path = raw.replace('\\', '/')
        patterns = []
        if path in available:
            selected.add(path)
            reasons.append(path + ': changed test')
        # Shared fixtures and dependency/runner configuration affect all domains;
        # quick coverage is a conservative subset, never labelled a full gate.
        code = path.endswith(('.py', '.sh', '.cjs', '.js', '.html', '.toml', '.lock', '.whl', '.json', '.yml', '.yaml', '.example'))
        runtime = path.startswith(('src/', 'vendor/', 'deploy/', 'personal-assistant/', 'official-services/'))
        if (runtime and code) or path in ('requirements.txt', 'pyproject.toml', 'tests/conftest.py', 'tests/_support.py'):
            patterns.extend(CRITICAL)
        for prefixes, tests in RULES:
            if path.startswith(prefixes):
                # Markdown-only maintenance stays light; product Skill prose
                # still has executable structure and routing checks.
                if code or path.startswith(('src/access/skills/', 'connector/', '.github/')):
                    patterns.extend(tests)
        if path.startswith('tests/') and path.endswith('.py'):
            # Test files also import fixture/helper functions from other tests.
            pending = [Path(path).stem]
            seen = set()
            while pending:
                module = pending.pop()
                if module in seen:
                    continue
                seen.add(module)
                for candidate in available:
                    if candidate not in selected and module in (root / candidate).read_text(encoding='utf-8'):
                        selected.add(candidate)
                        pending.append(Path(candidate).stem)
        if patterns:
            selected.update(name for name, stem in available.items()
                            if any(fnmatch.fnmatchcase(stem, pattern) for pattern in patterns))
            reasons.append(path + ': related tests and critical boundaries')
        elif code and not path.startswith(('tests/', '.agents/')):
            selected.update(name for name, stem in available.items() if stem in CRITICAL)
            reasons.append(path + ': unmapped code; critical boundary suite')
    return {'level': 'B' if selected else 'A', 'tests': sorted(selected), 'reasons': reasons,
            'limits': 'Quick feedback only; use --tests for additional affected invariants and --full for stage delivery.'}


def changed_paths(base, root=ROOT):
    # Resolve first, so user/CI input cannot be interpreted as git diff options.
    revision = subprocess.check_output(['git', 'rev-parse', '--verify', '--end-of-options', base + '^{commit}'],
                                       cwd=root, stderr=subprocess.PIPE).decode().strip()
    listing = subprocess.check_output(['git', 'diff', '--name-only', '--no-renames', '-z', revision, 'HEAD'], cwd=root)
    return [name for name in listing.decode('utf-8').split('\0') if name]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', required=True, help='committed base revision; working changes are not included')
    parser.add_argument('--github-output', type=Path)
    args = parser.parse_args()
    try:
        result = select_tests(changed_paths(args.base))
    except subprocess.CalledProcessError as exc:
        parser.error('Cannot resolve/diff base revision; fetch it first: ' + (exc.stderr or b'').decode(errors='replace'))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.github_output:
        with args.github_output.open('a', encoding='utf-8') as output:
            output.write('tests=' + json.dumps(result['tests']) + '\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
