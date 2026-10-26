from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 7  # v2 judgment lineage, v3 practices, v4 practice checks, v5 citations, v6 episodes, v7 world ledger

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY,
  ts TEXT NOT NULL,
  source TEXT NOT NULL,
  kind TEXT NOT NULL,
  content TEXT NOT NULL,
  actor TEXT,
  channel TEXT,
  refs TEXT NOT NULL DEFAULT '[]',
  meta TEXT NOT NULL DEFAULT '{}',
  prev_hash TEXT NOT NULL,
  hash TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_kind ON events(kind);
CREATE INDEX IF NOT EXISTS idx_events_source ON events(source);

CREATE TABLE IF NOT EXISTS judgments (
  id TEXT PRIMARY KEY,
  ts TEXT NOT NULL,
  claim TEXT NOT NULL,
  falsifier TEXT NOT NULL,
  evidence_refs TEXT NOT NULL,
  confidence REAL NOT NULL,
  trigger TEXT,
  supersedes TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_judgments_ts ON judgments(ts);

CREATE TABLE IF NOT EXISTS judgment_supersessions (
  new_id TEXT NOT NULL,
  old_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (new_id, old_id),
  FOREIGN KEY (new_id) REFERENCES judgments(id),
  FOREIGN KEY (old_id) REFERENCES judgments(id)
);
CREATE INDEX IF NOT EXISTS idx_js_old ON judgment_supersessions(old_id);

CREATE TABLE IF NOT EXISTS judgment_reviews (
  id TEXT PRIMARY KEY,
  judgment_id TEXT NOT NULL,
  ts TEXT NOT NULL,
  verdict TEXT NOT NULL,
  evidence_refs TEXT NOT NULL,
  note TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY (judgment_id) REFERENCES judgments(id)
);
CREATE INDEX IF NOT EXISTS idx_jr_judgment ON judgment_reviews(judgment_id, ts);

CREATE TABLE IF NOT EXISTS stances (
  id TEXT PRIMARY KEY,
  ts TEXT NOT NULL,
  content TEXT NOT NULL,
  based_on TEXT NOT NULL,
  trigger TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS self_versions (
  id TEXT PRIMARY KEY,
  ts TEXT NOT NULL,
  content TEXT NOT NULL,
  diff TEXT,
  reason TEXT NOT NULL,
  evidence_refs TEXT NOT NULL,
  accepted_by TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_self_ts ON self_versions(ts);

CREATE TABLE IF NOT EXISTS awareness_cards (
  id TEXT PRIMARY KEY,
  ts TEXT NOT NULL,
  trigger TEXT NOT NULL,
  observation TEXT NOT NULL,
  hypothesis TEXT NOT NULL,
  falsifier TEXT NOT NULL,
  evidence_refs TEXT NOT NULL,
  confidence REAL NOT NULL,
  proposed_self_diff TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cards_ts ON awareness_cards(ts);

CREATE TABLE IF NOT EXISTS awareness_reviews (
  id TEXT PRIMARY KEY,
  card_id TEXT NOT NULL,
  ts TEXT NOT NULL,
  status TEXT NOT NULL,
  note TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY (card_id) REFERENCES awareness_cards(id)
);
CREATE INDEX IF NOT EXISTS idx_ar_card ON awareness_reviews(card_id, ts);

CREATE TABLE IF NOT EXISTS alarms (
  id TEXT PRIMARY KEY,
  ts TEXT NOT NULL,
  due_at TEXT NOT NULL,
  reason TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  source_event TEXT,
  fired_event TEXT,
  note TEXT,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alarms_due ON alarms(status, due_at);

CREATE TABLE IF NOT EXISTS open_loops (
  id TEXT PRIMARY KEY,
  ts TEXT NOT NULL,
  title TEXT NOT NULL,
  detail TEXT,
  status TEXT NOT NULL DEFAULT 'open',
  due TEXT,
  refs TEXT NOT NULL DEFAULT '[]',
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_loops_status ON open_loops(status);

CREATE TABLE IF NOT EXISTS practices (
  id TEXT PRIMARY KEY,
  ts TEXT NOT NULL,
  text TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  evidence_refs TEXT NOT NULL,
  source_event TEXT,
  note TEXT,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_practices_status ON practices(status, ts);

CREATE TABLE IF NOT EXISTS practice_reviews (
  id TEXT PRIMARY KEY,
  practice_id TEXT NOT NULL,
  ts TEXT NOT NULL,
  status TEXT NOT NULL,
  evidence_refs TEXT NOT NULL DEFAULT '[]',
  note TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY (practice_id) REFERENCES practices(id)
);
CREATE INDEX IF NOT EXISTS idx_pr_practice ON practice_reviews(practice_id, ts);

CREATE TABLE IF NOT EXISTS messages (
  id TEXT PRIMARY KEY,
  channel TEXT NOT NULL,
  external_id TEXT,
  direction TEXT NOT NULL,
  sender TEXT,
  content TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  processed_at TEXT,
  error TEXT,
  meta TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_messages_status ON messages(status, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_external
  ON messages(channel, external_id) WHERE external_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS budget_ledger (
  id TEXT PRIMARY KEY,
  ts TEXT NOT NULL,
  day TEXT NOT NULL,
  kind TEXT NOT NULL,
  model TEXT,
  input_tokens INTEGER NOT NULL DEFAULT 0,
  output_tokens INTEGER NOT NULL DEFAULT 0,
  cost_cny REAL NOT NULL,
  note TEXT
);
CREATE INDEX IF NOT EXISTS idx_budget_day ON budget_ledger(day, kind);

CREATE TABLE IF NOT EXISTS cache_ledger (
  id TEXT PRIMARY KEY,
  ts TEXT NOT NULL,
  day TEXT NOT NULL,
  kind TEXT NOT NULL,
  model TEXT,
  input_tokens INTEGER NOT NULL DEFAULT 0,
  cache_hit_tokens INTEGER NOT NULL DEFAULT 0,
  cache_miss_tokens INTEGER NOT NULL DEFAULT 0,
  cost_cny REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_cache_day ON cache_ledger(day, kind);

CREATE TABLE IF NOT EXISTS recall_log (
  id TEXT PRIMARY KEY,
  ts TEXT NOT NULL,
  query TEXT NOT NULL,
  event_ids TEXT NOT NULL,
  context TEXT
);

CREATE TABLE IF NOT EXISTS recall_stats (
  event_id TEXT PRIMARY KEY,
  recall_count INTEGER NOT NULL DEFAULT 0,
  last_recalled_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS event_confirmations (
  event_id TEXT PRIMARY KEY,
  confirmations INTEGER NOT NULL DEFAULT 0,
  last_confirmed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS citation_confirmations (
  id TEXT PRIMARY KEY,
  event_id TEXT NOT NULL,
  source_event TEXT NOT NULL,
  ts TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(event_id, source_event)
);
CREATE INDEX IF NOT EXISTS idx_citation_event ON citation_confirmations(event_id);
CREATE INDEX IF NOT EXISTS idx_citation_source ON citation_confirmations(source_event);

CREATE TABLE IF NOT EXISTS episodes (
  id TEXT PRIMARY KEY,
  ts TEXT NOT NULL,
  start_ts TEXT NOT NULL,
  end_ts TEXT NOT NULL,
  title TEXT NOT NULL,
  summary TEXT NOT NULL,
  event_ids TEXT NOT NULL,
  origin TEXT NOT NULL DEFAULT 'model',
  source_event TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_episodes_ts ON episodes(ts DESC);

CREATE TABLE IF NOT EXISTS redactions (
  id TEXT PRIMARY KEY,
  ts TEXT NOT NULL,
  target_event_id TEXT NOT NULL,
  reason TEXT NOT NULL,
  actor TEXT NOT NULL,
  replacement TEXT
);

-- Every look at the world and every act in it. The read side is here instead of L1
-- so a day of browsing does not bury yesterday's events; the act side is also an
-- L1 event, because an experiment she ran is part of her history.
CREATE TABLE IF NOT EXISTS world_ledger (
  id TEXT PRIMARY KEY,
  ts TEXT NOT NULL,
  day TEXT NOT NULL,
  tool TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'read',
  ok INTEGER NOT NULL DEFAULT 1,
  detail TEXT NOT NULL DEFAULT '',
  chars INTEGER NOT NULL DEFAULT 0,
  ms INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_world_day ON world_ledger(day, tool);

-- People she knows, and the handles they can reach her from. Not L1: L1 is what
-- happened, this is who was there. last_seen moves; the events never do.
CREATE TABLE IF NOT EXISTS people (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  relation TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT '',
  first_seen_ts TEXT NOT NULL,
  last_seen_ts TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_people_name ON people(name);
CREATE TABLE IF NOT EXISTS person_handles (
  channel TEXT NOT NULL,
  handle TEXT NOT NULL,
  person_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (channel, handle)
);
CREATE INDEX IF NOT EXISTS idx_person_handles_person ON person_handles(person_id);

CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'events is append-only'); END;

CREATE TRIGGER IF NOT EXISTS judgments_no_update BEFORE UPDATE ON judgments
BEGIN SELECT RAISE(ABORT, 'judgments is append-only'); END;
CREATE TRIGGER IF NOT EXISTS judgments_no_delete BEFORE DELETE ON judgments
BEGIN SELECT RAISE(ABORT, 'judgments is append-only'); END;

CREATE TRIGGER IF NOT EXISTS judgment_supersessions_no_update BEFORE UPDATE ON judgment_supersessions
BEGIN SELECT RAISE(ABORT, 'judgment_supersessions is append-only'); END;
CREATE TRIGGER IF NOT EXISTS judgment_supersessions_no_delete BEFORE DELETE ON judgment_supersessions
BEGIN SELECT RAISE(ABORT, 'judgment_supersessions is append-only'); END;

CREATE TRIGGER IF NOT EXISTS self_versions_no_update BEFORE UPDATE ON self_versions
BEGIN SELECT RAISE(ABORT, 'self_versions is append-only'); END;
CREATE TRIGGER IF NOT EXISTS self_versions_no_delete BEFORE DELETE ON self_versions
BEGIN SELECT RAISE(ABORT, 'self_versions is append-only'); END;

CREATE TRIGGER IF NOT EXISTS awareness_cards_no_update BEFORE UPDATE ON awareness_cards
BEGIN SELECT RAISE(ABORT, 'awareness_cards is append-only'); END;
CREATE TRIGGER IF NOT EXISTS awareness_cards_no_delete BEFORE DELETE ON awareness_cards
BEGIN SELECT RAISE(ABORT, 'awareness_cards is append-only'); END;

CREATE TRIGGER IF NOT EXISTS practice_reviews_no_update BEFORE UPDATE ON practice_reviews
BEGIN SELECT RAISE(ABORT, 'practice_reviews is append-only'); END;
CREATE TRIGGER IF NOT EXISTS practice_reviews_no_delete BEFORE DELETE ON practice_reviews
BEGIN SELECT RAISE(ABORT, 'practice_reviews is append-only'); END;

CREATE TRIGGER IF NOT EXISTS budget_ledger_no_update BEFORE UPDATE ON budget_ledger
BEGIN SELECT RAISE(ABORT, 'budget_ledger is append-only'); END;
CREATE TRIGGER IF NOT EXISTS budget_ledger_no_delete BEFORE DELETE ON budget_ledger
BEGIN SELECT RAISE(ABORT, 'budget_ledger is append-only'); END;

CREATE TRIGGER IF NOT EXISTS citation_confirmations_no_update BEFORE UPDATE ON citation_confirmations
BEGIN SELECT RAISE(ABORT, 'citation_confirmations is append-only'); END;
CREATE TRIGGER IF NOT EXISTS citation_confirmations_no_delete BEFORE DELETE ON citation_confirmations
BEGIN SELECT RAISE(ABORT, 'citation_confirmations is append-only'); END;

CREATE TRIGGER IF NOT EXISTS world_ledger_no_delete BEFORE DELETE ON world_ledger
BEGIN SELECT RAISE(ABORT, 'world_ledger is append-only'); END;

CREATE TRIGGER IF NOT EXISTS episodes_no_update BEFORE UPDATE ON episodes
BEGIN SELECT RAISE(ABORT, 'episodes is append-only'); END;
CREATE TRIGGER IF NOT EXISTS episodes_no_delete BEFORE DELETE ON episodes
BEGIN SELECT RAISE(ABORT, 'episodes is append-only'); END;
"""


def connect(path: str | Path) -> sqlite3.Connection:
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _create_fts(conn: sqlite3.Connection) -> str | None:
    for tokenizer in ("trigram", "unicode61"):
        try:
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5("
                "content, content='events', content_rowid='rowid', "
                f"tokenize='{tokenizer}')"
            )
            conn.execute(
                "CREATE TRIGGER IF NOT EXISTS events_fts_ai AFTER INSERT ON events BEGIN "
                "INSERT INTO events_fts(rowid, content) VALUES (new.rowid, new.content); END"
            )
            return tokenizer
        except sqlite3.OperationalError:
            continue
    return None


def _migrate(conn: sqlite3.Connection) -> None:
    """Additive migrations for databases created before the current schema."""
    review_cols = {row[1] for row in conn.execute("PRAGMA table_info(practice_reviews)")}
    if "evidence_refs" not in review_cols:
        conn.execute(
            "ALTER TABLE practice_reviews ADD COLUMN evidence_refs TEXT NOT NULL DEFAULT '[]'"
        )


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    _migrate(conn)
    tokenizer = _create_fts(conn)
    conn.execute(
        "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(SCHEMA_VERSION),),
    )
    conn.execute(
        "INSERT INTO meta(key, value) VALUES('fts_tokenizer', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (tokenizer or "none",),
    )
