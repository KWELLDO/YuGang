#!/usr/bin/env bash
# Capture the one-time DSH Web token URL printed at startup into a 0600 file.
set -euo pipefail
LOG="${DSH_HOME:-/home/dafeiyu/.dsh}/web.log"
OUT="${DSH_HOME:-/home/dafeiyu/.dsh}/web-url"
for _ in $(seq 1 120); do
  if [ -s "$LOG" ]; then
    url="$(grep -oE 'http://127\.0\.0\.1:3199/\?token=[A-Za-z0-9_-]+' "$LOG" | tail -n1 || true)"
    if [ -n "$url" ]; then
      umask 077
      printf '%s\n' "$url" > "$OUT"
      exit 0
    fi
  fi
  sleep 0.5
done
echo "dsh web: no token URL appeared in $LOG" >&2
exit 1
