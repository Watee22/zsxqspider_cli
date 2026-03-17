from __future__ import annotations

import hashlib
from pathlib import Path

import httpx

from zsxq_pdf.util.sanitize import sanitize_filename


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def download_file(
    client: httpx.Client,
    url: str,
    dest_dir: Path,
    filename: str,
    *,
    overwrite: bool = False,
) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    safe_name = sanitize_filename(filename)
    dest_path = dest_dir / safe_name

    if dest_path.exists() and not overwrite:
        return dest_path

    with client.stream("GET", url, follow_redirects=True) as r:
        r.raise_for_status()
        tmp = dest_path.with_suffix(dest_path.suffix + ".part")
        with tmp.open("wb") as f:
            for chunk in r.iter_bytes(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
        tmp.replace(dest_path)

    return dest_path
