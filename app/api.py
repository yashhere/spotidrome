"""
FastAPI application for Spotidrome.
"""

import logging
import os
import re
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from mutagen.easyid3 import EasyID3
from mutagen.id3 import APIC, ID3
from mutagen.id3._util import ID3NoHeaderError

from .lyrics import embed_lyrics_in_file, update_metadata_in_file
from .worker import JobWorker

# Configure logging
log_level = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, log_level, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


class EndpointFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return record.getMessage().find("GET /health") == -1


# Filter out /health requests from access logs
logging.getLogger("uvicorn.access").addFilter(EndpointFilter())

# Configure uvicorn access logs to match our format
uvicorn_access = logging.getLogger("uvicorn.access")
uvicorn_access.handlers = []
handler = logging.StreamHandler()
handler.setFormatter(
    logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
)
uvicorn_access.addHandler(handler)


# App configuration
APP_DIR = Path(__file__).parent.parent
TEMPLATES_DIR = APP_DIR / "templates"
STATIC_DIR = APP_DIR / "static"
DATA_DIR = Path(os.getenv("DATA_DIR", APP_DIR / "data"))
MUSIC_DIR = Path(os.getenv("MUSIC_DIR", "/music"))

# Ensure directories exist
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Initialize FastAPI
app = FastAPI(
    title="Spotidrome", description="Music downloader with Spotify/YouTube support"
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=TEMPLATES_DIR)

# Initialize worker
worker = JobWorker(
    output_dir=MUSIC_DIR,
    spotify_client_id=os.getenv("SPOTIFY_CLIENT_ID"),
    spotify_client_secret=os.getenv("SPOTIFY_CLIENT_SECRET"),
    youtube_cookies=os.getenv("YOUTUBE_COOKIES") or os.getenv("YOUTUBE_COOKIES_FILE"),
)


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse(STATIC_DIR / "favicon.png")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Main page."""
    jobs = worker.get_all_jobs()
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "jobs": jobs,
        },
    )


@app.post("/api/download", response_class=HTMLResponse)
async def start_download(request: Request, url: str = Form(...)):
    """Start a new download job."""
    if not url.strip():
        raise HTTPException(status_code=400, detail="URL required")

    job = worker.create_job(url.strip())

    # Return job card partial for HTMX
    return templates.TemplateResponse(
        "partials/job_card.html",
        {
            "request": request,
            "job": job,
        },
    )


@app.get("/api/jobs", response_class=HTMLResponse)
async def get_jobs(request: Request):
    """Get all jobs as HTML partial."""
    jobs = worker.get_all_jobs()
    return templates.TemplateResponse(
        "partials/job_list.html",
        {
            "request": request,
            "jobs": jobs,
        },
    )


@app.get("/api/jobs/{job_id}", response_class=HTMLResponse)
async def get_job(request: Request, job_id: str):
    """Get single job as HTML partial."""
    job = worker.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    return templates.TemplateResponse(
        "partials/job_card.html",
        {
            "request": request,
            "job": job,
        },
    )


@app.delete("/api/jobs/{job_id}", response_class=HTMLResponse)
async def cancel_job(request: Request, job_id: str):
    """Cancel a job."""
    if worker.cancel_job(job_id):
        job = worker.get_job(job_id)
        return templates.TemplateResponse(
            "partials/job_card.html",
            {
                "request": request,
                "job": job,
            },
        )
    raise HTTPException(status_code=400, detail="Cannot cancel job")


@app.get("/api/library", response_class=HTMLResponse)
async def get_library(request: Request, q: str | None = None):
    """Get library browser partial."""
    tracks = []
    if MUSIC_DIR.exists():
        search_query = q.lower().strip() if q else ""

        for mp3_file in sorted(MUSIC_DIR.rglob("*.mp3")):
            track = {
                "file_path": str(mp3_file.relative_to(MUSIC_DIR)),
                "filename": mp3_file.name,
                "title": mp3_file.stem.replace("_", " "),
                "artist": mp3_file.parent.name.replace("_", " ")
                if mp3_file.parent != MUSIC_DIR
                else "",
                "album": "",
                "year": "",
            }

            # Try to read ID3 metadata
            try:
                audio = EasyID3(mp3_file)
                track["title"] = audio.get("title", [track["title"]])[0]
                track["artist"] = audio.get("artist", [track["artist"]])[0]
                track["album"] = audio.get("album", [""])[0]
                track["year"] = audio.get("date", [""])[0]
            except Exception:
                pass

            # Filter if query provided
            if search_query:
                if (
                    search_query not in track["title"].lower()
                    and search_query not in track["artist"].lower()
                    and search_query not in track["album"].lower()
                ):
                    continue

            tracks.append(track)

    # Return partial if requested via HTMX search
    if request.headers.get("HX-Target") == "library-list-container":
        return templates.TemplateResponse(
            "partials/library_list.html", {"request": request, "tracks": tracks}
        )

    return templates.TemplateResponse(
        "partials/library.html",
        {
            "request": request,
            "tracks": tracks,
            "total_tracks": len(tracks),
        },
    )


def get_all_genres():
    """Get list of all unique genres in the library."""
    genres = set()
    if MUSIC_DIR.exists():
        for mp3_file in MUSIC_DIR.rglob("*.mp3"):
            try:
                audio = EasyID3(mp3_file)
                genre_list = audio.get("genre", [])
                for g in genre_list:
                    if g.strip():
                        genres.add(g.strip())
            except Exception:
                pass

    if not genres:
        # Return common defaults if library has no genre metadata
        return [
            "Alternative",
            "Anime",
            "Blues",
            "Children's Music",
            "Classical",
            "Comedy",
            "Country",
            "Dance",
            "Disney",
            "Easy Listening",
            "Electronic",
            "Enka",
            "French Pop",
            "German Folk",
            "German Pop",
            "Fitness & Workout",
            "Hip-Hop/Rap",
            "Holiday",
            "Indie Pop",
            "Industrial",
            "Inspirational - Christian & Gospel",
            "Instrumental",
            "J-Pop",
            "Jazz",
            "K-Pop",
            "Karaoke",
            "Kayokyoku",
            "Latin",
            "New Age",
            "Opera",
            "Pop",
            "R&B/Soul",
            "Reggae",
            "Rock",
            "Singer/Songwriter",
            "Soundtrack",
            "Spoken Word",
            "Vocal",
            "World",
        ]

    return sorted(list(genres))


@app.get("/api/genres")
async def get_genres(request: Request):
    """Get list of all unique genres in the library."""
    sorted_genres = get_all_genres()

    # Return HTML options for HTMX
    if request.headers.get("HX-Request"):
        options = "".join([f'<option value="{g}">' for g in sorted_genres])
        return HTMLResponse(content=options)

    return sorted_genres


@app.get("/api/tracks/art")
async def get_album_art(file_path: str):
    """Get album art from an MP3 file."""
    track_path = Path(file_path)
    if not track_path.is_absolute():
        track_path = MUSIC_DIR / track_path

    if not track_path.exists():
        raise HTTPException(status_code=404, detail="File not found")

    try:
        audio = ID3(track_path)
        apic = audio.getall("APIC")
        if apic:
            return Response(content=apic[0].data, media_type=apic[0].mime)
    except Exception:
        pass

    raise HTTPException(status_code=404, detail="No album art found")


@app.get("/api/tracks/edit", response_class=HTMLResponse)
async def get_lyrics_editor(request: Request, file_path: str):
    """Get lyrics editor modal."""
    track_path = Path(file_path)
    if not track_path.is_absolute():
        track_path = MUSIC_DIR / track_path

    if not track_path.exists():
        raise HTTPException(status_code=404, detail="File not found")

    # Fetch existing metadata
    metadata = {
        "title": "",
        "artist": "",
        "album": "",
        "date": "",
        "track_number": "",
        "genre": "",
    }

    try:
        audio = EasyID3(track_path)
        metadata["title"] = audio.get("title", [""])[0]
        metadata["artist"] = audio.get("artist", [""])[0]
        metadata["album"] = audio.get("album", [""])[0]
        metadata["date"] = audio.get("date", [""])[0]
        metadata["track_number"] = audio.get("tracknumber", [""])[0]
        metadata["genre"] = audio.get("genre", [""])[0]
    except Exception:
        pass

    # Try to read existing lyrics
    lyrics = ""

    # Check for .lrc file first
    lrc_path = track_path.with_suffix(".lrc")
    if lrc_path.exists():
        try:
            lyrics = lrc_path.read_text(encoding="utf-8")
        except Exception:
            pass

    # Fallback to embedded USLT
    if not lyrics:
        try:
            audio = ID3(track_path)
            uslt = audio.getall("USLT")
            if uslt:
                lyrics = uslt[0].text
        except Exception:
            pass

    # Check for album art
    has_art = False
    try:
        audio = ID3(track_path)
        if audio.getall("APIC"):
            has_art = True
    except Exception:
        pass

    # Get all available genres
    all_genres = get_all_genres()

    return templates.TemplateResponse(
        "partials/lyrics_edit.html",
        {
            "request": request,
            "file_path": file_path,
            "lyrics": lyrics,
            "metadata": metadata,
            "filename": track_path.name,
            "has_art": has_art,
            "all_genres": all_genres,
        },
    )


@app.post("/api/tracks/update")
async def update_track(
    file_path: str = Form(...),
    title: str = Form(None),
    artist: str = Form(None),
    album: str = Form(None),
    date: str = Form(None),
    track_number: str = Form(None),
    genre: str = Form(None),
    lyrics: str = Form(""),
):
    """Update track metadata and lyrics."""

    # Validate file path
    track_path = Path(file_path)
    if not track_path.is_absolute():
        track_path = MUSIC_DIR / track_path

    try:
        track_path = track_path.resolve()
        if not str(track_path).startswith(str(MUSIC_DIR.resolve())):
            raise HTTPException(status_code=400, detail="Invalid file path")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid file path")

    if not track_path.exists():
        raise HTTPException(status_code=404, detail="File not found")

    # Get current metadata to check for changes
    old_artist = None
    try:
        audio = EasyID3(track_path)
        old_artist = audio.get("artist", [""])[0]
    except Exception:
        pass

    # Update metadata
    update_metadata_in_file(
        track_path,
        {
            "title": title,
            "artist": artist,
            "album": album,
            "date": date,
            "track_number": track_number,
            "genre": genre,
        },
    )

    # Reorganize file if artist changed
    new_path = track_path
    if artist and old_artist and artist != old_artist:
        # Sanitize artist name for directory
        safe_artist = "".join(
            c for c in artist if c.isalnum() or c in (" ", "_", "-")
        ).strip()
        safe_artist = safe_artist.replace(" ", "_")

        # New directory
        new_dir = MUSIC_DIR / safe_artist
        new_dir.mkdir(parents=True, exist_ok=True)

        # New file path
        new_filename = track_path.name
        new_path = new_dir / new_filename

        # Move file if destination doesn't exist (to avoid overwriting)
        if not new_path.exists():
            try:
                # Move .lrc file first if it exists
                lrc_path = track_path.with_suffix(".lrc")
                if lrc_path.exists():
                    new_lrc_path = new_path.with_suffix(".lrc")
                    lrc_path.rename(new_lrc_path)

                # Move audio file
                track_path.rename(new_path)
                logger.info(f"Moved track from {track_path} to {new_path}")

                # Try to remove old directory if empty
                try:
                    old_dir = track_path.parent
                    if old_dir != MUSIC_DIR and not any(old_dir.iterdir()):
                        old_dir.rmdir()
                        logger.info(f"Removed empty directory: {old_dir}")
                except Exception as e:
                    logger.warning(f"Failed to remove empty directory: {e}")

                track_path = new_path
            except Exception as e:
                logger.error(f"Failed to move file: {e}")
                # Revert track_path if move failed
                new_path = track_path

    # Update lyrics if provided
    if lyrics is not None:
        lyrics_text = lyrics.strip()
        lrc_path = track_path.with_suffix(".lrc")

        if not lyrics_text:
            # Empty lyrics - remove .lrc file and embedded lyrics
            if lrc_path.exists():
                lrc_path.unlink()
            # Remove embedded lyrics
            try:
                audio = ID3(track_path)
                audio.delall("USLT")
                audio.delall("SYLT")
                audio.save()
            except Exception:
                pass
        else:
            # Non-empty lyrics - save and embed
            is_synced = (
                lyrics_text.startswith("[") and "]" in lyrics_text.split("\n")[0]
            )

            # Save .lrc file
            lrc_path.write_text(lyrics_text, encoding="utf-8")

            # Embed lyrics
            plain_lyrics = lyrics_text
            synced_lyrics = lyrics_text if is_synced else None

            if is_synced:
                plain_lyrics = re.sub(r"\[\d{2}:\d{2}[.\d]*\]", "", lyrics_text)

                plain_lyrics = "\n".join(
                    line.strip() for line in plain_lyrics.split("\n") if line.strip()
                )

            embed_lyrics_in_file(track_path, plain_lyrics, synced_lyrics)

    return {"status": "ok", "message": "Track updated"}


@app.post("/api/tracks/art")
async def update_album_art(
    file_path: str = Form(...), art: UploadFile = File(None), art_url: str = Form(None)
):
    """Update album art for a track."""
    track_path = Path(file_path)
    if not track_path.is_absolute():
        track_path = MUSIC_DIR / track_path

    if not track_path.exists():
        raise HTTPException(status_code=404, detail="File not found")

    # Get image data from either file upload or URL
    art_data = None
    mime_type = "image/jpeg"

    if art:
        # File upload
        art_data = await art.read()
        mime_type = art.content_type or "image/jpeg"
    elif art_url:
        # Download from URL
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(art_url)
                response.raise_for_status()
                art_data = response.content
                mime_type = response.headers.get("content-type", "image/jpeg")
        except Exception as e:
            logger.error(f"Failed to download image from URL: {e}")
            raise HTTPException(
                status_code=400, detail=f"Failed to download image: {str(e)}"
            )
    else:
        raise HTTPException(status_code=400, detail="No image data provided")

    if not art_data:
        raise HTTPException(status_code=400, detail="No image data")

    try:
        try:
            audio = ID3(track_path)
        except ID3NoHeaderError:
            audio = ID3()
            audio.save(track_path)
            audio = ID3(track_path)

        # Remove existing album art
        audio.delall("APIC")

        # Add new album art
        audio.add(
            APIC(
                encoding=3,
                mime=mime_type,
                type=3,  # Cover (front)
                desc="Cover",
                data=art_data,
            )
        )
        audio.save()
        logger.info(f"Updated album art for: {track_path}")
        return {"status": "ok", "message": "Album art updated"}
    except Exception as e:
        logger.error(f"Failed to update album art: {e}")
        raise HTTPException(status_code=500, detail="Failed to update album art")


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8095)
