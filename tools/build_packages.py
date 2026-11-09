"""Maintainer command: build reproducible wheels with deploy.wheels."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from deploy.wheels import build_wheel


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('projects', nargs='+', type=Path)
    args = parser.parse_args()
    for project in args.projects:
        print(build_wheel(project, args.out), flush=True)


if __name__ == '__main__':
    main()
