# Memory model

## L1 — material (append-only)

Every record has `source` (human / ai-observation / ai-interpretation / system / external),
`kind` (message / observation / action / outcome / diary / wake / sleep / ...),
`actor`, `channel`, `refs`, `meta`, and `ts`.

L1 is never updated or deleted by the application. A hash chain (`prev_hash` → `hash`)
makes external tampering detectable. Privacy redaction, when added, will be a separate
audited record — not a silent delete.

## L2 — judgment history

A judgment is a claim plus a falsifier plus evidence refs. It is immutable. Reviews
(supported / refuted / needs-evidence) are appended separately; current status is derived
from the latest review. A new judgment can supersede an old one.

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

If the sleep output is truncated or not valid JSON, the gateway first records the raw
output as a `system/error` (not as a diary), retries once with a shorter schema, and
only then writes an honest failure diary. It never silently turns a broken JSON blob
into her first-person memory.

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
