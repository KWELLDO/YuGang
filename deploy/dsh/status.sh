#!/usr/bin/env bash
set -euo pipefail
BASE="http://127.0.0.1:3198"
curl -fsS -H "Authorization: Bearer ${YUGANG_HTTP_TOKEN:?}" "$BASE/status" | python3 -m json.tool --no-ensure-ascii
