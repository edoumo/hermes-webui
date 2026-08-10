#!/usr/bin/env bash
# Benchmark R3 impact — Python upstream vs Rust release (Hermes WebUI port)
# Conditions identiques, même hôte. MÉTHODE : curl -w time_total, ps RSS,
# N itérations /health + static cold/warm + concurrence.
set -u
export LC_ALL=C LC_NUMERIC=C
PY_URL="${PY_URL:-http://127.0.0.1:8793}"
RUST_URL="${RUST_URL:-http://127.0.0.1:8797}"
N="${N:-100}"

measure() { # $1=url $2=label -> prints "avg_ms rss_mb"
  local url="$1" label="$2"
  local curlargs=(-s -o /dev/null -w "%{time_total}")
  case "$url" in https://*) curlargs+=(-k);; esac
  local total=""
  local t
  for _ in $(seq 1 "$N"); do
    t=$(curl "${curlargs[@]}" "$url/health")
    total="$total $t"
  done
  local avg=$(echo "$total" | awk '{s=0; for(i=1;i<=NF;i++) s+=$i; printf "%.3f", s/NF*1000}')
  echo "$label health_avg_ms=$avg"
}

rss() { # $1=url -> pid via port; rough: report RSS of rust process by name
  local pid
  pid=$(pgrep -f "target/release/hermes-webui-rust" | head -1)
  if [ -n "$pid" ]; then
    echo "rust_rss_mb=$(ps -o rss= -p "$pid" | awk '{printf "%.1f", $1/1024}')"
  fi
}

echo "=== R3 impact benchmark: $PY_URL vs $RUST_URL (N=$N) ==="
measure "$PY_URL" "python"
measure "$RUST_URL" "rust_release"
rss
echo "--- static cold/warm boot.js ---"
curl -s -o /dev/null -w "rust_cold_ms=%{time_total}\n" "$RUST_URL/static/boot.js"
curl -s -o /dev/null -w "rust_warm_ms=%{time_total}\n" "$RUST_URL/static/boot.js"
echo "--- concurrence 100 ---"
seq 1 100 | xargs -P 100 -I{} curl -s -o /dev/null "$RUST_URL/health" && echo "concurrency_100=PASS"
