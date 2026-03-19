from __future__ import annotations

import hashlib
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


def _truncate_utf8_bytes(text: str, max_bytes: int) -> str:
    """Truncate text to at most max_bytes when UTF-8 encoded."""
    if max_bytes <= 0:
        return ""
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text
    cut = raw[:max_bytes]
    while cut:
        try:
            return cut.decode("utf-8").rstrip(" .")
        except UnicodeDecodeError:
            cut = cut[:-1]
    return ""


def sanitize_filename(name: str, *, max_len: int = 160, max_bytes: int = 180) -> str:
    """Make a filesystem-safe filename.

    - Strips invalid characters
    - Collapses whitespace
    - Avoids reserved device names
    - Truncates by both character count and UTF-8 byte length
    - Preserves extension when possible
    - Appends a short hash when truncation is needed to avoid collisions
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
    raw_out = out.encode("utf-8")
    if len(out) < max_len and len(raw_out) <= max_bytes:
        return out

    digest = hashlib.sha1(base.encode("utf-8")).hexdigest()[:8]
    marker = f"_{digest}"

    keep_chars = max(1, max_len - len(suffix) - len(marker))
    stem = stem[:keep_chars].rstrip(" .") or "file"

    keep_bytes = max(1, max_bytes - len(suffix.encode("utf-8")) - len(marker.encode("utf-8")))
    stem = _truncate_utf8_bytes(stem, keep_bytes) or "file"

    out = f"{stem}{marker}{suffix}".strip(" .")
    if len(out.encode("utf-8")) > max_bytes:
        # Last-resort: shrink further but keep suffix/hash.
        reserve = len((marker + suffix).encode("utf-8"))
        stem = _truncate_utf8_bytes(stem, max(1, max_bytes - reserve)) or "file"
        out = f"{stem}{marker}{suffix}".strip(" .")

    return out
