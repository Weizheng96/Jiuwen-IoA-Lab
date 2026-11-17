"""Host-native role-skill installation presentation."""
from dataclasses import dataclass
from pathlib import Path
from importlib.resources import files
import json
from ioalab_sdk.runtime import RuntimeFault

NATIVE_FILES=('rail.py','entry.py','worker.py','turn.py','README.md')

@dataclass(frozen=True)
class JiuwenSkills:
    def destination(self): return None
    def entry(self, name): return '选择'+name
    def manual_steps(self, package, name):
        return [f'在九问“我的技能→创建”导入 {package}。',
                f'在全新会话选择 {name}，用 skill_tool 读取 SKILL.md；核公开返回的实际技能目录或脚本入口并调用 runtime.status。',
                '宿主未提供实际目录或脚本入口时，保持发现/定位未就绪，保留导入回执并完成原生接入。']

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
