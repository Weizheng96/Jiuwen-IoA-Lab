#!/usr/bin/env bash
# 升级已部署的九问IoA仿真平台：拉代码 → 子模块 → 依赖 → 重启。数据在 /var/lib/ioalab，不受影响。
#   sudo /opt/ioalab/deploy/upgrade.sh [git-ref]
set -euo pipefail
APP_DIR="${IOALAB_APP_DIR:-/opt/ioalab}"
REF="${1:-}"
cd "$APP_DIR"
echo "[ioalab-upgrade] 停服前备份数据…"
ENV_FILE=/etc/ioalab/ioalab.env
systemctl stop ioalab
sudo -u "$(stat -c %U /var/lib/ioalab)" env $(grep -v '^#' "$ENV_FILE" | xargs) .venv/bin/ioalab backup --out /var/lib/ioalab/backups
git fetch --all --tags
if [ -n "$REF" ]; then git checkout -q "$REF"; else git pull --ff-only; fi
git submodule update --init --recursive
.venv/bin/python -m pip install -q -r requirements.txt
.venv/bin/python -m pip install -q -e .
systemctl start ioalab
sleep 2
systemctl --no-pager status ioalab | head -5
echo "[ioalab-upgrade] 完成：$(git describe --tags --always)"
