"""Host-native role-skill installation presentation."""
from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class JiuwenSkills:
    def destination(self): return None
    def entry(self, name): return '选择'+name
    def manual_steps(self, package, name):
        return [f'在九问“我的技能→创建”导入 {package}。',
                f'在全新会话选择 {name}，用 skill_tool 读取 SKILL.md；核公开返回的实际技能目录或脚本入口并调用 runtime.status。',
                '宿主未提供实际目录或脚本入口时，保持发现/定位未就绪，保留导入回执并完成原生接入。']

native_skills = JiuwenSkills()

def collect_context(reference, *, host_ref, config_path):
    from ioalab_sdk.runtime import RuntimeFault
    if not isinstance(reference,str) or not 1<=len(reference)<=8192:
        raise RuntimeFault("invalid_input","Use the opaque reference supplied by the native host","connect_native_context")
    return reference
