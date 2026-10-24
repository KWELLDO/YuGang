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
echo "排队时想到的一句话" | ./scripts/yugang-remote.sh
```

It reads `YUGANG_HTTP_TOKEN` from the host-side root-only env file and never exposes the port. Set `YUGANG_SSH_HOST` if the SSH alias is not `dsh-online`.

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
