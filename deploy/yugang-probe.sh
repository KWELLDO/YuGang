#!/bin/sh
# Ask her from outside: does the gateway answer, is the memory still honest,
# is there disk and budget left? Runs as dafeiyu under its own timer.
#
# Every run is cheap and text-only. Once per local day it also spends one tiny
# model call (--deep), because a live process with an expired key looks exactly
# like a quiet afternoon until somebody writes to her. The day is stamped
# after the attempt, so a crash loop cannot bill the model every 15 minutes.
set -eu

HOME_DIR="${YUGANG_HOME:-/home/dafeiyu/.yugang}"
export PYTHONPATH="${YUGANG_SRC:-/home/dafeiyu/yugang/src}"
STAMP="$HOME_DIR/health/deep-probe.day"
TODAY=$(date +%F)

DEEP=""
if [ ! -f "$STAMP" ] || [ "$(cat "$STAMP" 2>/dev/null)" != "$TODAY" ]; then
    DEEP="--deep"
fi

status=0
# shellcheck disable=SC2086
/usr/bin/python3 -m yugang --home "$HOME_DIR" probe --write --alert $DEEP || status=$?

if [ -n "$DEEP" ]; then
    mkdir -p "$HOME_DIR/health"
    printf '%s' "$TODAY" > "$STAMP"
fi

exit "$status"
