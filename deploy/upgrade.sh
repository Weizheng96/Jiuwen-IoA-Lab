#!/usr/bin/env bash
# 更新本仓与本地依赖；数据独立保存。同步目录使用 --local，可不安装Git。
# sudo bash deploy/upgrade.sh [git-ref|--local]
set -euo pipefail
umask 077
APP_DIR="${IOALAB_APP_DIR:-/opt/ioalab}"
STATE_DIR="${IOALAB_STATE_DIR:-/var/lib/ioalab}"
REF="${1:-}"
cd "$APP_DIR"

# 所有远端操作在停服前完成，只获取本仓配置的origin，不递归获取其他仓。
if [ "$REF" != "--local" ]; then
  command -v git >/dev/null || { echo "请安装Git，或同步完整目录后用 --local"; exit 1; }
  ORIGIN="$(git remote get-url origin)"
  case "${ORIGIN,,}" in *github.com*) echo "请将部署仓origin设置为GitCode，或同步目录后用 --local"; exit 1;; esac
  git diff --quiet && git diff --cached --quiet || { echo "部署源码有本地修改，请先处理"; exit 1; }
  git fetch --no-recurse-submodules origin --tags
  if [ -n "$REF" ]; then TARGET="$REF"; else TARGET="origin/$(git symbolic-ref --short HEAD)"; fi
  git rev-parse --verify "$TARGET^{commit}" >/dev/null
  git cat-file -e "$TARGET:vendor/SOURCES.json" || { echo "目标版本没有随仓依赖，请选择带vendor清单的版本"; exit 1; }
fi

# 升级九问venv前停止使用它的进程；仅恢复升级前正在运行的单元。
ACTIVE_UNITS=()
for unit in ioalab ioalab-providers ioalab-swarm ioalab-assistant; do
  if systemctl is-active --quiet "$unit"; then ACTIVE_UNITS+=("$unit"); fi
done
for unit in ioalab-assistant ioalab-swarm ioalab-providers ioalab; do
  if systemctl cat "$unit.service" >/dev/null 2>&1; then systemctl stop "$unit"; fi
done

echo "[ioalab-upgrade] 备份独立数据…"
ENV_FILE=/etc/ioalab/ioalab.env
SVC_USER="$(stat -c %U "$STATE_DIR")"
sudo -u "$SVC_USER" bash -c 'set -a; source "$1"; set +a; exec "$2" backup --out "$3"' \
  ioalab-backup "$ENV_FILE" "$APP_DIR/.venv/bin/ioalab" "$STATE_DIR/backups"
PROVIDER_DATA="$(if [ -f /etc/ioalab/providers.env ]; then source /etc/ioalab/providers.env; fi; printf '%s' "${IOALAB_PROVIDER_DATA:-$STATE_DIR/providers}")"
if [ -d "$PROVIDER_DATA" ]; then
  tar -czf "$STATE_DIR/backups/providers-$(date +%Y%m%d-%H%M%S).tar.gz" -C "$(dirname "$PROVIDER_DATA")" "$(basename "$PROVIDER_DATA")"
fi
if [ "$REF" != "--local" ]; then
  if [ -n "$REF" ]; then git checkout -q "$TARGET"; else git merge --ff-only "$TARGET"; fi
fi
IOALAB_APP_DIR="$APP_DIR" IOALAB_STATE_DIR="$STATE_DIR" IOALAB_USER="$SVC_USER" bash deploy/install.sh
if [ "${#ACTIVE_UNITS[@]}" -gt 0 ]; then systemctl start "${ACTIVE_UNITS[@]}"; fi
echo "[ioalab-upgrade] 完成：$(cat 设计/VERSION)"
