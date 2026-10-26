#!/usr/bin/env bash
# 升级已部署的九问IoA仿真平台：拉代码 → 子模块 → 依赖 → 重启。数据在 /var/lib/ioalab，不受影响。
#   sudo /opt/ioalab/deploy/upgrade.sh [git-ref]
set -euo pipefail
umask 077
APP_DIR="${IOALAB_APP_DIR:-/opt/ioalab}"
STATE_DIR="${IOALAB_STATE_DIR:-/var/lib/ioalab}"
REF="${1:-}"
cd "$APP_DIR"
echo "[ioalab-upgrade] 停服前备份数据…"
ENV_FILE=/etc/ioalab/ioalab.env
if systemctl cat ioalab-providers.service >/dev/null 2>&1; then systemctl stop ioalab-providers; fi
systemctl stop ioalab
SVC_USER="$(stat -c %U "$STATE_DIR")"
sudo -u "$SVC_USER" bash -c 'set -a; source "$1"; set +a; exec "$2" backup --out "$3"' \
  ioalab-backup "$ENV_FILE" "$APP_DIR/.venv/bin/ioalab" "$STATE_DIR/backups"
PROVIDER_DATA="$(if [ -f /etc/ioalab/providers.env ]; then source /etc/ioalab/providers.env; fi; printf '%s' "${IOALAB_PROVIDER_DATA:-$STATE_DIR/providers}")"
if [ -d "$PROVIDER_DATA" ]; then
  tar -czf "$STATE_DIR/backups/providers-$(date +%Y%m%d-%H%M%S).tar.gz" -C "$(dirname "$PROVIDER_DATA")" "$(basename "$PROVIDER_DATA")"
fi
git fetch --all --tags
if [ -n "$REF" ]; then git checkout -q "$REF"; else git pull --ff-only; fi
git submodule update --init --recursive
IOALAB_APP_DIR="$APP_DIR" IOALAB_STATE_DIR="$STATE_DIR" IOALAB_USER="$SVC_USER" bash deploy/install.sh
systemctl start ioalab ioalab-providers
sleep 2
systemctl --no-pager status ioalab | head -5
echo "[ioalab-upgrade] 完成：$(git describe --tags --always)"
