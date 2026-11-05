"""Build ordinary native business documents and unsigned template inputs; no effects."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import zipfile


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[4]
# Repository-owned resource loading only; no account configuration is read.
sys.path.insert(0, str(ROOT / 'src/official/services'))
sys.path.insert(0, str(HERE))
from scene import (DEFAULT_SERVICE, SKILL, SKILLS, acceptance_document, definition, execution_input,
                   production_document, published_verifier_reference, skill_name, template_input, validate, verifier_input)


def utf8(text):
    return text.replace('\r\n', '\n').replace('\r', '\n').encode('utf-8')


def build(output, *, profile='miniapp', service_id=DEFAULT_SERVICE, source=None, verifier_reference=None):
    value = validate(source if source is not None else definition(service_id), profile)
    if source is not None and service_id != DEFAULT_SERVICE and value['id'] != service_id:
        raise ValueError('The selected service and supplied scene definition differ')
    skill = skill_name(value)
    reference = (published_verifier_reference(value, verifier_reference)
                 if verifier_reference is not None else None)
    needs_reference = value['formation'] == 'countersign' and verifier_reference is None
    draft = None if needs_reference else template_input(value, profile, verifier_reference=reference)
    execution = execution_input(value, verifier_reference=reference)
    documents = {}
    source_root = HERE / 'skills' / SKILL
    replacements = {
        '__SCENE_SKILL__': skill,
        '__SCENE_NAME__': value['name'],
        '__SCENE_CAPABILITY__': value['deliverable']['description'],
        '__SCENE_PREPARATION__': ('quote 阶段先读报价与验收依据，优先复用文档中的真实预发布验收引用，不逐单转抄源码'
                                  if value['formation'] == 'proposal' and reference is not None else
                                  'quote 阶段先读报价与验收依据，优先复用已配置引用；缺少时用本单回调准备验收器并取得实际引用后报价'
                                  if value['formation'] == 'proposal' else
                                  'decision 阶段审查原合同与全部必需材料；不能报价或替换模板验收器'),
        '__SCENE_ACCEPTANCE__': acceptance_document(value, verifier_reference=reference),
        '__SCENE_PRODUCTION__': production_document(value),
    }
    for relative in ('SKILL.md', 'references/production.md', 'references/acceptance.md'):
        text = (source_root / relative).read_text(encoding='utf-8')
        if not text.strip():
            raise ValueError('Business documents cannot be empty')
        required = {'SKILL.md': ('__SCENE_SKILL__', '__SCENE_NAME__', '__SCENE_CAPABILITY__', '__SCENE_PREPARATION__'),
                    'references/production.md': ('__SCENE_PRODUCTION__',),
                    'references/acceptance.md': ('__SCENE_ACCEPTANCE__',)}[relative]
        if any(text.count(placeholder) != 1 for placeholder in required):
            raise ValueError('Business documents need exactly one occurrence per source placeholder')
        for placeholder, replacement in replacements.items():
            if placeholder in text:
                text = text.replace(placeholder, replacement)
        if '__SCENE_' in text:
            raise ValueError('Business document contains an unresolved scene placeholder')
        documents[relative] = utf8(text)
    if f'name: {skill}\n' not in documents['SKILL.md'].decode('utf-8'):
        raise ValueError('Business Skill name must match the configured selection')
    output = Path(output).resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError('Use a new output directory; preserve existing artifacts')
    output.mkdir(parents=True, exist_ok=True)
    package = output / (skill + '.zip')
    with zipfile.ZipFile(package, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for relative, data in sorted(documents.items()):
            info = zipfile.ZipInfo(skill + '/' + relative, date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    outputs = {'execution-fields.json': execution,
               'publication-fields.json': {'profile': profile, **{key: value[key] for key in ('name', 'description', 'tags')}}}
    if draft is not None:
        outputs['template-draft-input.json'] = draft
    outputs['verifier-publish-input.json'] = verifier_input(value, profile)
    for name, data in outputs.items():
        (output / name).write_bytes(utf8(json.dumps(data, ensure_ascii=False, indent=2) + '\n'))
    report = {'serviceId': value['id'], 'profile': profile, 'skill': skill, 'package': package.name,
              'formation': value['formation'],
              'templateStatus': 'verifier_reference_required' if needs_reference else 'draft_ready',
              'verifierKind': value['verifier'].get('kind', 'script'),
              'disputeModels': value['verifier'].get('disputeModels', []),
              'modelAvailability': 'not_checked' if value['verifier'].get('kind') == 'skill' else 'not_required',
              'packageSha256': hashlib.sha256(package.read_bytes()).hexdigest(),
              'documents': {name: hashlib.sha256(data).hexdigest() for name, data in sorted(documents.items())},
              'verifierId': value['verifier']['id'],
              'verifierSourceSha256': hashlib.sha256(utf8(value['verifier']['source'])).hexdigest(),
              'verifierReference': reference,
              'verifierPreparation': ('reuse_published' if reference is not None else
                                      'task_upload' if value['formation'] == 'proposal' else 'management_upload_required'),
              'criteria': value['verifier']['criteria'],
              'operations': ['provider.verifier.publish', 'provider.template.draft', 'provider.preview', 'provider.publish'],
              'limits': ['Build only; no installation, configuration edit, publication or business execution',
                         'Existing templates and cards remain unchanged; new terms require actual confirmation']}
    (output / 'build-report.json').write_bytes(utf8(json.dumps(report, ensure_ascii=False, indent=2) + '\n'))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--profile', default='miniapp')
    parser.add_argument('--service-id', choices=tuple(SKILLS), default=DEFAULT_SERVICE)
    parser.add_argument('--verifier-reference', type=Path,
                        help='JSON containing the actual management upload {id, uri, digest} reference')
    args = parser.parse_args(argv)
    try:
        reference = (json.loads(args.verifier_reference.read_text(encoding='utf-8'))
                     if args.verifier_reference else None)
        result = build(args.out, profile=args.profile, service_id=args.service_id, verifier_reference=reference)
    except (ValueError, KeyError, OSError, TypeError) as exc:
        print(json.dumps({'status': 'rejected', 'error': str(exc)}, ensure_ascii=True))
        return 1
    print(json.dumps({'status': 'completed', 'data': result}, ensure_ascii=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
