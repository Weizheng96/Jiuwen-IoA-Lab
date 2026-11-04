#!/usr/bin/env bash
# Local source only; the Python entry submits or observes the original detached task.
set -euo pipefail
umask 077
HERE="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PYTHON:-python3.11}"
cd "$HERE"
exec "$PY" -m deploy install "$@"
