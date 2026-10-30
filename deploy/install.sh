#!/usr/bin/env bash
# 九问IoA仿真平台（Jiuwen IoA Lab）· 裸 Linux 安装（无 Docker）：
#   /opt/ioalab（本仓库，含 .venv）· /etc/ioalab/ioalab.env · /var/lib/ioalab（数据）· systemd 单元 ioalab.service
#
#   将本仓从部署方的GitCode镜像克隆或同步到 /opt/ioalab（依赖已随仓）
#   sudo /opt/ioalab/deploy/install.sh            # 幂等；装完先编辑 /etc/ioalab/ioalab.env（key · PUBLIC_URL）
#   sudo systemctl enable --now ioalab            # 首次启动创建内部账号与分类元数据；服务由独立进程登记
#
# 可选环境变量：PYTHON=python3.11（解释器）· IOALAB_APP_DIR=/opt/ioalab · IOALAB_STATE_DIR=/var/lib/ioalab · IOALAB_USER=ioalab
set -euo pipefail

APP_DIR="${IOALAB_APP_DIR:-/opt/ioalab}"
STATE_DIR="${IOALAB_STATE_DIR:-/var/lib/ioalab}"
ENV_FILE=/etc/ioalab/ioalab.env
SVC_USER="${IOALAB_USER:-ioalab}"
PY="${PYTHON:-python3.11}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"

say() { echo "[ioalab-install] $*"; }

[ "$(id -u)" -eq 0 ] || { say "请用 root（sudo）运行"; exit 1; }
command -v "$PY" >/dev/null 2>&1 || { say "需要 Python 3.11（$PY 不存在）：apt install python3.11 python3.11-venv"; exit 1; }
"$PY" -c 'import sys; assert sys.version_info[:2] == (3, 11), sys.version' || { say "解释器必须是 3.11"; exit 1; }

# ── 代码目录 ────────────────────────────────────────────────────────
if [ "$HERE" != "$APP_DIR" ]; then
  say "代码不在 $APP_DIR（当前 $HERE）：复制过去（已有则更新）"
  mkdir -p "$APP_DIR"
  cp -a "$HERE/." "$APP_DIR/"
fi
cd "$APP_DIR"
"$PY" tools/check_vendor.py

# ── 账号与目录 ───────────────────────────────────────────────────────
id -u "$SVC_USER" >/dev/null 2>&1 || useradd --system --home-dir "$STATE_DIR" --shell /usr/sbin/nologin "$SVC_USER"
mkdir -p "$STATE_DIR/data" "$STATE_DIR/providers" "$STATE_DIR/a2c-cache" "$STATE_DIR/provider-a2c-cache" /etc/ioalab
chown -R "$SVC_USER:$SVC_USER" "$STATE_DIR"
chmod 750 "$STATE_DIR"

# ── 虚拟环境与依赖（a2c wheel 与A2X随仓；其余依赖首次安装需要网络）────
if [ ! -x .venv/bin/python ]; then
  "$PY" -m venv .venv
fi
.venv/bin/python -m pip install -q --upgrade pip
.venv/bin/python -m pip install -q -r requirements.txt
.venv/bin/python -m pip install -q -e ./src/platform -e ./src/access/sdk -e ./src/official
.venv/bin/python tools/build_connector.py
.venv/bin/ioalab --help >/dev/null

# ── 配置文件（已存在则不动）────────────────────────────────────────────
if [ ! -f "$ENV_FILE" ]; then
  cp deploy/ioalab.env.example "$ENV_FILE"
  ADMIN_PW="$(.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(12))')"
  SECRET="$(.venv/bin/python -c 'import secrets; print(secrets.token_hex(32))')"
  sed -i "s#^IOALAB_ADMIN_PASSWORD=.*#IOALAB_ADMIN_PASSWORD=$ADMIN_PW#" "$ENV_FILE"
  sed -i "s#^IOALAB_SECRET=.*#IOALAB_SECRET=$SECRET#" "$ENV_FILE"
  sed -i "s#^IOALAB_DATA=.*#IOALAB_DATA=$STATE_DIR/data#" "$ENV_FILE"
  sed -i "s#^IOALAB_SEED_FILE=.*#IOALAB_SEED_FILE=$APP_DIR/deploy/default-seed.json#" "$ENV_FILE"
  sed -i "s#^IOALAB_BUNDLES_DIR=.*#IOALAB_BUNDLES_DIR=$APP_DIR/dist#" "$ENV_FILE"
  sed -i "s#^A2C_CACHE_DIR=.*#A2C_CACHE_DIR=$STATE_DIR/a2c-cache#" "$ENV_FILE"
  IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
  [ -n "$IP" ] && sed -i "s#^IOALAB_PUBLIC_URL=.*#IOALAB_PUBLIC_URL=http://$IP:8750#" "$ENV_FILE"
  chmod 640 "$ENV_FILE"; chown root:"$SVC_USER" "$ENV_FILE"
  say "已生成 $ENV_FILE（管理员口令：$ADMIN_PW）—— 请填 IOALAB_LLM_API_KEY，核对 IOALAB_PUBLIC_URL"
else
  say "$ENV_FILE 已存在，不改"
fi
bash deploy/configure_paths.sh "$ENV_FILE" "$APP_DIR"

# ── 预热验收器沙箱的 wasm 运行时（随 a2c wheel 入库，首次解压约 14 MB）──
sudo -u "$SVC_USER" env A2C_CACHE_DIR="$STATE_DIR/a2c-cache" .venv/bin/python - <<'PY'
from a2c.verifier import sandbox
for rid in sandbox.runtimes():
    print("[ioalab-install] wasm 运行时就位：", sandbox.install(rid))
PY

# ── systemd ─────────────────────────────────────────────────────────
sed -e "s#/opt/ioalab#$APP_DIR#g" -e "s#/var/lib/ioalab#$STATE_DIR#g" -e "s#^User=.*#User=$SVC_USER#" -e "s#^Group=.*#Group=$SVC_USER#" \
  deploy/ioalab.service > /etc/systemd/system/ioalab.service
if [ ! -f /etc/ioalab/providers.env ]; then
  cp deploy/providers.env.example /etc/ioalab/providers.env
  chmod 640 /etc/ioalab/providers.env
  chown root:"$SVC_USER" /etc/ioalab/providers.env
fi
sed -e "s#/opt/ioalab#$APP_DIR#g" -e "s#/var/lib/ioalab#$STATE_DIR#g" -e "s#^User=.*#User=$SVC_USER#" -e "s#^Group=.*#Group=$SVC_USER#" \
  deploy/ioalab-providers.service > /etc/systemd/system/ioalab-providers.service
# 独立管理员九问；默认附带安装，可在受限服务器先设置IOALAB_INSTALL_SWARM=0后补装。
if [ "${IOALAB_INSTALL_SWARM:-1}" = "1" ]; then
  .venv/bin/python personal-assistant/install_swarm.py --venv "$APP_DIR/.swarm-venv" --data "$STATE_DIR/swarm"
  .venv/bin/python deploy/configure_assistant.py --core-env "$ENV_FILE" --assistant-env /etc/ioalab/personal-assistant.env --swarm-data "$STATE_DIR/swarm" --assistant-data "$STATE_DIR/personal-assistant"
  chown -R "$SVC_USER:$SVC_USER" "$STATE_DIR/swarm"
  chown root:"$SVC_USER" /etc/ioalab/personal-assistant.env
  chmod 640 /etc/ioalab/personal-assistant.env
fi
for unit in ioalab-assistant ioalab-swarm; do
  sed -e "s#/opt/ioalab#$APP_DIR#g" -e "s#/var/lib/ioalab#$STATE_DIR#g" -e "s#^User=.*#User=$SVC_USER#" -e "s#^Group=.*#Group=$SVC_USER#" "deploy/$unit.service" > "/etc/systemd/system/$unit.service"
done
systemctl daemon-reload
say "安装完成。下一步："
say "  1) 编辑 $ENV_FILE：IOALAB_PUBLIC_URL（首次启动后不可改）；发现模型key可选；另配providers.env与服务九问"
say "  2) 加载 $ENV_FILE 后执行 $APP_DIR/.venv/bin/ioalab check-config"
say "  3) sudo systemctl enable --now ioalab && journalctl -u ioalab -f"
say "  4) 可选：deploy/nginx.conf 反代与 HTTPS"

say "官方服务：配置 /etc/ioalab/providers.env 中地址与九问入口后，systemctl enable --now ioalab-providers"
