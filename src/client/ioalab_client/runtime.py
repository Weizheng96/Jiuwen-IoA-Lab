"""Select one explicit host; the SDK remains the sole resident business runtime."""
import argparse
import copy
import importlib
import json
from pathlib import Path

from ioalab_sdk.provider import EDITABLE_SERVICE_FIELDS, validate_provider
from ioalab_sdk.runtime import RuntimeConfig, RuntimeFault, configure_stdio, public_error, setup_command

HOSTS = {'jiuwen': 'ioalab_jiuwen', 'codex': 'ioalab_codex', 'claude_code': 'ioalab_claude_code'}
HOST_FIELDS = {'default', 'registrations'}


def registry(value, *, required=True):
    if not value:
        if required:raise RuntimeFault('not_ready','Register a default host before starting','configure_host')
        return {'default': None, 'registrations': {}}
    if set(value) != {'default', 'registrations'} or not isinstance(value['registrations'], dict):
        raise RuntimeFault('not_ready', 'Explicitly migrate with runtime.configure; legacy hosts without type require host.migrate_from', 'migrate_host')
    registrations = value['registrations']
    if not registrations or value['default'] not in registrations:
        raise RuntimeFault('invalid_input', 'Select a registered default host')
    import re
    for ref, item in registrations.items():
        if not isinstance(ref, str) or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,63}', ref):
            raise RuntimeFault('invalid_input', 'Host references require bounded stable names')
        if not isinstance(item, dict) or item.get('type') not in HOSTS:
            raise RuntimeFault('invalid_input', 'Each registration requires a supported type')
    if len({key.casefold() for key in registrations}) != len(registrations) or len(registrations) > 64:
        raise RuntimeFault('invalid_input', 'Host references must be distinct and bounded')
    return value


def selected(config, *, required=True):
    value = registry(config.host, required=required)
    return value['registrations'][value['default']]['type'] if value['default'] else None


def validate(config, *, base_dir):
    from dataclasses import replace
    validate_provider(config.provider, config.identities)
    hosts={}
    for ref,item in registry(config.host, required=False)['registrations'].items():
        try:
            owner = importlib.import_module(HOSTS[item['type']])
        except ImportError:
            raise RuntimeFault('not_ready', 'Install the registered host adapter dependency', 'install_host') from None
        hosts[ref]=owner.validate_host(replace(config, host=item), base_dir=base_dir)
    return hosts


def merge_host(current, changes):
    if not isinstance(changes, dict) or not changes or set(changes) - {'default', 'registrations', 'migrate_from'}:
        raise RuntimeFault('invalid_input', 'Use host.registrations and optional host.default')
    if 'migrate_from' in changes:
        kind=changes['migrate_from']
        if not current or 'registrations' in current or current.get('type',kind)!=kind or kind not in HOSTS:
            raise RuntimeFault('identity_conflict','Identify the original legacy host for a one-time migration')
        current={**current,'type':kind}
    # This path is the explicit one-time configuration migration, never a runtime fallback.
    if current and 'type' in current:
        kind = current['type']
        if kind not in HOSTS:
            raise RuntimeFault('invalid_input', 'Identify the original host type before migration')
        current = {'default': kind, 'registrations': {kind: copy.deepcopy(current)}}
    original = registry(current, required=False)
    entries = copy.deepcopy(original['registrations'])
    additions = changes.get('registrations', {})
    if not isinstance(additions, dict):
        raise RuntimeFault('invalid_input', 'Provide registrations keyed by stable references')
    for ref, fields in additions.items():
        if not isinstance(fields, dict) or not fields:
            raise RuntimeFault('invalid_input', 'Provide non-empty host fields')
        prior = entries.get(ref, {})
        kind = fields.get('type', prior.get('type'))
        if kind not in HOSTS:
            raise RuntimeFault('invalid_input', 'Select a supported host type')
        if prior and prior['type'] != kind:
            raise RuntimeFault('identity_conflict', 'Keep the type of an existing host registration')
        try:
            owner = importlib.import_module(HOSTS[kind])
        except ImportError:
            raise RuntimeFault('not_ready', 'Install the registered adapter', 'install_host') from None
        update = {k: v for k, v in fields.items() if k != 'type'}
        base = {k: v for k, v in prior.items() if k != 'type'}
        entries[ref] = {**(owner.merge_host(base, update) if update else base), 'type': kind}
    default = changes.get('default', original['default'] or next(iter(entries), None))
    return registry({'default': default, 'registrations': entries})


def main(argv=None):
    configure_stdio()
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('configuration', 'configure', 'validate', 'start', 'serve', 'web-extension'))
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--args-file', type=Path)
    parser.add_argument('--role', choices=('assistant', 'service'))
    args = parser.parse_args(argv)
    try:
        if args.command in ('configuration', 'configure', 'validate'):
            if (args.command == 'configure') != bool(args.args_file) or args.role:
                raise RuntimeFault('invalid_input', 'Only configure requires an args-file; role is not used here')
            chosen = []
            fields={'default','registrations'}
            def check(config):
                if args.command == 'configuration' and config.host and 'registrations' not in config.host:
                    chosen[:] = [config.host.get('type')]
                    fields.add('migrate_from')
                    return
                validate(config, base_dir=args.config.resolve().parent)
                chosen[:] = [selected(config, required=False)]
            result = setup_command(args.command, args.config, args.args_file, validate=check,
                                   service_fields=EDITABLE_SERVICE_FIELDS, host_update=merge_host,
                                   host_fields=fields)
            if args.command == 'configuration':
                result['data']['supportedHostTypes'] = list(HOSTS)
                result['data']['hostType'] = chosen[0]
            print(json.dumps(result, ensure_ascii=True))
            return 0
        config = RuntimeConfig.load(args.config)
        kind = selected(config)
        if args.command == 'web-extension':
            if kind != 'jiuwen':
                raise RuntimeFault('not_ready', 'Select the intended Jiuwen registration')
            owner = importlib.import_module('ioalab_jiuwen')
            result = owner.prepare_extension(args.config, args.args_file, args.role)
            print(json.dumps(result, ensure_ascii=True))
            return 0
        if args.command == 'start':
            import sys
            from ioalab_sdk.runtime import start
            validate(config, base_dir=args.config.resolve().parent)
            result = start(args.config, command=[sys.executable, '-m', 'ioalab_client.runtime'], assembly='ioalab-client')
            print(json.dumps(result, ensure_ascii=True))
            return 0 if result['status'] in ('completed', 'waiting') else 1
        from .assembly import assembled
        with assembled(args.config) as assembly:
            assembly.run()
        return 0
    except RuntimeFault as exc:
        print(json.dumps({'status': 'rejected', 'data': None, 'error': public_error(exc)}, ensure_ascii=True))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
