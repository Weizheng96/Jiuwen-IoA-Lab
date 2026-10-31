"""Stamp canonical static content digests into the two browser entry pages."""
import argparse
import hashlib
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[1]/'src/platform/ioalab/web/static'
PATTERN=re.compile(r'(/static/([A-Za-z0-9_-]+\.(?:css|js)))(?:\?sha256=[a-f0-9]{16})?(?=["\'])')


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--check',action='store_true')
    args=parser.parse_args();stale=[]
    def stamped(match):
        content=(ROOT/match[2]).read_bytes().replace(b'\r\n',b'\n')
        return match[1]+'?sha256='+hashlib.sha256(content).hexdigest()[:16]
    for name in ('index.html','admin.html'):
        path=ROOT/name;old=path.read_text(encoding='utf-8');new=PATTERN.sub(stamped,old)
        if old==new:continue
        stale.append(name)
        if not args.check:path.write_text(new,encoding='utf-8',newline='\n')
    if args.check and stale:raise SystemExit('Run tools/stamp_static.py: '+', '.join(stale))
    print('Static content digests '+('checked' if args.check else 'updated'))


if __name__=='__main__':main()
