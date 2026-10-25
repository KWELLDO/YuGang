#!/bin/sh
# Ask her from outside: does the gateway answer, is the memory still honest,
# is there disk and budget left? Runs as dafeiyu under its own timer.
#
# Every run is cheap and text-only. Once per local day it also spends one tiny
# model call (--deep), because a live process with an expired key looks exactly
# like a quiet afternoon until somebody writes to her. The day stamp is written
# by the probe itself, right before the paid call, so this script owns no state.
set -eu

HOME_DIR="${YUGANG_HOME:-/home/dafeiyu/.yugang}"
export PYTHONPATH="${YUGANG_SRC:-/home/dafeiyu/yugang/src}"

exec /usr/bin/python3 -m yugang --home "$HOME_DIR" probe --write --alert --deep-if-due
