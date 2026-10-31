"""Build the public initialization manifest from owned official definitions."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'official-services/domains'
OUTPUT = ROOT / 'deploy/default-seed.json'


def document():
    domains, sources = [], {}
    for path in sorted(SOURCE.glob('*.json')):
        # Owned JSON sources use Git's canonical LF bytes across checkouts.
        # Vendored archives/wheels keep their separate exact-byte checks.
        raw = path.read_bytes().replace(b'\r\n', b'\n')
        domain = json.loads(raw)
        summaries = []
        for service in domain['services']:
            summary = {key: service[key] for key in ('id','name','description','formation','runnable','pricing') if key in service}
            summary['category'] = domain['category']['id']
            summaries.append(summary)
        domains.append({key: domain[key] for key in ('account','label','category')} | {'services': summaries})
        sources[path.relative_to(ROOT).as_posix()] = hashlib.sha256(raw).hexdigest()
    if not domains:
        raise ValueError('No official domain sources')
    return {'source': sources, 'domains': domains}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    body = json.dumps(document(), ensure_ascii=False, indent=2) + '\n'
    if args.check:
        if not OUTPUT.is_file() or OUTPUT.read_text(encoding='utf-8') != body:
            parser.exit(1, 'Public initialization manifest differs from its sources; run tools/build_seed.py\n')
    else:
        OUTPUT.write_text(body, encoding='utf-8', newline='\n')
    print('Public initialization manifest verified' if args.check else 'Public initialization manifest built')


if __name__ == '__main__':
    main()
