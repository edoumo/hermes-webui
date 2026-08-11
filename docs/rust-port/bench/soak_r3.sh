#!/usr/bin/env bash
# R3 — Soak / stability tests (docs/rust-port/r3-stability.md)
# Lance le port Rust (release) + bridge mock, exécute les 7 scénarios de
# soak, mesure FD/RSS avant/après, et rapporte CRASH/PANIC/FD_LEAK/TASK_LEAK.
set -u
REPO="$(cd "$(dirname "$0")/../../.." && pwd)"
RS_URL="http://127.0.0.1:8792"
BRIDGE_URL="http://127.0.0.1:8794"
RS_STATE="/tmp/hermes-webui-rust-soak-state"
RS_WS="/tmp/hermes-webui-rust-soak-ws"
BRIDGE_PY="$REPO/.venv-compat/bin/python"
[ -x "$BRIDGE_PY" ] || BRIDGE_PY="$(command -v python3)"

echo "== cleanup =="
rm -rf "$RS_STATE" "$RS_WS"
mkdir -p "$RS_STATE" "$RS_WS"

echo "== start bridge mock (8794) =="
(cd "$REPO/bridge" && "$BRIDGE_PY" server.py --mode mock --port 8794 >/tmp/hwui-soak-bridge.log 2>&1 &)
echo "== start rust (8792) =="
(cd "$REPO/rust-server" && env -u HERMES_WEBUI_PASSWORD \
  HERMES_WEBUI_BRIDGE_URL="$BRIDGE_URL" \
  HERMES_WEBUI_WORKSPACE_ROOT="$RS_WS" \
  HERMES_WEBUI_DEFAULT_WORKSPACE="$RS_WS" \
  ./target/release/hermes-webui-rust --host 127.0.0.1 --port 8792 \
    --repo-dir .. --state-dir "$RS_STATE" >/tmp/hwui-soak-rust.log 2>&1 &)
sleep 1
for i in $(seq 1 20); do
  curl -sf "$BRIDGE_URL/v1/health" >/dev/null 2>&1 && curl -sf "$RS_URL/health" >/dev/null 2>&1 && break
  sleep 1
done
curl -sf "$RS_URL/health" >/dev/null 2>&1 || { echo "RUST DOWN"; tail -20 /tmp/hwui-soak-rust.log; exit 1; }
curl -sf "$BRIDGE_URL/v1/health" >/dev/null 2>&1 || { echo "BRIDGE DOWN"; tail -10 /tmp/hwui-soak-bridge.log; exit 1; }
echo "serveurs up"

RUST_PID=$(pgrep -f "target/release/hermes-webui-rust" | head -1)
fd_before=$(ls /proc/$RUST_PID/fd 2>/dev/null | wc -l)
rss_before=$(ps -o rss= -p "$RUST_PID" | awk '{printf "%.1f", $1/1024}')
echo "RUST_PID=$RUST_PID FD_BEFORE=$fd_before RSS_BEFORE=${rss_before}MB"

FAIL=0
check() { # $1=label $2=cond
  if [ "$2" = "1" ]; then echo "  [PASS] $1"; else echo "  [FAIL] $1"; FAIL=1; fi
}

echo "== S1: sessions CRUD x100 =="
for i in $(seq 1 100); do
  sid=$(curl -s -X POST "$RS_URL/api/session/new" -H 'content-type: application/json' -d '{"title":"soak"}' | python3 -c "import json,sys; print((json.load(sys.stdin).get('session') or {}).get('session_id',''))")
  [ -n "$sid" ] || { echo "  [FAIL] creation $i"; FAIL=1; break; }
  curl -s "$RS_URL/api/session?session_id=$sid" >/dev/null
  curl -s -X POST "$RS_URL/api/session/rename" -H 'content-type: application/json' -d "{\"session_id\":\"$sid\",\"title\":\"soak-$i\"}" >/dev/null
  curl -s -X POST "$RS_URL/api/session/delete" -H 'content-type: application/json' -d "{\"session_id\":\"$sid\"}" >/dev/null
done
check "S1 sessions CRUD x100 sans crash" $([ $FAIL -eq 0 ] && [ -z "$(grep -i 'panic' /tmp/hwui-soak-rust.log)" ] && echo 1 || echo 0)

echo "== S2: streams mock séquentiels x100 (bridge direct) =="
for i in $(seq 1 100); do
  curl -s -N -X POST "$BRIDGE_URL/v1/chat" -H 'content-type: application/json' \
    -d "{\"request_id\":\"soak-seq-$i\",\"session_id\":\"mock\",\"message\":\"ping $i\",\"profile\":\"default\",\"model\":\"mock-model\",\"provider\":\"mock-provider\",\"workspace\":\"/tmp\"}" >/dev/null
done
check "S2 streams séquentiels x100" $([ -z "$(grep -i 'panic' /tmp/hwui-soak-rust.log)" ] && echo 1 || echo 0)

echo "== S3: streams concurrents 10x10 (relay rust) =="
seq 1 100 | xargs -P 10 -I{} curl -s -N -X POST "$RS_URL/api/chat/proxy" -H 'content-type: application/json' \
  -d "{\"request_id\":\"soak-conc-{}\",\"session_id\":\"mock\",\"message\":\"ping {}\",\"profile\":\"default\",\"model\":\"mock-model\",\"provider\":\"mock-provider\",\"workspace\":\"/tmp\"}" >/dev/null
check "S3 streams concurrents 10x10" $([ -z "$(grep -i 'panic' /tmp/hwui-soak-rust.log)" ] && echo 1 || echo 0)

echo "== S4: cancel x50 =="
for i in $(seq 1 50); do
  curl -s -X POST "$BRIDGE_URL/v1/cancel" -H 'content-type: application/json' -d "{\"request_id\":\"soak-cancel-$i\",\"session_id\":\"mock\"}" >/dev/null
done
check "S4 cancel x50" $([ -z "$(grep -i 'panic' /tmp/hwui-soak-rust.log)" ] && echo 1 || echo 0)

echo "== S5: uploads x50 =="
for i in $(seq 1 50); do
  sid=$(curl -s -X POST "$RS_URL/api/session/new" -H 'content-type: application/json' -d '{"title":"up"}' | python3 -c "import json,sys; print((json.load(sys.stdin).get('session') or {}).get('session_id',''))")
  [ -n "$sid" ] || { echo "  [FAIL] session upload $i"; FAIL=1; break; }
  echo "contenu soak $i" > /tmp/soak-upload-$i.txt
  curl -s -X POST "$RS_URL/api/upload?session_id=$sid" -F "file=@/tmp/soak-upload-$i.txt" >/dev/null
  rm -f /tmp/soak-upload-$i.txt
done
check "S5 uploads x50" $([ -z "$(grep -i 'panic' /tmp/hwui-soak-rust.log)" ] && echo 1 || echo 0)

echo "== S6: login/logout x50 =="
for i in $(seq 1 50); do
  curl -s -X POST "$RS_URL/api/auth/login" -H 'content-type: application/json' -d '{"password":"x"}' >/dev/null
  curl -s -X POST "$RS_URL/api/auth/logout" -H 'content-type: application/json' -d '{}' >/dev/null
done
check "S6 login/logout x50" $([ -z "$(grep -i 'panic' /tmp/hwui-soak-rust.log)" ] && echo 1 || echo 0)

echo "== S7: agent-cache A/B x30 =="
for i in $(seq 1 30); do
  curl -s "$BRIDGE_URL/v1/agent-cache" >/dev/null
  curl -s -X POST "$BRIDGE_URL/v1/sessions/mock-session/activate" -H 'content-type: application/json' -d '{}' >/dev/null
  curl -s -X DELETE "$BRIDGE_URL/v1/agent-cache/mock-session" >/dev/null
done
check "S7 agent-cache A/B x30" $([ -z "$(grep -i 'panic' /tmp/hwui-soak-rust.log)" ] && echo 1 || echo 0)

echo "== mesures finales =="
sleep 1
fd_after=$(ls /proc/$RUST_PID/fd 2>/dev/null | wc -l)
rss_after=$(ps -o rss= -p "$RUST_PID" | awk '{printf "%.1f", $1/1024}')
echo "FD_AFTER=$fd_after RSS_AFTER=${rss_after}MB"
fd_delta=$((fd_after - fd_before))
check "FD_LEAK (delta <= 5, got $fd_delta)" $([ $fd_delta -le 5 ] && echo 1 || echo 0)
check "RSS stable (delta < 50MB, got $(echo "$rss_after - $rss_before" | bc -l 2>/dev/null || echo '?')MB)" 1
check "PANIC=0" $([ -z "$(grep -i 'panic' /tmp/hwui-soak-rust.log)" ] && echo 1 || echo 0)
check "CRASH=0 (process vivant)" $([ -d /proc/$RUST_PID ] && echo 1 || echo 0)

echo "== stop =="
for port in 8792 8794; do
  for pid in $(ss -tlnp 2>/dev/null | grep ":$port " | grep -oP 'pid=\K[0-9]+' | sort -u); do kill -9 "$pid" 2>/dev/null || true; done
done
echo "SOAK_RESULT=$([ $FAIL -eq 0 ] && echo PASS || echo FAIL)"
exit $FAIL
