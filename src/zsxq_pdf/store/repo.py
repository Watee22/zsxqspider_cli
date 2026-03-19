from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@dataclass(frozen=True)
class TopicUpsert:
    topic_id: str
    group_id: str
    create_time: str | None
    talk_text: str | None
    raw_json: dict


@dataclass(frozen=True)
class AttachmentUpsert:
    attachment_id: str
    group_id: str
    topic_id: str
    filename: str | None
    size_bytes: int | None
    download_count: int | None
    create_time: str | None


@dataclass(frozen=True)
class TagUpsert:
    group_id: str
    tag_id: str
    name: str
    url: str | None


def upsert_group(conn: sqlite3.Connection, group_id: str, *, cursor: str | None) -> None:
    conn.execute(
        """
        INSERT INTO groups(group_id, cursor_or_watermark, last_synced_at)
        VALUES(?, ?, ?)
        ON CONFLICT(group_id) DO UPDATE SET
          cursor_or_watermark=excluded.cursor_or_watermark,
          last_synced_at=excluded.last_synced_at
        """,
        (group_id, cursor, int(time.time())),
    )


def get_group_cursor(conn: sqlite3.Connection, group_id: str) -> str | None:
    row = conn.execute(
        "SELECT cursor_or_watermark FROM groups WHERE group_id=?", (group_id,)
    ).fetchone()
    return (row[0] if row else None)


def upsert_topic(conn: sqlite3.Connection, t: TopicUpsert) -> None:
    conn.execute(
        """
        INSERT INTO topics(topic_id, group_id, create_time, talk_text, raw_json)
        VALUES(?, ?, ?, ?, ?)
        ON CONFLICT(topic_id) DO UPDATE SET
          group_id=excluded.group_id,
          create_time=excluded.create_time,
          talk_text=excluded.talk_text,
          raw_json=excluded.raw_json
        """,
        (t.topic_id, t.group_id, t.create_time, t.talk_text, json.dumps(t.raw_json, ensure_ascii=False)),
    )


def upsert_attachment(conn: sqlite3.Connection, a: AttachmentUpsert) -> None:
    conn.execute(
        """
        INSERT INTO attachments(
          attachment_id, group_id, topic_id, filename, size_bytes, download_count, create_time, status
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, 'new')
        ON CONFLICT(attachment_id) DO UPDATE SET
          group_id=excluded.group_id,
          topic_id=excluded.topic_id,
          filename=excluded.filename,
          size_bytes=excluded.size_bytes,
          download_count=excluded.download_count,
          create_time=excluded.create_time
        """,
        (
            a.attachment_id,
            a.group_id,
            a.topic_id,
            a.filename,
            a.size_bytes,
            a.download_count,
            a.create_time,
        ),
    )


def iter_attachments_by_status(
    conn: sqlite3.Connection,
    *,
    group_id: str,
    status: str,
    limit: int | None = None,
    topic_id: str | None = None,
):
    sql = """
    SELECT attachment_id, topic_id, filename, size_bytes, download_count, create_time, download_url, local_path, sha256
    FROM attachments
    WHERE group_id=? AND status=?
    """
    params: list[object] = [group_id, status]
    if topic_id is not None:
        sql += " AND topic_id=?"
        params.append(topic_id)
    sql += " ORDER BY create_time DESC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    yield from conn.execute(sql, params)


def set_attachment_downloaded(
    conn: sqlite3.Connection,
    *,
    attachment_id: str,
    download_url: str,
    local_path: str,
    sha256: str,
):
    conn.execute(
        """
        UPDATE attachments
        SET status='downloaded', download_url=?, local_path=?, sha256=?, error_message=NULL
        WHERE attachment_id=?
        """,
        (download_url, local_path, sha256, attachment_id),
    )


def set_attachment_failed(conn: sqlite3.Connection, *, attachment_id: str, error: str):
    conn.execute(
        """
        UPDATE attachments
        SET status='failed', error_message=?
        WHERE attachment_id=?
        """,
        (error, attachment_id),
    )


def set_attachment_converted(conn: sqlite3.Connection, *, attachment_id: str):
    conn.execute(
        """
        UPDATE attachments
        SET status='converted', error_message=NULL
        WHERE attachment_id=?
        """,
        (attachment_id,),
    )


def set_attachment_convert_failed(conn: sqlite3.Connection, *, attachment_id: str, error: str):
    conn.execute(
        """
        UPDATE attachments
        SET status='convert_failed', error_message=?
        WHERE attachment_id=?
        """,
        (error, attachment_id),
    )


def upsert_tags(conn: sqlite3.Connection, tags: list[TagUpsert]) -> None:
    conn.executemany(
        """
        INSERT INTO tags(group_id, tag_id, name, url)
        VALUES(?, ?, ?, ?)
        ON CONFLICT(group_id, tag_id) DO UPDATE SET
          name=excluded.name,
          url=excluded.url
        """,
        [(t.group_id, t.tag_id, t.name, t.url) for t in tags],
    )


def replace_topic_tags(conn: sqlite3.Connection, *, group_id: str, topic_id: str, tag_ids: list[str]) -> None:
    conn.execute(
        "DELETE FROM topic_tags WHERE group_id=? AND topic_id=?",
        (group_id, topic_id),
    )
    if not tag_ids:
        return
    conn.executemany(
        "INSERT OR IGNORE INTO topic_tags(group_id, topic_id, tag_id) VALUES(?, ?, ?)",
        [(group_id, topic_id, tid) for tid in tag_ids],
    )


def iter_attachments_for_download(
    conn: sqlite3.Connection,
    *,
    group_id: str,
    status: str,
    limit: int | None = None,
    topic_id: str | None = None,
    day: str | None = None,
    tag_names: list[str] | None = None,
    include_unclassified: bool = False,
):
    """Iterate PDF attachments optionally filtered by tag name.

    If tag_names is provided, only attachments whose topic is mapped to those tags are returned.
    If include_unclassified is True, also include attachments with no topic_tags mapping.
    If day is provided, only attachments whose create_time falls on YYYY-MM-DD are returned.
    Non-PDF attachments are excluded here so the yanbao pipeline only downloads/converts PDFs.
    """

    base_select = """
    SELECT DISTINCT a.attachment_id, a.topic_id, a.filename, a.size_bytes, a.download_count, a.create_time,
           a.download_url, a.local_path, a.sha256
    FROM attachments a
    """

    where = [
        "a.group_id=?",
        "a.status=?",
        "a.filename IS NOT NULL",
        "LOWER(TRIM(a.filename)) LIKE '%.pdf'",
    ]
    params: list[object] = [group_id, status]

    if topic_id is not None:
        where.append("a.topic_id=?")
        params.append(topic_id)

    if day is not None:
        where.append("substr(a.create_time, 1, 10)=?")
        params.append(day)

    join = ""
    tag_filter_sql = ""

    if tag_names:
        join = """
        LEFT JOIN topic_tags tt
          ON tt.group_id=a.group_id AND tt.topic_id=a.topic_id
        LEFT JOIN tags t
          ON t.group_id=tt.group_id AND t.tag_id=tt.tag_id
        """

        placeholders = ",".join(["?"] * len(tag_names))
        if include_unclassified:
            tag_filter_sql = f"(t.name IN ({placeholders}) OR t.tag_id IS NULL)"
        else:
            tag_filter_sql = f"t.name IN ({placeholders})"
        params.extend(tag_names)
        where.append(tag_filter_sql)

    sql = base_select + join + " WHERE " + " AND ".join(where) + " ORDER BY a.create_time DESC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)

    yield from conn.execute(sql, params)


def get_attachment_topic_and_time(
    conn: sqlite3.Connection,
    *,
    group_id: str,
    attachment_id: str,
) -> tuple[str | None, str | None]:
    """Return (topic_id, create_time_prefer_attachment_then_topic)."""

    row = conn.execute(
        """
        SELECT a.topic_id, a.create_time AS a_ct, p.create_time AS t_ct
        FROM attachments a
        LEFT JOIN topics p ON p.topic_id=a.topic_id
        WHERE a.group_id=? AND a.attachment_id=?
        """,
        (group_id, attachment_id),
    ).fetchone()
    if not row:
        return None, None
    topic_id = row[0]
    ct = row[1] or row[2]
    return topic_id, ct


def get_topic_tag_ids(conn: sqlite3.Connection, *, group_id: str, topic_id: str) -> list[str]:
    rows = conn.execute(
        "SELECT tag_id FROM topic_tags WHERE group_id=? AND topic_id=?",
        (group_id, topic_id),
    ).fetchall()
    return [r[0] for r in rows]


def list_tag_stats(conn: sqlite3.Connection, *, group_id: str) -> list[sqlite3.Row]:
    """Return rows: (tag_id, name, topics_count, attachments_count)."""

    return conn.execute(
        """
        SELECT t.tag_id, t.name,
               COUNT(DISTINCT tt.topic_id) AS topics_count,
               COUNT(DISTINCT a.attachment_id) AS attachments_count
        FROM tags t
        LEFT JOIN topic_tags tt
          ON tt.group_id=t.group_id AND tt.tag_id=t.tag_id
        LEFT JOIN attachments a
          ON a.group_id=tt.group_id AND a.topic_id=tt.topic_id
        WHERE t.group_id=?
        GROUP BY t.tag_id, t.name
        ORDER BY t.name
        """,
        (group_id,),
    ).fetchall()
