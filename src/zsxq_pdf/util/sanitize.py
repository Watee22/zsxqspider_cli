from __future__ import annotations

import re


_WINDOWS_INVALID = re.compile(r"[<>:\"/\\|?*]")
_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def sanitize_filename(name: str, *, max_len: int = 160) -> str:
    """Make a Windows-safe filename.

    - Strips invalid characters
    - Collapses whitespace
    - Avoids reserved device names
    """
    base = name.strip().replace("\u0000", "")
    base = _WINDOWS_INVALID.sub("_", base)
    base = re.sub(r"\s+", " ", base).strip()
    if not base:
        base = "file"

    stem = base
    suffix = ""
    if "." in base and not base.endswith("."):
        # Keep last extension if present.
        parts = base.split(".")
        stem = ".".join(parts[:-1])
        suffix = "." + parts[-1]

    if stem.upper() in _RESERVED:
        stem = stem + "_"

    out = (stem + suffix).strip(" .")
    if len(out) >= max_len:
        # Truncate while preserving extension
        keep = max_len - len(suffix)
        out = stem[:keep].rstrip(" .") + suffix

    return out
