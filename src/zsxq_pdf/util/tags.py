from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import quote, unquote


@dataclass(frozen=True)
class TagDef:
    name: str
    tag_id: str  # ZSXQ hashtag hid
    url: str


# Built-in tag registry (sanitized examples)
BUILTIN_TAGS: list[TagDef] = [
    TagDef(name="示例标签A", tag_id="10000000000001", url="https://wx.zsxq.com/tags/%E7%A4%BA%E4%BE%8B%E6%A0%87%E7%AD%BEA/10000000000001"),
    TagDef(name="示例标签B", tag_id="10000000000002", url="https://wx.zsxq.com/tags/%E7%A4%BA%E4%BE%8B%E6%A0%87%E7%AD%BEB/10000000000002"),
    TagDef(name="示例标签C", tag_id="10000000000003", url="https://wx.zsxq.com/tags/%E7%A4%BA%E4%BE%8B%E6%A0%87%E7%AD%BEC/10000000000003"),
]


TAGS_FILENAME = "tags.json"


def _make_url(name: str, tag_id: str) -> str:
    return f"https://wx.zsxq.com/tags/{quote(name)}/{tag_id}"


def load_tags(data_dir: Path) -> list[TagDef]:
    """Load tags from data_dir/tags.json. Falls back to BUILTIN_TAGS if file missing."""
    path = data_dir / TAGS_FILENAME
    if not path.exists():
        return list(BUILTIN_TAGS)
    raw = json.loads(path.read_text(encoding="utf-8"))
    out: list[TagDef] = []
    for item in raw:
        name = item["name"]
        tag_id = item["tag_id"]
        url = item.get("url") or _make_url(name, tag_id)
        out.append(TagDef(name=name, tag_id=tag_id, url=url))
    return out


def save_tags(data_dir: Path, tags: list[TagDef]) -> Path:
    """Write tags to data_dir/tags.json. Returns the file path."""
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / TAGS_FILENAME
    payload = [{"name": t.name, "tag_id": t.tag_id, "url": t.url} for t in tags]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


_E_HASHTAG = re.compile(r"<e\b[^>]*\btype=\"hashtag\"[^>]*/?>", re.IGNORECASE)
_E_ATTR = re.compile(r"(\w+)=\"([^\"]*)\"")
_PLAIN_HASHTAG = re.compile(r"#([^#\s]{1,60})#")


def normalize_tag_name(text: str) -> str:
    s = (text or "").strip()
    if s.startswith("#") and s.endswith("#") and len(s) >= 2:
        s = s[1:-1]
    return s.strip()


@dataclass(frozen=True)
class ParsedHashtag:
    hid: str | None
    name: str
    display: str | None = None


def parse_hashtags(text: str | None) -> list[ParsedHashtag]:
    """Parse hashtags from ZSXQ talk text.

    Supports:
    - Rich markup: <e type="hashtag" hid="..." title="%23...%23" />
    - Fallback plain text: #示例标签A#

    Returns parsed hashtags in appearance order (not yet filtered by registry).
    """

    if not text:
        return []

    out: list[ParsedHashtag] = []

    # 1) Rich markup
    for m in _E_HASHTAG.finditer(text):
        frag = m.group(0)
        attrs = {k: v for k, v in _E_ATTR.findall(frag)}
        hid = attrs.get("hid")
        title = attrs.get("title")
        display = None
        name = ""
        if title:
            decoded = html.unescape(unquote(title))
            display = decoded
            name = normalize_tag_name(decoded)
        if not name:
            # As a last resort, allow a direct title/name attribute if present
            for k in ("name", "text"):
                if attrs.get(k):
                    name = normalize_tag_name(attrs[k])
                    break
        if name:
            out.append(ParsedHashtag(hid=hid, name=name, display=display))

    if out:
        return out

    # 2) Plain text fallback
    for m in _PLAIN_HASHTAG.finditer(text):
        name = normalize_tag_name(m.group(0))
        if name:
            out.append(ParsedHashtag(hid=None, name=name, display=m.group(0)))

    return out


def registry_maps(tags: Iterable[TagDef] = BUILTIN_TAGS) -> tuple[dict[str, TagDef], dict[str, TagDef]]:
    by_id = {t.tag_id: t for t in tags}
    by_name = {normalize_tag_name(t.name): t for t in tags}
    return by_id, by_name


def match_registry(
    parsed: Iterable[ParsedHashtag],
    *,
    tags: list[TagDef] = BUILTIN_TAGS,
) -> list[TagDef]:
    """Match parsed hashtags to the built-in registry.

    Returns TagDefs in registry priority order, de-duplicated.
    """

    by_id, by_name = registry_maps(tags)

    matched_ids: set[str] = set()
    # First collect which tag_ids are hit.
    for p in parsed:
        if p.hid and p.hid in by_id:
            matched_ids.add(p.hid)
        else:
            key = normalize_tag_name(p.name)
            if key in by_name:
                matched_ids.add(by_name[key].tag_id)

    # Then order by registry priority.
    out: list[TagDef] = []
    for t in tags:
        if t.tag_id in matched_ids:
            out.append(t)
    return out
