from __future__ import annotations

from datetime import datetime


def yyyymmdd_from_zsxq_time(value: str | None) -> str:
    """Extract YYYYMMDD from ZSXQ create_time string.

    Observed examples:
    - 2026-03-16T17:27:55.843+0800

    If parsing fails, falls back to first 10 chars if they look like YYYY-MM-DD.
    """

    if not value:
        return "00000000"

    s = value.strip()
    # Fast path: YYYY-MM-DD...
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        yyyy, mm, dd = s[:4], s[5:7], s[8:10]
        if yyyy.isdigit() and mm.isdigit() and dd.isdigit():
            return f"{yyyy}{mm}{dd}"

    # More strict parse attempts
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S%z",
    ):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.strftime("%Y%m%d")
        except Exception:
            pass

    return "00000000"
