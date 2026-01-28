"""
YouTube provider - fetches playlists/tracks from YouTube/YouTube Music.
"""

import json
import re
import subprocess
import unicodedata
from pathlib import Path

from .base import Track
from .spotify import SpotifyProvider


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

    def _get_cookie_args(self) -> list[str]:
        """Build cookie arguments for yt-dlp."""
        if not self.cookies:
            return []

        if Path(self.cookies).exists():
            return ["--cookies", self.cookies]
        return ["--cookies-from-browser", self.cookies]

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

    def _normalize(self, text: str) -> set[str]:
        """Normalize text for comparison - lowercase, remove punctuation, split into words."""

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
        """Fetch single video info from YouTube URL."""
        cmd = ["yt-dlp", *self._get_cookie_args(), "--dump-json", "--no-download", url]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            data = json.loads(result.stdout)

            title = data.get("title", "")
            uploader = data.get("uploader") or data.get("channel", "Unknown")
            artist, track_title = self._parse_video_title(title, uploader)

            # Try to get Spotify metadata
            spotify_track = self._enrich_with_spotify(artist, track_title)
            if spotify_track:
                # Add source URL for direct download
                spotify_track["source_url"] = url
                return spotify_track

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
        except Exception:
            return None

    def get_playlist(self, url: str) -> tuple[str, list[Track]]:
        """Fetch playlist from YouTube URL."""
        cmd = [
            "yt-dlp",
            *self._get_cookie_args(),
            "--dump-json",
            "--flat-playlist",
            "--no-download",
            url,
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            entries = [
                json.loads(line) for line in result.stdout.strip().split("\n") if line
            ]

            if not entries:
                return "YouTube Playlist", []

            playlist_name = entries[0].get("playlist_title", "YouTube Playlist")
            tracks: list[Track] = []

            for entry in entries:
                video_title = entry.get("title", "")
                video_uploader = entry.get("uploader") or entry.get(
                    "channel", "Unknown"
                )
                video_url = entry.get("url") or entry.get("webpage_url", "")

                if not video_title:
                    continue

                artist, title = self._parse_video_title(video_title, video_uploader)

                # Try Spotify enrichment
                spotify_track = self._enrich_with_spotify(artist, title)
                if spotify_track:
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

            return playlist_name, tracks

        except subprocess.CalledProcessError as e:
            error_msg = e.stderr.strip() if e.stderr else str(e)
            raise RuntimeError(f"Failed to fetch playlist: {error_msg}")
