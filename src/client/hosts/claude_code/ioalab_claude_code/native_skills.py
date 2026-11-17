"""Host-native role-skill installation presentation."""
from dataclasses import dataclass
from importlib.resources import files
import hashlib
import json
from pathlib import Path

from ioalab_sdk.runtime import RuntimeFault,NATIVE_ENTRY_BIND_OPERATION

NATIVE_FILES=('.claude-plugin/plugin.json','hooks/hooks.json','hooks/entry.mjs','hooks/causality.mjs','wrapper.mjs','README.md')


def _binding(value):
    if (not isinstance(value,dict) or set(value)!={'format','hostRef','profiles','sourceOperations','setupOperation'} or
        value['format']!='ioalab.native-entry/1' or not isinstance(value['hostRef'],str) or not value['hostRef'] or
        value['setupOperation']!=NATIVE_ENTRY_BIND_OPERATION or
        not isinstance(value['profiles'],dict) or set(value['profiles'])-{'assistant','provider'} or
        not isinstance(value['sourceOperations'],list) or any(not isinstance(op,str) or not op for op in value['sourceOperations']) or
        len(set(value['sourceOperations']))!=len(value['sourceOperations']) or value['setupOperation'] in value['sourceOperations']):
        raise RuntimeFault('invalid_input','Use the SDK-bound native entry resource metadata')
    for entry in value['profiles'].values():
        if (not isinstance(entry,dict) or set(entry)!={'profile','descriptorFile','ensureCommand','callerCommand'} or
            not isinstance(entry['profile'],str) or not entry['profile'] or not isinstance(entry['descriptorFile'],str) or
            not Path(entry['descriptorFile']).is_absolute()):
            raise RuntimeFault('invalid_input','Use non-secret profile and descriptor locations')
        for key in ('ensureCommand','callerCommand'):
            argv=entry[key]
            if (not isinstance(argv,list) or not 1<=len(argv)<=64 or
                any(not isinstance(x,str) or not x or len(x)>8192 for x in argv) or not Path(argv[0]).is_absolute()):
                raise RuntimeFault('invalid_input','Use installation-bound argument vectors without a shell')
    return json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2).encode('utf-8')

@dataclass(frozen=True)
class ClaudeSkills:
    def destination(self): return Path.home()/'.claude/skills'
    def entry(self, name): return '/'+name
    def manual_steps(self, package, name):
        return [f'将 {package} 内的 {name} 文件夹安装到 {self.destination()/name}，保留同名冲突目录。',
                f'加载该目录内 native 完整扩展资源（两角色选一份即可），完成原生信任／必要重载；原会话正常退出后按原ID及 --plugin-dir <native目录> 恢复，勿并发resume。',
                f'在全新会话用 /{name} 加载并调用 runtime.status；缺入口时按实际错误补配置，不手填Session ID。']

    def resource_files(self,binding):
        """P07 static export; never reads a grant or starts the client/host."""
        hydrated=_binding(binding)
        root=files('ioalab_claude_code').joinpath('resources','native')
        result={name:root.joinpath(*name.split('/')).read_bytes() for name in NATIVE_FILES}
        result['entry-binding.json']=hydrated
        return result

    def install(self,destination,binding):
        """Adapter-private local copy, also usable for explicit manual export."""
        target=Path(destination)
        if not target.is_absolute() or any(p.is_symlink() for p in (target,*target.parents)):
            raise RuntimeFault('invalid_input','Use an owned absolute native resource directory')
        content=self.resource_files(binding)
        expected={'format':'ioalab.claude-native/1','files':{name:hashlib.sha256(value).hexdigest() for name,value in content.items()}}
        marker=target/'.ioalab-native.json'
        if target.exists():
            try:
                identical=(not marker.is_symlink() and json.loads(marker.read_text(encoding='utf-8'))==expected and all(
                    not any(path.is_symlink() for path in (target/name,*(target/name).parents)) and (target/name).is_file() and
                    hashlib.sha256((target/name).read_bytes()).hexdigest()==digest for name,digest in expected['files'].items()))
            except (OSError,ValueError):identical=False
            if not identical:raise RuntimeFault('identity_conflict','Preserve the existing directory; export the updated native bundle to a new owned location')
        else:
            target.mkdir(parents=True)
            # Binding last: a partial copy never exposes a usable entry descriptor.
            for name,value in content.items():
                path=target/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(value)
            marker.write_text(json.dumps(expected,sort_keys=True),encoding='utf-8')
        return {'directory':str(target),'files':list(content),'nativeLoaded':False,'manualRequired':True}



native_skills = ClaudeSkills()
