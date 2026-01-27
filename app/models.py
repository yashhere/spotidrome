"""
Pydantic models for API requests/responses and job state.
"""

from datetime import datetime
from enum import Enum
from pydantic import BaseModel


class JobStatus(str, Enum):
    """Status of a download job."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DownloadRequest(BaseModel):
    """Request to start a download job."""
    url: str
    sync_to_navidrome: bool = False


class JobProgress(BaseModel):
    """Progress information for a download job."""
    current: int = 0
    total: int = 0
    current_track: str | None = None


class DownloadedTrack(BaseModel):
    """Info about a downloaded track for UI display."""
    name: str
    artist: str
    album: str | None = None
    cover_url: str | None = None
    file_path: str
    has_lyrics: bool = False


class Job(BaseModel):
    """A download job."""
    id: str
    url: str
    playlist_name: str | None = None
    status: JobStatus = JobStatus.PENDING
    progress: JobProgress = JobProgress()
    error: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    downloaded_files: list[str] = []
    tracks: list[DownloadedTrack] = []


class Settings(BaseModel):
    """Application settings."""
    spotify_client_id: str | None = None
    spotify_client_secret: str | None = None
    navidrome_url: str | None = None
    navidrome_user: str | None = None
    navidrome_pass: str | None = None
    music_output_dir: str = "/music"
