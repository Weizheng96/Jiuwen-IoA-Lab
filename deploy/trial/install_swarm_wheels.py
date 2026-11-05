"""Install original bundled Swarm wheels using the existing source manifest."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def plan(root):
    root=Path(root).resolve()
    vendor=root/'vendor'
    data=json.loads((vendor/'SOURCES.json').read_text('utf-8'))
    known={item['wheelInfo']['file']:item['wheelInfo']['sha256']
           for item in data['components'] if 'wheelInfo' in item}
    wheels=[]
    for line in (vendor/'jiuwen/requirements-swarm.lock').read_text('utf-8').splitlines():
        if not line.startswith('./vendor/'):continue
        path=(root/line[2:]).resolve()
        if not path.is_relative_to(vendor) or path.suffix!='.whl' or not path.is_file():
            raise ValueError('Missing or invalid original Swarm wheel')
        expected=known.get(path.relative_to(vendor).as_posix())
        with path.open('rb') as stream:actual=hashlib.file_digest(stream,'sha256').hexdigest()
        if not expected or expected!=actual:raise ValueError('Original Swarm wheel digest mismatch')
        wheels.append(path)
    if not wheels:raise ValueError('Original Swarm requirements contain no bundled wheels')
    return wheels


def install(root,python,constraints,*,run=subprocess.run):
    wheels=plan(root)
    python=Path(python)
    constraints=Path(constraints)
    if not constraints.is_file():raise ValueError('Existing Linux Swarm constraints are required')
    run([str(python),'-m','pip','install','--no-index','--no-deps','-c',str(constraints),
         *map(str,wheels)],check=True)
    run([str(python),'-m','pip','check'],check=True)
    if not (python.parent/'jiuwenswarm-process').is_file():
        raise ValueError('The original Swarm process entry point was not installed')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',required=True,type=Path)
    parser.add_argument('--python',required=True,type=Path)
    parser.add_argument('--constraints',required=True,type=Path)
    args=parser.parse_args()
    install(args.root,args.python,args.constraints)


if __name__=='__main__':main()
