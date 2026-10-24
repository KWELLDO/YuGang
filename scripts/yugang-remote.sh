#!/usr/bin/env bash
# Talk to the private YuGang instance over SSH.
#
#   ./scripts/yugang-remote.sh "我来看你了。"   # send a message, wait for reply
#   ./scripts/yugang-remote.sh --status         # gateway + memory + budget
#   ./scripts/yugang-remote.sh --tank           # her body: SELF.md, sleep, loops, recent events
#   ./scripts/yugang-remote.sh --health         # liveness only
#   echo "从手机发来的" | ./scripts/yugang-remote.sh
#
# The gateway binds 127.0.0.1 on the host; this script never opens a public port.
# It reads YUGANG_HTTP_TOKEN from /etc/dafeiyu-dsh.env on the host (root-owned).
set -euo pipefail

HOST="${YUGANG_SSH_HOST:-dsh-online}"
TIMEOUT="${YUGANG_TIMEOUT:-220}"
BASE="http://127.0.0.1:3198"

pretty() {
  python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin), ensure_ascii=False, indent=2))'
}

remote_get() {
  local path="$1"
  ssh -o BatchMode=yes "$HOST" "TOKEN=\$(awk -F= '/^YUGANG_HTTP_TOKEN=/{print \$2}' /etc/dafeiyu-dsh.env); curl -fsS -H \"Authorization: Bearer \$TOKEN\" $BASE$path"
}

remote_status() {
  remote_get /status
}

case "${1:-}" in
  --health|-h)
    ssh -o BatchMode=yes "$HOST" "curl -fsS $BASE/health"
    echo
    ;;
  --status|-s)
    remote_status | pretty
    ;;
  --tank|-t)
    remote_get /tank | pretty
    ;;
  --replies)
    remote_get /replies | pretty
    ;;
  --help)
    sed -n '1,20p' "$0"
    ;;
  *)
    if [ "$#" -gt 0 ]; then
      MESSAGE="$*"
    else
      MESSAGE="$(cat)"
    fi
    if [ -z "${MESSAGE//[[:space:]]/}" ]; then
      echo "empty message" >&2
      exit 2
    fi
    PAYLOAD="$(printf '%s' "$MESSAGE" | python3 -c 'import json,sys; print(json.dumps({"content": sys.stdin.read()}))')"
    printf '%s' "$PAYLOAD" | ssh -o BatchMode=yes "$HOST" "TOKEN=\$(awk -F= '/^YUGANG_HTTP_TOKEN=/{print \$2}' /etc/dafeiyu-dsh.env); curl -fsS -m $TIMEOUT -X POST $BASE/message -H \"Authorization: Bearer \$TOKEN\" -H 'Content-Type: application/json' --data-binary @-" | pretty
    ;;
esac
