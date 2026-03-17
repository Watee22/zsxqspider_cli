from __future__ import annotations

import sqlite3
from pathlib import Path


SCHEMA_SQL = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS groups (
  group_id TEXT PRIMARY KEY,
  name TEXT,
  cursor_or_watermark TEXT,
  last_synced_at INTEGER
);

CREATE TABLE IF NOT EXISTS topics (
  topic_id TEXT PRIMARY KEY,
  group_id TEXT NOT NULL,
  create_time TEXT,
  talk_text TEXT,
  raw_json TEXT
);

CREATE TABLE IF NOT EXISTS attachments (
  attachment_id TEXT PRIMARY KEY,
  group_id TEXT NOT NULL,
  topic_id TEXT NOT NULL,
  filename TEXT,
  size_bytes INTEGER,
  download_count INTEGER,
  create_time TEXT,
  download_url TEXT,
  sha256 TEXT,
  local_path TEXT,
  status TEXT NOT NULL DEFAULT 'new',
  error_message TEXT
);

-- Built-in tag registry + topic mapping (hashtag HID-based)
CREATE TABLE IF NOT EXISTS tags (
  group_id TEXT NOT NULL,
  tag_id TEXT NOT NULL,
  name TEXT NOT NULL,
  url TEXT,
  PRIMARY KEY(group_id, tag_id)
);

CREATE TABLE IF NOT EXISTS topic_tags (
  group_id TEXT NOT NULL,
  topic_id TEXT NOT NULL,
  tag_id TEXT NOT NULL,
  PRIMARY KEY(group_id, topic_id, tag_id)
);

CREATE INDEX IF NOT EXISTS attachments_status_ix ON attachments(status);
CREATE INDEX IF NOT EXISTS attachments_topic_ix ON attachments(topic_id);
CREATE INDEX IF NOT EXISTS attachments_group_ix ON attachments(group_id);

CREATE INDEX IF NOT EXISTS topic_tags_group_tag_ix ON topic_tags(group_id, tag_id);
CREATE INDEX IF NOT EXISTS topic_tags_group_topic_ix ON topic_tags(group_id, topic_id);
"""


def ensure_schema(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(SCHEMA_SQL)
        conn.commit()
