"""
FastAPI application for Spotidrome.
"""

import logging
import os
from pathlib import Path

from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .models import Settings
from .worker import JobWorker

# Configure logging
log_level = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, log_level, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)


# App configuration
APP_DIR = Path(__file__).parent.parent
TEMPLATES_DIR = APP_DIR / "templates"
STATIC_DIR = APP_DIR / "static"
DATA_DIR = Path(os.getenv("DATA_DIR", APP_DIR / "data"))
MUSIC_DIR = Path(os.getenv("MUSIC_DIR", "/music"))

# Ensure directories exist
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Initialize FastAPI
app = FastAPI(title="Spotidrome", description="Music downloader with Spotify/YouTube support")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=TEMPLATES_DIR)

# Initialize worker
worker = JobWorker(
    output_dir=MUSIC_DIR,
    spotify_client_id=os.getenv("SPOTIFY_CLIENT_ID"),
    spotify_client_secret=os.getenv("SPOTIFY_CLIENT_SECRET"),
    youtube_cookies=os.getenv("YOUTUBE_COOKIES"),
)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Main page."""
    jobs = worker.get_all_jobs()
    return templates.TemplateResponse("index.html", {
        "request": request,
        "jobs": jobs,
    })


@app.post("/api/download", response_class=HTMLResponse)
async def start_download(request: Request, url: str = Form(...)):
    """Start a new download job."""
    if not url.strip():
        raise HTTPException(status_code=400, detail="URL required")

    job = worker.create_job(url.strip())

    # Return job card partial for HTMX
    return templates.TemplateResponse("partials/job_card.html", {
        "request": request,
        "job": job,
    })


@app.get("/api/jobs", response_class=HTMLResponse)
async def get_jobs(request: Request):
    """Get all jobs as HTML partial."""
    jobs = worker.get_all_jobs()
    return templates.TemplateResponse("partials/job_list.html", {
        "request": request,
        "jobs": jobs,
    })


@app.get("/api/jobs/{job_id}", response_class=HTMLResponse)
async def get_job(request: Request, job_id: str):
    """Get single job as HTML partial."""
    job = worker.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    return templates.TemplateResponse("partials/job_card.html", {
        "request": request,
        "job": job,
    })


@app.delete("/api/jobs/{job_id}", response_class=HTMLResponse)
async def cancel_job(request: Request, job_id: str):
    """Cancel a job."""
    if worker.cancel_job(job_id):
        job = worker.get_job(job_id)
        return templates.TemplateResponse("partials/job_card.html", {
            "request": request,
            "job": job,
        })
    raise HTTPException(status_code=400, detail="Cannot cancel job")


@app.get("/api/library", response_class=HTMLResponse)
async def get_library(request: Request):
    """Get library browser partial."""
    # Get artist directories
    artists = []
    if MUSIC_DIR.exists():
        artists = sorted([
            {"name": d.name, "track_count": len(list(d.glob("*.mp3")))}
            for d in MUSIC_DIR.iterdir()
            if d.is_dir()
        ], key=lambda x: x["name"])

    return templates.TemplateResponse("partials/library.html", {
        "request": request,
        "artists": artists,
        "total_artists": len(artists),
    })


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8095)
