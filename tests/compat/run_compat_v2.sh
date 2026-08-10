#!/usr/bin/env bash
# Track E R3 — lance le harnais de compatibilité v2 (stateful + SSE).
# Démarre : bridge mock (8794), backend Python upstream (8793), port Rust (8792),
# puis exécute tests/compat/compat_v2.py et arrête tout.
#
# Usage: tests/compat/run_compat_v2.sh [--skip-build] [--keep-alive]
set -u

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
PY_URL="http://127.0.0.1:8793"
RS_URL="http://127.0.0.1:8792"
BRIDGE_URL="http://127.0.0.1:8794"

PY_STATE="/tmp/hwui-compat-state"
PY_HOME="/tmp/hwui-compat-home"
RS_STATE="/tmp/hermes-webui-rust-state"
WS_PY="/tmp/hwui-compat-workspace"
WS_RS="/tmp/hermes-webui-rust-workspace"

SKIP_BUILD=0
KEEP_ALIVE=0
for arg in "$@"; do
  case "$arg" in
    --skip-build) SKIP_BUILD=1 ;;
    --keep-alive) KEEP_ALIVE=1 ;;
  esac
done

echo "== cleanup state =="
rm -rf "$PY_STATE" "$PY_HOME" "$RS_STATE" "$WS_PY" "$WS_RS"
mkdir -p "$PY_STATE" "$PY_HOME" "$RS_STATE" "$WS_PY" "$WS_RS"

# Sel PBKDF2 COMMUN aux deux serveurs, écrit AVANT leur démarrage :
# Python met _PBKDF2_KEY_CACHE en cache à l'import (api/auth.py:356) — un sel
# écrit APRÈS le boot ne serait jamais relu. Le scénario auth/password-ratelimit
# lit ce sel commun pour hacher le password de test.
python3 - <<'EOF'
import secrets
from pathlib import Path
salt = secrets.token_bytes(32)
for d in ("/tmp/hwui-compat-state", "/tmp/hermes-webui-rust-state"):
    Path(d, ".pbkdf2_key").write_bytes(salt)
print("pbkdf2 salt commun écrit (32 octets) dans les deux STATE_DIR")
EOF

echo "== build rust (si besoin) =="
if [ "$SKIP_BUILD" -eq 0 ]; then
  (cd "$REPO/rust-server" && cargo build 2>&1 | tail -3) || exit 1
fi

echo "== start bridge mock (8794) =="
BRIDGE_PY="$REPO/.venv-compat/bin/python"
if [ ! -x "$BRIDGE_PY" ]; then
  BRIDGE_PY="$(command -v python3)"
fi
(cd "$REPO/bridge" && "$BRIDGE_PY" server.py --mode mock --port 8794 \
  >/tmp/hwui-bridge.log 2>&1 &)
sleep 1

echo "== start python upstream (8793) =="
(
  cd "$REPO"
  env -u HERMES_WEBUI_TLS_CERT -u HERMES_WEBUI_TLS_KEY \
  -u HERMES_WEBUI_PASSWORD \
  HERMES_HOME="$PY_HOME" \
  HERMES_WEBUI_STATE_DIR="$PY_STATE" \
  HERMES_WEBUI_PORT=8793 \
  HERMES_WEBUI_HOST=127.0.0.1 \
  HERMES_WEBUI_SKIP_ONBOARDING=1 \
  HERMES_WEBUI_DEFAULT_WORKSPACE="$WS_PY" \
  HERMES_WEBUI_PYTHON="$REPO/.venv-compat/bin/python" \
  "$REPO/.venv-compat/bin/python" server.py >/tmp/hwui-python.log 2>&1 &
)
sleep 2

echo "== start rust port (8792) =="
(
  cd "$REPO/rust-server"
  env -u HERMES_WEBUI_PASSWORD \
  HERMES_WEBUI_BRIDGE_URL="$BRIDGE_URL" \
  HERMES_WEBUI_WORKSPACE_ROOT="$WS_RS" \
  HERMES_WEBUI_DEFAULT_WORKSPACE="$WS_RS" \
  ./target/debug/hermes-webui-rust --host 127.0.0.1 --port 8792 \
    --repo-dir .. --state-dir "$RS_STATE" >/tmp/hwui-rust.log 2>&1 &
)
sleep 1

echo "== health checks =="
for i in $(seq 1 20); do
  ok=1
  curl -sf "$BRIDGE_URL/v1/health" >/dev/null 2>&1 || ok=0
  curl -sf "$PY_URL/health" >/dev/null 2>&1 || ok=0
  curl -sf "$RS_URL/health" >/dev/null 2>&1 || ok=0
  [ "$ok" -eq 1 ] && break
  sleep 1
done
curl -sf "$BRIDGE_URL/v1/health" >/dev/null 2>&1 || { echo "BRIDGE DOWN"; tail -5 /tmp/hwui-bridge.log; exit 1; }
curl -sf "$PY_URL/health" >/dev/null 2>&1 || { echo "PYTHON DOWN"; tail -20 /tmp/hwui-python.log; exit 1; }
curl -sf "$RS_URL/health" >/dev/null 2>&1 || { echo "RUST DOWN"; tail -20 /tmp/hwui-rust.log; exit 1; }
echo "all servers up"

echo "== run compat harness v2 =="
python3 "$REPO/tests/compat/compat_v2.py" \
  --python-url "$PY_URL" --rust-url "$RS_URL" --bridge-url "$BRIDGE_URL" \
  --python-state-dir "$PY_STATE" --rust-state-dir "$RS_STATE" \
  --python-workspace "$WS_PY" --rust-workspace "$WS_RS"
RC=$?

echo "== stop servers =="
pkill -f "server.py --mode mock --port 8794" 2>/dev/null
pkill -f "HERMES_WEBUI_STATE_DIR=$PY_STATE" 2>/dev/null
pkill -f "target/debug/hermes-webui-rust" 2>/dev/null
# fallback : kill par port (fuser absent sur cet hôte → ss + kill)
for port in 8794 8793 8792; do
  for pid in $(ss -tlnp 2>/dev/null | grep ":$port " | grep -oP 'pid=\K[0-9]+' | sort -u); do
    kill "$pid" 2>/dev/null || true
  done
done
sleep 1

if [ "$KEEP_ALIVE" -eq 1 ]; then
  echo "== keep-alive : serveurs laissés en marche =="
fi
exit $RC
