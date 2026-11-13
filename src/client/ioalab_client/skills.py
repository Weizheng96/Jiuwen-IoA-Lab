"""Export two lightweight native skills bound to the same client interpreter."""
import io
from pathlib import Path
import sys
import zipfile


def export_skills(output, *, source=None, python=None):
    source = Path(source) if source else Path(__file__).parent/'data/skills'
    output = Path(output).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    products = []
    for folder, name, role in [('assistant', 'ioalab-delegate', 'assistant'), ('provider', 'ioalab-provider', 'service')]:
        files = {}
        for path in sorted((source/folder).rglob('*.md')):
            files[path.relative_to(source/folder).as_posix()] = path.read_bytes()
        for path in sorted((source/'_shared/references').glob('*.md')):
            key = 'references/'+path.name
            if key in files: raise ValueError('Duplicate shared skill page: '+key)
            files[key] = path.read_bytes().replace(b'__IOALAB_SKILL_NAME__', name.encode())
        launcher = (source/'_shared/ioalab.py').read_text(encoding='utf-8')
        launcher = launcher.replace('__IOALAB_ROLE__', role)
        launcher = launcher.replace('__IOALAB_PYTHON__', repr(str(python or sys.executable)))
        files['scripts/ioalab.py'] = launcher.encode('utf-8')
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
            for key, content in sorted(files.items()):
                item = zipfile.ZipInfo(name+'/'+key, (1980, 1, 1, 0, 0, 0))
                item.compress_type = zipfile.ZIP_DEFLATED
                item.external_attr = 0o100644 << 16
                archive.writestr(item, content)
        path = output/('ioalab-'+folder+'-skill.zip')
        content = buffer.getvalue()
        if path.exists():
            if path.is_symlink() or path.read_bytes() != content:
                raise RuntimeError('Existing skill export differs; preserve it and choose a new output directory')
        else:
            with path.open('xb') as stream: stream.write(content)
        products.append(path)
    return products
