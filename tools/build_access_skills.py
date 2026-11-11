"""Maintainer command: build both product skill ZIPs with deploy.skills; refresh the shared lock."""
import argparse
from pathlib import Path
import sys

from build_requirements import constraints

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from deploy.build import build_skills as build


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',type=Path,default=ROOT/'dist')
    parser.add_argument('--update-lock',action='store_true',help='refresh the shared release lock from a verified development environment')
    args=parser.parse_args()
    if args.update_lock:(ROOT/'src/access/skills/_shared/requirements.lock').write_text(constraints([ROOT/'src/access/sdk',ROOT/'src/hosts/jiuwen',ROOT/'src/official']),encoding='utf-8')
    for path in build(args.out):print(path)
