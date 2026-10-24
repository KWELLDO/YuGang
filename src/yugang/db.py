from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 1

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

CREATE TABLE IF NOT EXISTS recall_log (
  id TEXT PRIMARY KEY,
  ts TEXT NOT NULL,
  query TEXT NOT NULL,
  event_ids TEXT NOT NULL,
  context TEXT
);

CREATE TABLE IF NOT EXISTS redactions (
  id TEXT PRIMARY KEY,
  ts TEXT NOT NULL,
  target_event_id TEXT NOT NULL,
  reason TEXT NOT NULL,
  actor TEXT NOT NULL,
  replacement TEXT
);

CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'events is append-only'); END;

CREATE TRIGGER IF NOT EXISTS judgments_no_update BEFORE UPDATE ON judgments
BEGIN SELECT RAISE(ABORT, 'judgments is append-only'); END;
CREATE TRIGGER IF NOT EXISTS judgments_no_delete BEFORE DELETE ON judgments
BEGIN SELECT RAISE(ABORT, 'judgments is append-only'); END;

CREATE TRIGGER IF NOT EXISTS self_versions_no_update BEFORE UPDATE ON self_versions
BEGIN SELECT RAISE(ABORT, 'self_versions is append-only'); END;
CREATE TRIGGER IF NOT EXISTS self_versions_no_delete BEFORE DELETE ON self_versions
BEGIN SELECT RAISE(ABORT, 'self_versions is append-only'); END;

CREATE TRIGGER IF NOT EXISTS awareness_cards_no_update BEFORE UPDATE ON awareness_cards
BEGIN SELECT RAISE(ABORT, 'awareness_cards is append-only'); END;
CREATE TRIGGER IF NOT EXISTS awareness_cards_no_delete BEFORE DELETE ON awareness_cards
BEGIN SELECT RAISE(ABORT, 'awareness_cards is append-only'); END;

CREATE TRIGGER IF NOT EXISTS budget_ledger_no_update BEFORE UPDATE ON budget_ledger
BEGIN SELECT RAISE(ABORT, 'budget_ledger is append-only'); END;
CREATE TRIGGER IF NOT EXISTS budget_ledger_no_delete BEFORE DELETE ON budget_ledger
BEGIN SELECT RAISE(ABORT, 'budget_ledger is append-only'); END;
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


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
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
