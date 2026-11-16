"""Host-native role-skill installation presentation."""
from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class ClaudeSkills:
    def destination(self): return Path.home()/'.claude/skills'
    def entry(self, name): return '/'+name
    def manual_steps(self, package, name):
        return [f'将 {package} 内的 {name} 文件夹安装到 {self.destination()/name}，保留同名冲突目录。',
                f'在全新会话用 /{name} 加载，核宿主提供的实际技能目录并调用 runtime.status。']



native_skills = ClaudeSkills()

def collect_context(reference, *, host_ref, config_path):
    from ioalab_sdk.runtime import RuntimeFault
    if not isinstance(reference,str) or not 1<=len(reference)<=8192:
        raise RuntimeFault("invalid_input","Use the opaque reference supplied by the native host","connect_native_context")
    return reference
