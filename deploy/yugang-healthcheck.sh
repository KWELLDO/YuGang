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

if ! body=$(curl -fsS --max-time 5 "$HEALTH_URL" 2>/dev/null); then
    logger -t yugang-healthcheck "health check failed; restarting yugang.service"
else
    # Degraded means the process is alive but at least one channel is failing
    # (for example IMAP login). Restarting cannot fix credentials and would
    # loop every two minutes, so leave it running and make it visible.
    if printf '%s' "$body" | grep -q '"degraded": *true'; then
        logger -t yugang-healthcheck "gateway is degraded but alive: $body"
    fi
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
