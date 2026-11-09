"""Check recorded production locks offline; optionally match their actual wheel inputs."""
import argparse
from pathlib import Path

from deploy.release import HEX, TARGETS, UNITS, pins, read_json, sha
from deploy.dependencies import inputs

ROOT = Path(__file__).resolve().parents[1]


def validate(root=ROOT, wheels=None):
    reports = []
    for target in TARGETS:
        for unit in UNITS:
            base = Path(root) / 'deploy/locks' / target
            lock, record = base / (unit + '.lock'), read_json(base / (unit + '.json'))
            content = lock.read_bytes()
            if b'\r' in content or not content.endswith(b'\n'):
                raise ValueError('Production locks require canonical UTF-8/LF bytes')
            if (set(record) != {'format', 'unit', 'target', 'python', 'inputSha256', 'lockSha256', 'resolver'}
                    or record['format'] != 'ioalab.deployment-lock/1' or record['unit'] != unit
                    or record['target'] != target or record['python'] != '3.11'
                    or not HEX.fullmatch(str(record['inputSha256'])) or record['lockSha256'] != sha(lock)):
                raise ValueError('Invalid production lock record: ' + target + '/' + unit)
            versions = pins(lock.read_text(encoding='utf-8'))
            if set(versions).intersection(set().union(*UNITS.values())):
                raise ValueError('A mirror lock cannot replace local components')
            if wheels is not None and inputs(unit, target, Path(wheels).glob('*.whl'))['inputSha256'] != record['inputSha256']:
                raise ValueError('Wheel dependencies have changed; regenerate this target lock')
            reports.append({'target': target, 'unit': unit, 'ordinaryPackages': len(versions)})
    return reports


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wheels', type=Path)
    args = parser.parse_args()
    for record in validate(wheels=args.wheels):
        print(record['target'] + '/' + record['unit'] + ': ' + str(record['ordinaryPackages']) + ' exact ordinary packages')
    print('Recorded lock checks passed; target runtime installation requires separate evidence')


if __name__ == '__main__':
    main()
