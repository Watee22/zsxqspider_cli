from __future__ import annotations

import json
from dataclasses import dataclass
from http.cookiejar import MozillaCookieJar
from pathlib import Path
from typing import Iterable

import httpx


@dataclass(frozen=True)
class CookieLoadResult:
    cookies: httpx.Cookies
    source: str  # "netscape" | "json"


def _load_netscape_cookiejar(path: Path) -> httpx.Cookies:
    jar = MozillaCookieJar(str(path))
    jar.load(ignore_discard=True, ignore_expires=True)
    cookies = httpx.Cookies()
    for c in jar:
        cookies.set(c.name, c.value, domain=c.domain, path=c.path)
    return cookies


def _load_json_cookies(path: Path) -> httpx.Cookies:
    # Support common extension exports: list of dicts
    # e.g. [{"domain": ".zsxq.com", "name": "xxx", "value": "yyy", "path": "/"}, ...]
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        # Some exporters nest cookies under a key.
        for key in ("cookies", "Items", "items"):
            if key in data and isinstance(data[key], list):
                data = data[key]
                break

    if not isinstance(data, list):
        raise ValueError("Unsupported cookies JSON format (expected list)")

    cookies = httpx.Cookies()
    for item in data:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        value = item.get("value")
        if not name or value is None:
            continue
        domain = item.get("domain") or item.get("host") or ""
        pathv = item.get("path") or "/"
        cookies.set(name, value, domain=domain or None, path=pathv)

    return cookies


def load_cookies(path: Path) -> CookieLoadResult:
    text = path.read_text(encoding="utf-8", errors="ignore").lstrip()
    if text.startswith("[") or text.startswith("{"):
        return CookieLoadResult(cookies=_load_json_cookies(path), source="json")

    return CookieLoadResult(cookies=_load_netscape_cookiejar(path), source="netscape")


def redact_headers(headers: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    redacted: list[tuple[str, str]] = []
    for k, v in headers:
        if k.lower() in {"cookie", "authorization"}:
            redacted.append((k, "REDACTED"))
        else:
            redacted.append((k, v))
    return redacted
