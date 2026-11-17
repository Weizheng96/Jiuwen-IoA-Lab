"""Install/remove the D31 Codex PreToolUse hook for the fixed IoA entries.

Writes only: the adapter-owned hook config (``<client roles>/native-adapters/<hostRef>/codex-hook/hook.json``)
and one matcher group in ``<CODEX_HOME>/hooks.json``. It never edits config.toml,
never writes a trusted hash and never changes approval or sandbox settings; the
installer does not infer whether the current Codex surface requires trust.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import tempfile

from .native_hook import HOOK_FORMAT, ENTRY_FILES, entry_binding, state_root, load_config

STATUS_MESSAGE = 'IoA Lab: bind the fixed entry call to this chat'
TIMEOUT_SECONDS = 15
# Characters a nested cmd.exe or PowerShell stop-parsing could still interpret inside quotes.
_CMD_UNSAFE = set('"%!^&|<>\r\n\t')


def codex_home(value=None):
    return Path(value or os.environ.get('CODEX_HOME') or Path.home()/'.codex').expanduser().resolve()


def windows_command_line(argv, *, system_root=None):
    """One line that runs ``argv`` identically under ``cmd.exe /C`` and PowerShell ``-Command``.

    Codex may launch Windows command hooks through cmd.exe or through the session
    PowerShell. A leading quoted path is a string expression in PowerShell, so the
    line starts with the absolute system cmd.exe (no PATH or cwd lookup) and the
    PowerShell stop-parsing token ``--%``; cmd ignores that token. ``/d`` skips cmd
    AutoRun and ``/s /c "..."`` keeps quoted paths with spaces intact.
    """
    root = system_root or os.environ.get('SystemRoot') or os.environ.get('windir')
    if not root:
        raise ValueError('SystemRoot is unavailable; cannot locate the system cmd.exe')
    shell = str(Path(root)/'System32'/'cmd.exe')
    if ' ' in shell or any(ch in _CMD_UNSAFE for ch in shell) or not Path(shell).is_absolute():
        raise ValueError('The system cmd.exe path cannot be used without quoting')
    parts = []
    for value in argv:
        value = str(value)
        if not value or any(ch in _CMD_UNSAFE for ch in value):
            raise ValueError('The bound interpreter or hook path contains a character cmd.exe or PowerShell would interpret')
        parts.append(f'"{value}"' if ' ' in value else value)
    return f'{shell} --% /d /s /c "{" ".join(parts)}"'


def _hook_command(python, config):
    argv = [python, '-I', '-X', 'utf8', '-m', 'ioalab_codex.native_hook', str(config)]
    if os.name == 'nt':
        return windows_command_line(argv)
    return shlex.join(argv)


def plan(entries, *, home=None):
    """Pure plan from the installed entry paths; validates both roles share one binding."""
    entries = {role: str(Path(path)) for role, path in entries.items()}
    if not entries or set(entries)-set(ENTRY_FILES):
        raise ValueError('Provide installed assistant and/or provider entry paths')
    bindings = {}
    for role, entry in entries.items():
        path = Path(entry)
        if not path.is_absolute() or path.name != ENTRY_FILES[role] or not path.is_file():
            raise ValueError(f'Installed {role} entry is missing: {entry}')
        bindings[role] = entry_binding(path, role)
    host_refs = {binding['hostRef'] for binding, _ in bindings.values()}
    pythons = {profile['callerCommand'][0] for _, profile in bindings.values()}
    roots = {state_root(binding) for binding, _ in bindings.values()}
    if len(host_refs) != 1 or len(pythons) != 1 or len(roots) != 1:
        raise ValueError('Entries belong to different client bindings')
    config = roots.pop()/'hook.json'
    group = {'matcher': 'Bash', 'hooks': [{'type': 'command', 'command': _hook_command(pythons.pop(), config),
             'timeout': TIMEOUT_SECONDS, 'statusMessage': STATUS_MESSAGE}]}
    return {'hooksFile': codex_home(home)/'hooks.json', 'hookConfig': config,
            'config': {'format': HOOK_FORMAT, 'hostRef': host_refs.pop(), 'entries': entries}, 'group': group}


def _ours(group):
    return isinstance(group, dict) and any(isinstance(item, dict) and item.get('statusMessage') == STATUS_MESSAGE
                                           for item in group.get('hooks', []))


def _same_binding(group, config_file):
    """Our group for this binding's hook config, whatever command rendering an older install wrote."""
    return _ours(group) and all(isinstance(item, dict) and str(config_file) in str(item.get('command', ''))
                                for item in group.get('hooks', []))


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix='.ioalab-', dir=path.parent)
    with os.fdopen(handle, 'wb') as stream:
        stream.write(data)
    os.replace(temporary, path)


def _encode(value):
    return (json.dumps(value, ensure_ascii=False, indent=2)+'\n').encode('utf-8')


def install(entries, *, home=None):
    target = plan(entries, home=home)
    hooks_file, config_file = target['hooksFile'], target['hookConfig']
    original = hooks_file.read_bytes() if hooks_file.exists() else None
    document = json.loads(original) if original is not None else {'hooks': {}}
    if not isinstance(document, dict) or not isinstance(document.get('hooks', {}), dict):
        raise ValueError('Existing hooks.json is not a hooks object; resolve it manually')
    groups = document.setdefault('hooks', {}).setdefault('PreToolUse', [])
    if not isinstance(groups, list):
        raise ValueError('Existing PreToolUse hooks are not a list; resolve it manually')
    present = [index for index, group in enumerate(groups) if _ours(group)]
    if len(present) > 1 or present and not _same_binding(groups[present[0]], config_file):
        raise ValueError('Another IoA entry hook is installed; uninstall it first')
    config_bytes = _encode(target['config'])
    if config_file.exists() and config_file.read_bytes() != config_bytes:
        raise ValueError('Another IoA hook config exists for this host; uninstall it first')
    changed = not present or groups[present[0]] != target['group']
    backup = None
    if changed:
        # A new group, or this binding's group written by an older command rendering: replace in place.
        if original is not None:
            backup = config_file.parent/('hooks.json.before-'+hashlib.sha256(original).hexdigest()[:16])
            _write(backup, original)
        _write(config_file, config_bytes)
        load_config(config_file)
        if present:
            groups[present[0]] = target['group']
        else:
            groups.append(target['group'])
        _write(hooks_file, _encode(document))
    else:
        _write(config_file, config_bytes)
    return {'status': 'installed', 'changed': changed, 'replaced': bool(present) and changed, 'hooksFile': str(hooks_file),
            'hookConfig': str(config_file), 'createdHooksFile': original is None and not present,
            'backup': str(backup) if backup else None,
            'command': target['group']['hooks'][0]['command'], 'nativeTrustRequired': None, 'reloadRequired': True,
            'nativeLoaded': False}


def uninstall(entries, *, home=None):
    """Remove only this matcher group and the adapter config; other hooks stay unchanged."""
    target = plan(entries, home=home)
    hooks_file, config_file = target['hooksFile'], target['hookConfig']
    removed = False
    if hooks_file.exists():
        document = json.loads(hooks_file.read_bytes())
        groups = document.get('hooks', {}).get('PreToolUse', [])
        kept = [group for group in groups if not _same_binding(group, config_file)]
        if len(kept) != len(groups):
            removed = True
            if kept:
                document['hooks']['PreToolUse'] = kept
            else:
                document['hooks'].pop('PreToolUse')
            if document == {'hooks': {}}:
                hooks_file.unlink()
            else:
                _write(hooks_file, _encode(document))
    config_file.unlink(missing_ok=True)
    return {'status': 'uninstalled', 'removed': removed, 'hooksFile': str(hooks_file), 'hookConfig': str(config_file)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('action', choices=('plan', 'install', 'uninstall'))
    parser.add_argument('--entry', action='append', required=True, help='Installed entry-assistant.py or entry-provider.py')
    parser.add_argument('--codex-home', type=Path)
    args = parser.parse_args(argv)
    entries = {}
    for value in args.entry:
        role = next((role for role, name in ENTRY_FILES.items() if Path(value).name == name), None)
        if role is None or role in entries:
            parser.error('Each --entry must be one installed role entry')
        entries[role] = value
    try:
        if args.action == 'plan':
            target = plan(entries, home=args.codex_home)
            result = {'status': 'planned', 'hooksFile': str(target['hooksFile']), 'hookConfig': str(target['hookConfig']),
                      'config': target['config'], 'group': target['group']}
        else:
            result = (install if args.action == 'install' else uninstall)(entries, home=args.codex_home)
    except (OSError, ValueError) as error:
        result = {'status': 'rejected', 'error': {'code': 'invalid_input', 'message': str(error)}}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['status'] != 'rejected' else 1


if __name__ == '__main__':
    raise SystemExit(main())
