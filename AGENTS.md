# AGENTS.md - Spotidrome Development Guide

This file contains essential information for AI agents working on the Spotidrome codebase.

## Project Overview

Spotidrome is a FastAPI web application for downloading music from Spotify/YouTube with metadata enrichment. Uses yt-dlp for downloads, mutagen for ID3 tagging, and HTMX + Jinja2 for the frontend.

## Build / Lint / Test Commands

### Running the Application
```bash
# Development server with hot reload
uv run uvicorn app.api:app --host 0.0.0.0 --port 8095 --reload

# Or using just
just run
```

### Linting and Formatting
```bash
# Run all pre-commit hooks (lint, format, import sort)
uv run pre-commit run --all-files

# Run individual tools
uv run ruff check .                    # Lint Python
uv run ruff check --fix .              # Lint and auto-fix Python
uv run ruff format .                   # Format Python
uv run isort .                         # Sort imports
uv run prettier --write "**/*.html"    # Format HTML/CSS

# Check syntax without running full suite
python -m py_compile app/api.py app/downloader.py
```

### Pre-commit Hooks
Install hooks to run checks automatically on commit:
```bash
uv run pre-commit install
```

Hooks include:
- `trailing-whitespace`, `end-of-file-fixer`, `check-yaml`
- `ruff` (lint with auto-fix)
- `ruff-format` (format)
- `isort` (import sorting)
- `prettier` (HTML/CSS formatting)

### Testing
**Note:** This project currently has no test suite. To add tests:
- Create a `tests/` directory
- Use `pytest` via `uv run pytest`
- Run single test: `uv run pytest tests/test_file.py::test_function -v`

## Code Style Guidelines

### Python Style
- **Line length:** 88 characters (Black-compatible)
- **Target Python:** 3.12+ (use modern syntax: `str | None`, `list[str]`)
- **Formatter:** Ruff (replaces Black)
- **Import style:** isort with Black profile

### Imports
```python
# Standard library imports first
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

# Third-party imports second
import yt_dlp
from fastapi import FastAPI
from mutagen.easyid3 import EasyID3

# Local imports last (relative preferred)
from .lyrics import LyricsProvider
from .providers.base import Track
```

### Type Hints
- Use modern union syntax: `str | None` instead of `Optional[str]`
- Use built-in generics: `list[str]` instead of `List[str]`
- Use TypedDict for dictionary structures (see `app/providers/base.py`)
- Use Protocol for interfaces

Example:
```python
def process_track(track: Track) -> Path | None:
    """Process a track and return output path or None on failure."""
    pass
```

### Naming Conventions
- **Modules:** `lowercase.py`
- **Classes:** `PascalCase`
- **Functions/Variables:** `snake_case`
- **Constants:** `UPPER_SNAKE_CASE`
- **Private methods:** `_leading_underscore`

### Docstrings
- Use Google-style docstrings
- Include type info in Args/Returns sections
- Keep first line as a brief summary

```python
def download_track(self, track: Track) -> Path | None:
    """Download a single track using yt-dlp.

    Args:
        track: Track dictionary with name, artists, etc.

    Returns:
        Path to downloaded file, or None if failed.
    """
```

### Error Handling
- Use specific exceptions when possible
- Log errors with context using `logger.exception()` or `logger.error()`
- Return `None` for recoverable failures, raise for critical errors
- Use custom exceptions for domain-specific errors (e.g., `CookieExpiredError`)

```python
try:
    result = risky_operation()
except yt_dlp.DownloadError as e:
    logger.error(f"Download failed for {url}: {e}")
    return None
except Exception:
    logger.exception(f"Unexpected error processing {url}")
    raise
```

### Logging
- Use module-level logger: `logger = logging.getLogger(__name__)`
- Log levels:
  - `debug`: Detailed info for troubleshooting
  - `info`: Normal operations (downloads started/completed)
  - `warning`: Recoverable issues (fallbacks used)
  - `error`: Failures that prevent operation completion

### File Organization
```
app/
├── __init__.py
├── api.py              # FastAPI routes
├── downloader.py       # Core yt-dlp logic
├── lyrics.py          # Lyrics fetching/embedding
├── models.py          # Pydantic models
├── worker.py          # Background job processing
└── providers/         # Music source providers
    ├── __init__.py
    ├── base.py        # TypedDicts and Protocols
    ├── spotify.py     # Spotify API
    ├── youtube.py     # YouTube provider
    └── ytmusic.py     # YouTube Music provider
```

### Key Patterns

1. **Track TypedDict:** Use the `Track` type from `app.providers.base` for track data
2. **Provider Protocol:** New providers should implement `PlaylistProvider` protocol
3. **Path Safety:** Always validate paths are within `MUSIC_DIR` before operations
4. **Metadata:** Join multiple artists with `", "` (comma + space)

### Dependencies
- Manage with `uv` (modern Python package manager)
- Lock file: `uv.lock`
- Add dependency: `uv add package_name`
- Sync: `uv sync`

### Environment Variables
Create `.env` file based on `.env.example`:
- `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET`
- `YOUTUBE_COOKIES_FILE` (optional)
- `MUSIC_OUTPUT_DIR`

## Docker Commands

```bash
# Build image
just release

# Or manually:
docker buildx build --platform linux/arm64 -t spotidrome:latest .
```

## Important Notes

- Always run `uv run pre-commit run --all-files` before committing
- Python 3.12+ features are encouraged (match statements, better error messages)
- The app uses HTMX for frontend interactions - backend returns HTML partials, not JSON
- File paths should use first artist only when multiple artists exist
- External JavaScript solver (ejs:github) is required for YouTube downloads (yt-dlp 2026+)
