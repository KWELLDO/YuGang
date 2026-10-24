#!/usr/bin/env bash
# Run as root after editing /etc/dafeiyu-dsh.env: copy only the HTTP token into
# a dafeiyu-readable 0600 file so the operator console scripts can use it even
# when the DSH tool shell does not inherit the service environment.
set -euo pipefail
SOURCE="${1:-/etc/dafeiyu-dsh.env}"
DEST="${2:-/home/dafeiyu/.yugang/http-token}"
TOKEN="$(sed -n "s/^YUGANG_HTTP_TOKEN=//p" "$SOURCE" | head -n1)"
[ -n "$TOKEN" ] || { echo "YUGANG_HTTP_TOKEN not found in $SOURCE" >&2; exit 2; }
install -d -o dafeiyu -g dafeiyu -m 0700 "$(dirname "$DEST")"
umask 077
printf "%s\n" "$TOKEN" > "$DEST"
chown dafeiyu:dafeiyu "$DEST"
chmod 600 "$DEST"
echo "wrote $DEST (mode 0600, owner dafeiyu)"
