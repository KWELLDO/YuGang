# Architecture

## Layers

- **Memory core** (`src/yugang/memory`): SQLite, append-only events with hash chain, FTS5 recall, L2 judgments + reviews, L3 stance snapshots.
- **Self model** (`src/yugang/selfmodel`): SELF.md parse/render/diff/commit; awareness cards, including an optional inline awareness block in a live reply that the gateway strips, validates, and records.
- **Protocols** (`src/yugang/protocol`): wake context/prompt; sleep consolidation prompt + parser + apply.
- **Gateway** (`src/yugang/gateway`): always-on body, wake window state machine, budget guard, HTTP/email/OneBot channels.
- **Runtime** (`src/yugang/runtime`): model adapter interface; mock for tests; OpenAI-compatible adapter for deployment.

## Channels

- `http`: authenticated request/response and the fish tank page; loopback only by default.
- `email`: IMAP poll + SMTP reply, subject-tag and sender allowlist; never marks a stranger's mail read.
- `onebot`: OneBot v11 private-message webhook for a local NapCat/QQ bridge; explicit user allowlist, secret required off loopback, no group messages, no public inbound port.

## Invariants

1. L1 events are append-only (SQLite triggers) and hash-chained.
2. L2 judgments require a falsifier and evidence refs; reviews are append-only.
3. SELF.md changes are versions with a diff, reason, and real L1 evidence refs; never silently overwrite. Filling an empty section, adding a supported claim, and revising a refuted claim are all legitimate growth — not only "overturning" the old self.
4. A wake never claims continuous experience; it records the time gap.
5. Budget is enforced before the model call; failures are visible.

## Wake window

```
asleep --message--> waking --reply--> awake
awake --message--> awake (same session, reset idle)
awake --idle--> sleeping --consolidate--> asleep
```

A user message always wins over sleep. The per-day floor is for her life, not for availability.