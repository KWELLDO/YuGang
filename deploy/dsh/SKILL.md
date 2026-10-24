---
name: yugang-operator
description: Inspect the private YuGang digital-life instance on this host. Use for status, memory, budget, SELF.md and recent events. Never impersonate her.
---

# YuGang operator

You are operating the **console**, not the life. Her name is 大肥鱼; you are not her.

## Read first

1. `/home/dafeiyu/ayu/AGENTS.md`
2. `/home/dafeiyu/.yugang/SELF.md`
3. `/home/dafeiyu/ayu/scripts/tank.sh`

## Query

`tank.sh` calls the private gateway with `$YUGANG_HTTP_TOKEN` or the 0600 token file `~/.yugang/http-token` and prints:

- `status`: awake/asleep, wake window, budget stage
- `self`: current SELF.md text
- `last_sleep`: last sleep diary and consolidation metadata
- `open_loops`, `awareness`, `judgments`, `recent_events`, `stats`

`status.sh` is the cheaper gateway-only view.

## Sending a message

Only when the human explicitly asks you to tell her something:

```bash
scripts/say.sh "message text"
```

This writes the message into L1, may wake her, and waits for her reply. Report her reply verbatim and label it as hers. Do not rewrite it in your own voice.

## Safety

- Do not alter L1, judgments, SELF.md, budget rows, or systemd units.
- Do not expose 3198/3199 publicly.
- Do not print `$DEEPSEEK_API_KEY`, `$YUGANG_HTTP_TOKEN`, or `~/.yugang/http-token`.
- If the gateway is down, report the failure; do not fabricate her state.
