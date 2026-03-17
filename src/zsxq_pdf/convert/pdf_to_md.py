from __future__ import annotations

from pathlib import Path

import fitz  # PyMuPDF


def pdf_to_markdown(pdf_path: Path, *, title: str | None = None) -> str:
    doc = fitz.open(pdf_path)
    parts: list[str] = []
    if title:
        parts.append(f"# {title}\n")

    for i, page in enumerate(doc, start=1):
        text = page.get_text("text")
        text = text.rstrip()
        parts.append(f"## Page {i}\n")
        parts.append(text if text else "(empty)")
        parts.append("\n\n---\n")

    return "\n".join(parts).rstrip() + "\n"
