"""Install both bound skills through host-native local skill directories."""
import hashlib
import json
from pathlib import Path
import tempfile
import zipfile

from .layout import binding, configuration_path
from .skills import export_skills, skill_manifest

NAMES = ('ioalab-delegate', 'ioalab-provider')
HOSTS = ('jiuwen', 'codex', 'claude_code')


def destination(host):
    if host == 'codex': return Path.home()/'.agents/skills'
    if host == 'claude_code': return Path.home()/'.claude/skills'
    if host == 'jiuwen': return None
    raise ValueError('Select jiuwen, codex or claude_code')


def checked(path, expected):
    if (not isinstance(expected, dict) or set(expected) != {'format', 'binding', 'files'}
            or expected['format'] != 'ioalab.skill/1' or not isinstance(expected['files'], dict)
            or not 1 <= len(expected['files']) <= 256): return False
    if any(not isinstance(name, str) or not name or Path(name).is_absolute() or '..' in Path(name).parts
           or not isinstance(value, str) or len(value) != 64 for name, value in expected['files'].items()): return False
    if path.is_symlink() or not path.is_dir(): return False
    record = path/'.ioalab-skill.json'
    try:
        if record.is_symlink() or json.loads(record.read_text(encoding='utf-8')) != expected: return False
        items = list(path.rglob('*'))
        if any(item.is_symlink() for item in items): return False
        actual = {item.relative_to(path).as_posix() for item in items if item.is_file()}
        if actual != set(expected['files']) | {'.ioalab-skill.json'}: return False
        return all(not (path/name).is_symlink() and hashlib.sha256((path/name).read_bytes()).hexdigest() == digest
                   for name, digest in expected['files'].items())
    except (OSError, ValueError): return False


def install(host, *, config=None, output=None, source=None, python=None):
    native = destination(host)
    target = native
    config = configuration_path(config)
    packages = export_skills(output or config.parent/'role-skills', source=source, python=python, config=config)
    results = []
    for name, package in zip(NAMES, packages):
        item = {'name': name, 'package': str(package), 'path': str(target/name) if target else None,
                'installed': False, 'newSessionVerified': False}
        item['entry'] = ('$'+name) if host == 'codex' else ('/'+name if host == 'claude_code' else '选择'+name)
        if target is None:
            item.update(status='manual_required', instructions=[f'在九问“我的技能→创建”导入 {package}',
                f'在新会话选择 {name}，用 skill_tool 读取 SKILL.md，并调用 runtime.status。'])
            results.append(item); continue
        try:
            if any(parent.is_symlink() for parent in [target, *target.parents]):
                raise RuntimeError('Linked skill directories require explicit owner review')
            with zipfile.ZipFile(package) as archive:
                files = {key[len(name)+1:]: archive.read(key) for key in archive.namelist() if key.startswith(name+'/')}
            recorded = json.loads(files.pop('.ioalab-skill.json'))
            if any(Path(key).is_absolute() or '..' in Path(key).parts for key in files):
                raise RuntimeError('Invalid skill archive path')
            expected = skill_manifest(files, binding(config, python))
            if recorded != expected: raise RuntimeError('Skill export does not match this client binding')
            path = target/name
            if path.exists() or path.is_symlink():
                if not checked(path, expected): raise RuntimeError('Existing skill differs; preserve it and explicitly resolve the conflict')
            else:
                target.mkdir(parents=True, exist_ok=True)
                with tempfile.TemporaryDirectory(prefix='.ioalab-', dir=target) as temporary:
                    staged = Path(temporary)/name; staged.mkdir()
                    for key, data in files.items():
                        dest = staged/key; dest.parent.mkdir(parents=True, exist_ok=True); dest.write_bytes(data)
                    (staged/'.ioalab-skill.json').write_text(json.dumps(expected, ensure_ascii=True), encoding='utf-8')
                    # rename (not replace) refuses a concurrently created destination.
                    staged.rename(path)
            item.update(status='installed', installed=True)
        except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as error:
            item.update(status='manual_required', reason=str(error), instructions=[
                f'检查目录权限及同名冲突；保留原文件，将 {package} 中的 {name} 文件夹安装到 {target/name}。',
                f'在新会话加载 {name} 并调用 runtime.status；不要修改绑定脚本或复制账号密码。'])
        results.append(item)
    return {'host': host, 'binding': binding(config, python), 'skills': results,
            'installed': all(item['installed'] for item in results), 'newSessionVerified': False}


def status(host, *, config=None, python=None):
    native = destination(host)
    result = []
    for name in NAMES:
        path = native/name if native else None
        valid = False
        if path:
            try:
                manifest = path/'.ioalab-skill.json'
                if not any(parent.is_symlink() for parent in [manifest, *manifest.parents]):
                    record = json.loads(manifest.read_text(encoding='utf-8'))
                    valid = isinstance(record, dict) and record.get('binding') == binding(config, python) and checked(path, record)
            except (OSError, ValueError): pass
        result.append({'name': name, 'path': str(path) if path else None, 'installed': valid,
                       'newSessionVerified': False})
    return result
