#!/bin/sh
# Turn on her self-change lane: a writable mirror, a test environment, and a
# root-only applier. Idempotent; safe to re-run after a deploy.
#
#   1. /opt/yugang-venv gets pytest (root-owned: she runs it, she cannot edit it)
#   2. ~/.yugang/self becomes her git mirror of the live tree
#   3. the live tree becomes root:root, read-only for her
#   4. a timer runs `yugang apply --auto` — auto tier only, tests must be green
#
# What this deliberately does not do: give her write access to live, a remote to
# push to, or the ability to approve her own review-tier changes. Live is hers to
# read and copy; it is not hers to edit.
set -eu

LIVE="${YUGANG_LIVE:-/home/dafeiyu/yugang}"
HOME_DIR="${YUGANG_HOME:-/home/dafeiyu/.yugang}"
VENV="${YUGANG_VENV:-/opt/yugang-venv}"
USER_NAME="${YUGANG_USER:-dafeiyu}"
STATE="${YUGANG_STATE:-/var/lib/yugang}"
HERE="$(cd "$(dirname "$0")" && pwd)"

say() { printf '%s\n' "$*"; }

say "1/5 test environment: $VENV"
if [ ! -x "$VENV/bin/python" ]; then
  /usr/bin/python3 -m venv "$VENV"
fi
"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/python" -m pip install --quiet pytest
"$VENV/bin/python" -m pytest --version

say "2/5 state dirs"
install -d -m 0700 "$STATE/candidates" "$STATE/rollback"
install -d -o "$USER_NAME" -g "$USER_NAME" -m 0755 "$HOME_DIR/proposals" "$HOME_DIR/apply-results"

say "3/5 her mirror of the live tree"
# --sync on an existing mirror, not just "leave it alone": after a deploy a
# stale mirror means she tests code that is no longer running, and her patches
# are cut against a base that already shipped, so git apply fails for reasons
# that have nothing to do with her change. Her commits stay; this adds one.
if [ -d "$HOME_DIR/self/.git" ]; then
  sudo -u "$USER_NAME" env PYTHONPATH="$LIVE/src" /usr/bin/python3 \
    -m yugang --home "$HOME_DIR" source --sync
else
  sudo -u "$USER_NAME" env PYTHONPATH="$LIVE/src" /usr/bin/python3 \
    -m yugang --home "$HOME_DIR" source --init
fi

say "4/5 live becomes read-only for her"
# Read for everyone, write for nobody. Do not just chmod a-w: a tree built with
# umask 077 is 0600, so subtracting write leaves 0400 root-only — and the
# service, which runs as her, cannot read its own source and crash-loops. Set
# the bits explicitly instead of subtracting from whatever happened to be there.
chown -R root:root "$LIVE"
find "$LIVE" -type d -exec chmod 0555 {} +
find "$LIVE" -type f -exec chmod 0444 {} +

say "5/5 applier timer"
install -m 0644 "$HERE/yugang-apply.service" /etc/systemd/system/yugang-apply.service
install -m 0644 "$HERE/yugang-apply.timer" /etc/systemd/system/yugang-apply.timer
systemctl daemon-reload
systemctl enable --now yugang-apply.timer >/dev/null

systemctl restart yugang.service
sleep 2
systemctl is-active yugang.service
say "done: she can write $HOME_DIR/self and propose; only root applies."
