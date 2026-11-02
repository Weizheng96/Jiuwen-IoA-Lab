"""Build ordinary native business documents and unsigned template inputs; no effects."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import zipfile


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
# Repository-owned resource loading only; no account configuration is read.
sys.path.insert(0, str(ROOT / 'src/official/services'))
sys.path.insert(0, str(HERE))
from scene import SKILL, acceptance_document, definition, execution_input, template_input, validate


def utf8(text):
    return text.replace('\r\n', '\n').replace('\r', '\n').encode('utf-8')


def build(output, *, profile='miniapp', source=None):
    value = validate(source if source is not None else definition(), profile)
    draft = template_input(value, profile)
    execution = execution_input(value)
    documents = {}
    source_root = HERE / 'skills' / SKILL
    for relative in ('SKILL.md', 'references/production.md', 'references/acceptance.md'):
        text = (source_root / relative).read_text(encoding='utf-8')
        if not text.strip():
            raise ValueError('Business documents cannot be empty')
        if relative == 'references/acceptance.md':
            if text.count('__SCENE_ACCEPTANCE__') != 1:
                raise ValueError('The acceptance document needs exactly one source placeholder')
            text = text.replace('__SCENE_ACCEPTANCE__', acceptance_document(value))
        documents[relative] = utf8(text)
    if f'name: {SKILL}\n' not in documents['SKILL.md'].decode('utf-8'):
        raise ValueError('Business Skill name must match the configured selection')
    output = Path(output).resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError('Use a new output directory; preserve existing artifacts')
    output.mkdir(parents=True, exist_ok=True)
    package = output / (SKILL + '.zip')
    with zipfile.ZipFile(package, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for relative, data in sorted(documents.items()):
            info = zipfile.ZipInfo(SKILL + '/' + relative, date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    for name, data in (('template-draft-input.json', draft), ('execution-fields.json', execution)):
        (output / name).write_bytes(utf8(json.dumps(data, ensure_ascii=False, indent=2) + '\n'))
    report = {'profile': profile, 'skill': SKILL, 'package': package.name,
              'packageSha256': hashlib.sha256(package.read_bytes()).hexdigest(),
              'documents': {name: hashlib.sha256(data).hexdigest() for name, data in sorted(documents.items())},
              'verifierId': value['verifier']['id'],
              'verifierSourceSha256': hashlib.sha256(utf8(value['verifier']['source'])).hexdigest(),
              'criteria': value['verifier']['criteria'],
              'operations': ['provider.template.draft', 'provider.preview', 'provider.publish'],
              'limits': ['Build only; no installation, configuration edit, publication or business execution',
                         'Existing templates and cards remain unchanged; new terms require actual confirmation']}
    (output / 'build-report.json').write_bytes(utf8(json.dumps(report, ensure_ascii=False, indent=2) + '\n'))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--profile', default='miniapp')
    args = parser.parse_args(argv)
    try:
        result = build(args.out, profile=args.profile)
    except (ValueError, KeyError, OSError, TypeError) as exc:
        print(json.dumps({'status': 'rejected', 'error': str(exc)}, ensure_ascii=True))
        return 1
    print(json.dumps({'status': 'completed', 'data': result}, ensure_ascii=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
