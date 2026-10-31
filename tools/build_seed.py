"""Build the public initialization manifest from owned official definitions."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src/official/services'))
from ioalab_services.catalog import seed_manifest
OUTPUT = ROOT / 'deploy/default-seed.json'


def document():
    return seed_manifest()


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
