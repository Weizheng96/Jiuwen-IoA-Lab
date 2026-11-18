"""Codex PreToolUse hook for the fixed IoA role entry (D31 narrow authorization).

Only a command that is exactly the installed entry invocation receives
``permissionDecision=allow`` with an ``updatedInput`` carrying a one-time private
handoff reference. The handoff stores the hook's own native metadata (session,
cwd, turn, tool call); the entry script later registers it through SDK P10.
Anything else produces no output, so Codex keeps its original decision.

Standard library only: this module runs inside every matched Bash hook call.
"""
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import secrets
import sys
import time

HOOK_FORMAT = 'ioalab.codex-hook/1'
HANDOFF_FORMAT = 'ioalab.codex-handoff/1'
HANDOFF_FLAG = '--native-handoff'
ARGS_FLAG = '--args-file'
ENTRY_FILES = {'assistant': 'entry-assistant.py', 'provider': 'entry-provider.py'}
# Adapter-fixed SDK tool relay of a queued route A turn: '<role>.native.tool'.
TOOL_SUFFIX = '.native.tool'
# PreToolUse runs before the host's approval UI. Keep a bounded window that
# includes human approval latency, while preserving exact-call single use.
HANDOFF_SECONDS = 30*60
_OPERATION = re.compile(r'[a-z][a-z0-9_-]*(?:\.[a-z0-9_-]+)+')
_HANDOFF = re.compile(r'[0-9a-f]{64}')
_PUNCTUATION = set('\\/:._-')
_AUDIT_LIMIT = 1024*1024


def _safe(text, *, space):
    return bool(text) and all(ch.isalnum() or ch in _PUNCTUATION or (space and ch == ' ') for ch in text)


def split_command(command):
    """Tokenize only the closed grammar; None for anything a shell could expand.

    Accepted: an optional PowerShell ``& `` prefix, then tokens separated by one
    space. A token is bare, '...' or "..." and contains only letters, digits,
    spaces (quoted only) and ``\\ / : . _ -``. No other character, escape,
    variable, glob, operator, redirection or control character is allowed.
    """
    if not isinstance(command, str) or not 1 <= len(command) <= 8192:
        return None
    text = command[2:] if command.startswith('& ') else command
    tokens, index = [], 0
    while True:
        if index >= len(text):
            return None
        quote = text[index]
        if quote in '"\'':
            end = text.find(quote, index+1)
            if end < 0 or not _safe(text[index+1:end], space=True):
                return None
            tokens.append(text[index+1:end]); index = end+1
        else:
            end = text.find(' ', index)
            end = len(text) if end < 0 else end
            if not _safe(text[index:end], space=False):
                return None
            tokens.append(text[index:end]); index = end
        if index == len(text):
            return tokens
        if text[index] != ' ':
            return None
        index += 1


def _absolute_file(text):
    if os.name == 'nt':
        path = PureWindowsPath(text)
        if not re.fullmatch(r'[A-Za-z]:\\', path.anchor) or text.startswith(('\\\\', '//')):
            return False
    elif not text.startswith('/'):
        return False
    parts = re.split(r'[\\/]', text)
    return not text.endswith(('\\', '/')) and '' not in parts[1:] and not {'.', '..'} & set(parts)


def match_entry(command, bound):
    """Return the fixed invocation for an exact bound entry command, else None.

    ``bound`` maps role -> {'python','entry','sourceOperations'}; the python and
    entry tokens must equal the installed strings exactly (no case, slash or
    relative-path normalization).
    """
    tokens = split_command(command)
    if not tokens or len(tokens) not in (3, 5):
        return None
    python, entry, operation, *rest = tokens
    roles = [role for role, value in bound.items() if value['entry'] == entry and value['python'] == python]
    if len(roles) != 1 or not _OPERATION.fullmatch(operation) or len(operation) > 100:
        return None
    # The role is fixed by the entry file: an assistant entry never carries provider calls.
    setup = operation == bound[roles[0]]['setupOperation']
    tool = operation == roles[0]+TOOL_SUFFIX
    if not setup and not tool and (operation not in bound[roles[0]]['sourceOperations'] or not operation.startswith(roles[0]+'.')):
        return None
    # Source operations are mutating calls: the args file keeps the original operationId.
    if len(rest) != 2 or rest[0] != ARGS_FLAG or not _absolute_file(rest[1]):
        return None
    return {'role': roles[0], 'operation': operation, 'argsFile': rest[1], 'entry': entry}


def _read_json(path):
    value = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError('JSON object required')
    return value


def entry_binding(entry, role):
    """Installed per-role binding beside the entry script (P07 hydrated, no grants)."""
    binding = _read_json(Path(entry).parent/'entry-binding.json')
    profile = binding.get('profiles', {}).get(role)
    if (binding.get('format') != 'ioalab.native-entry/1' or not isinstance(profile, dict) or
            not isinstance(binding.get('sourceOperations'), list) or
            not isinstance(profile.get('callerCommand'), list) or not profile['callerCommand'] or
            not isinstance(profile.get('descriptorFile'), str)):
        raise ValueError('native_entry_binding_invalid')
    return binding, profile


def state_root(binding):
    """Adapter-owned resource root that the client designates for this host ref."""
    descriptors = {Path(value['descriptorFile']).parent for value in binding['profiles'].values()}
    if len(descriptors) != 1:
        raise ValueError('native_entry_binding_invalid')
    home_roles = descriptors.pop().parent
    return home_roles/'native-adapters'/binding['hostRef']/'codex-hook'


def load_config(path):
    config = _read_json(path)
    if (config.get('format') != HOOK_FORMAT or set(config) != {'format', 'hostRef', 'entries'} or
            not isinstance(config['entries'], dict) or not config['entries'] or set(config['entries']) - set(ENTRY_FILES)):
        raise ValueError('codex_hook_config_invalid')
    bound = {}
    for role, entry in config['entries'].items():
        if not isinstance(entry, str) or Path(entry).name != ENTRY_FILES[role] or not Path(entry).is_absolute():
            raise ValueError('codex_hook_config_invalid')
        binding, profile = entry_binding(entry, role)
        if binding['hostRef'] != config['hostRef'] or state_root(binding) != Path(path).parent:
            raise ValueError('codex_hook_config_invalid')
        if binding.get('setupOperation') != 'runtime.entry.bind':
            raise ValueError('native_entry_binding_invalid')
        bound[role] = {'python': profile['callerCommand'][0], 'entry': entry, 'setupOperation': binding['setupOperation'],
                       'sourceOperations': frozenset(binding['sourceOperations'])}
    return config, bound


class Handoffs:
    """One-time private handoff files under the adapter-owned state root."""

    def __init__(self, root, *, clock=time.time):
        self.directory, self.clock = Path(root)/'handoffs', clock

    def _path(self, reference):
        if not isinstance(reference, str) or not _HANDOFF.fullmatch(reference):
            raise ValueError('native_handoff_invalid')
        return self.directory/(hashlib.sha256(reference.encode()).hexdigest()+'.json')

    def issue(self, record):
        self.directory.mkdir(parents=True, exist_ok=True)
        self._prune()
        reference = secrets.token_hex(32)
        with self._path(reference).open('x', encoding='utf-8') as stream:
            json.dump({'format': HANDOFF_FORMAT, **record, 'expiresAt': self.clock()+HANDOFF_SECONDS}, stream)
        return reference

    def claim(self, reference):
        """Atomically take the record once; a second claim or a replay finds nothing."""
        path = self._path(reference)
        taken = path.with_name(path.stem+'.claimed-'+secrets.token_hex(8))
        try:
            os.rename(path, taken)
        except FileNotFoundError:
            raise LookupError('native_handoff_missing') from None
        try:
            record = _read_json(taken)
        finally:
            taken.unlink(missing_ok=True)
        if record.get('format') != HANDOFF_FORMAT or not record.get('expiresAt', 0) >= self.clock():
            raise LookupError('native_handoff_expired')
        return record

    def _prune(self):
        horizon = self.clock()-3600
        for item in self.directory.glob('*'):
            try:
                if item.stat().st_mtime < horizon:
                    item.unlink(missing_ok=True)
            except OSError:
                pass


def _audit(root, entry):
    """Non-secret evidence of actual hits and near-miss refusals (digest, never text)."""
    try:
        path = Path(root)/'audit.jsonl'
        if path.exists() and path.stat().st_size > _AUDIT_LIMIT:
            os.replace(path, path.with_suffix('.jsonl.1'))
        with path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(entry, ensure_ascii=True, sort_keys=True)+'\n')
    except OSError:
        pass


def respond(payload, config_path, *, clock=time.time):
    """Decide one PreToolUse call. Returns the hook stdout object or None (no opinion)."""
    if (not isinstance(payload, dict) or payload.get('hook_event_name') != 'PreToolUse' or
            payload.get('tool_name') != 'Bash'):
        return None
    tool_input = payload.get('tool_input')
    if not isinstance(tool_input, dict) or set(tool_input) != {'command'} or not isinstance(tool_input['command'], str):
        return None
    command = tool_input['command']
    config, bound = load_config(config_path)
    root = Path(config_path).parent
    audit = {'at': clock(), 'commandSha256': hashlib.sha256(command.encode()).hexdigest(),
             'toolUseId': payload.get('tool_use_id'), 'turnId': payload.get('turn_id'), 'sessionId': payload.get('session_id')}
    invocation = match_entry(command, bound)
    if invocation is None:
        if any(Path(value['entry']).name in command for value in bound.values()):
            _audit(root, {**audit, 'decision': 'unchanged', 'reason': 'not_exact_entry'})
        return None
    session, tool_cwd, call, turn = (payload.get(key) for key in ('session_id', 'cwd', 'tool_use_id', 'turn_id'))
    # Codex launches the hook in the actual host cwd, whereas stdin.cwd can be
    # an exec_command workdir chosen by the model. Never freeze that tool cwd.
    cwd = str(Path.cwd())
    if (not all(isinstance(value, str) and 1 <= len(value) <= 1024 for value in (session, tool_cwd, call, turn)) or
            not Path(tool_cwd).is_absolute()):
        _audit(root, {**audit, 'decision': 'unchanged', 'reason': 'native_metadata_missing'})
        return None
    reference = Handoffs(root, clock=clock).issue({
        **invocation, 'sessionId': session, 'workspace': cwd, 'toolUseId': call, 'turnId': turn,
        'agentId': payload.get('agent_id') if isinstance(payload.get('agent_id'), str) else None})
    _audit(root, {**audit, 'decision': 'allow', 'role': invocation['role'], 'operation': invocation['operation']})
    return {'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'allow',
            'updatedInput': {'command': command+' '+HANDOFF_FLAG+' '+reference}}}


def main(argv=None):
    """Codex hook process: never exits 2 and never blocks; failures keep native approval."""
    argv = sys.argv[1:] if argv is None else argv
    # Structural breadcrumb only (no command text): proves an actual invocation and
    # where it stopped, because Codex does not persist hook output.
    run = {'at': time.time(), 'pid': os.getpid(), 'argc': len(argv), 'stage': 'stdin'}
    output = None
    try:
        raw = sys.stdin.buffer.read(1024*1024+1)
        run.update(stdinBytes=len(raw), stage='parse')
        if len(argv) != 1 or len(raw) > 1024*1024:
            run['stage'] = 'rejected_arguments'
            return 0
        payload = json.loads(raw.decode('utf-8'))
        if isinstance(payload, dict):
            tool_input = payload.get('tool_input')
            run.update(event=payload.get('hook_event_name'), tool=payload.get('tool_name'),
                       inputKeys=sorted(tool_input) if isinstance(tool_input, dict) else type(tool_input).__name__)
        run['stage'] = 'respond'
        output = respond(payload, argv[0])
        run['stage'] = 'allow' if output is not None else 'no_opinion'
    except Exception as error:
        run['error'] = type(error).__name__
        sys.stderr.write('ioalab codex hook kept the original decision: '+type(error).__name__+'\n')
        output = None
    finally:
        if len(argv) == 1:
            _audit(Path(argv[0]).parent, {'hookRun': run})
    if output is not None:
        sys.stdout.write(json.dumps(output, ensure_ascii=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
