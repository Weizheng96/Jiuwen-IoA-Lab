"""Maintainer command: verify vendored deployment inputs with deploy.vendor."""
import argparse
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from deploy.vendor import validate


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=ROOT);args=p.parse_args()
    data=validate(args.root);print('Local source, license, wheel, web UI and dependency lock checks passed:',len(data['components']),'components')
