"""
YouTube provider - fetches playlists/tracks from YouTube/YouTube Music.
"""

import logging
import re
import unicodedata
from pathlib import Path
from typing import Any

import yt_dlp

from .base import Track
from .spotify import SpotifyProvider

logger = logging.getLogger(__name__)

# Common user agent for requests
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"


class YouTubeProvider:
    """YouTube/YouTube Music playlist/video provider using yt-dlp."""

    def __init__(
        self,
        cookies: str | None = None,
        spotify_provider: SpotifyProvider | None = None,
    ):
        """
        Args:
            cookies: Path to cookies.txt or browser name (chrome, firefox, etc.)
            spotify_provider: Optional SpotifyProvider for metadata enrichment
        """
        self.cookies = cookies
        self.spotify_provider = spotify_provider

    def configure(
        self,
        cookies: str | None = None,
        spotify_provider: SpotifyProvider | None = None,
    ) -> None:
        """Configure provider settings."""
        if cookies is not None:
            self.cookies = cookies
        if spotify_provider is not None:
            self.spotify_provider = spotify_provider

    def supports_url(self, url: str) -> bool:
        """Check if URL is a YouTube URL."""
        return any(
            domain in url for domain in ["youtube.com", "youtu.be", "music.youtube.com"]
        )

    def _build_yt_dlp_opts(self, playlist: bool = False) -> dict[str, Any]:
        """Build yt-dlp options for extracting info."""
        opts: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": playlist,
            "skip_download": True,
            "http_headers": {
                "User-Agent": USER_AGENT,
                "Referer": "https://www.youtube.com/",
            },
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

    def _parse_video_title(self, title: str, uploader: str) -> tuple[str, str]:
        """Parse artist and title from video title.

        Returns:
            Tuple of (artist, title)
        """
        artist = uploader
        track_title = title

        # Try "Artist - Title" format
        if " - " in title:
            parts = title.split(" - ", 1)
            artist = parts[0].strip()
            track_title = parts[1].strip()

        # Clean up title (remove common suffixes)
        for suffix in [
            "(Official Video)",
            "(Official Audio)",
            "(Lyric Video)",
            "(Official Music Video)",
            "[Official Video]",
            "[Official Audio]",
            "| Official Video",
            "| Official Audio",
            "(Audio)",
            "[Audio]",
            "(Full Video)",
            "(HD)",
            "(4K)",
            "4K UHD",
            "HD Video",
        ]:
            track_title = track_title.replace(suffix, "").strip()

        return artist, track_title

    def _normalize(self, text: str | None) -> set[str]:
        """Normalize text for comparison - lowercase, remove punctuation, split into words."""
        if not text:
            return set()

        # Normalize unicode
        text = unicodedata.normalize("NFKD", text)
        # Lowercase and remove non-alphanumeric
        text = re.sub(r"[^\w\s]", " ", text.lower())
        # Split into words, filter short ones
        return {w for w in text.split() if len(w) > 2}

    def _validate_match(
        self, youtube_artist: str, youtube_title: str, spotify_track: Track
    ) -> bool:
        """Check if Spotify result actually matches the YouTube video."""
        # Get Spotify artist and title
        spotify_artist = (
            spotify_track.get("artists", [""])[0]
            if spotify_track.get("artists")
            else ""
        )
        spotify_title = spotify_track.get("name", "")

        # Normalize all text
        yt_artist_words = self._normalize(youtube_artist)
        yt_title_words = self._normalize(youtube_title)
        sp_artist_words = self._normalize(spotify_artist)
        sp_title_words = self._normalize(spotify_title)

        # Check artist overlap (at least one word should match)
        artist_overlap = bool(yt_artist_words & sp_artist_words)

        # Check title overlap (at least 30% of Spotify title words should be in YouTube title)
        if sp_title_words:
            title_overlap_ratio = len(sp_title_words & yt_title_words) / len(
                sp_title_words
            )
        else:
            title_overlap_ratio = 0

        # Consider it a match if artist matches AND title has some overlap
        # Or if title has very strong overlap (>50%)
        return (
            artist_overlap and title_overlap_ratio >= 0.3
        ) or title_overlap_ratio >= 0.5

    def _enrich_with_spotify(self, artist: str, title: str) -> Track | None:
        """Try to find matching track on Spotify for richer metadata."""
        if not self.spotify_provider:
            return None

        try:
            # Try artist + title first
            track = self.spotify_provider.search_track(f"{artist} {title}")
            if track and self._validate_match(artist, title, track):
                return track

            # Fallback to just title
            track = self.spotify_provider.search_track(title)
            if track and self._validate_match(artist, title, track):
                return track

            return None
        except Exception:
            return None

    def get_track(self, url: str) -> Track | None:
        """Fetch single video info from YouTube URL using yt-dlp Python library."""
        logger.info(f"Fetching track info from: {url}")

        opts = self._build_yt_dlp_opts(playlist=False)
        opts["noplaylist"] = True

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                logger.debug(f"Extracting info for URL: {url}")
                data = ydl.extract_info(url, download=False)

                if not data:
                    logger.error(f"No data returned from yt-dlp for URL: {url}")
                    return None

                title = data.get("title", "")
                uploader = data.get("uploader") or data.get("channel", "Unknown")
                video_id = data.get("id", "unknown")

                logger.info(f"Found video: '{title}' by {uploader} (ID: {video_id})")

                artist, track_title = self._parse_video_title(title, uploader)
                logger.debug(f"Parsed as: artist='{artist}', title='{track_title}'")

                # Try to get Spotify metadata
                spotify_track = self._enrich_with_spotify(artist, track_title)
                if spotify_track:
                    logger.info(
                        f"Enriched with Spotify metadata for: {artist} - {track_title}"
                    )
                    # Add source URL for direct download
                    spotify_track["source_url"] = url
                    return spotify_track

                logger.debug("No Spotify match found, using YouTube metadata")

                # Return YouTube-based track
                return Track(
                    name=track_title,
                    artists=[artist],
                    album=None,
                    cover_url=data.get("thumbnail"),
                    duration_ms=int(data.get("duration", 0) * 1000)
                    if data.get("duration")
                    else None,
                    release_date=data.get("upload_date"),
                    track_number=None,
                    artist_ids=[],
                    source_url=url,
                )

        except yt_dlp.DownloadError as e:
            logger.error(f"yt-dlp DownloadError fetching track info for {url}: {e}")
            return None
        except yt_dlp.ExtractorError as e:
            logger.error(f"yt-dlp ExtractorError fetching track info for {url}: {e}")
            return None
        except Exception as e:
            logger.error(
                f"Unexpected error fetching track info for {url}: "
                f"{type(e).__name__}: {e}"
            )
            return None

    def get_playlist(self, url: str) -> tuple[str, list[Track]]:
        """Fetch playlist from YouTube URL using yt-dlp Python library."""
        logger.info(f"Fetching playlist info from: {url}")

        opts = self._build_yt_dlp_opts(playlist=True)

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                logger.debug(f"Extracting playlist info for URL: {url}")
                data = ydl.extract_info(url, download=False)

                if not data:
                    logger.error(
                        f"No data returned from yt-dlp for playlist URL: {url}"
                    )
                    return "YouTube Playlist", []

                # Get playlist title
                playlist_name = data.get("title") or data.get(
                    "playlist_title", "YouTube Playlist"
                )
                entries = data.get("entries", [])

                if not entries:
                    logger.warning(f"No entries found in playlist: {playlist_name}")
                    return playlist_name, []

                logger.info(
                    f"Found playlist '{playlist_name}' with {len(entries)} entries"
                )

                tracks: list[Track] = []

                for i, entry in enumerate(entries, 1):
                    if not entry:
                        continue

                    video_title = entry.get("title", "")
                    video_uploader = entry.get("uploader") or entry.get(
                        "channel", "Unknown"
                    )
                    video_url = entry.get("url") or entry.get("webpage_url", "")

                    if not video_title:
                        logger.debug(f"Skipping entry {i} with no title")
                        continue

                    artist, title = self._parse_video_title(video_title, video_uploader)
                    logger.debug(f"[{i}/{len(entries)}] Parsed: '{artist}' - '{title}'")

                    # Try Spotify enrichment
                    spotify_track = self._enrich_with_spotify(artist, title)
                    if spotify_track:
                        logger.debug(f"Enriched with Spotify: {artist} - {title}")
                        spotify_track["source_url"] = video_url
                        tracks.append(spotify_track)
                    else:
                        tracks.append(
                            Track(
                                name=title,
                                artists=[artist],
                                album=None,
                                cover_url=entry.get("thumbnail"),
                                duration_ms=int(entry.get("duration", 0) * 1000)
                                if entry.get("duration")
                                else None,
                                release_date=None,
                                track_number=None,
                                artist_ids=[],
                                source_url=video_url,
                            )
                        )

                logger.info(
                    f"Processed {len(tracks)} tracks from playlist '{playlist_name}'"
                )
                return playlist_name, tracks

        except yt_dlp.DownloadError as e:
            logger.error(f"yt-dlp DownloadError fetching playlist for {url}: {e}")
            raise RuntimeError(f"Failed to fetch playlist: {e}")
        except yt_dlp.ExtractorError as e:
            logger.error(f"yt-dlp ExtractorError fetching playlist for {url}: {e}")
            raise RuntimeError(f"Failed to fetch playlist: {e}")
        except Exception as e:
            logger.error(
                f"Unexpected error fetching playlist for {url}: {type(e).__name__}: {e}"
            )
            raise RuntimeError(f"Failed to fetch playlist: {e}")
