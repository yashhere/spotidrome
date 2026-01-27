"""
Core download logic - downloads tracks using yt-dlp and applies metadata.
"""

import logging
import re
import subprocess
import unicodedata
import urllib.request
from pathlib import Path
from typing import Callable

from .providers.base import Track
from .lyrics import LyricsProvider

logger = logging.getLogger(__name__)


class TrackDownloader:
    """Downloads tracks from YouTube and applies metadata."""

    def __init__(self, output_dir: Path, progress_callback: Callable[[str, int, int], None] | None = None):
        """
        Args:
            output_dir: Directory to save downloaded files
            progress_callback: Optional callback(status, current, total) for progress updates
        """
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.progress_callback = progress_callback
        self.lyrics_provider = LyricsProvider()

    def _sanitize_filename(self, name: str) -> str:
        """Create safe filename (ASCII with underscores)."""
        # Normalize unicode and convert to ASCII
        name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode('ascii')
        # Remove invalid filesystem characters
        name = re.sub(r'[<>:"/\\|?*\[\]\']', '', name)
        # Replace spaces with underscores
        name = re.sub(r'\s+', '_', name)
        # Remove multiple underscores
        name = re.sub(r'_+', '_', name)
        # Trim underscores from ends
        name = name.strip('_')
        return name[:180]  # Limit length

    def _report_progress(self, status: str, current: int = 0, total: int = 0) -> None:
        """Report progress via callback if set."""
        if self.progress_callback:
            self.progress_callback(status, current, total)

    def download_track(self, track: Track) -> Path | None:
        """Download a single track.

        Returns:
            Path to downloaded file, or None if failed
        """
        track_name = track.get("name", "")
        if not track_name:
            logger.warning("Skipping track with no name")
            return None

        artist = track["artists"][0] if track.get("artists") else "Unknown"

        safe_artist = self._sanitize_filename(artist)
        safe_title = self._sanitize_filename(track_name)

        artist_dir = self.output_dir / safe_artist
        artist_dir.mkdir(parents=True, exist_ok=True)

        output_path = artist_dir / f"{safe_title}.mp3"

        # Skip if already downloaded
        if output_path.exists():
            logger.info(f"Already exists: {safe_artist}/{safe_title}.mp3")
            self._report_progress(f"Already exists: {safe_artist}/{safe_title}.mp3")
            return output_path

        # Build search query or use source URL
        source_url = track.get("source_url")
        if source_url:
            search_term = source_url
            logger.info(f"Downloading from URL: {source_url}")
        else:
            search_term = f"ytsearch1:{artist} {track_name}"
            logger.info(f"Searching YouTube for: {artist} - {track_name}")

        self._report_progress(f"Downloading: {artist} - {track_name}")

        cmd = [
            "yt-dlp",
            search_term,
            "--extract-audio",
            "--audio-format", "mp3",
            "--audio-quality", "0",
            "--output", str(artist_dir / f"{safe_title}.%(ext)s"),
            "--no-playlist",
            "--no-warnings",
            "--quiet",
        ]

        try:
            result = subprocess.run(cmd, check=True, capture_output=True, text=True)
            logger.debug(f"yt-dlp completed for: {artist} - {track_name}")
        except subprocess.CalledProcessError as e:
            error_msg = e.stderr[:200] if e.stderr else str(e)
            logger.error(f"Download failed for {artist} - {track_name}: {error_msg}")
            self._report_progress(f"Failed: {error_msg}")
            return None

        # Find the downloaded file
        downloaded_path = None
        if output_path.exists():
            downloaded_path = output_path
        else:
            # Try other extensions
            for ext in [".mp3", ".m4a", ".opus", ".webm"]:
                alt_path = artist_dir / f"{safe_title}{ext}"
                if alt_path.exists():
                    downloaded_path = alt_path
                    break

        if not downloaded_path:
            logger.error(f"Could not find downloaded file for: {artist} - {track_name}")
            return None

        # Apply metadata and album art
        self._tag_file(downloaded_path, track)

        # Fetch and save lyrics
        self._fetch_lyrics(downloaded_path, track)

        return downloaded_path

    def _tag_file(self, file_path: Path, track: Track) -> None:
        """Tag audio file with metadata and album art."""
        try:
            from mutagen.easyid3 import EasyID3
            from mutagen.id3 import ID3NoHeaderError, ID3, APIC
        except ImportError:
            logger.warning("mutagen not installed, skipping tagging")
            return

        artist = track.get('artists', ['Unknown'])[0] if track.get('artists') else 'Unknown'
        title = track.get('name', '')

        try:
            try:
                audio = EasyID3(file_path)
            except ID3NoHeaderError:
                audio_id3 = ID3()
                audio_id3.save(file_path)
                audio = EasyID3(file_path)

            audio['title'] = title
            audio['artist'] = track.get('artists', ['Unknown'])
            audio['album'] = track.get('album', '') or 'Unknown Album'

            if track.get('track_number'):
                audio['tracknumber'] = str(track['track_number'])

            if track.get('release_date'):
                year = track['release_date'].split('-')[0]
                audio['date'] = year

            audio.save()
            logger.debug(f"Tagged: {artist} - {title}")

            # Embed album art from Spotify if available
            cover_url = track.get('cover_url')
            if cover_url:
                try:
                    audio_id3 = ID3(file_path)
                    req = urllib.request.Request(cover_url, headers={
                        "User-Agent": "Mozilla/5.0"
                    })
                    with urllib.request.urlopen(req, timeout=10) as response:
                        cover_data = response.read()

                    audio_id3.delall('APIC')
                    audio_id3.add(APIC(
                        encoding=3,
                        mime='image/jpeg',
                        type=3,
                        desc='Cover',
                        data=cover_data
                    ))
                    audio_id3.save()
                    logger.debug(f"Embedded album art for: {artist} - {title}")
                except Exception as e:
                    logger.warning(f"Failed to embed album art: {e}")

        except Exception as e:
            logger.error(f"Failed to tag file: {e}")

    def _fetch_lyrics(self, file_path: Path, track: Track) -> None:
        """Fetch and save lyrics for a track."""
        artist = track.get('artists', ['Unknown'])[0] if track.get('artists') else 'Unknown'
        title = track.get('name', '')

        if not title:
            return

        plain_lyrics, synced_lyrics = self.lyrics_provider.get_lyrics(title, artist)

        if synced_lyrics:
            # Save as .lrc file
            lrc_path = file_path.with_suffix('.lrc')
            lrc_path.write_text(synced_lyrics, encoding='utf-8')
            logger.info(f"Saved synced lyrics: {lrc_path.name}")

        if plain_lyrics:
            # Embed plain lyrics in the file
            try:
                from mutagen.id3 import ID3, USLT
                from mutagen.id3._util import ID3NoHeaderError

                try:
                    audio = ID3(file_path)
                except ID3NoHeaderError:
                    audio = ID3()
                    audio.save(file_path)
                    audio = ID3(file_path)

                # Remove existing lyrics
                audio.delall('USLT')

                # Add plain lyrics
                from mutagen.id3 import Encoding
                audio.add(USLT(
                    encoding=Encoding.UTF8,
                    lang='xxx',
                    desc='',
                    text=plain_lyrics
                ))
                audio.save()
                logger.debug(f"Embedded plain lyrics for: {artist} - {title}")
            except Exception as e:
                logger.warning(f"Failed to embed lyrics: {e}")

    def download_tracks(self, tracks: list[Track]) -> list[Path]:
        """Download multiple tracks.

        Returns:
            List of paths to successfully downloaded files
        """
        downloaded = []
        total = len(tracks)
        logger.info(f"Starting download of {total} tracks")

        for i, track in enumerate(tracks, 1):
            artist = track.get('artists', ['?'])[0] if track.get('artists') else '?'
            title = track.get('name', '?')
            logger.info(f"[{i}/{total}] Processing: {artist} - {title}")
            self._report_progress(f"[{i}/{total}] {artist} - {title}", i, total)

            path = self.download_track(track)
            if path:
                downloaded.append(path)
                logger.info(f"[{i}/{total}] Completed: {path.name}")
            else:
                logger.warning(f"[{i}/{total}] Failed: {artist} - {title}")

        logger.info(f"Download complete: {len(downloaded)}/{total} tracks")
        return downloaded
