#!/usr/bin/env bash
set -euo pipefail
if [ "$#" -gt 0 ]; then MESSAGE="$*"; else MESSAGE="$(cat)"; fi
[ -n "${MESSAGE//[[:space:]]/}" ] || { echo "empty message" >&2; exit 2; }
BASE="http://127.0.0.1:3198"
TOKEN="${YUGANG_HTTP_TOKEN:-}"
if [ -z "$TOKEN" ] && [ -r "${YUGANG_TOKEN_FILE:-/home/dafeiyu/.yugang/http-token}" ]; then
  TOKEN="$(cat "${YUGANG_TOKEN_FILE:-/home/dafeiyu/.yugang/http-token}")"
fi
[ -n "$TOKEN" ] || { echo "missing YUGANG_HTTP_TOKEN or token file" >&2; exit 2; }
python3 -c 'import json,sys; print(json.dumps({"content": sys.argv[1]}))' "$MESSAGE" \
  | curl -fsS -m 240 -X POST "$BASE/message" \
      -H "Authorization: Bearer $TOKEN" \
      -H 'Content-Type: application/json' --data-binary @- \
  | python3 -m json.tool --no-ensure-ascii
