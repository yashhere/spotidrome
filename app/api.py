"""
FastAPI application for Spotidrome.
"""

# Load .env file before other imports to ensure environment variables are available
from dotenv import load_dotenv

load_dotenv()

import logging  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
from pathlib import Path  # noqa: E402

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from fastapi import (  # noqa: E402
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import FileResponse, HTMLResponse, Response  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from fastapi.templating import Jinja2Templates  # noqa: E402
from mediafile import Image, ImageType, MediaFile  # noqa: E402

from .lyrics import embed_lyrics_in_file, update_metadata_in_file  # noqa: E402
from .tagging_utils import normalize_artists  # noqa: E402
from .worker import JobWorker  # noqa: E402

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

# Constants
COOKIES_FILE = DATA_DIR / "cookies.txt"

# Initialize FastAPI
app = FastAPI(
    title="Spotidrome", description="Music downloader with Spotify/YouTube support"
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=TEMPLATES_DIR)

# Initialize worker
# Only use cookies if the file exists, otherwise download without authentication
cookies_path = COOKIES_FILE if COOKIES_FILE.exists() else None
worker = JobWorker(
    output_dir=MUSIC_DIR,
    spotify_client_id=os.getenv("SPOTIFY_CLIENT_ID"),
    spotify_client_secret=os.getenv("SPOTIFY_CLIENT_SECRET"),
    youtube_cookies=str(cookies_path) if cookies_path else None,
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

            # Try to read metadata
            try:
                mf = MediaFile(mp3_file)
                if mf.title:
                    track["title"] = str(mf.title)
                if mf.artist:
                    track["artist"] = str(mf.artist)
                if mf.album:
                    track["album"] = str(mf.album)
                if mf.year is not None:
                    track["year"] = str(mf.year)
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
                mf = MediaFile(mp3_file)
                if mf.genre:
                    genres.add(str(mf.genre).strip())
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
        mf = MediaFile(track_path)
        if mf.images:
            image = mf.images[0]
            if isinstance(image, Image):
                media_type = image.mime_type or "image/jpeg"
                return Response(content=image.data, media_type=media_type)
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
        mf = MediaFile(track_path)
        metadata["title"] = str(mf.title) if mf.title else ""
        metadata["artist"] = str(mf.artist) if mf.artist else ""
        metadata["album"] = str(mf.album) if mf.album else ""
        metadata["date"] = str(mf.year) if mf.year is not None else ""
        metadata["track_number"] = str(mf.track) if mf.track is not None else ""
        metadata["genre"] = str(mf.genre) if mf.genre else ""
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

    # Fallback to embedded lyrics
    if not lyrics:
        try:
            mf = MediaFile(track_path)
            lyrics = mf.lyrics or ""
        except Exception:
            pass

    # Check for album art
    has_art = False
    try:
        mf = MediaFile(track_path)
        has_art = bool(mf.images)
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
    old_title = None
    try:
        mf = MediaFile(track_path)
        old_artist = mf.artist or ""
        old_title = mf.title or ""
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

    # Reorganize file if artist or title changed, or if filename doesn't match
    new_path = track_path
    artist_changed = artist and old_artist and artist != old_artist
    title_changed = title and old_title and title != old_title

    # Calculate expected filename from current title
    expected_filename = None
    if title:
        safe_title = "".join(
            c for c in title if c.isalnum() or c in (" ", "_", "-")
        ).strip()
        safe_title = safe_title.replace(" ", "_")
        expected_filename = f"{safe_title}.mp3"

    # Check if filename needs updating (even if title hasn't changed)
    filename_mismatch = expected_filename and track_path.name != expected_filename

    if artist_changed or title_changed or filename_mismatch:
        logger.info(
            f"Reorganizing file: artist_changed={artist_changed}, title_changed={title_changed}, filename_mismatch={filename_mismatch}"
        )

        # Determine new directory (use first artist only if multiple artists)
        if artist:
            # Use only the first artist for the directory name
            artist_list = normalize_artists(artist)
            first_artist = artist_list[0] if artist_list else artist
            safe_artist = "".join(
                c for c in first_artist if c.isalnum() or c in (" ", "_", "-")
            ).strip()
            safe_artist = safe_artist.replace(" ", "_")
            new_dir = MUSIC_DIR / safe_artist
        else:
            new_dir = track_path.parent

        new_dir.mkdir(parents=True, exist_ok=True)

        # Determine new filename
        if expected_filename:
            new_filename = expected_filename
        else:
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
        else:
            logger.warning(
                f"Cannot move file: destination already exists at {new_path}"
            )

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
                mf = MediaFile(track_path)
                mf.lyrics = None
                mf.save()
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

    if art:
        # File upload
        art_data = await art.read()
    elif art_url:
        # Download from URL
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(art_url)
                response.raise_for_status()
                art_data = response.content
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
        mf = MediaFile(track_path)
        mf.images = [Image(data=art_data, desc="Cover", type=ImageType.front)]
        mf.save()
        logger.info(f"Updated album art for: {track_path}")
        return {"status": "ok", "message": "Album art updated"}
    except Exception as e:
        logger.error(f"Failed to update album art: {e}")
        raise HTTPException(status_code=500, detail="Failed to update album art")


@app.get("/api/settings", response_class=HTMLResponse)
async def get_settings(request: Request):
    """Get settings page partial."""
    cookies_present = COOKIES_FILE.exists()
    return templates.TemplateResponse(
        "partials/settings.html",
        {
            "request": request,
            "cookies_present": cookies_present,
        },
    )


@app.post("/api/settings/cookies", response_class=HTMLResponse)
async def upload_cookies(request: Request, cookies_file: UploadFile = File(...)):
    """Upload YouTube cookies file."""
    if not cookies_file.filename or not cookies_file.filename.endswith(".txt"):
        raise HTTPException(status_code=400, detail="Only .txt files are allowed")

    try:
        content = await cookies_file.read()
        COOKIES_FILE.write_bytes(content)

        # Update worker settings
        worker.update_settings(youtube_cookies=str(COOKIES_FILE))

        return templates.TemplateResponse(
            "partials/settings.html",
            {
                "request": request,
                "cookies_present": True,
                "success_message": "Cookies uploaded successfully!",
            },
        )
    except Exception as e:
        logger.error(f"Failed to save cookies file: {e}")
        raise HTTPException(status_code=500, detail="Failed to save cookies file")


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8095)
