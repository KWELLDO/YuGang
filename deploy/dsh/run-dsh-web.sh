#!/usr/bin/env bash
# Run the operator console and capture its one-time launch-token URL.
set -euo pipefail
LOG="${DSH_HOME:-/home/dafeiyu/.dsh}/web.log"
OUT="${DSH_HOME:-/home/dafeiyu/.dsh}/web-url"
: > "$LOG"
rm -f "$OUT"
/usr/bin/dsh web --host 127.0.0.1 --port 3199 --no-open >>"$LOG" 2>&1 &
pid=$!
for _ in $(seq 1 120); do
  if ! kill -0 "$pid" 2>/dev/null; then
    wait "$pid"
    exit $?
  fi
  url="$(grep -oE 'http://127\.0\.0\.1:3199/\?token=[A-Za-z0-9_-]+' "$LOG" | tail -n1 || true)"
  if [ -n "$url" ]; then
    umask 077
    printf '%s\n' "$url" > "$OUT"
    break
  fi
  sleep 0.5
done
wait "$pid"
