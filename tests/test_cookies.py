from pathlib import Path

import pytest

from zsxq_pdf.zsxq.cookies import load_cookies


def test_load_cookies_netscape(tmp_path: Path):
    p = tmp_path / "cookies.txt"
    p.write_text(
        "# Netscape HTTP Cookie File\n"
        ".zsxq.com\tTRUE\t/\tFALSE\t0\tfoo\tbar\n",
        encoding="utf-8",
    )
    r = load_cookies(p)
    assert r.source == "netscape"


def test_load_cookies_json(tmp_path: Path):
    p = tmp_path / "cookies.json"
    p.write_text(
        '[{"domain": ".zsxq.com", "name": "foo", "value": "bar", "path": "/"}]',
        encoding="utf-8",
    )
    r = load_cookies(p)
    assert r.source == "json"
