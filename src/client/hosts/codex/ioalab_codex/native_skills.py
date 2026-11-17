"""Host-native role-skill installation presentation and fixed entry resources."""
from dataclasses import dataclass
from importlib.resources import files
import json
from pathlib import Path

NATIVE_FILES = ('entry-assistant.py', 'entry-provider.py', 'README.md')


def _binding(value):
    """Accept only the SDK-hydrated P07 binding; it holds paths and argv, never grants."""
    from ioalab_sdk.runtime import RuntimeFault, NATIVE_ENTRY_BIND_OPERATION
    if (not isinstance(value, dict) or set(value) != {'format', 'hostRef', 'profiles', 'sourceOperations', 'setupOperation'} or
            value['format'] != 'ioalab.native-entry/1' or not isinstance(value['hostRef'], str) or not value['hostRef'] or
            value['setupOperation'] != NATIVE_ENTRY_BIND_OPERATION or
            not isinstance(value['profiles'], dict) or set(value['profiles'])-{'assistant', 'provider'} or
            not isinstance(value['sourceOperations'], list) or any(not isinstance(op, str) or not op for op in value['sourceOperations']) or
            len(set(value['sourceOperations'])) != len(value['sourceOperations']) or value['setupOperation'] in value['sourceOperations']):
        raise RuntimeFault('invalid_input', 'Use the SDK-bound native entry resource metadata')
    for entry in value['profiles'].values():
        if (not isinstance(entry, dict) or set(entry) != {'profile', 'descriptorFile', 'ensureCommand', 'callerCommand'} or
                not isinstance(entry['profile'], str) or not entry['profile'] or not isinstance(entry['descriptorFile'], str) or
                not Path(entry['descriptorFile']).is_absolute()):
            raise RuntimeFault('invalid_input', 'Use non-secret profile and descriptor locations')
        for key in ('ensureCommand', 'callerCommand'):
            argv = entry[key]
            if (not isinstance(argv, list) or not 1 <= len(argv) <= 64 or
                    any(not isinstance(x, str) or not x or len(x) > 8192 for x in argv) or not Path(argv[0]).is_absolute()):
                raise RuntimeFault('invalid_input', 'Use installation-bound argument vectors without a shell')
    pythons = {entry['callerCommand'][0] for entry in value['profiles'].values()}
    if len(pythons) > 1:
        raise RuntimeFault('invalid_input', 'Both roles must use the same bound client interpreter')
    return pythons.pop() if pythons else None


@dataclass(frozen=True)
class CodexSkills:
    def destination(self): return Path.home()/'.agents/skills'
    def entry(self, name): return '$'+name
    def manual_steps(self, package, name):
        return [f'将 {package} 内的 {name} 文件夹安装到 {self.destination()/name}，保留同名冲突目录。',
                f'由智能体按 native/README.md 使用绑定解释器运行 ioalab_codex.native_install install，并以 --entry 指定已装角色入口；安装文件后在新会话核验实际加载，只有Codex实际提示的用户动作才交给用户。',
                f'在全新会话用 ${name} 加载，核宿主提供的实际技能目录并调用 runtime.status。']

    def resource_files(self, binding):
        """P07 static export; never reads a grant, starts the client or edits Codex settings."""
        python = _binding(binding)
        root = files('ioalab_codex').joinpath('resources', 'native')
        result = {name: root.joinpath(name).read_bytes() for name in NATIVE_FILES}
        result['README.md'] = result['README.md'].replace(
            b'__IOALAB_PYTHON__', (python or '<bound client interpreter: not configured>').encode('utf-8'))
        result['entry-binding.json'] = json.dumps(binding, ensure_ascii=False, sort_keys=True, indent=2).encode('utf-8')
        return result


native_skills = CodexSkills()
