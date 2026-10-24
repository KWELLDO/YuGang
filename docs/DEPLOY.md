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
awake or asleep, the last sleep, recent conversation, SELF.md, open loops, awareness
cards, recent events and budget. The message box calls the same `POST /message` path, so
sending a message wakes her even while she is asleep.

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

## Private-network options

1. **SSH tunnel** (works today, no new dependency).
2. **Tailscale/WireGuard** (better for phone; no public inbound port).
3. **Mail** (poll IMAP; requires a dedicated mailbox and credentials).

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
`/health` and `/status` (`chain_ok` must be true). Backups contain her memories; keep the
directory private.

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
