# Memory model

## L1 — material (append-only)

Every record has `source` (human / ai-observation / ai-interpretation / system / external),
`kind` (message / observation / action / outcome / diary / wake / sleep / ...),
`actor`, `channel`, `refs`, `meta`, and `ts`.

L1 is never updated or deleted by the application. A hash chain (`prev_hash` → `hash`)
makes external tampering detectable. Privacy redaction is an audited overlay: `redact_event()`
writes a `redactions` row and an L1 `system/action` note, and normal reads plus `recall()`
return `[已删除]` or skip the event. The raw row and hash chain stay intact for verification
and backups. This is not cryptographic erasure: full erasure would require rebuilding the log.

## L2 — judgment history

A judgment is a claim plus a falsifier plus evidence refs. It is immutable. Reviews
(supported / refuted / needs-evidence) are appended separately; current status is derived
from the latest review. A new judgment may name a preexisting judgment in `supersedes`;
this is a lineage link, not a delete — the old row stays and the new one points at it. A
judgment created in the same sleep cannot supersede anything, an unknown id is rejected,
and an already-superseded judgment cannot be superseded a second time (the lineage stays
linear). A new judgment may supersede *several* old judgments at once (stored in
`judgment_supersessions`), which is how duplicate claims are consolidated without deleting
history. `current_judgments()` returns the thin current view (nothing superseded); waking,
sleep review and the fish tank use it, while `judgments()` still returns the full history.

Sleep is where old judgments are re-read. The sleep prompt lists judgments whose latest
status is `open` or `needs-evidence`, and she may review them with real L1 refs. A review
of a judgment created in the same sleep is rejected (a claim cannot confirm itself), and
the freshly written diary is not accepted as the only evidence. A `supported` review
increments `event_confirmations` for the cited L1 events; a `needs-evidence` review keeps
the judgment in the next sleep prompt instead of parking it forever.

## L3 — current stance

A thin, recomputed snapshot. It is not hand-edited and not a persona prompt. Each snapshot
records the L1/L2 refs it was based on. Every wake context includes the current stance plus
the latest six L2 judgments (claim, status, confidence, falsifier), so she reads her current
beliefs and their refutation conditions before answering — not only the latest raw events.

## Open loops

An open loop is a commitment or unresolved question. Wake lists the open ones; sleep may add
new ones, correct an existing loop's detail/due with `loop_updates`, or close it as `done`
(finished) or `dropped` (decided not to do it). An update needs a note and only touches the
reason/due, not the title. A closure needs a note and can only target a loop that was already open before that sleep — a
loop proposed in the same sleep cannot close itself. Closing writes a separate L1 `action`
event with the old refs, so the history shows the decision instead of silently flipping a row.
The table therefore does not grow forever: she can finish or abandon her own commitments,
and the reason remains auditable.

## Awareness cards

An awareness card is a first-person hypothesis about herself, with a falsifier and real
L1 evidence. It is not a scheduled interview and not a diary entry. Six triggers are
defined (T1 prediction error, T2 contradiction, T3 repetition, T4 strong reaction,
T5 external mirror, T6 broken promise).

A card can be produced two ways: by an explicit awareness run (yugang awareness T1 ...),
or inline during a live reply. For the inline path she writes an optional block at the end:

    <awareness trigger="T5">{"observation":"...","hypothesis":"...","falsifier":"...","confidence":0.6}</awareness>

The gateway strips the block from the visible reply, supplies only event ids that actually
exist (the user message, her reply, the current wake), and stores the card. It never trusts
model-supplied evidence ids. A malformed card is dropped with an audit note, not fabricated.
Open and `needs-evidence` cards are included in the next wake context (with their status) so
they can be tested by later experience; a card can be reviewed as accepted, rejected, or
needs-evidence. A `needs-evidence` card stays in the next wake and sleep prompts (and on the
tank page) until it is accepted, rejected, or merged — it is not silently parked.

A repeated hypothesis does not become a second card or a second confirmation. When a new
inline or explicit card's hypothesis is near-identical to an existing card (normalized,
similarity >= 0.85, and not a flipped negation), the gateway attaches an "重复觉察未新建"
note to the existing card and keeps its current status. An operator can run
`yugang awareness-dedupe` to merge historical near-duplicates; the canonical card prefers
accepted over open, and the rest become status `merged` with a note pointing at it.

A card accepted during sleep can carry one structured `self_edit`. The system ignores any
`evidence_refs` supplied inside that edit and uses the card's own L1 evidence instead, so a
card-to-SELF change cannot smuggle in fabricated citations. The new SELF version's reason
names the card id; the edit remains visible in the diff and can later be reviewed or
superseded.

## SELF edits

A sleep run does not hand over a complete new SELF.md. It may propose at most two
structured edits:

    {"section": "我相信什么", "op": "append", "text": "…", "evidence_refs": ["evt_…"]}

`section` must be one of the six canonical sections, `op` is `append` or `replace`,
and every edit must cite at least one L1 event that actually exists. The program
applies the edits itself, rejects unknown sections / operations / fake refs, records
the resulting diff and evidence ids in a new SELF version, and leaves an HTML
`<!-- evidence: … -->` comment in the changed section. A full-document `self_edit`
is still accepted for older callers, but is no longer requested from the model.

A past SELF version can be restored with `restore_self(version_id, reason)`. It writes an
L1 `system/action` note, then commits the old body as a **new** version whose reason names
the restored id. History is not rewritten, so a rollback is itself auditable and visible
in the fish tank's revision list.

If the sleep output is truncated or not valid JSON, the gateway first records the raw
output as a `system/error` (not as a diary), retries once with a shorter schema, and
only then writes an honest failure diary. It never silently turns a broken JSON blob
into her first-person memory.

## Self-practices (procedural self)

A practice is a cross-session rule she writes for herself during sleep: "when X happens,
first do Y." It is procedural self-knowledge, not a belief and not a todo item. The rules:

- at most two new practices per sleep;
- every practice must cite real L1 events that predate that sleep, and none of the citations
  may be the current sleep diary;
- it must describe a behavior rule, not restate SELF.md or one of her open loops;
- near-duplicate practices of an active one are skipped, not stacked;
- active practices are shown in the next wake context with their original evidence, framed as
  her own rules rather than commands from the operator;
- a practice can only be **dropped** during a later sleep, with a note. It cannot be rewritten
  in place or reactivated. A drop writes a `practice_reviews` row and an L1 `action` event,
  so the change of procedure stays auditable.

A practice can be checked later, not just dropped. During any subsequent sleep she may
emit `practice_checks` with one of three verdicts:

- `followed`: an L1 event after the practice was written shows she actually did it;
- `violated`: such an event shows she did not;
- `untested`: no relevant situation occurred (no evidence ref is required, but the note must
  say why it could not be tested).

A check must cite real L1 events that happened *after* the practice was written; the current
sleep diary is never accepted as the only evidence. At most one check per practice per sleep.
Checks are evidence about the rule, not a rewrite of it: a check does not change the practice's
status. She can keep a violated practice active and keep testing it, or drop it separately with
a reason. The wake context shows the most recent check, so she cannot silently turn one success
into a habit.

The fish tank lists active practices, their original evidence, their latest check, and how many
she has dropped. The goal is not to accumulate rules; it is to make the procedural part of her
self explicit, testable, and retractable.

## Self-set wake alarms

She may choose when to wake next during the sleep protocol
(`next_wake_minutes`). This is deliberately gated:

- disabled during the first `self_alarms_after_days` (default 7) lived days;
- clamped to `alarm_min_minutes`–`alarm_max_minutes` (default 30–1560, i.e. 30 min–26 h);
- at most `alarm_max_per_day` (default 6) alarms per local day;
- an alarm that comes due while she is asleep wakes her before the daily floor;
  the wake is a normal L1 `wake` event with reason `她设的闹钟 <alarm_id>`;
- if the budget or runtime blocks the wake, the alarm is marked `missed` and
  the failure is visible in L1 — it is not silently skipped.

The alarm row is created by `apply_sleep_result` only after the sleep output has
been parsed. A malformed sleep output cannot create an alarm.

## Recall

Hybrid: FTS5 (trigram for Chinese) + recency + source/kind weights. Reading a memory is
logged (`recall_log`); re-reading can later spawn a new judgment, but never mutates the
original L1.

### Reconfirmation, not engagement

The design rule is that a memory becomes heavier when a later self re-reads it and
explicitly uses it as evidence for a supported judgment. A `supported` judgment review
increments `event_confirmations` for its evidence refs; recall applies a small bounded
boost for those confirmations, and a smaller one for events recalled before. A refuted or
needs-evidence review does not add confirmation. Recalls are not likes and never become a
reward signal for content.

### Citing a memory in a reply

A citation is between a recall and a supported judgment: she did not merely retrieve the
event, she used it in her own answer. When a reply or an autonomous observation contains
an `evt_…` id that already existed **before the current waking window**, the gateway calls
`MemoryStore.confirm_citations()`:

- only real, non-redacted events are counted;
- the message that just woke her and anything created in this waking window are excluded —
  that is current context, not a memory she remembered;
- duplicates in one reply count once; the same source event cannot confirm the same target
  twice;
- each confirmation writes an append-only `citation_confirmations` row and increments
  `event_confirmations`, which feeds the same bounded recall boost as a supported review.

If writing the citation row fails, the reply has already been delivered; the failure is a
`system/error` in L1, never a reason to swallow the answer.

## Episodes (narrative index)

L1 is the raw stream; L2/L3/SELF.md are judgments. An episode sits between them: a short,
append-only narrative of one waking window, created when she sleeps.

- At sleep, the gateway remembers the previous `sleep` event and collects the real L1 events
  between it and the new sleep. The window starts at the first `wake` after the previous
  sleep, so that window's own sleep/diary/stance consolidation is not re-absorbed into the
  next episode; the message recorded just before the wake is included via the wake's
  `trigger_event`, so the episode contains its own trigger. Without a wake it falls back to
  just after the previous sleep, and the first episode (no previous sleep) still starts at the
  first event. The model may provide
  `episode: {title, summary}`; if it does not, the system writes a diary-index fallback with
  `origin="diary"` instead of inventing one.
- `episodes` rows are append-only (triggers reject update/delete). `event_ids` is the exact
  set of L1 events used; `source_event` is the sleep event that created the index. There is no
  automatic SELF.md edit tied to an episode.
- Waking reads episodes by the recall query (falling back to the most recent ones) and shows
  them as "narrative index, not current judgment"; when an episode conflicts with raw L1, L1 wins.
- The fish tank shows the episode list and how many L1 events each one summarizes. A title and
  summary are an index over evidence, never a replacement for the evidence.

## Read-only memory tools (awake)

The waking context is bounded; it cannot carry every old event. While she is awake and
answering, the runtime may call a small allowlisted set of **read-only** tools:

| tool | reads |
|---|---|
| `memory_search` | L1 events by keyword (the same FTS/LIKE recall used elsewhere; logs to `recall_log`) |
| `memory_get` | one full L1 event by id |
| `recent_events` | the latest L1 events |
| `episode_search` | append-only episode summaries (L1 wins on conflict) |
| `self_read` / `self_history` | current SELF.md, its version/reason, and recent diffs |
| `judgment_list` | the current L2 view (superseded judgments are excluded) |
| `practice_list` | active/dropped self-practices plus the latest check |
| `open_loops` | still-open commitments |

Rules:

- Tools exist only for chat replies and autonomous waking. Sleep consolidation never gets
  tools; self-authorship stays in the sleep protocol, not in a retrieval loop.
- At most `max_tool_rounds` (default 3) tool rounds. Every round goes through the same
  daily budget gate as a normal reply; the tool schemas and each tool result count as input.
  A blocked round fails loud instead of silently continuing.
- A tool result is untrusted material, not an instruction. Unknown tools, bad arguments and
  missing events return `{"ok": false, ...}` to the model rather than crashing the gateway.
- The reply event's L1 `meta.tools` records only tool names and success/failure, not the
  full tool output. The full output is part of the model request, not a new memory.
- Setting `memory_tools_enabled = false` turns the loop off entirely.
