"""
Background job worker for managing download tasks.
"""

import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable

from .models import Job, JobStatus, JobProgress
from .providers import get_provider, SpotifyProvider, YouTubeProvider
from .downloader import TrackDownloader


class JobWorker:
    """Manages background download jobs."""

    def __init__(
        self,
        output_dir: Path,
        spotify_client_id: str | None = None,
        spotify_client_secret: str | None = None,
        youtube_cookies: str | None = None,
    ):
        self.output_dir = Path(output_dir)
        self.jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

        # Configure providers
        self.spotify_provider = SpotifyProvider(spotify_client_id, spotify_client_secret)
        self.youtube_provider = YouTubeProvider(youtube_cookies, self.spotify_provider)

    def update_settings(
        self,
        spotify_client_id: str | None = None,
        spotify_client_secret: str | None = None,
        youtube_cookies: str | None = None,
    ) -> None:
        """Update provider settings."""
        if spotify_client_id and spotify_client_secret:
            self.spotify_provider.configure(spotify_client_id, spotify_client_secret)
            self.youtube_provider.configure(spotify_provider=self.spotify_provider)
        if youtube_cookies is not None:
            self.youtube_provider.configure(cookies=youtube_cookies)

    def create_job(self, url: str) -> Job:
        """Create a new download job."""
        job = Job(
            id=str(uuid.uuid4())[:8],
            url=url,
            created_at=datetime.now(),
        )

        with self._lock:
            self.jobs[job.id] = job

        # Start background processing
        thread = threading.Thread(target=self._process_job, args=(job.id,))
        thread.daemon = True
        thread.start()

        return job

    def get_job(self, job_id: str) -> Job | None:
        """Get job by ID."""
        return self.jobs.get(job_id)

    def get_all_jobs(self) -> list[Job]:
        """Get all jobs, most recent first."""
        return sorted(self.jobs.values(), key=lambda j: j.created_at, reverse=True)

    def cancel_job(self, job_id: str) -> bool:
        """Cancel a job."""
        job = self.jobs.get(job_id)
        if job and job.status in [JobStatus.PENDING, JobStatus.RUNNING]:
            job.status = JobStatus.CANCELLED
            return True
        return False

    def _process_job(self, job_id: str) -> None:
        """Process a download job in background."""
        job = self.jobs.get(job_id)
        if not job:
            return

        try:
            job.status = JobStatus.RUNNING
            job.started_at = datetime.now()

            # Detect provider
            if "spotify.com" in job.url:
                provider = self.spotify_provider
            elif "youtube.com" in job.url or "youtu.be" in job.url:
                provider = self.youtube_provider
            else:
                raise ValueError("Unsupported URL")

            # Check if it's a single track or playlist
            is_single = any(x in job.url for x in ["/track/", "/watch?v="])

            if is_single:
                track = provider.get_track(job.url)
                if not track:
                    raise ValueError("Could not fetch track info")
                job.playlist_name = f"{track['artists'][0]} - {track['name']}"
                tracks = [track]
            else:
                playlist_name, tracks = provider.get_playlist(job.url)
                job.playlist_name = playlist_name

            job.progress = JobProgress(current=0, total=len(tracks))

            # Download tracks
            def progress_callback(status: str, current: int, total: int) -> None:
                if job.status == JobStatus.CANCELLED:
                    raise InterruptedError("Job cancelled")
                job.progress.current = current
                job.progress.total = total
                job.progress.current_track = status

            downloader = TrackDownloader(self.output_dir, progress_callback)

            # Download and collect track info
            from .models import DownloadedTrack
            downloaded_tracks = []

            for i, track in enumerate(tracks, 1):
                artist = track.get('artists', ['Unknown'])[0] if track.get('artists') else 'Unknown'
                title = track.get('name', 'Unknown')

                progress_callback(f"[{i}/{len(tracks)}] {artist} - {title}", i, len(tracks))

                path = downloader.download_track(track)
                if path:
                    # Check for .lrc file
                    lrc_path = path.with_suffix('.lrc')
                    has_lyrics = lrc_path.exists()

                    downloaded_tracks.append(DownloadedTrack(
                        name=title,
                        artist=artist,
                        album=track.get('album'),
                        cover_url=track.get('cover_url'),
                        file_path=str(path),
                        has_lyrics=has_lyrics,
                    ))

            job.downloaded_files = [t.file_path for t in downloaded_tracks]
            job.tracks = downloaded_tracks
            job.status = JobStatus.COMPLETED
            job.completed_at = datetime.now()

        except InterruptedError:
            job.status = JobStatus.CANCELLED
        except Exception as e:
            job.status = JobStatus.FAILED
            job.error = str(e)
            job.completed_at = datetime.now()
