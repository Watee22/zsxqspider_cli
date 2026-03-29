from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


DIRECT_PANDOC_EXTENSIONS = {".docx"}
OFFICE_CONVERT_EXTENSIONS = {".doc", ".wps"}
SUPPORTED_DOCUMENT_EXTENSIONS = {".pdf", ".doc", ".docx", ".wps"}


@dataclass(frozen=True)
class OfficeMarkdownResult:
    markdown: str
    converter: str


def supported_document_extensions() -> tuple[str, ...]:
    return tuple(sorted(SUPPORTED_DOCUMENT_EXTENSIONS))


def is_supported_document(filename: str | None) -> bool:
    if not filename:
        return False
    return Path(filename).suffix.lower() in SUPPORTED_DOCUMENT_EXTENSIONS


def office_document_to_markdown(path: Path, *, title: str | None = None) -> str:
    return office_document_to_markdown_result(path, title=title).markdown


def office_document_to_markdown_result(path: Path, *, title: str | None = None) -> OfficeMarkdownResult:
    suffix = path.suffix.lower()
    if suffix not in DIRECT_PANDOC_EXTENSIONS | OFFICE_CONVERT_EXTENSIONS:
        raise ValueError(f"Unsupported office document type: {suffix or '<none>'}")

    source_for_pandoc = path
    converter = "pandoc-docx-direct"
    with tempfile.TemporaryDirectory(prefix="zsxq-office-") as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str)

        if suffix in OFFICE_CONVERT_EXTENSIONS:
            converted_docx = _soffice_convert(path, outdir=tmp_dir, target="docx")
            if converted_docx is not None and converted_docx.exists():
                source_for_pandoc = converted_docx
                converter = "libreoffice-docx->pandoc"
            else:
                converted_txt = _soffice_convert(path, outdir=tmp_dir, target="txt")
                if converted_txt is None or not converted_txt.exists():
                    raise RuntimeError(f"LibreOffice could not convert {path.name} to docx/txt")
                text = converted_txt.read_text(encoding="utf-8", errors="replace").strip()
                return OfficeMarkdownResult(
                    markdown=_wrap_text_as_markdown(text, title=title or path.stem),
                    converter="libreoffice-txt-fallback",
                )

        markdown = _pandoc_to_markdown(source_for_pandoc)
        if title:
            markdown = f"# {title}\n\n{markdown.lstrip()}"
        return OfficeMarkdownResult(markdown=markdown.rstrip() + "\n", converter=converter)


def _pandoc_to_markdown(path: Path) -> str:
    result = subprocess.run(
        [
            "pandoc",
            str(path),
            "-t",
            "gfm",
            "--wrap=none",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"pandoc failed for {path.name}")
    return result.stdout or ""


def _soffice_convert(path: Path, *, outdir: Path, target: str) -> Path | None:
    result = subprocess.run(
        [
            "libreoffice",
            "--headless",
            "--convert-to",
            target,
            "--outdir",
            str(outdir),
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    if target == "docx":
        candidate = outdir / f"{path.stem}.docx"
    elif target == "txt":
        candidate = outdir / f"{path.stem}.txt"
    else:
        candidate = outdir / f"{path.stem}.{target}"
    return candidate


def _wrap_text_as_markdown(text: str, *, title: str) -> str:
    body = text if text else "(empty)"
    return f"# {title}\n\n{body}\n"
