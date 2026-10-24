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

## Private-network options

1. **SSH tunnel** (works today, no new dependency).
2. **Tailscale/WireGuard** (better for phone; no public inbound port).
3. **Mail** (works today; subject tag `[YuGang]`, allow-listed sender only).

Public `ayu.*` DNS/nginx exposure is intentionally not used in v1.

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

To restore, stop the gateway first, replace `/home/dafeiyu/.yugang/yugang.db` with the
snapshot (and `SELF.md` if needed), `chown dafeiyu:dafeiyu`, then start the gateway and check
`/health`, `/status`, and the hash chain with `yugang verify` (or the latest backup
manifest's `chain_ok`). Backups contain her memories; keep the directory private.

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