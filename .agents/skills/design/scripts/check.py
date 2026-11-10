"""Run scoped repository checks with live output and durable stage results."""

from __future__ import annotations

import argparse
import codecs
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[4]
# Highest required reading measured on 2026-11-10 (部署 92411) plus 10%; raising it needs user consent.
READING_BUDGET = 101652


def base_steps():
    return [("Design versions", ["设计/check.py"]),
            ("Documentation links and index", ["tools/check_docs.py"]),
            ("Module folders", ["tools/check_modules.py"]),
            ("Reading budget", ["tools/reading_budget.py", "--max-required", str(READING_BUDGET)])]


def test_step(targets):
    return ("Tests", ["-m", "pytest", "-v", "--durations=20", "--durations-min=1", '-o', 'faulthandler_timeout=60', *targets])


def full_steps():
    return [test_step(["tests"]), ("Repository selfcheck", ["tools/selfcheck.py"])]


def run_checks(steps, report_dir=None):
    directory = Path(report_dir) if report_dir else ROOT / 'temp/checks' / (
        datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8])
    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    report = {'status': 'running', 'startedAt': datetime.now(timezone.utc).isoformat(), 'steps': []}

    def save():
        temporary = directory / 'report.json.tmp'
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        temporary.replace(directory / 'report.json')

    save()
    print(f'Report: {directory / "report.json"}', flush=True)
    env = {**os.environ, 'PYTHONUTF8': '1', 'PYTHONUNBUFFERED': '1', 'IOALAB_STUB': '1'}
    for index, (label, command) in enumerate(steps, 1):
        argv = [sys.executable, '-u', *command]
        if command[:2] == ['-m', 'pytest']:
            argv.append('--junitxml=' + str(directory / 'pytest.xml'))
            # Each new report owns its temporary files. Do not repeatedly scan
            # old global pytest directories, which can contain live/ACL data.
            argv.append('--basetemp=' + str(directory / 'pytest-work'))
        entry = {'label': label, 'command': argv, 'status': 'running', 'log': f'{index:02d}.log',
                 'startedAt': datetime.now(timezone.utc).isoformat()}
        report['steps'].append(entry)
        save()
        print(f'Running: {label}: {subprocess.list2cmdline(argv)}', flush=True)
        start = time.monotonic()
        try:
            decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
            with (directory / entry['log']).open('wb') as log:
                with subprocess.Popen(argv, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT) as process:
                    while chunk := process.stdout.read1(65536):
                        log.write(chunk)
                        log.flush()
                        sys.stdout.write(decoder.decode(chunk))
                        sys.stdout.flush()
                    sys.stdout.write(decoder.decode(b'', final=True))
                    code = process.wait()
        except BaseException as exc:
            entry.update(status='interrupted' if isinstance(exc, KeyboardInterrupt) else 'failed', errorType=type(exc).__name__)
            report['status'] = entry['status']
            entry['seconds'] = round(time.monotonic() - start, 3)
            report['finishedAt'] = datetime.now(timezone.utc).isoformat()
            save()
            raise
        entry.update(exitCode=code, seconds=round(time.monotonic() - start, 3), status='passed' if code == 0 else 'failed')
        if code:
            report['status'] = 'failed'
            report['finishedAt'] = datetime.now(timezone.utc).isoformat()
        save()
        print(f'\n{entry["status"].capitalize()}: {label} (exit {code}, {entry["seconds"]}s)', flush=True)
        if code:
            return code if code > 0 else 1
    report.update(status='passed', finishedAt=datetime.now(timezone.utc).isoformat())
    save()
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--full', action='store_true', help='explicitly run all four repository gates')
    parser.add_argument('--report-dir', type=Path, help='new directory for stage logs and report.json')
    args = parser.parse_args()
    if not all((ROOT / path).is_file() for path in ('AGENTS.md', '设计/check.py', 'tools/check_docs.py', 'tools/check_modules.py', 'tools/selfcheck.py')):
        parser.error('place this script at .agents/skills/design/scripts/check.py in the repository')
    return run_checks(base_steps() + (full_steps() if args.full else []), args.report_dir)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", newline="")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
