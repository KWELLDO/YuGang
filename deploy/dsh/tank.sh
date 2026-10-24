#!/usr/bin/env bash
set -euo pipefail
BASE="http://127.0.0.1:3198"
TOKEN="${YUGANG_HTTP_TOKEN:-}"
if [ -z "$TOKEN" ] && [ -r "${YUGANG_TOKEN_FILE:-/home/dafeiyu/.yugang/http-token}" ]; then
  TOKEN="$(cat "${YUGANG_TOKEN_FILE:-/home/dafeiyu/.yugang/http-token}")"
fi
[ -n "$TOKEN" ] || { echo "missing YUGANG_HTTP_TOKEN or token file" >&2; exit 2; }
curl -fsS -H "Authorization: Bearer $TOKEN" "$BASE/tank" | python3 -m json.tool --no-ensure-ascii
