# Deploying YuGang

This is the v1 private deployment. The body is a systemd service; the model is called only inside a wake window.

## Topology

```
           SSH tunnel / private network              127.0.0.1:3198
phone / laptop  ───────────────────────────────▶  yugang-gateway (dafeiyu)
                                                      │
                                                      ├── SQLite + SELF.md
                                                      └── DeepSeek API (only while awake)
```

No public port is opened. The gateway binds `127.0.0.1`; access is through an SSH local forward or a private overlay network.

## Host setup (Debian)

```bash
sudo useradd --create-home --shell /bin/bash dafeiyu
sudo install -d -o dafeiyu -g dafeiyu /home/dafeiyu/yugang /home/dafeiyu/.yugang
# rsync is not installed on every minimal Debian host; tar over SSH works everywhere.
tar -C . -cf - --exclude=.git --exclude=__pycache__ --exclude=.pytest_cache --exclude='*.db' . \
  | ssh root@host 'tar -C /home/dafeiyu/yugang -xf - && chown -R dafeiyu:dafeiyu /home/dafeiyu/yugang'
```

As root, create `/etc/dafeiyu-dsh.env` from `deploy/yugang.env.example`, then `chmod 600 /etc/dafeiyu-dsh.env`. It must not be in the repository or world-readable.

```bash
sudo -u dafeiyu env PYTHONPATH=/home/dafeiyu/yugang/src \
  python3 -m yugang --home /home/dafeiyu/.yugang init
sudo install -m 644 deploy/yugang.service /etc/systemd/system/yugang.service
sudo systemctl daemon-reload
sudo systemctl enable --now yugang.service
sudo systemctl status yugang.service --no-pager
```

## Health watchdog

`Restart=always` covers a crashed process. A live process can still lose its
worker thread, so a small root-owned watchdog polls `GET /health` every two
minutes. The endpoint returns 503 unless the worker is alive and its heartbeat
is recent; the watchdog restarts `yugang.service` only if systemd reports it as
active, so an intentional `systemctl stop` is not overridden.

```bash
sudo install -m 755 deploy/yugang-healthcheck.sh /usr/local/sbin/yugang-healthcheck.sh
sudo install -m 644 deploy/yugang-healthcheck.service /etc/systemd/system/
sudo install -m 644 deploy/yugang-healthcheck.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now yugang-healthcheck.timer
systemctl list-timers yugang-healthcheck.timer --no-pager
```

Run it once by hand with `sudo systemctl start yugang-healthcheck.service` and
check `journalctl -t yugang-healthcheck` after a real incident.

### The probe: is anything actually reaching her?

The watchdog above answers "is the process hung". It cannot answer "can a
message still get in", "is the memory still honest", "is there disk and budget
left". A bot that was alive and disconnected for 57 minutes looks perfectly
healthy from the outside, and a quiet afternoon looks exactly like her sleeping.
So a second timer asks from outside the process instead of trusting it:

```bash
sudo install -m 755 deploy/yugang-probe.sh /usr/local/sbin/yugang-probe.sh
sudo install -m 644 deploy/yugang-probe.service /etc/systemd/system/
sudo install -m 644 deploy/yugang-probe.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now yugang-probe.timer
systemctl list-timers yugang-probe.timer --no-pager
```

The unit must carry `EnvironmentFile=/etc/dafeiyu-dsh.env`, like the gateway
does. Without it the deep probe cannot authenticate, the unit exits before
writing `health.json`, and the last healthy verdict stays on disk — a probe
that dies every fifteen minutes looks exactly like a quiet day. Check it with
`journalctl -u yugang-probe.service` after installing; the first real deployment
here failed on this and reported success for the timer.

It runs as `dafeiyu` every 15 minutes and writes `<home>/health/health.json`
(the newest verdict), `history.jsonl` (last 500 verdicts), and `alert-state.json`.
Checks: gateway reachable and self-reporting healthy, memory audit problems
(chain, evidence, backups), free disk (<1G problem, <3G warning), budget left for
today. The same fault is one L1 event per six hours, not one per run, and the
recovery event says how many suppressed failures it closes.

Once per local day it adds `--deep`: one model call with `max_tokens=16`, which
is the only way to notice an expired key or a dead upstream before a human
writes to her. The timer passes `--deep-if-due` rather than deciding the day in
shell, so `<home>/health/deep-probe.day` is written by the code that spends the
money: a missing key fails before the stamp, and a crash after the call cannot
bill again fifteen minutes later.

```bash
sudo -u dafeiyu env PYTHONPATH=/home/dafeiyu/yugang/src \
  python3 -m yugang --home /home/dafeiyu/.yugang probe --deep-if-due
```

A by-hand run that must reach the model even on an already-claimed day uses
`--deep`; the timer uses `--deep-if-due`.

It exits 1 when anything is wrong, so it can also be run by hand before/after a
deploy. It never repairs anything: a probe that silently fixes what it measures
turns a fault into a delay.

## Access

From a laptop:

```bash
ssh -N -L 3198:127.0.0.1:3198 user@host
# then, in another shell:
curl -fsS -H "Authorization: Bearer $YUGANG_HTTP_TOKEN" http://127.0.0.1:3198/status
curl -fsS -X POST http://127.0.0.1:3198/message \
  -H "Authorization: Bearer $YUGANG_HTTP_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"content":"我来看你了。"}'
```

`POST /message` waits for her reply, so a sleeping process still answers: the request is stored in L1 first, then it wakes her.

Operators can end the current window immediately with an authenticated `POST /sleep` (body optional: `{"reason":"..."}`). It runs the same sleep protocol in the live process and returns the applied result; normal sleep still happens on the idle window. This endpoint is for deployment checks and manual consolidation, not for her.

`POST /pause` stops model calls and autonomous wakes without pretending she slept. Messages that arrive while paused are written to L1 and answered with a system notice (not her voice); they stay buffered and are replayed by `POST /resume`. If she was awake, pausing ends the window and records that this was not a sleep, so there is no fake diary.

```bash
curl -fsS -X POST http://127.0.0.1:3198/pause \
  -H "Authorization: Bearer $YUGANG_HTTP_TOKEN" \
  -H 'Content-Type: application/json' -d '{"reason":"maintenance"}'
curl -fsS -X POST http://127.0.0.1:3198/resume \
  -H "Authorization: Bearer $YUGANG_HTTP_TOKEN"
```

`/status` exposes `paused` and `pause.reason`; `/sleep` returns 409 while paused.

`GET /audit` (authenticated) runs the same read-only memory audit as the CLI. It returns
200 when the body is clean and 409 when it has problems; the JSON still carries the full
report. It never repairs or rewrites anything.

```bash
curl -fsS -H "Authorization: Bearer $YUGANG_HTTP_TOKEN" http://127.0.0.1:3198/audit
sudo -u dafeiyu env PYTHONPATH=/home/dafeiyu/yugang/src \
  python3 -m yugang --home /home/dafeiyu/.yugang audit
```

The CLI exits 1 when `problems` is non-empty, so it can be used as a deployment gate.
`warnings` do not change the exit code or the HTTP status; they flag things worth a human
look, such as several current L2 judgments whose wording is highly similar. The audit only
reports those groups; it never merges them.

A small client wraps this so a phone/laptop only needs the existing SSH access:

```bash
./scripts/yugang-remote.sh "我来看你了。"
./scripts/yugang-remote.sh --status
./scripts/yugang-remote.sh --tank     # SELF.md、最近一次睡去、未闭合事项、最近事件
echo "排队时想到的一句话" | ./scripts/yugang-remote.sh
```

It reads `YUGANG_HTTP_TOKEN` from the host-side root-only env file and never exposes the port. Set `YUGANG_SSH_HOST` if the SSH alias is not `dsh-online`.

## Fish tank UI

The gateway also serves a mobile-first read/write page at `/`. It shows whether she is
awake or asleep, the last sleep, recent conversation, SELF.md, the L2 judgments she still
holds (with their falsifiers), open awareness cards, open loops, recent events and budget.
Repeated awareness hypotheses are not shown as separate confirmations; `yugang
awareness-dedupe` can merge historical near-duplicates into one canonical card. The message
box calls the same `POST /message` path, so sending a message wakes her even while she is
asleep.

With the SSH tunnel above open, visit:

    http://127.0.0.1:3198/?token=<YUGANG_HTTP_TOKEN>

The token is in `/etc/dafeiyu-dsh.env` on the host. The page reads it once, stores it in
`sessionStorage`, and removes it from the address bar. Without a token the page returns
401. The page makes no external requests and has no external assets.

## Wake semantics

- The gateway is always on; the model is not.
- Idle window: 15 minutes; hard cap: 90 minutes.
- A user message wakes her immediately, even if the daily budget is low (it can spend to the daily cap).
- Autonomous activity reserves the user budget (`user_reserve_cny`).
- Budget or model failure is fail-loud: the message is marked failed and a system reply is recorded.

## Email channel

The gateway can read one mailbox over IMAP and reply over SMTP. The mailbox is also a
human inbox, so the channel is deliberately narrow:

- the subject must contain the tag `[YuGang]` (checked after decoding the header; a
  non-ASCII subject is RFC 2047 encoded, so server-side IMAP subject search is not used);
- the sender must be in `allow_senders`;
- untagged mail is header-peeked only: its body is not downloaded and its unread flag is
  never cleared;
- replies carry `Auto-Submitted: auto-replied` and `X-YuGang-Reply: 1`, and the poller
  skips its own replies so it cannot loop;
- an untagged message is not a message for her, even if it comes from an allowed address.

Config in `/home/dafeiyu/.yugang/config.json`:

```json
{
  "email": {
    "enabled": true,
    "imap_host": "imap.qq.com",
    "imap_port": 993,
    "smtp_host": "smtp.qq.com",
    "smtp_port": 465,
    "username": "you@example.com",
    "password_env": "YUGANG_EMAIL_PASSWORD",
    "folder": "INBOX",
    "allow_senders": ["you@example.com"],
    "notify_to": ["you@example.com"],
    "subject_tag": "[YuGang]",
    "poll_seconds": 60
  }
}
```

`notify_to` is where her self-initiated output (an autonomous wake, an awareness
card) is emailed; leave it empty to fall back to `allow_senders`. This never
expands who may write to her — it only reuses the already allowlisted operator.

Put the password/app-password in `/etc/dafeiyu-dsh.env` as `YUGANG_EMAIL_PASSWORD`
(root-only, mode 0600). To test without changing unread state, run a single poll from the
host with the same credentials and check that `polled` is 0 when no tagged mail exists.

Send a test message to the mailbox with a subject like `[YuGang] 我来看你了`; her reply
goes back to the same thread.

## OneBot / QQ bridge

The `onebot` channel is a OneBot v11 private-message bridge for a local
[NapCat](https://github.com/NapNeko/NapCatQQ) (or compatible) process. It is
disabled by default. It does not open a public port and it never accepts group
messages.

Topology:

```
QQ app ──▶ Tencent ──▶ NapCat (local) ── HTTP API 127.0.0.1:3000
                              │
                              └── reverse HTTP event ──▶ 127.0.0.1:3197/onebot/event
                                                              │
                                                              ▼
                                                        yugang gateway
```

Config in `/home/dafeiyu/.yugang/config.json`:

```json
{
  "onebot": {
    "enabled": true,
    "api_base": "http://127.0.0.1:3000",
    "webhook_host": "127.0.0.1",
    "webhook_port": 3197,
    "webhook_secret_env": "YUGANG_ONEBOT_WEBHOOK_SECRET",
    "access_token_env": "YUGANG_ONEBOT_TOKEN",
    "allowed_user_ids": ["YOUR_QQ_NUMBER"],
    "request_timeout_seconds": 30,
    "max_message_chars": 1500
  }
}
```

Put both secrets in `/etc/dafeiyu-dsh.env` (root-only, 0600):

```sh
YUGANG_ONEBOT_WEBHOOK_SECRET=<long-random-secret>
YUGANG_ONEBOT_TOKEN=<the HTTP API token NapCat is configured with>
```

NapCat side:

- enable its HTTP server on `127.0.0.1:3000` (not `0.0.0.0`) and set its
  access token to `YUGANG_ONEBOT_TOKEN`;
- configure the reverse HTTP event target to
  `http://127.0.0.1:3197/onebot/event`, with
  `Authorization: Bearer <YUGANG_ONEBOT_WEBHOOK_SECRET>` (the bridge also
  accepts `X-YuGang-Secret` and `X-Signature: sha1=...` /
  `X-Signature: sha256=...` HMAC of the raw body);
- add only the intended QQ number(s) to `allowed_user_ids`. An empty allowlist
  rejects every message (fail closed);
- do not enable group messages. The bridge rejects `message_type != private`.

Test the webhook locally without QQ:

```bash
curl -sS -X POST http://127.0.0.1:3197/onebot/event \
  -H "Content-Type: application/json" \
  -H "X-YuGang-Secret: $YUGANG_ONEBOT_WEBHOOK_SECRET" \
  -d '{"post_type":"message","message_type":"private","self_id":"999","user_id":"YOUR_QQ_NUMBER","message_id":1,"message":[{"type":"text","data":{"text":"在吗"}}]}'
```

It returns `{"ok":true,"accepted":true,...}`; her reply goes through
`send_private_msg` to the same QQ number. If delivery fails, the gateway writes
a `system/error` event and the message remains visible in L1.

## QQ official bot platform (webhook)

The `qq` channel talks to the **official** platform (bot.q.qq.com) instead of a
local NapCat. It is a different protocol, not a different transport for the same
thing, and the difference that shapes everything is this: the platform
addresses people by **openid**, and there is no lookup from a QQ number to an
openid. The only way to learn somebody's openid is to receive a message from
them.

Topology:

```
QQ app ──▶ Tencent platform ── signed HTTPS callback ──▶ nginx (ayu.dsh.kwelldo.cn/qq/event)
                                                              │
                                                              ▼
                                                    127.0.0.1:3196 (yugang gateway)
```

Config in `/home/dafeiyu/.yugang/config.json`:

```json
{
  "qq": {
    "enabled": true,
    "app_id": "1903699681",
    "app_secret_env": "YUGANG_QQ_APP_SECRET",
    "api_base": "https://api.sgroup.qq.com",
    "token_url": "https://bots.qq.com/app/getAppAccessToken",
    "webhook_host": "127.0.0.1",
    "webhook_port": 3196,
    "allowed_openids": [],
    "request_timeout_seconds": 30,
    "max_message_chars": 1000
  }
}
```

Put the bot secret in `/etc/dafeiyu-dsh.env` (root-only, 0600):

```sh
YUGANG_QQ_APP_SECRET=<the AppSecret from bot.q.qq.com>
```

nginx: `deploy/yugang-qq.nginx.conf` exposes exactly one path — the callback —
and returns 404 for everything else on that host. Install it, register the
callback URL in the platform console (**Webhook** mode) as

```
https://ayu.dsh.kwelldo.cn/qq/event
```

and the platform performs its own URL verification (op 13) on save. If saving
fails, the signature is wrong — check the secret, not the URL.

Opening that URL in a browser proves nothing: a `GET` answers `405` with
`{"error":"method not allowed",...}` and an `Allow: POST` header. That is
deliberate. The stock `http.server` answer is an HTML `501` page, and a real
deployment already returned one to a browser; it tells the operator nothing and
looks like a broken endpoint. The `Server` header is pinned to
`YuGangQQ/0.1` as well, so the one public endpoint does not advertise the
interpreter version.

First message from a new person:

- The platform delivers `C2C_MESSAGE_CREATE` with their openid.
- Openids not in `allowed_openids` get **no reply and no model call**. Instead a
  `system/note` event is written to L1 carrying the openid and marking the
  material untrusted. Not replying is the fail-closed choice; writing it down is
  what makes it possible to allow that person afterwards.
- Read the openid from L1 (`SELECT meta FROM events WHERE kind='note' ORDER BY
  ts DESC LIMIT 3;`), add it to `qq.allowed_openids`, restart `yugang.service`.
  Only then does she see that conversation at all.

Facts worth knowing before debugging:

- The channel **refuses to start** without the secret. An unauthenticated public
  endpoint that answers as her would be worse than no IM channel.
- Every callback is verified with Ed25519 over `X-Signature-Timestamp + body`.
  The library is `src/yugang/gateway/channels/ed25519.py` — plain stdlib, because
  the runtime has no `cryptography` and no `nacl` — and it is pinned to the RFC
  8032 vectors plus the conformance pair published in the platform's sign docs.
  Timestamp freshness is deliberately not enforced: the store already ignores a
  repeated `external_id`, so a replayed delivery is dropped where it would
  otherwise cost a wake.
- The handler verifies, enqueues, and answers `200` immediately; the platform
  retries anything slower, which would double every message.
- Replies are sent with `msg_id` set to the inbound message's id, because a
  message without it counts as **proactive** (quota-limited, and it may be
  refused). That is why `GatewayService._reply_meta` passes it through.
- Group messages, guild traffic and DMs to other bots are ignored: only
  `C2C_MESSAGE_CREATE` is her channel.
- Delivery failures are written as `system/error` in L1 and surface in
  `/health` under `channels.qq`.

Verify the whole path without involving Tencent:

```bash
python3 - <<'PY'
import json, sys, time, urllib.request
sys.path.insert(0, "/home/dafeiyu/yugang/src")
from yugang.gateway.channels import ed25519

secret = "<your secret>"
seed = ed25519.seed_from_secret(secret)
ts = str(int(time.time()))
body = json.dumps({"op": 13, "d": {"plain_token": "probe", "event_ts": ts}}).encode()
request = urllib.request.Request(
    "https://ayu.dsh.kwelldo.cn/qq/event",
    data=body,
    headers={
        "Content-Type": "application/json",
        "X-Signature-Timestamp": ts,
        "X-Signature-Ed25519": ed25519.sign(seed, ts.encode() + body).hex(),
    },
    method="POST",
)
print(json.load(urllib.request.urlopen(request)))
PY
```

It returns `{"plain_token": "probe", "signature": "..."}`; an unsigned POST to
the same URL returns 403. A wrong secret fails here and nowhere later, which is
the point of testing it this way.

## Private-network options

1. **SSH tunnel** (works today, no new dependency).
2. **Tailscale/WireGuard** (better for phone; no public inbound port).
3. **Mail** (works today; subject tag `[YuGang]`, allow-listed sender only).

`ayu.dsh.kwelldo.cn` is public for exactly one path — `/qq/event`, the signed QQ
platform callback. The tank page, the HTTP API and her whole body stay on
`127.0.0.1`.

## Daily backup

`yugang-backup.timer` runs at 04:15 local time (with up to 3 minutes of jitter) and writes a
consistent snapshot to `/home/dafeiyu/.yugang/backups/`:

- `<stamp>.db` — SQLite snapshot in `journal_mode=DELETE`, mode 0600, integrity-checked
- `<stamp>.SELF.md` — the SELF.md as of the backup
- `<stamp>.manifest.json` — sha256, event count, hash-chain status, SELF version

It keeps 14 days. Run one by hand:

```bash
sudo systemctl start yugang-backup.service
sudo systemctl status yugang-backup.service --no-pager
ls -lh /home/dafeiyu/.yugang/backups/
```

To restore, stop the gateway first. The CLI verifies the backup before it writes anything:

```bash
sudo systemctl stop yugang.service
sudo -u dafeiyu env PYTHONPATH=/home/dafeiyu/yugang/src \
  /usr/bin/python3 -m yugang --home /home/dafeiyu/.yugang \
  restore --from /home/dafeiyu/.yugang/backups/<stamp>.db --dry-run
sudo -u dafeiyu env PYTHONPATH=/home/dafeiyu/yugang/src \
  /usr/bin/python3 -m yugang --home /home/dafeiyu/.yugang \
  restore --from /home/dafeiyu/.yugang/backups/<stamp>.db --force
sudo systemctl start yugang.service
```

`restore` checks the manifest sha256, event count, SQLite `integrity_check`, and the L1 hash
chain. Without `--force` it refuses to overwrite an existing body. With `--force` it first
snapshots the current body under `backups/`, swaps the db atomically, writes SELF.md from the
restored database, appends one `system/note` recording the restore, and verifies the chain
again; a failure rolls back to the pre-restore snapshot. Run a drill into a **fresh** home
before trusting a backup:

```bash
sudo -u dafeiyu env PYTHONPATH=/home/dafeiyu/yugang/src \
  /usr/bin/python3 -m yugang --home /home/dafeiyu/.yugang-restore-drill \
  restore --from /home/dafeiyu/.yugang/backups/<stamp>.db --dry-run
```

Then run it without `--dry-run` and check the reported `events_after_note`, `chain_ok`, and
`self_version_after`. Backups contain her memories; keep the directory private.

To consolidate historical awareness cards that say almost the same thing:

```bash
sudo -u dafeiyu env PYTHONPATH=/home/dafeiyu/yugang/src \
  /usr/bin/python3 -m yugang --home /home/dafeiyu/.yugang awareness-dedupe --threshold 0.9
```

It never merges a hypothesis with a flipped negation, and prints the merged card ids as JSON.

## DSH operator console

The console is a separate DSH process for **operations**, not a second persona and not her
inference runtime. It has its own `DSH_HOME=/home/dafeiyu/.dsh` and its own web session.

```bash
sudo install -m 644 deploy/dsh/dafeiyu-dsh.service /etc/systemd/system/dafeiyu-dsh.service
sudo systemctl daemon-reload
sudo systemctl enable --now dafeiyu-dsh.service
# make the HTTP token available to the console scripts (0600, owner dafeiyu)
sudo deploy/dsh/install-token.sh
```

The operator console's tool shell does not always inherit the service environment, so the
scripts read `~/.yugang/http-token` when `$YUGANG_HTTP_TOKEN` is absent. The API key stays in
`/etc/dafeiyu-dsh.env` (root-only); this file contains only the local gateway token.
It binds `127.0.0.1:3199`. The launch token is random per process and is written by the
wrapper to `/home/dafeiyu/.dsh/web-url` (mode 0600) once the server is listening:

```bash
sudo cat /home/dafeiyu/.dsh/web-url   # http://127.0.0.1:3199/?token=...
ssh -N -L 3199:127.0.0.1:3199 user@host
```

Then open that URL on the laptop. It is an operator console: read SELF.md, inspect the tank,
and send messages with `scripts/say.sh`. It shares the DeepSeek key with the gateway, but its
own model usage is **not** counted against her 5 CNY daily budget. Do not run her memory or
gateway inside it.