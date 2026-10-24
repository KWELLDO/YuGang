# Availability

The body is always on; the mind wakes on demand.

- `yugang-gateway` is a small always-on process. It holds channels, the inbox, the budget
  ledger, and the wake window. It does not call the model while idle.
- Any user message wakes her. Sleep is a low-power state, not a do-not-disturb schedule.
- A wake is a window: messages inside the window share one session; idle 10–15 minutes
  closes it. A continuous conversation keeps her awake.
- If the model API, gateway, or budget fails, the gateway replies with a system notice and
  stores the message in L1. It never silently drops a message.
- The only true unavailability is host/network/process failure. systemd, health checks, and
  a daily heartbeat alert cover that; it is an operational failure, not "she is sleeping".
