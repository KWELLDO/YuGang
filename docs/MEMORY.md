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

## L3 — current stance

A thin, recomputed snapshot. It is not hand-edited and not a persona prompt. Each snapshot
records the L1/L2 refs it was based on.

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
