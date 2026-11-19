"""Host-native role-skill installation presentation."""
from dataclasses import dataclass
from pathlib import Path
from importlib.resources import files
import json
from zipfile import BadZipFile, ZipFile
from ioalab_sdk.runtime import LocalClient, RuntimeConfig, RuntimeFault, require_configuration

NATIVE_FILES=('rail.py','entry.py','worker.py','turn.py','README.md')

@dataclass(frozen=True)
class JiuwenSkills:
    bind_before_skill_import = True

    def mcp_setup(self, python, config):
        """Describe the native foreground action without reading account data."""
        if not Path(python).is_absolute() or not Path(config).is_absolute():
            raise RuntimeFault('invalid_input', 'Use the bound absolute client and configuration paths')
        return {'tool': 'connect_mcp', 'arguments': {'name': 'ioalab', 'command': str(python),
                'args': ['-m', 'ioalab_sdk.mcp', '--config', str(config)]}}

    def destination(self): return None
    def entry(self, name): return '选择'+name
    def manual_steps(self, package, name):
        return [f'在九问“我的技能→创建”导入 {package}。',
                f'在全新会话选择 {name}，用 skill_tool 读取 SKILL.md；核公开返回的实际技能目录或脚本入口并调用 runtime.status。',
                '宿主未提供实际目录或脚本入口时，保持发现/定位未就绪，保留导入回执并完成原生接入。']

    def install_extension(self, assistant_package, binding, config):
        from . import native_extension
        if set(binding['profiles']) != {'assistant', 'provider'}:
            return native_extension.result('waiting', 'roles_pending')
        host_ref = binding['hostRef']
        try:
            configured = RuntimeConfig.load(config)
            registrations = configured.host.get('registrations')
            registration = registrations.get(host_ref) if isinstance(registrations, dict) else None
            if not isinstance(registration, dict) or registration.get('type') != 'jiuwen':
                return native_extension.result('waiting', 'runtime_pending')
            runtime = LocalClient(configured.home, timeout=1).call('runtime.status')
            if runtime.get('status') != 'completed':
                return native_extension.result('waiting', 'runtime_pending')
            state = runtime['data']
            require_configuration(state, configured, assembly='ioalab-client')
            if state.get('stopping') or not state['account'].get('authenticated') or not state.get('platformConnected'):
                return native_extension.result('waiting', 'runtime_pending')
        except RuntimeFault:
            return native_extension.result('waiting', 'runtime_pending')
        expected = self.resource_files(binding)
        expected.pop('README.md')
        try:
            with ZipFile(assistant_package) as archive:
                resources = {name: archive.read('ioalab-delegate/native/'+name) for name in expected}
        except (OSError, KeyError, BadZipFile):
            return native_extension.result('conflict', 'extension_bundle_unverified')
        if resources != expected:
            return native_extension.result('conflict', 'extension_bundle_unverified')
        return native_extension.install(assistant_package.parent, resources)

    def resource_files(self,binding):
        from ioalab_sdk.runtime import NATIVE_ENTRY_BIND_OPERATION
        if (not isinstance(binding,dict) or set(binding)!={'format','hostRef','profiles','sourceOperations','setupOperation'} or
            binding['format']!='ioalab.native-entry/1' or binding['setupOperation']!=NATIVE_ENTRY_BIND_OPERATION or
            not isinstance(binding['hostRef'],str) or not binding['hostRef'] or
            not isinstance(binding['profiles'],dict) or set(binding['profiles'])-{'assistant','provider'} or
            not isinstance(binding['sourceOperations'],list) or any(not isinstance(op,str) or not op for op in binding['sourceOperations']) or
            len(set(binding['sourceOperations']))!=len(binding['sourceOperations']) or binding['setupOperation'] in binding['sourceOperations']):
            raise RuntimeFault('invalid_input','Use the SDK-provided native entry binding')
        for value in binding['profiles'].values():
            if (not isinstance(value,dict) or set(value)!={'profile','descriptorFile','ensureCommand','callerCommand'} or
                not isinstance(value['profile'],str) or not value['profile'] or not isinstance(value['descriptorFile'],str) or
                not Path(value['descriptorFile']).is_absolute()):
                raise RuntimeFault('invalid_input','Use the actual SDK profile and descriptor locator')
            for key in ('ensureCommand','callerCommand'):
                command=value[key]
                if (not isinstance(command,list) or not 1<=len(command)<=64 or
                    any(not isinstance(x,str) or not x or len(x)>8192 for x in command) or not Path(command[0]).is_absolute()):
                    raise RuntimeFault('invalid_input','Use fixed installation-owned command vectors')
        root=files('ioalab_jiuwen').joinpath('resources','native')
        result={name:root.joinpath(name).read_bytes() for name in NATIVE_FILES}
        result['entry-binding.json']=json.dumps(binding,ensure_ascii=False,sort_keys=True).encode('utf-8')
        return result

native_skills = JiuwenSkills()
