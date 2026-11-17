"""Maintainer command: build both product skill ZIPs with deploy.skills; refresh the shared lock."""
import argparse
from pathlib import Path
import sys

from build_requirements import constraints

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from deploy.skills import build


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',type=Path,default=ROOT/'dist')
    parser.add_argument('--update-lock',action='store_true',help='refresh the shared release lock universally from declared source requirements')
    parser.add_argument('--client-python',type=Path,help='actual client interpreter to bind; defaults to this interpreter')
    parser.add_argument('--config',type=Path,help='original client account configuration path, never read by this builder')
    args=parser.parse_args()
    if args.update_lock:(ROOT/'src/client/skills/_shared/requirements.lock').write_text(constraints(root=ROOT),encoding='utf-8',newline='\n')
    for path in build(args.out,python=args.client_python,config=args.config):print(path)
