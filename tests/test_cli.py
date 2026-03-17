from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from zsxq_pdf.cli import app
from zsxq_pdf.store.db import ensure_schema
from zsxq_pdf.store.repo import (
    AttachmentUpsert,
    TopicUpsert,
    connect,
    replace_topic_tags,
    set_attachment_downloaded,
    upsert_attachment,
    upsert_topic,
)
from zsxq_pdf.util.tags import TagDef, save_tags

runner = CliRunner()

GROUP_ID = "20000000000001"
CREATE_TIME = "2026-03-16T17:27:55.843+0800"


def _parse_json(stdout: str) -> dict:
    return json.loads(stdout.strip())


def _seed_group(data_dir: Path, *, attachment_status: str = "new") -> Path:
    db_path = data_dir / "db" / "app.sqlite3"
    ensure_schema(db_path)
    save_tags(
        data_dir,
        [
            TagDef(
                name="示例标签A",
                tag_id="10000000000001",
                url="https://wx.zsxq.com/tags/%E7%A4%BA%E4%BE%8B%E6%A0%87%E7%AD%BEA/10000000000001",
            )
        ],
    )

    with connect(db_path) as conn:
        upsert_topic(
            conn,
            TopicUpsert(
                topic_id="topic-1",
                group_id=GROUP_ID,
                create_time=CREATE_TIME,
                talk_text="#示例标签A#",
                raw_json={"topic_id": "topic-1"},
            ),
        )
        upsert_attachment(
            conn,
            AttachmentUpsert(
                attachment_id="file-1",
                group_id=GROUP_ID,
                topic_id="topic-1",
                filename="report.pdf",
                size_bytes=123,
                download_count=0,
                create_time=CREATE_TIME,
            ),
        )
        replace_topic_tags(
            conn,
            group_id=GROUP_ID,
            topic_id="topic-1",
            tag_ids=["10000000000001"],
        )
        if attachment_status == "downloaded":
            pdf_path = data_dir / "示例标签A" / "20260316" / "report.pdf"
            pdf_path.parent.mkdir(parents=True, exist_ok=True)
            pdf_path.write_bytes(b"%PDF-1.4\n")
            set_attachment_downloaded(
                conn,
                attachment_id="file-1",
                download_url="https://example.com/report.pdf",
                local_path=str(pdf_path),
                sha256="abc123",
            )
        conn.commit()

    return db_path


def test_init_json_output(tmp_path: Path):
    data_dir = tmp_path / "data"

    result = runner.invoke(app, ["--json", "init", "--data-dir", str(data_dir)])

    assert result.exit_code == 0
    payload = _parse_json(result.stdout)
    assert payload["ok"] is True
    assert payload["command"] == "init"
    assert payload["summary"]["tag_count"] >= 1
    assert payload["summary"]["created_tags_file"] is True
    assert (data_dir / "db" / "app.sqlite3").exists()
    assert (data_dir / "tags.json").exists()


def test_status_json_output_reports_attachment_states(tmp_path: Path):
    data_dir = tmp_path / "data"
    _seed_group(data_dir, attachment_status="downloaded")

    with connect(data_dir / "db" / "app.sqlite3") as conn:
        upsert_attachment(
            conn,
            AttachmentUpsert(
                attachment_id="file-2",
                group_id=GROUP_ID,
                topic_id="topic-1",
                filename="pending.pdf",
                size_bytes=456,
                download_count=0,
                create_time=CREATE_TIME,
            ),
        )
        conn.commit()

    result = runner.invoke(app, ["--json", "status", "--group", GROUP_ID, "--data-dir", str(data_dir)])

    assert result.exit_code == 0
    payload = _parse_json(result.stdout)
    assert payload["summary"]["topics"] == 1
    assert payload["summary"]["attachments"] == 2
    assert payload["summary"]["statuses"] == {"downloaded": 1, "new": 1}


def test_download_dry_run_json_lists_planned_items_without_cookies(tmp_path: Path):
    data_dir = tmp_path / "data"
    db_path = _seed_group(data_dir)

    result = runner.invoke(
        app,
        [
            "--json",
            "download",
            "--group",
            GROUP_ID,
            "--data-dir",
            str(data_dir),
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    payload = _parse_json(result.stdout)
    assert payload["ok"] is True
    assert payload["summary"]["dry_run"] is True
    assert payload["summary"]["planned"] == 1
    assert payload["summary"]["downloaded"] == 0
    assert payload["summary"]["items"][0]["tag_name"] == "示例标签A"
    assert payload["summary"]["items"][0]["path"].endswith("20260316\\report.pdf")

    with connect(db_path) as conn:
        row = conn.execute("SELECT status FROM attachments WHERE attachment_id=?", ("file-1",)).fetchone()
    assert row[0] == "new"


def test_doctor_json_reports_cookie_source(tmp_path: Path):
    data_dir = tmp_path / "data"
    ensure_schema(data_dir / "db" / "app.sqlite3")
    save_tags(data_dir, [])

    cookies_path = tmp_path / "cookies.txt"
    cookies_path.write_text(
        "# Netscape HTTP Cookie File\n.zsxq.com\tTRUE\t/\tFALSE\t0\tfoo\tbar\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "--json",
            "doctor",
            "--data-dir",
            str(data_dir),
            "--cookies",
            str(cookies_path),
        ],
    )

    assert result.exit_code == 0
    payload = _parse_json(result.stdout)
    assert payload["ok"] is True
    assert payload["summary"]["cookie_source"] == "netscape"
    assert payload["summary"]["cookie_count"] == 1


def test_tag_add_is_idempotent_in_json_mode(tmp_path: Path):
    data_dir = tmp_path / "data"

    first = runner.invoke(
        app,
        ["--json", "tag-add", "--name", "新标签", "--hid", "12345678901234", "--data-dir", str(data_dir)],
    )
    second = runner.invoke(
        app,
        ["--json", "tag-add", "--name", "新标签", "--hid", "12345678901234", "--data-dir", str(data_dir)],
    )

    assert first.exit_code == 0
    assert second.exit_code == 0
    assert _parse_json(first.stdout)["summary"]["created"] is True
    assert _parse_json(second.stdout)["summary"]["created"] is False
    assert _parse_json(second.stdout)["summary"]["existing"] is True
