#!/usr/bin/env bash
set -euo pipefail
if [ "$#" -gt 0 ]; then MESSAGE="$*"; else MESSAGE="$(cat)"; fi
[ -n "${MESSAGE//[[:space:]]/}" ] || { echo "empty message" >&2; exit 2; }
BASE="http://127.0.0.1:3198"
python3 -c 'import json,sys; print(json.dumps({"content": sys.argv[1]}))' "$MESSAGE" \
  | curl -fsS -m 240 -X POST "$BASE/message" \
      -H "Authorization: Bearer ${YUGANG_HTTP_TOKEN:?}" \
      -H 'Content-Type: application/json' --data-binary @- \
  | python3 -m json.tool --no-ensure-ascii
