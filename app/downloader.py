"""
Core download logic - downloads tracks using yt-dlp and applies metadata.
"""

import logging
import re
import unicodedata
import urllib.request
from pathlib import Path
from typing import Any, Callable

import yt_dlp
from mutagen.easyid3 import EasyID3
from mutagen.id3 import ID3
from mutagen.id3._frames import APIC, USLT
from mutagen.id3._specs import Encoding
from mutagen.id3._util import ID3NoHeaderError

from .lyrics import LyricsProvider
from .providers.base import Track
from .providers.youtube import (
    CookieExpiredError,
    get_cookie_error_message,
    is_cookie_error,
)
from .providers.ytmusic import YTMusicProvider

logger = logging.getLogger(__name__)

# Common user agent for requests
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"

# Full browser headers to avoid detection
BROWSER_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.youtube.com/",
    "Sec-Ch-Ua": '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}


class TrackDownloader:
    """Downloads tracks from YouTube and applies metadata."""

    def __init__(
        self,
        output_dir: Path,
        progress_callback: Callable[[str, int, int], None] | None = None,
        cookies: str | None = None,
    ):
        """
        Args:
            output_dir: Directory to save downloaded files
            progress_callback: Optional callback(status, current, total) for progress updates
            cookies: Path to cookies file or browser name
        """
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.progress_callback = progress_callback
        self.cookies = cookies
        self.lyrics_provider = LyricsProvider()
        self._current_track_info: dict[str, str] = {}  # For progress hook context

    def _sanitize_filename(self, name: str | None) -> str:
        """Create safe filename (ASCII with underscores)."""
        if not name:
            return "Unknown"

        # Normalize unicode and convert to ASCII
        name = (
            unicodedata.normalize("NFKD", name)
            .encode("ascii", "ignore")
            .decode("ascii")
        )
        # Remove invalid filesystem characters
        name = re.sub(r'[<>:"/\\|?*\[\]\']', "", name)
        # Replace spaces with underscores
        name = re.sub(r"\s+", "_", name)
        # Remove multiple underscores
        name = re.sub(r"_+", "_", name)
        # Trim underscores from ends
        name = name.strip("_")
        return name[:180]  # Limit length

    def _report_progress(self, status: str, current: int = 0, total: int = 0) -> None:
        """Report progress via callback if set."""
        if self.progress_callback:
            self.progress_callback(status, current, total)

    def _yt_dlp_progress_hook(self, d: dict[str, Any]) -> None:
        """Progress hook for yt-dlp to report download progress."""
        status = d.get("status", "")
        artist = self._current_track_info.get("artist", "Unknown")
        title = self._current_track_info.get("title", "Unknown")

        if status == "downloading":
            downloaded = d.get("downloaded_bytes", 0)
            total = d.get("total_bytes") or d.get("total_bytes_estimate", 0)
            if total > 0:
                percent = int((downloaded / total) * 100)
                speed = d.get("speed", 0)
                speed_str = f"{speed / 1024:.1f} KB/s" if speed else "calculating..."
                self._report_progress(
                    f"Downloading {artist} - {title}: {percent}% ({speed_str})"
                )
                logger.debug(f"Download progress: {percent}% for {artist} - {title}")
        elif status == "finished":
            filename = d.get("filename", "unknown")
            logger.info(f"Download finished: {filename}")
            self._report_progress(f"Processing: {artist} - {title}")
        elif status == "error":
            logger.error(f"Download error in progress hook for {artist} - {title}")

    def _yt_dlp_postprocessor_hook(self, d: dict[str, Any]) -> None:
        """Postprocessor hook for yt-dlp to log postprocessing steps."""
        status = d.get("status", "")
        postprocessor = d.get("postprocessor", "unknown")
        artist = self._current_track_info.get("artist", "Unknown")
        title = self._current_track_info.get("title", "Unknown")

        if status == "started":
            logger.debug(
                f"Postprocessor '{postprocessor}' started for {artist} - {title}"
            )
        elif status == "finished":
            logger.debug(
                f"Postprocessor '{postprocessor}' finished for {artist} - {title}"
            )

    def _check_missing_metadata(self, file_path: Path) -> dict:
        """Check what metadata is missing from an existing file.

        Returns:
            Dict with keys: album_art, lyrics, lrc_file - True if missing
        """
        missing = {"album_art": False, "lyrics": False, "lrc_file": False}

        try:
            try:
                audio = ID3(file_path)
            except ID3NoHeaderError:
                # No ID3 header means everything is missing
                return {"album_art": True, "lyrics": True, "lrc_file": True}

            # Check for album art (APIC frame)
            if not audio.getall("APIC"):
                missing["album_art"] = True

            # Check for embedded lyrics (USLT frame)
            if not audio.getall("USLT"):
                missing["lyrics"] = True

            # Check for .lrc file
            lrc_path = file_path.with_suffix(".lrc")
            if not lrc_path.exists():
                missing["lrc_file"] = True

        except Exception as e:
            logger.warning(f"Failed to check metadata: {e}")
            # If we can't read, assume nothing is missing to avoid breaking things

        return missing

    def _build_yt_dlp_opts(self, output_template: str) -> dict[str, Any]:
        """Build yt-dlp options dictionary.

        Args:
            output_template: Output path template for downloaded files

        Returns:
            Dictionary of yt-dlp options
        """
        opts: dict[str, Any] = {
            # Audio extraction settings
            "format": "bestaudio/best",
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "0",  # Best quality
                }
            ],
            # Output settings
            "outtmpl": output_template,
            "noplaylist": True,
            # Logging and progress
            "quiet": True,
            "no_warnings": True,
            "progress_hooks": [self._yt_dlp_progress_hook],
            "postprocessor_hooks": [self._yt_dlp_postprocessor_hook],
            # Network settings - full browser headers to avoid detection
            "http_headers": BROWSER_HEADERS,
            # Enable external JS solver for YouTube (required since yt-dlp 2025.11.12)
            "remote_components": ["ejs:github"],
            # Rate limiting to avoid being blocked
            "sleep_interval": 1,
            "max_sleep_interval": 3,
            # Retry settings
            "retries": 5,
            "fragment_retries": 5,
            "file_access_retries": 3,
            # Additional options to help with problematic downloads
            "nocheckcertificate": True,
            "continuedl": True,
            # Mark of the web to avoid some restrictions
            "mtime": True,
        }

        # Add cookies if configured
        if self.cookies:
            # Determine if this is a file path or browser name
            # File paths contain slashes or end with .txt/.json
            is_file_path = (
                "/" in self.cookies
                or "\\" in self.cookies
                or self.cookies.endswith(".txt")
                or self.cookies.endswith(".json")
            )

            if is_file_path:
                cookie_path = Path(self.cookies)
                if cookie_path.exists():
                    opts["cookiefile"] = self.cookies
                    logger.debug(f"Using cookies file: {self.cookies}")
                else:
                    logger.warning(f"Cookies file not found: {self.cookies}")
            else:
                # Treat as browser name (chrome, firefox, safari, etc.)
                opts["cookiesfrombrowser"] = (self.cookies,)
                logger.debug(f"Using cookies from browser: {self.cookies}")

        return opts

    def _try_download(
        self,
        artist: str,
        track_name: str,
        search_term: str,
        artist_dir: Path,
        safe_title: str,
        output_path: Path,
    ) -> Path | None:
        """Attempt to download a track with the given search term.

        Returns:
            Path to downloaded file, or None if failed
        """
        # Build yt-dlp options
        output_template = str(artist_dir / f"{safe_title}.%(ext)s")
        ydl_opts = self._build_yt_dlp_opts(output_template)

        logger.info(
            f"Initializing yt-dlp for: {artist} - {track_name} "
            f"(cookies={'yes' if self.cookies else 'no'})"
        )
        logger.debug(
            f"yt-dlp options: format={ydl_opts['format']}, output={output_template}"
        )

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                logger.debug(f"Extracting info for: {search_term}")

                # Extract info first to get metadata
                try:
                    info = ydl.extract_info(search_term, download=False)
                    if info:
                        # Handle search results (playlist-like 'entries')
                        if "entries" in info:
                            entries = info.get("entries", [])
                            if entries:
                                video_info = entries[0]
                                video_title = video_info.get("title", "Unknown")
                                video_id = video_info.get("id", "Unknown")
                                duration = video_info.get("duration", 0)
                                logger.info(
                                    f"Found video via search: '{video_title}' (ID: {video_id}, duration: {duration}s)"
                                )
                            else:
                                logger.warning(
                                    f"No entries found for search: {search_term}"
                                )
                        else:
                            # Direct video info
                            video_title = info.get("title", "Unknown")
                            video_id = info.get("id", "Unknown")
                            duration = info.get("duration", 0)
                            logger.info(
                                f"Found video: '{video_title}' (ID: {video_id}, duration: {duration}s)"
                            )
                except Exception as extract_err:
                    logger.warning(
                        f"Failed to extract info before download: {extract_err}"
                    )
                    # Continue anyway, download might still work

                # Perform the download
                logger.info(f"Starting download for: {artist} - {track_name}")
                error_code = ydl.download([search_term])

                if error_code != 0:
                    logger.error(
                        f"yt-dlp returned error code {error_code} for {artist} - {track_name}"
                    )
                    self._report_progress(f"Failed: yt-dlp error code {error_code}")
                    return None

                logger.info(
                    f"Download completed successfully for: {artist} - {track_name}"
                )

        except yt_dlp.DownloadError as e:
            error_msg = str(e)
            logger.error(
                f"yt-dlp DownloadError for {artist} - {track_name}: {error_msg}"
            )
            if is_cookie_error(error_msg):
                raise CookieExpiredError(get_cookie_error_message()) from e
            if "HTTP Error 403" in error_msg:
                logger.warning(
                    f"Got 403 error for {artist} - {track_name}, will try fallback"
                )
                return None
            self._report_progress(f"Failed: {error_msg[:100]}")
            return None
        except yt_dlp.ExtractorError as e:
            error_msg = str(e)
            logger.error(
                f"yt-dlp ExtractorError for {artist} - {track_name}: {error_msg}"
            )
            if is_cookie_error(error_msg):
                raise CookieExpiredError(get_cookie_error_message()) from e
            if "HTTP Error 403" in error_msg:
                logger.warning(
                    f"Got 403 error for {artist} - {track_name}, will try fallback"
                )
                return None
            self._report_progress(f"Failed: {error_msg[:100]}")
            return None
        except yt_dlp.PostProcessingError as e:
            error_msg = str(e)
            logger.error(
                f"yt-dlp PostProcessingError for {artist} - {track_name}: {error_msg}"
            )
            self._report_progress(f"Failed: Post-processing error - {error_msg[:80]}")
            return None
        except CookieExpiredError:
            raise
        except Exception as e:
            error_msg = str(e)
            logger.error(
                f"Unexpected error downloading {artist} - {track_name}: "
                f"{type(e).__name__}: {error_msg}"
            )
            if is_cookie_error(error_msg):
                raise CookieExpiredError(get_cookie_error_message()) from e
            self._report_progress(f"Failed: {error_msg[:100]}")
            return None

        # Find the downloaded file
        logger.debug(f"Looking for downloaded file at: {output_path}")
        downloaded_path = None
        if output_path.exists():
            downloaded_path = output_path
            logger.debug(f"Found expected file: {output_path}")
        else:
            # Try other extensions (yt-dlp might not have converted yet)
            logger.debug("Expected .mp3 not found, checking other extensions...")
            for ext in [".mp3", ".m4a", ".opus", ".webm"]:
                alt_path = artist_dir / f"{safe_title}{ext}"
                if alt_path.exists():
                    downloaded_path = alt_path
                    logger.debug(f"Found alternative file: {alt_path}")
                    break

        if not downloaded_path:
            logger.error(f"Could not find downloaded file for: {artist} - {track_name}")
            # List directory contents for debugging
            try:
                dir_contents = list(artist_dir.iterdir())
                logger.debug(f"Directory contents of {artist_dir}: {dir_contents}")
            except Exception as e:
                logger.debug(f"Failed to list directory: {e}")
            return None

        return downloaded_path

    def download_track(self, track: Track) -> Path | None:
        """Download a single track using yt-dlp Python library.

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

        # Store current track info for progress hooks
        self._current_track_info = {"artist": artist, "title": track_name}

        artist_dir = self.output_dir / safe_artist
        artist_dir.mkdir(parents=True, exist_ok=True)

        output_path = artist_dir / f"{safe_title}.mp3"

        # Check if file already exists
        if output_path.exists():
            # Check for missing metadata and update if needed
            missing = self._check_missing_metadata(output_path)

            if any(missing.values()):
                missing_items = [k for k, v in missing.items() if v]
                logger.info(
                    f"Updating metadata ({', '.join(missing_items)}): {safe_artist}/{safe_title}.mp3"
                )
                self._report_progress(
                    f"Updating metadata: {safe_artist}/{safe_title}.mp3"
                )

                # Update album art if missing (via _tag_file which handles album art)
                if missing.get("album_art"):
                    self._tag_file(output_path, track)

                # Update lyrics if missing (embedded or .lrc)
                if missing.get("lyrics") or missing.get("lrc_file"):
                    self._fetch_lyrics(output_path, track)

                logger.info(f"Updated metadata for: {safe_artist}/{safe_title}.mp3")
            else:
                logger.info(
                    f"Already exists (complete): {safe_artist}/{safe_title}.mp3"
                )
                self._report_progress(f"Already exists: {safe_artist}/{safe_title}.mp3")

            return output_path

        # Try YTMusic search first, then fallback to regular YouTube search
        self._report_progress(f"Searching YouTube Music: {artist} - {track_name}")

        try:
            ytm = YTMusicProvider()
            yt_url = ytm.search_video(artist, track_name)

            if yt_url:
                logger.info(f"Found YTMusic URL: {yt_url}")
                downloaded_path = self._try_download(
                    artist, track_name, yt_url, artist_dir, safe_title, output_path
                )
                if downloaded_path:
                    return self._finalize_download(
                        downloaded_path, track, artist, track_name
                    )

            logger.warning(f"No YTMusic result found for: {artist} - {track_name}")
        except Exception as e:
            logger.warning(f"YTMusic search failed: {e}")

        # Fallback to regular YouTube search
        logger.info(f"Trying regular YouTube search for: {artist} - {track_name}")
        self._report_progress(f"Trying YouTube search: {artist} - {track_name}")

        search_term = f"ytsearch1:{artist} {track_name}"
        downloaded_path = self._try_download(
            artist, track_name, search_term, artist_dir, safe_title, output_path
        )

        if not downloaded_path:
            logger.error(f"Failed to download: {artist} - {track_name}")
            return None

        return self._finalize_download(downloaded_path, track, artist, track_name)

    def _finalize_download(
        self, downloaded_path: Path, track: Track, artist: str, track_name: str
    ) -> Path | None:
        """Finalize the download by applying metadata and validating the file."""
        # Check file size to detect failed/empty downloads
        file_size = downloaded_path.stat().st_size
        if file_size < 1024:  # Less than 1KB is definitely wrong
            logger.error(
                f"Downloaded file is too small ({file_size} bytes), likely a failed download: {downloaded_path}"
            )
            # Clean up the invalid file
            try:
                downloaded_path.unlink()
                logger.debug(f"Removed invalid file: {downloaded_path}")
            except Exception as e:
                logger.warning(f"Failed to remove invalid file: {e}")
            return None

        logger.info(f"Downloaded file size: {file_size / 1024 / 1024:.2f} MB")

        # Apply metadata and album art
        logger.debug(f"Applying metadata tags to: {downloaded_path}")
        self._tag_file(downloaded_path, track)

        # Fetch and save lyrics
        logger.debug(f"Fetching lyrics for: {artist} - {track_name}")
        self._fetch_lyrics(downloaded_path, track)

        logger.info(f"Track processing complete: {downloaded_path}")
        return downloaded_path

    def _tag_file(self, file_path: Path, track: Track) -> None:
        """Tag audio file with metadata and album art."""

        artist = (
            track.get("artists", ["Unknown"])[0] if track.get("artists") else "Unknown"
        )
        title = track.get("name", "")

        try:
            try:
                audio = EasyID3(file_path)
            except ID3NoHeaderError:
                audio_id3 = ID3()
                audio_id3.save(file_path)
                audio = EasyID3(file_path)

            audio["title"] = title
            audio["artist"] = track.get("artists", ["Unknown"])
            audio["album"] = track.get("album", "") or "Unknown Album"

            if track.get("track_number"):
                audio["tracknumber"] = str(track["track_number"])

            if track.get("release_date"):
                year = track["release_date"].split("-")[0]
                audio["date"] = year

            if track.get("genre"):
                audio["genre"] = track["genre"]

            audio.save()
            logger.debug(f"Tagged: {artist} - {title}")

            # Embed album art from Spotify if available
            cover_url = track.get("cover_url")
            if cover_url:
                try:
                    audio_id3 = ID3(file_path)
                    req = urllib.request.Request(
                        cover_url, headers={"User-Agent": "Mozilla/5.0"}
                    )
                    with urllib.request.urlopen(req, timeout=10) as response:
                        cover_data = response.read()

                    audio_id3.delall("APIC")
                    audio_id3.add(
                        APIC(
                            encoding=3,
                            mime="image/jpeg",
                            type=3,
                            desc="Cover",
                            data=cover_data,
                        )
                    )
                    audio_id3.save()
                    logger.debug(f"Embedded album art for: {artist} - {title}")
                except Exception as e:
                    logger.warning(f"Failed to embed album art: {e}")

        except Exception as e:
            logger.error(f"Failed to tag file: {e}")

    def _fetch_lyrics(self, file_path: Path, track: Track) -> None:
        """Fetch and save lyrics for a track."""
        artist = (
            track.get("artists", ["Unknown"])[0] if track.get("artists") else "Unknown"
        )
        title = track.get("name", "")

        if not title:
            return

        plain_lyrics, synced_lyrics = self.lyrics_provider.get_lyrics(title, artist)

        if synced_lyrics:
            # Save as .lrc file
            lrc_path = file_path.with_suffix(".lrc")
            lrc_path.write_text(synced_lyrics, encoding="utf-8")
            logger.info(f"Saved synced lyrics: {lrc_path.name}")

        if plain_lyrics:
            # Embed plain lyrics in the file
            try:
                try:
                    audio = ID3(file_path)
                except ID3NoHeaderError:
                    audio = ID3()
                    audio.save(file_path)
                    audio = ID3(file_path)

                # Remove existing lyrics
                audio.delall("USLT")

                # Add plain lyrics

                audio.add(
                    USLT(encoding=Encoding.UTF8, lang="xxx", desc="", text=plain_lyrics)
                )
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
            artist = track.get("artists", ["?"])[0] if track.get("artists") else "?"
            title = track.get("name", "?")
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
