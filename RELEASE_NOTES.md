# Spotidrome v1.0.0

First stable release of Spotidrome, a self-hosted music downloader and manager.

## Features

- **Downloader**: Support for Spotify (Tracks, Albums, Playlists) and YouTube.
- **Metadata**: Automatic tagging (ID3) with high-res album art.
- **Lyrics**: Fetches synced lyrics from Musixmatch/LRCLIB. Embeds `SYLT` tags and generates `.lrc` files.
- **Library**: clean web interface to browse, search, and manage downloaded tracks.
- **Editor**: Built-in metadata and lyrics editor.

## Technical Details

- Stack: FastAPI (Backend), HTMX (Frontend), yt-dlp (Core).
- Dependency Management: `uv`.
- Code Quality: Linting/Formatting via Ruff, Isort, and Prettier.

## Usage

1. Run `uv sync`
2. Start server: `uv run uvicorn app.api:app --host 0.0.0.0 --port 8095`
