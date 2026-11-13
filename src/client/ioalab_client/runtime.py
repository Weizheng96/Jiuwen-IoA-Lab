"""Select one explicit host; the SDK remains the sole resident business runtime."""
import argparse
import copy
import importlib
import json
from pathlib import Path

from ioalab_sdk.provider import EDITABLE_SERVICE_FIELDS, validate_provider
from ioalab_sdk.runtime import RuntimeConfig, RuntimeFault, configure_stdio, public_error, setup_command

HOSTS = {'jiuwen': 'ioalab_jiuwen', 'codex': 'ioalab_codex', 'claude_code': 'ioalab_claude_code'}
HOST_FIELDS = {'type', 'command', 'model', 'data_dir', 'skill_directories'}


def selected(config, *, required=True):
    kind = config.host.get('type')
    if kind is None and not required:
        return None
    if not isinstance(kind, str) or kind not in HOSTS:
        raise RuntimeFault('not_ready', 'Select host.type as jiuwen, codex or claude_code', 'configure_host')
    return kind


def validate(config, *, base_dir):
    validate_provider(config.provider, config.identities)
    kind = selected(config, required=False)
    if kind:
        owner = importlib.import_module(HOSTS[kind])
        owner.validate_host(config, base_dir=base_dir)


def merge_host(current, changes):
    if not isinstance(changes, dict) or not changes or set(changes) - HOST_FIELDS:
        raise RuntimeFault('invalid_input', 'Use only declared non-credential host fields')
    kind = changes.get('type', current.get('type'))
    if not isinstance(kind, str) or kind not in HOSTS:
        raise RuntimeFault('invalid_input', 'Select host.type as jiuwen, codex or claude_code')
    if current.get('type') and current['type'] != kind:
        raise RuntimeFault('identity_conflict', 'Host changes require an explicit migration; keep the existing host')
    owner = importlib.import_module(HOSTS[kind])
    fields = {key: value for key, value in changes.items() if key != 'type'}
    original = {key: copy.deepcopy(value) for key, value in current.items() if key != 'type'}
    updated = owner.merge_host(original, fields) if fields else original
    return {**updated, 'type': kind}


def main(argv=None):
    configure_stdio()
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('configuration', 'configure', 'validate', 'start', 'web-extension'))
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--args-file', type=Path)
    parser.add_argument('--role', choices=('assistant', 'service'))
    args = parser.parse_args(argv)
    try:
        if args.command in ('configuration', 'configure', 'validate'):
            if (args.command == 'configure') != bool(args.args_file) or args.role:
                raise RuntimeFault('invalid_input', 'Only configure requires an args-file; role is not used here')
            fields = set(HOST_FIELDS)
            chosen = []
            def check(config):
                validate(config, base_dir=args.config.resolve().parent)
                kind = selected(config, required=False)
                chosen[:] = [kind]
                if kind:
                    fields.clear()
                    fields.update({'type'} | set(importlib.import_module(HOSTS[kind]).HOST_FIELDS))
            result = setup_command(args.command, args.config, args.args_file,
                                   validate=check, service_fields=EDITABLE_SERVICE_FIELDS,
                                   host_update=merge_host, host_fields=fields)
            if args.command == 'configuration':
                result['data']['supportedHostTypes'] = list(HOSTS)
                result['data']['hostType'] = chosen[0]
            print(json.dumps(result, ensure_ascii=True))
            return 0
        config = RuntimeConfig.load(args.config)
        kind = selected(config)
        if args.command == 'web-extension' and kind != 'jiuwen':
            raise RuntimeFault('not_ready', 'The Jiuwen web extension is only available for the Jiuwen host')
        owner = importlib.import_module(HOSTS[kind] + '.__main__')
        forwarded = [args.command, '--config', str(args.config)]
        if args.args_file: forwarded += ['--args-file', str(args.args_file)]
        if args.role: forwarded += ['--role', args.role]
        return owner.main(forwarded)
    except RuntimeFault as exc:
        print(json.dumps({'status': 'rejected', 'data': None, 'error': public_error(exc)}, ensure_ascii=True))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
