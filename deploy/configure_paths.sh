#!/usr/bin/env bash
# Add newly introduced resource locations without overwriting operator settings.
set -euo pipefail
ENV_FILE="${1:?environment file is required}"
APP_DIR="${2:?installation directory is required}"
[ -f "$ENV_FILE" ] || { echo 'Environment file does not exist' >&2; exit 2; }

append_missing() {
  local key="$1" value="$2"
  if ! grep -Eq "^[[:space:]]*(export[[:space:]]+)?${key}=" "$ENV_FILE"; then
    printf '\n%s=%q\n' "$key" "$value" >> "$ENV_FILE"
  fi
}

append_missing IOALAB_SEED_FILE "$APP_DIR/deploy/default-seed.json"
append_missing IOALAB_BUNDLES_DIR "$APP_DIR/dist"
