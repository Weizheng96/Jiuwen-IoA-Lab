"""Run existing repository checks for a design-document change."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[4]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full", action="store_true", help="run tests and the full repository selfcheck")
    args = parser.parse_args()
    if not all((ROOT / path).is_file() for path in ("AGENTS.md", "设计/check.py", "tools/check_docs.py", "tools/selfcheck.py")):
        parser.error("place this script at .agents/skills/design/scripts/check.py in the repository")
    steps = (
        [("Tests", ["-m", "pytest", "-q", "tests"]),
         ("Repository selfcheck", ["tools/selfcheck.py"])]
        if args.full else
        [("Design versions", ["设计/check.py"]),
         ("Documentation links and index", ["tools/check_docs.py"])]
    )
    env = {**os.environ, "PYTHONUTF8": "1", "IOALAB_STUB": "1"}
    for label, command in steps:
        print(f"Running: {label}", flush=True)
        result = subprocess.run(
            [sys.executable, *command], cwd=ROOT, env=env,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if result.returncode:
            print(result.stdout, end="")
            print(result.stderr, end="", file=sys.stderr)
            print(f"Failed: {label} (exit {result.returncode})", file=sys.stderr)
            return result.returncode if result.returncode > 0 else 1
        lines = result.stdout.strip().splitlines()
        print(lines[-1] if lines else f"Passed: {label}", flush=True)
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
