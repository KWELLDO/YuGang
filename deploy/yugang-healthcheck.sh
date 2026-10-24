#!/bin/sh
# Root-owned watchdog: restart the gateway if its HTTP body answers unhealthy.
# systemd already restarts a crashed process; this covers a live process whose
# worker thread died or stopped heartbeating.
set -eu

HEALTH_URL="${YUGANG_HEALTH_URL:-http://127.0.0.1:3198/health}"

# A deliberately stopped service is maintenance, not a hang. Do not override it.
if ! systemctl is-active --quiet yugang.service; then
    exit 0
fi

if curl -fsS --max-time 5 "$HEALTH_URL" >/dev/null 2>&1; then
    exit 0
fi

logger -t yugang-healthcheck "health check failed; restarting yugang.service"
systemctl restart yugang.service
sleep 3

if curl -fsS --max-time 5 "$HEALTH_URL" >/dev/null 2>&1; then
    exit 0
fi

logger -t yugang-healthcheck "still unhealthy after restart"
exit 1
