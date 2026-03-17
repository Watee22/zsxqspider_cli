from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from typer import Context, Option, Typer

from zsxq_pdf.config import AppConfig
from zsxq_pdf.store.db import ensure_schema
from zsxq_pdf.zsxq.client import ZsxqClient

app = Typer(
    add_completion=False,
    help="Sync ZSXQ PDFs and convert to Markdown. Supports machine-readable terminal output for local agents.",
)
console = Console()
stderr_console = Console(stderr=True)


@dataclass(frozen=True)
class OutputSettings:
    json_output: bool = False
    jsonl_output: bool = False
    quiet: bool = False


class SyncMode(str, Enum):
    tag = "tag"
    full = "full"


@app.callback()
def main(
    ctx: Context,
    json_output: bool = Option(False, "--json", help="Emit a single JSON result for the command."),
    jsonl_output: bool = Option(False, "--jsonl", help="Emit newline-delimited JSON events for machine readers."),
    no_color: bool = Option(False, "--no-color", help="Disable Rich color output."),
    quiet: bool = Option(False, "--quiet", help="Suppress non-error human-readable progress logs."),
):
    """Configure global output controls used by all commands."""
    global console, stderr_console

    if json_output and jsonl_output:
        raise typer.BadParameter("Use either --json or --jsonl, not both.")

    console = Console(no_color=no_color)
    stderr_console = Console(no_color=no_color, stderr=True)
    ctx.obj = OutputSettings(
        json_output=json_output,
        jsonl_output=jsonl_output,
        quiet=quiet,
    )


def _settings(ctx: Context) -> OutputSettings:
    root = ctx.find_root()
    if isinstance(root.obj, OutputSettings):
        return root.obj
    return OutputSettings()


def _json_dump(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _emit_event(ctx: Context, command: str, event: str, *, error: bool = False, **payload: Any) -> None:
    if not _settings(ctx).jsonl_output:
        return
    typer.echo(_json_dump({"command": command, "event": event, **payload}), err=error)


def _print(ctx: Context, message: str, *, error: bool = False) -> None:
    settings = _settings(ctx)
    if settings.json_output or settings.jsonl_output:
        return
    if settings.quiet and not error:
        return
    target_console = console if not error else stderr_console
    target_console.print(message)


def _finish(
    ctx: Context,
    command: str,
    *,
    ok: bool = True,
    exit_code: int = 0,
    summary: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
    errors: list[str] | None = None,
    **payload: Any,
) -> None:
    settings = _settings(ctx)
    result: dict[str, Any] = {"ok": ok, "command": command}
    if summary is not None:
        result["summary"] = summary
    if warnings:
        result["warnings"] = warnings
    if errors:
        result["errors"] = errors
    result.update(payload)

    if settings.jsonl_output:
        typer.echo(_json_dump({"event": "finish", **result}))
    elif settings.json_output:
        typer.echo(_json_dump(result))

    if exit_code:
        raise typer.Exit(code=exit_code)


@app.command()
def init(
    ctx: Context,
    data_dir: Path = Option(Path("data"), help="Data directory (db/downloads/markdown)."),
):
    """Initialize local data directory, SQLite schema, and default tags.json."""
    from zsxq_pdf.util.tags import BUILTIN_TAGS, load_tags, save_tags

    cfg = AppConfig(data_dir=data_dir)
    ensure_schema(cfg.db_path)

    tags_path = cfg.data_dir / "tags.json"
    created_tags = False
    if not tags_path.exists():
        save_tags(cfg.data_dir, BUILTIN_TAGS)
        created_tags = True
        _print(ctx, f"Created default {tags_path}")
    else:
        _print(ctx, f"tags.json already exists ({len(load_tags(cfg.data_dir))} tags)")

    tag_count = len(load_tags(cfg.data_dir))
    _print(ctx, f"Initialized DB at {cfg.db_path}")
    _finish(
        ctx,
        "init",
        summary={
            "data_dir": str(cfg.data_dir),
            "db_path": str(cfg.db_path),
            "tags_path": str(tags_path),
            "tag_count": tag_count,
            "created_tags_file": created_tags,
        },
    )


@app.command("auth-check")
def auth_check(
    ctx: Context,
    group: str = Option(..., help="Group ID (e.g. 20000000000001)."),
    cookies: Path = Option(..., exists=True, dir_okay=False, help="Browser cookies file (Netscape .txt or JSON)."),
    base_url: str = Option("https://api.zsxq.com", help="ZSXQ API base URL."),
):
    """Validate cookies by calling a lightweight API endpoint."""
    with ZsxqClient(base_url=base_url, cookies_file=cookies) as client:
        ok, detail = client.auth_check(group_id=group)
    if ok:
        _print(ctx, "[green]Auth OK[/green]")
    else:
        _print(ctx, f"[red]Auth FAILED[/red]: {detail}", error=True)
    _finish(
        ctx,
        "auth-check",
        ok=ok,
        exit_code=(0 if ok else 3),
        summary={
            "group_id": group,
            "detail": detail,
        },
    )


@app.command()
def sync(
    ctx: Context,
    group: str = Option(..., help="Group ID to sync."),
    cookies: Path = Option(..., exists=True, dir_okay=False, help="Browser cookies file (Netscape .txt or JSON)."),
    base_url: str = Option("https://api.zsxq.com", help="ZSXQ API base URL."),
    data_dir: Path = Option(Path("data"), help="Data directory."),
    count: int = Option(30, help="Page size."),
    max_pages: int = Option(0, help="Max pages (0 = no limit)."),
    tag: list[str] = Option([], "--tag", "-t", help="Only sync these tag names (repeatable). Default: all tags in tags.json."),
    mode: SyncMode = Option(SyncMode.tag, help="Sync mode: 'tag' = per-tag hashtag API, 'full' = group files API + local hashtag parsing."),
):
    """Sync topics & attachments.

    --mode tag (default): per-tag via /v2/hashtags/{hid}/topics.
    --mode full: all files via /v2/groups/{group}/files, then parse hashtags locally.
    """
    cfg = AppConfig(data_dir=data_dir)
    ensure_schema(cfg.db_path)

    from zsxq_pdf.store.repo import (
        TagUpsert,
        connect,
        upsert_tags,
    )
    from zsxq_pdf.util.tags import load_tags

    all_tags = load_tags(cfg.data_dir)
    if not all_tags:
        message = "No tags configured. Run 'zsxq-pdf init' or 'zsxq-pdf tag-add'."
        _print(ctx, f"[red]{message}[/red]", error=True)
        _finish(ctx, "sync", ok=False, exit_code=1, errors=[message], summary={"group_id": group, "mode": mode.value})
        return

    tags_to_sync = all_tags
    if tag:
        tag_set = set(tag)
        tags_to_sync = [t for t in all_tags if t.name in tag_set]
        if not tags_to_sync:
            message = f"No matching tags found for: {tag}"
            _print(ctx, f"[red]{message}[/red]", error=True)
            _finish(ctx, "sync", ok=False, exit_code=1, errors=[message], summary={"group_id": group, "mode": mode.value})
            return

    summary: dict[str, Any] = {"mode": mode.value, "api_errors": []}
    _emit_event(
        ctx,
        "sync",
        "start",
        group_id=group,
        mode=mode.value,
        tag_names=[t.name for t in tags_to_sync],
    )

    try:
        with connect(cfg.db_path) as conn:
            upsert_tags(
                conn,
                [TagUpsert(group_id=group, tag_id=t.tag_id, name=t.name, url=t.url) for t in all_tags],
            )

            with ZsxqClient(base_url=base_url, cookies_file=cookies) as client:
                if mode == SyncMode.tag:
                    summary = _sync_by_tag(ctx, conn, client, group, tags_to_sync, count, max_pages)
                else:
                    summary = _sync_full(ctx, conn, client, group, all_tags, count, max_pages)
    except Exception as exc:
        message = str(exc)
        _print(ctx, f"[red]Sync failed[/red]: {message}", error=True)
        _finish(
            ctx,
            "sync",
            ok=False,
            exit_code=4,
            errors=[message],
            summary={"group_id": group, "mode": mode.value},
        )
        return

    summary["group_id"] = group
    summary["selected_tags"] = [t.name for t in tags_to_sync]
    ok = not summary["api_errors"]
    _finish(
        ctx,
        "sync",
        ok=ok,
        exit_code=(0 if ok else 5),
        summary=summary,
        errors=[e["message"] for e in summary["api_errors"]],
    )


def _sync_by_tag(ctx: Context, conn, client, group, tags_to_sync, count, max_pages):
    """Per-tag sync via /v2/hashtags/{hid}/topics."""
    from zsxq_pdf.store.repo import (
        AttachmentUpsert,
        TopicUpsert,
        replace_topic_tags,
        upsert_attachment,
        upsert_topic,
    )

    summary = {
        "mode": "tag",
        "tags_requested": len(tags_to_sync),
        "tags_processed": 0,
        "pages": 0,
        "topics": 0,
        "files": 0,
        "api_errors": [],
    }

    for td in tags_to_sync:
        _print(ctx, f"Syncing tag: {td.name} (hid={td.tag_id})")
        _emit_event(ctx, "sync", "tag_start", tag_name=td.name, tag_id=td.tag_id)
        end_time: str | None = None
        tag_pages = 0
        tag_topics = 0
        tag_files = 0

        while True:
            data = client.list_hashtag_topics(hid=td.tag_id, count=count, end_time=end_time)
            if not data.get("succeeded"):
                message = f"code={data.get('code')} msg={data.get('message') or data.get('error')}"
                summary["api_errors"].append({"tag_name": td.name, "tag_id": td.tag_id, "message": message})
                _print(ctx, f"[red]  API error: {message}[/red]", error=True)
                _emit_event(ctx, "sync", "tag_error", error=True, tag_name=td.name, tag_id=td.tag_id, message=message)
                break

            topics = (data.get("resp_data") or {}).get("topics") or []
            if not topics:
                break

            tag_pages += 1
            summary["pages"] += 1

            for t in topics:
                topic_id = str(t.get("topic_id") or "")
                if not topic_id:
                    continue

                talk = t.get("talk") or {}
                talk_text = talk.get("text") if isinstance(talk, dict) else None
                topic_group = t.get("group") or {}
                topic_group_id = str(topic_group.get("group_id") or group)

                upsert_topic(
                    conn,
                    TopicUpsert(
                        topic_id=topic_id,
                        group_id=topic_group_id,
                        create_time=t.get("create_time"),
                        talk_text=talk_text,
                        raw_json=t,
                    ),
                )

                replace_topic_tags(
                    conn,
                    group_id=topic_group_id,
                    topic_id=topic_id,
                    tag_ids=[td.tag_id],
                )

                files = talk.get("files") or [] if isinstance(talk, dict) else []
                for f in files:
                    file_id = f.get("file_id")
                    if file_id is None:
                        continue
                    upsert_attachment(
                        conn,
                        AttachmentUpsert(
                            attachment_id=str(file_id),
                            group_id=topic_group_id,
                            topic_id=topic_id,
                            filename=f.get("name"),
                            size_bytes=f.get("size"),
                            download_count=f.get("download_count"),
                            create_time=f.get("create_time"),
                        ),
                    )
                    tag_files += 1

                tag_topics += 1

            conn.commit()
            _print(ctx, f"  page {tag_pages}: +{len(topics)} topics")
            _emit_event(ctx, "sync", "page", tag_name=td.name, tag_id=td.tag_id, page=tag_pages, topics=len(topics))

            end_time = topics[-1].get("create_time")

            if max_pages and tag_pages >= max_pages:
                break

        summary["tags_processed"] += 1
        summary["topics"] += tag_topics
        summary["files"] += tag_files
        _print(ctx, f"  Done tag={td.name}: topics={tag_topics} files={tag_files}")
        _emit_event(
            ctx,
            "sync",
            "tag_complete",
            tag_name=td.name,
            tag_id=td.tag_id,
            pages=tag_pages,
            topics=tag_topics,
            files=tag_files,
        )

    return summary


def _sync_full(ctx: Context, conn, client, group, all_tags, count, max_pages):
    """Full sync via /v2/groups/{group}/files + local hashtag parsing."""
    from zsxq_pdf.store.repo import (
        AttachmentUpsert,
        TopicUpsert,
        replace_topic_tags,
        upsert_attachment,
        upsert_topic,
    )
    from zsxq_pdf.util.tags import match_registry, parse_hashtags

    _print(ctx, f"Syncing all files (full mode) for group={group}")
    _emit_event(ctx, "sync", "full_start", group_id=group)
    index: str | None = None
    pages = 0
    total_topics = 0
    total_files = 0
    api_errors: list[dict[str, str]] = []

    while True:
        data = client.list_files(group_id=group, count=count, index=index)
        if not data.get("succeeded"):
            message = f"code={data.get('code')} msg={data.get('message') or data.get('error')}"
            api_errors.append({"message": message})
            _print(ctx, f"[red]  API error: {message}[/red]", error=True)
            _emit_event(ctx, "sync", "full_error", error=True, group_id=group, message=message)
            break

        resp = data.get("resp_data") or {}
        files_list = resp.get("files") or []
        if not files_list:
            break

        pages += 1

        for item in files_list:
            topic = item.get("topic") or {}
            topic_id = str(topic.get("topic_id") or "")
            if not topic_id:
                continue

            talk = topic.get("talk") or {}
            talk_text = talk.get("text") if isinstance(talk, dict) else None
            topic_group = topic.get("group") or {}
            topic_group_id = str(topic_group.get("group_id") or group)

            upsert_topic(
                conn,
                TopicUpsert(
                    topic_id=topic_id,
                    group_id=topic_group_id,
                    create_time=topic.get("create_time"),
                    talk_text=talk_text,
                    raw_json=topic,
                ),
            )

            # Parse hashtags from talk text and map to known tags
            parsed = parse_hashtags(talk_text)
            matched = match_registry(parsed, tags=all_tags)
            replace_topic_tags(
                conn,
                group_id=topic_group_id,
                topic_id=topic_id,
                tag_ids=[m.tag_id for m in matched],
            )

            file_info = item.get("file") or {}
            file_id = file_info.get("file_id")
            if file_id is not None:
                upsert_attachment(
                    conn,
                    AttachmentUpsert(
                        attachment_id=str(file_id),
                        group_id=topic_group_id,
                        topic_id=topic_id,
                        filename=file_info.get("name"),
                        size_bytes=file_info.get("size"),
                        download_count=file_info.get("download_count"),
                        create_time=file_info.get("create_time"),
                    ),
                )
                total_files += 1

            total_topics += 1

        conn.commit()
        _print(ctx, f"  page {pages}: +{len(files_list)} files")
        _emit_event(ctx, "sync", "page", page=pages, files=len(files_list))

        # Pagination via index (last file's create_time or index field)
        index = resp.get("index")
        if not index:
            break

        if max_pages and pages >= max_pages:
            break

    _print(ctx, f"  Done full sync: topics={total_topics} files={total_files}")
    _emit_event(ctx, "sync", "full_complete", pages=pages, topics=total_topics, files=total_files)
    return {
        "mode": "full",
        "pages": pages,
        "topics": total_topics,
        "files": total_files,
        "api_errors": api_errors,
    }


def _resolve_tag_name(conn, *, group: str, topic_id: str | None, all_tags) -> str:
    from zsxq_pdf.store.repo import get_topic_tag_ids

    if not topic_id or topic_id == "0":
        return "_unclassified"

    tag_ids = set(get_topic_tag_ids(conn, group_id=group, topic_id=topic_id))
    for td in all_tags:
        if td.tag_id in tag_ids:
            return td.name
    return "_unclassified"


@app.command()
def download(
    ctx: Context,
    group: str = Option(..., help="Group ID."),
    cookies: Path | None = Option(None, exists=True, dir_okay=False, help="Browser cookies file (Netscape .txt or JSON). Required unless --dry-run is used."),
    base_url: str = Option("https://api.zsxq.com", help="ZSXQ API base URL."),
    data_dir: Path = Option(Path("data"), help="Data directory."),
    limit: int = Option(0, help="Max files to download (0 = no limit)."),
    retry_failed: bool = Option(False, help="Also retry attachments with status=failed."),
    retries: int = Option(5, help="Max retry attempts for transient errors."),
    topic: str = Option("", help="Only download files for this topic_id (optional)."),
    tag: list[str] = Option([], "--tag", "-t", help="Only download attachments for these tag names (repeatable)."),
    include_unclassified: bool = Option(True, help="Include attachments with no matched tag (stored under _unclassified)."),
    only_unclassified: bool = Option(False, help="Only download attachments with no matched tag."),
    dry_run: bool = Option(False, help="Show planned downloads without fetching files or changing SQLite state."),
):
    """Download PDFs for attachments marked as new.

    Uses /v2/files/{file_id}/download_url to fetch a short-lived signed URL.

    Output layout:
      data/<tag>/<YYYYMMDD>/<filename>.pdf
    Where tag comes from tags.json registry match; otherwise _unclassified.
    """
    cfg = AppConfig(data_dir=data_dir)
    ensure_schema(cfg.db_path)

    if not dry_run and cookies is None:
        raise typer.BadParameter("--cookies is required unless --dry-run is used.")

    import httpx

    from zsxq_pdf.download.downloader import download_file, sha256_file
    from zsxq_pdf.store.repo import (
        connect,
        get_attachment_topic_and_time,
        iter_attachments_for_download,
        set_attachment_downloaded,
        set_attachment_failed,
    )
    from zsxq_pdf.util.sanitize import sanitize_filename
    from zsxq_pdf.util.tags import load_tags
    from zsxq_pdf.util.timefmt import yyyymmdd_from_zsxq_time

    from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

    all_tags = load_tags(cfg.data_dir)
    downloaded = 0
    failed = 0
    planned = 0
    planned_items: list[dict[str, Any]] = []
    failures: list[str] = []
    statuses = ["new"] + (["failed"] if retry_failed else [])

    _emit_event(
        ctx,
        "download",
        "start",
        group_id=group,
        dry_run=dry_run,
        statuses=statuses,
        tag_names=tag,
    )

    try:
        with connect(cfg.db_path) as conn:
            if dry_run:
                for st in statuses:
                    for row in iter_attachments_for_download(
                        conn,
                        group_id=group,
                        status=st,
                        limit=(limit if limit else None),
                        topic_id=(topic if topic else None),
                        tag_names=(tag if tag else None),
                        include_unclassified=include_unclassified,
                    ):
                        file_id = row["attachment_id"]
                        filename = row["filename"] or f"{file_id}.pdf"
                        topic_id2, ct = get_attachment_topic_and_time(conn, group_id=group, attachment_id=file_id)
                        ymd = yyyymmdd_from_zsxq_time(ct)
                        tag_name = _resolve_tag_name(conn, group=group, topic_id=topic_id2, all_tags=all_tags)

                        if only_unclassified and tag_name != "_unclassified":
                            continue
                        if (not include_unclassified) and tag_name == "_unclassified":
                            continue

                        dest_path = cfg.data_dir / tag_name / ymd / sanitize_filename(filename)
                        item = {
                            "attachment_id": file_id,
                            "filename": filename,
                            "status": st,
                            "tag_name": tag_name,
                            "path": str(dest_path),
                        }
                        planned += 1
                        planned_items.append(item)
                        _print(ctx, f"Would download {filename} -> {dest_path}")
                        _emit_event(ctx, "download", "item_planned", **item)
            else:
                assert cookies is not None
                with ZsxqClient(base_url=base_url, cookies_file=cookies) as client:

                    @retry(
                        retry=retry_if_exception_type((httpx.HTTPError, RuntimeError)),
                        wait=wait_exponential(multiplier=0.8, min=1, max=20),
                        stop=stop_after_attempt(retries),
                        reraise=True,
                    )
                    def _get_download_url_with_retry(file_id: str) -> str:
                        return client.get_file_download_url(file_id=file_id, group_id=group)

                    for st in statuses:
                        for row in iter_attachments_for_download(
                            conn,
                            group_id=group,
                            status=st,
                            limit=(limit if limit else None),
                            topic_id=(topic if topic else None),
                            tag_names=(tag if tag else None),
                            include_unclassified=include_unclassified,
                        ):
                            file_id = row["attachment_id"]
                            filename = row["filename"] or f"{file_id}.pdf"
                            topic_id2, ct = get_attachment_topic_and_time(conn, group_id=group, attachment_id=file_id)
                            ymd = yyyymmdd_from_zsxq_time(ct)
                            tag_name = _resolve_tag_name(conn, group=group, topic_id=topic_id2, all_tags=all_tags)

                            if only_unclassified and tag_name != "_unclassified":
                                continue
                            if (not include_unclassified) and tag_name == "_unclassified":
                                continue

                            dest_dir = cfg.data_dir / tag_name / ymd

                            try:
                                url = _get_download_url_with_retry(file_id)
                                path = download_file(client._client, url, dest_dir, filename)
                                digest = sha256_file(path)
                                set_attachment_downloaded(
                                    conn,
                                    attachment_id=file_id,
                                    download_url=url,
                                    local_path=str(path),
                                    sha256=digest,
                                )
                                conn.commit()
                                downloaded += 1
                                _print(ctx, f"Downloaded {filename} -> {path}")
                                _emit_event(
                                    ctx,
                                    "download",
                                    "item_downloaded",
                                    attachment_id=file_id,
                                    filename=filename,
                                    tag_name=tag_name,
                                    path=str(path),
                                )
                            except RuntimeError as exc:
                                msg = str(exc)
                                if "code=1030" in msg:
                                    msg = "code 1030: file may require mobile app"
                                set_attachment_failed(conn, attachment_id=file_id, error=msg)
                                conn.commit()
                                failed += 1
                                failures.append(f"{file_id}: {msg}")
                                _print(ctx, f"[red]Failed[/red] {filename}: {msg}", error=True)
                                _emit_event(
                                    ctx,
                                    "download",
                                    "item_failed",
                                    error=True,
                                    attachment_id=file_id,
                                    filename=filename,
                                    message=msg,
                                )
                            except Exception as exc:
                                msg = str(exc)
                                set_attachment_failed(conn, attachment_id=file_id, error=msg)
                                conn.commit()
                                failed += 1
                                failures.append(f"{file_id}: {msg}")
                                _print(ctx, f"[red]Failed[/red] {filename}: {msg}", error=True)
                                _emit_event(
                                    ctx,
                                    "download",
                                    "item_failed",
                                    error=True,
                                    attachment_id=file_id,
                                    filename=filename,
                                    message=msg,
                                )
    except Exception as exc:
        message = str(exc)
        _print(ctx, f"[red]Download failed[/red]: {message}", error=True)
        _finish(
            ctx,
            "download",
            ok=False,
            exit_code=4,
            errors=[message],
            summary={"group_id": group, "dry_run": dry_run},
        )
        return

    if dry_run:
        _print(ctx, f"Dry run complete. Planned={planned}")
    else:
        _print(ctx, f"Done. Downloaded={downloaded} failed={failed}")

    summary = {
        "group_id": group,
        "dry_run": dry_run,
        "planned": planned,
        "downloaded": downloaded,
        "failed": failed,
        "statuses": statuses,
    }
    if dry_run:
        summary["items"] = planned_items

    _finish(
        ctx,
        "download",
        ok=(failed == 0),
        exit_code=(0 if dry_run or failed == 0 else 5),
        summary=summary,
        errors=failures,
    )


@app.command()
def convert(
    ctx: Context,
    group: str = Option(..., help="Group ID."),
    data_dir: Path = Option(Path("data"), help="Data directory."),
    limit: int = Option(0, help="Max files to convert (0 = no limit)."),
    tag: list[str] = Option([], "--tag", "-t", help="Only convert attachments for these tag names (repeatable)."),
    include_unclassified: bool = Option(True, help="Include attachments with no matched tag (stored under _unclassified)."),
    only_unclassified: bool = Option(False, help="Only convert attachments with no matched tag."),
    dry_run: bool = Option(False, help="Show planned conversions without writing Markdown or changing SQLite state."),
):
    """Convert downloaded PDFs to Markdown.

    Output layout:
      data/<tag>/<YYYYMMDD>/<filename>.md

    If --only-unclassified is set, only items with no matched tag are processed.
    """
    cfg = AppConfig(data_dir=data_dir)
    ensure_schema(cfg.db_path)

    from zsxq_pdf.convert.pdf_to_md import pdf_to_markdown
    from zsxq_pdf.store.repo import (
        connect,
        get_attachment_topic_and_time,
        iter_attachments_for_download,
        set_attachment_converted,
        set_attachment_convert_failed,
    )
    from zsxq_pdf.util.tags import load_tags
    from zsxq_pdf.util.timefmt import yyyymmdd_from_zsxq_time

    all_tags = load_tags(cfg.data_dir)
    converted = 0
    failed = 0
    planned = 0
    planned_items: list[dict[str, Any]] = []
    failures: list[str] = []

    _emit_event(ctx, "convert", "start", group_id=group, dry_run=dry_run, tag_names=tag)

    try:
        with connect(cfg.db_path) as conn:
            for row in iter_attachments_for_download(
                conn,
                group_id=group,
                status="downloaded",
                limit=(limit if limit else None),
                tag_names=(tag if tag else None),
                include_unclassified=(include_unclassified or only_unclassified),
            ):
                file_id = row["attachment_id"]
                pdf_path = row["local_path"]
                if not pdf_path:
                    continue

                topic_id2, ct = get_attachment_topic_and_time(conn, group_id=group, attachment_id=file_id)
                ymd = yyyymmdd_from_zsxq_time(ct)
                tag_name = _resolve_tag_name(conn, group=group, topic_id=topic_id2, all_tags=all_tags)

                if only_unclassified and tag_name != "_unclassified":
                    continue
                if (not include_unclassified) and tag_name == "_unclassified":
                    continue

                out_dir = cfg.data_dir / tag_name / ymd
                out_path = out_dir / (Path(pdf_path).stem + ".md")

                if dry_run:
                    item = {
                        "attachment_id": file_id,
                        "pdf_path": str(pdf_path),
                        "tag_name": tag_name,
                        "path": str(out_path),
                    }
                    planned += 1
                    planned_items.append(item)
                    _print(ctx, f"Would convert {pdf_path} -> {out_path}")
                    _emit_event(ctx, "convert", "item_planned", **item)
                    continue

                out_dir.mkdir(parents=True, exist_ok=True)

                try:
                    md = pdf_to_markdown(Path(pdf_path), title=row["filename"])
                    out_path.write_text(md, encoding="utf-8")
                    set_attachment_converted(conn, attachment_id=file_id)
                    conn.commit()
                    converted += 1
                    _print(ctx, f"Converted {pdf_path} -> {out_path}")
                    _emit_event(
                        ctx,
                        "convert",
                        "item_converted",
                        attachment_id=file_id,
                        pdf_path=str(pdf_path),
                        path=str(out_path),
                    )
                except Exception as exc:
                    msg = str(exc)
                    set_attachment_convert_failed(conn, attachment_id=file_id, error=msg)
                    conn.commit()
                    failed += 1
                    failures.append(f"{file_id}: {msg}")
                    _print(ctx, f"[red]Convert failed[/red] {pdf_path}: {msg}", error=True)
                    _emit_event(
                        ctx,
                        "convert",
                        "item_failed",
                        error=True,
                        attachment_id=file_id,
                        pdf_path=str(pdf_path),
                        message=msg,
                    )
    except Exception as exc:
        message = str(exc)
        _print(ctx, f"[red]Convert failed[/red]: {message}", error=True)
        _finish(
            ctx,
            "convert",
            ok=False,
            exit_code=4,
            errors=[message],
            summary={"group_id": group, "dry_run": dry_run},
        )
        return

    if dry_run:
        _print(ctx, f"Dry run complete. Planned={planned}")
    else:
        _print(ctx, f"Done. Converted={converted} failed={failed}")

    summary = {
        "group_id": group,
        "dry_run": dry_run,
        "planned": planned,
        "converted": converted,
        "failed": failed,
    }
    if dry_run:
        summary["items"] = planned_items

    _finish(
        ctx,
        "convert",
        ok=(failed == 0),
        exit_code=(0 if dry_run or failed == 0 else 5),
        summary=summary,
        errors=failures,
    )


@app.command()
def status(
    ctx: Context,
    group: str = Option(..., help="Group ID."),
    data_dir: Path = Option(Path("data"), help="Data directory."),
):
    """Show machine-friendly local state summary for one group."""
    cfg = AppConfig(data_dir=data_dir)

    from zsxq_pdf.store.repo import connect
    from zsxq_pdf.util.tags import load_tags

    db_exists = cfg.db_path.exists()
    topics = 0
    attachments = 0
    statuses: dict[str, int] = {}

    if db_exists:
        with connect(cfg.db_path) as conn:
            topic_row = conn.execute("SELECT COUNT(*) FROM topics WHERE group_id=?", (group,)).fetchone()
            attachment_row = conn.execute("SELECT COUNT(*) FROM attachments WHERE group_id=?", (group,)).fetchone()
            status_rows = conn.execute(
                "SELECT status, COUNT(*) FROM attachments WHERE group_id=? GROUP BY status ORDER BY status",
                (group,),
            ).fetchall()
        topics = int(topic_row[0]) if topic_row else 0
        attachments = int(attachment_row[0]) if attachment_row else 0
        statuses = {str(row[0]): int(row[1]) for row in status_rows}

    summary = {
        "group_id": group,
        "data_dir": str(cfg.data_dir),
        "db_path": str(cfg.db_path),
        "db_exists": db_exists,
        "topics": topics,
        "attachments": attachments,
        "statuses": statuses,
        "configured_tags": len(load_tags(cfg.data_dir)),
    }

    _print(ctx, f"Group={group}")
    _print(ctx, f"DB exists={db_exists} path={cfg.db_path}")
    _print(ctx, f"Topics={topics} attachments={attachments}")
    for name, count in statuses.items():
        _print(ctx, f"  {name}: {count}")

    _finish(ctx, "status", summary=summary)


@app.command()
def doctor(
    ctx: Context,
    data_dir: Path = Option(Path("data"), help="Data directory."),
    cookies: Path | None = Option(None, exists=True, dir_okay=False, help="Optional cookies file to validate parsing."),
):
    """Run local readiness checks for terminal agents."""
    cfg = AppConfig(data_dir=data_dir)

    from zsxq_pdf.util.tags import load_tags
    from zsxq_pdf.zsxq.cookies import load_cookies

    checks: list[dict[str, Any]] = []
    warnings: list[str] = []
    errors: list[str] = []

    python_ok = sys.version_info >= (3, 11)
    checks.append({"name": "python", "ok": python_ok, "detail": sys.version.split()[0]})
    if not python_ok:
        errors.append("Python 3.11 or newer is required.")

    data_dir_exists = cfg.data_dir.exists()
    checks.append({"name": "data_dir", "ok": data_dir_exists, "path": str(cfg.data_dir)})
    if not data_dir_exists:
        warnings.append(f"Data directory does not exist yet: {cfg.data_dir}")

    db_exists = cfg.db_path.exists()
    checks.append({"name": "database", "ok": db_exists, "path": str(cfg.db_path)})
    if not db_exists:
        warnings.append(f"Database file does not exist yet: {cfg.db_path}")

    tags_path = cfg.data_dir / "tags.json"
    checks.append({"name": "tags_file", "ok": tags_path.exists(), "path": str(tags_path)})
    if not tags_path.exists():
        warnings.append(f"tags.json does not exist yet: {tags_path}")

    tag_count = 0
    try:
        tag_count = len(load_tags(cfg.data_dir))
        checks.append({"name": "tags_parse", "ok": True, "count": tag_count})
    except Exception as exc:
        errors.append(f"Failed to parse tags.json: {exc}")
        checks.append({"name": "tags_parse", "ok": False, "detail": str(exc)})

    cookie_source: str | None = None
    cookie_count = 0
    if cookies is not None:
        try:
            cookie_result = load_cookies(cookies)
            cookie_source = cookie_result.source
            cookie_count = len(list(cookie_result.cookies.jar))
            checks.append(
                {
                    "name": "cookies_parse",
                    "ok": True,
                    "path": str(cookies),
                    "source": cookie_source,
                    "count": cookie_count,
                }
            )
        except Exception as exc:
            errors.append(f"Failed to parse cookies file: {exc}")
            checks.append({"name": "cookies_parse", "ok": False, "path": str(cookies), "detail": str(exc)})

    for check in checks:
        mark = "OK" if check["ok"] else "WARN"
        detail = check.get("detail") or check.get("path") or ""
        _print(ctx, f"{mark} {check['name']}: {detail}".rstrip())
    for warning in warnings:
        _print(ctx, f"[yellow]{warning}[/yellow]")
    for error in errors:
        _print(ctx, f"[red]{error}[/red]", error=True)

    _finish(
        ctx,
        "doctor",
        ok=(len(errors) == 0),
        exit_code=(0 if not errors else 1),
        summary={
            "data_dir": str(cfg.data_dir),
            "db_path": str(cfg.db_path),
            "tag_count": tag_count,
            "cookie_source": cookie_source,
            "cookie_count": cookie_count,
            "checks": checks,
        },
        warnings=warnings,
        errors=errors,
    )


@app.command("backfill-tags")
def backfill_tags(
    ctx: Context,
    group: str = Option(..., help="Group ID."),
    data_dir: Path = Option(Path("data"), help="Data directory."),
):
    """Backfill topic_tags from existing topics using tags.json registry."""

    cfg = AppConfig(data_dir=data_dir)
    ensure_schema(cfg.db_path)

    from zsxq_pdf.store.repo import TagUpsert, connect, replace_topic_tags, upsert_tags
    from zsxq_pdf.util.tags import load_tags, match_registry, parse_hashtags

    all_tags = load_tags(cfg.data_dir)

    with connect(cfg.db_path) as conn:
        upsert_tags(
            conn,
            [TagUpsert(group_id=group, tag_id=t.tag_id, name=t.name, url=t.url) for t in all_tags],
        )

        rows = conn.execute(
            "SELECT topic_id, talk_text FROM topics WHERE group_id=?",
            (group,),
        ).fetchall()

        mapped = 0
        for topic_id, talk_text in rows:
            parsed = parse_hashtags(talk_text)
            matched = match_registry(parsed, tags=all_tags)
            replace_topic_tags(
                conn,
                group_id=group,
                topic_id=topic_id,
                tag_ids=[x.tag_id for x in matched],
            )
            if matched:
                mapped += 1

        conn.commit()

    _print(ctx, f"Backfilled topics={len(rows)} mapped={mapped}")
    _finish(
        ctx,
        "backfill-tags",
        summary={
            "group_id": group,
            "topics": len(rows),
            "mapped": mapped,
        },
    )


@app.command("tags")
def tags(
    ctx: Context,
    group: str = Option(..., help="Group ID."),
    data_dir: Path = Option(Path("data"), help="Data directory."),
):
    """Show tag stats (topics/attachments counts)."""

    cfg = AppConfig(data_dir=data_dir)
    ensure_schema(cfg.db_path)

    from zsxq_pdf.store.repo import connect, list_tag_stats

    with connect(cfg.db_path) as conn:
        rows = list_tag_stats(conn, group_id=group)

    items = []
    for r in rows:
        items.append({"tag_id": r[0], "name": r[1], "topics": r[2], "attachments": r[3]})
        _print(ctx, f"{r[1]}\t(tag_id={r[0]})\ttopics={r[2]}\tattachments={r[3]}")

    _finish(
        ctx,
        "tags",
        summary={
            "group_id": group,
            "count": len(items),
            "items": items,
        },
    )


# --- Tag management commands ---


@app.command("tag-list")
def tag_list(
    ctx: Context,
    data_dir: Path = Option(Path("data"), help="Data directory."),
):
    """List all tags in tags.json."""
    from zsxq_pdf.util.tags import load_tags

    cfg = AppConfig(data_dir=data_dir)
    all_tags = load_tags(cfg.data_dir)
    if not all_tags:
        message = "No tags configured. Run 'zsxq-pdf init' to create default tags.json."
        _print(ctx, message)
        _finish(ctx, "tag-list", summary={"count": 0, "items": []})
        return
    for t in all_tags:
        _print(ctx, f"  {t.name}\t(hid={t.tag_id})")
    _print(ctx, f"Total: {len(all_tags)} tags")
    _finish(
        ctx,
        "tag-list",
        summary={
            "count": len(all_tags),
            "items": [{"name": t.name, "tag_id": t.tag_id, "url": t.url} for t in all_tags],
        },
    )


@app.command("tag-add")
def tag_add(
    ctx: Context,
    name: str = Option(..., help="Tag display name."),
    hid: str = Option(..., help="Tag hashtag ID (hid)."),
    data_dir: Path = Option(Path("data"), help="Data directory."),
):
    """Add a tag to tags.json."""
    from zsxq_pdf.util.tags import _make_url, load_tags, save_tags, TagDef

    cfg = AppConfig(data_dir=data_dir)
    all_tags = load_tags(cfg.data_dir)

    # Check for duplicates
    for t in all_tags:
        if t.tag_id == hid:
            _print(ctx, f"[yellow]Tag with hid={hid} already exists: {t.name}[/yellow]")
            _finish(
                ctx,
                "tag-add",
                summary={"created": False, "existing": True, "name": t.name, "tag_id": t.tag_id, "url": t.url},
            )
            return
        if t.name == name:
            _print(ctx, f"[yellow]Tag with name={name} already exists (hid={t.tag_id})[/yellow]")
            _finish(
                ctx,
                "tag-add",
                summary={"created": False, "existing": True, "name": t.name, "tag_id": t.tag_id, "url": t.url},
            )
            return

    all_tags.append(TagDef(name=name, tag_id=hid, url=_make_url(name, hid)))
    save_tags(cfg.data_dir, all_tags)
    _print(ctx, f"Added tag: {name} (hid={hid})")
    _finish(
        ctx,
        "tag-add",
        summary={"created": True, "existing": False, "name": name, "tag_id": hid, "url": _make_url(name, hid)},
    )


@app.command("tag-remove")
def tag_remove(
    ctx: Context,
    name: str = Option(..., help="Tag name to remove."),
    data_dir: Path = Option(Path("data"), help="Data directory."),
):
    """Remove a tag from tags.json by name."""
    from zsxq_pdf.util.tags import load_tags, save_tags

    cfg = AppConfig(data_dir=data_dir)
    all_tags = load_tags(cfg.data_dir)
    before = len(all_tags)
    all_tags = [t for t in all_tags if t.name != name]

    if len(all_tags) == before:
        _print(ctx, f"[yellow]Tag '{name}' not found in tags.json[/yellow]")
        _finish(ctx, "tag-remove", summary={"removed": False, "name": name})
        return

    save_tags(cfg.data_dir, all_tags)
    _print(ctx, f"Removed tag: {name}")
    _finish(ctx, "tag-remove", summary={"removed": True, "name": name})
