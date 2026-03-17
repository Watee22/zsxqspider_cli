# AGENTS.md

Python CLI package for syncing Knowledge Planet (ZSXQ) PDFs, storing metadata in SQLite, and converting PDFs to Markdown.

## Repository Snapshot
- Python: `>=3.11`
- Package: `zsxq-pdf`
- Import root: `zsxq_pdf`
- CLI entrypoint: `zsxq-pdf = zsxq_pdf.cli:app`
- Packaging backend: `setuptools.build_meta`
- Tests: `pytest` with `-q` from `pyproject.toml`
- Dev extras: `pytest`, `respx`

## Rule Files
- `.cursor/rules/`: absent
- `.cursorrules`: absent
- `.github/copilot-instructions.md`: absent
- No Cursor or Copilot repo rules exist today
- If any are added later, treat them as higher-priority guidance and update this file

## Important Paths
- `pyproject.toml` - package metadata and pytest config
- `README.md` - install, usage, architecture
- `src/zsxq_pdf/cli.py` - Typer commands and orchestration
- `src/zsxq_pdf/zsxq/client.py` - API client
- `src/zsxq_pdf/zsxq/cookies.py` - cookie parsing and redaction
- `src/zsxq_pdf/store/db.py` - SQLite schema
- `src/zsxq_pdf/store/repo.py` - SQL helpers
- `src/zsxq_pdf/download/downloader.py` - download helpers
- `src/zsxq_pdf/convert/pdf_to_md.py` - PDF to Markdown
- `src/zsxq_pdf/util/` - tags, time parsing, filename sanitization
- `tests/` - unit tests

## Environment Setup
Use editable install for local development:
```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e .[dev]
```
Alternative flow from `README.md`:
```bash
uv venv
uv pip install -e .
```

## Build Commands
There is no custom build script.
Standard package build:
```bash
pip install build
python -m build --sdist --wheel
```
Notes:
- `build` is not declared in project dependencies, so install it first
- if you only need a local runnable install, `pip install -e .` is enough

## Test Commands
Run all tests:
```bash
python -m pytest
```
Run one file:
```bash
python -m pytest tests/test_tags.py
```
Run one test:
```bash
python -m pytest tests/test_tags.py::test_plain_text_fallback
```
Run a subset by keyword:
```bash
python -m pytest -k sanitize
```
Verified while creating this file:
- `python -m pytest` passed
- `python -m pytest tests/test_tags.py::test_plain_text_fallback` passed

## Lint / Format Commands
No linter, formatter, or type checker is configured.
Not present:
- `ruff`
- `black`
- `isort`
- `flake8`
- `mypy`
- `pyright`
Do not invent mandatory lint steps unless the user asks.
Lightweight validation after edits:
```bash
python -m pytest
python -m compileall src tests
```

## Code Style
Follow the existing style in `src/zsxq_pdf/` instead of introducing a new one.
### Imports
- Start modules with `from __future__ import annotations`
- Group imports as standard library, third-party, local package
- Use explicit imports; avoid wildcard imports
- Function-local imports are acceptable when they avoid circular imports or keep command startup light
### Formatting
- Use 4-space indentation
- Prefer double quotes
- Keep docstrings short and practical
- Preserve current spacing and layout in touched files
- Avoid broad reformatting
- Add comments only for non-obvious logic
### Types
- Add type hints to public functions and most helpers
- Prefer `list[str]`, `dict[str, str]`, and `X | None`
- Use frozen dataclasses for small value objects where useful
- Keep types simple and concrete
### Naming
- Functions, variables, modules: `snake_case`
- Classes and dataclasses: `PascalCase`
- Constants: `UPPER_SNAKE_CASE`
- Prefer explicit domain names like `group_id`, `topic_id`, `attachment_id`, `tag_id`, `tag_name`
- Very short names are fine only in tiny local scopes
### Error Handling
- Use `raise_for_status()` on HTTP responses
- Convert API failures into readable messages or `RuntimeError`
- Catch narrow exceptions before broad fallbacks
- At CLI boundaries, print actionable errors and continue only when that matches existing behavior
- Persist attachment success and failure states explicitly in SQLite
- Never print cookies, auth headers, or other secrets
### Data, Files, Persistence
- Keep schema work in `src/zsxq_pdf/store/db.py`
- Keep SQL access logic in `src/zsxq_pdf/store/repo.py`
- Use parameterized SQL only
- Commit after batch writes or status transitions
- Use `pathlib.Path` throughout
- Read/write text with `encoding="utf-8"`
- Create directories with `mkdir(parents=True, exist_ok=True)`
- Route filenames through `sanitize_filename` for Windows safety
- Avoid destructive operations in `data/` unless explicitly requested
### CLI Conventions
- Use Typer `Option(...)` with clear help text
- Reuse `AppConfig(data_dir=...)` and `ensure_schema(...)` at command entrypoints
- Keep orchestration in `cli.py`; move reusable logic into helper modules
- Prefer concise messages through the shared Rich `console`

## Testing Conventions
- Put tests in `tests/` with `test_*.py` names
- Use behavior-focused names like `test_load_cookies_json`
- Keep tests small and direct
- Prefer `tmp_path` for filesystem behavior
- Add or update tests when changing parsing, cookie loading, sanitization, or repository logic

## Security Notes
- Do not commit `cookies*.txt`, `cookies*.json`, `.env`, or `data/`
- Treat browser cookies as credentials
- Preserve header redaction for `cookie` and `authorization`
- Avoid exposing signed download URLs in docs or logs

## Agent Guidance
- Make narrow, local changes by default
- Avoid introducing new tooling unless requested
- Prefer extending existing modules over adding new layers
- If behavior changes affect parsing, persistence, or CLI behavior, update tests too
- If you add repo-wide tooling later, document it here and in `pyproject.toml`

## Default Verification
For most changes:
```bash
python -m pytest
```
For packaging changes:
```bash
pip install build
python -m build --sdist --wheel
```
