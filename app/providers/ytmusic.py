"""
YouTube Music provider - fetches high-quality metadata from music.youtube.com.
"""

import logging
import re

from ytmusicapi import YTMusic

from .base import Track

logger = logging.getLogger(__name__)


class YTMusicProvider:
    """YouTube Music provider using ytmusicapi."""

    def __init__(self, language: str = "en", location: str = "US"):
        """
        Args:
            language: Language code (e.g. 'en')
            location: Location code (e.g. 'US')
        """
        self.yt = YTMusic(language=language, location=location)

    def supports_url(self, url: str) -> bool:
        """Check if URL is a YouTube Music URL."""
        return "music.youtube.com" in url

    def _extract_id(self, url: str) -> str | None:
        """Extract video/playlist ID from URL."""
        # Video ID: v=VIDEO_ID
        if "v=" in url:
            match = re.search(r"v=([a-zA-Z0-9_-]+)", url)
            if match:
                return match.group(1)

        # Playlist/Album ID: list=PLAYLIST_ID
        if "list=" in url:
            match = re.search(r"list=([a-zA-Z0-9_-]+)", url)
            if match:
                return match.group(1)

        return None

    def _parse_track_duration(self, duration: str | None) -> int | None:
        """Parse duration string (e.g. '3:45') to milliseconds."""
        if not duration:
            return None
        try:
            parts = duration.split(":")
            if len(parts) == 2:
                minutes, seconds = map(int, parts)
                return (minutes * 60 + seconds) * 1000
            elif len(parts) == 3:
                hours, minutes, seconds = map(int, parts)
                return (hours * 3600 + minutes * 60 + seconds) * 1000
        except ValueError:
            pass
        return None

    def get_track(self, url: str) -> Track | None:
        """Fetch single track from YouTube Music."""
        video_id = self._extract_id(url)
        if not video_id:
            logger.error(f"Could not extract video ID from URL: {url}")
            return None

        try:
            logger.info(f"Fetching track info from YouTube Music for ID: {video_id}")
            track_info = self.yt.get_song(video_id)
            video_details = track_info.get("videoDetails", {})

            title = video_details.get("title", "Unknown")
            author = video_details.get("author", "Unknown Artist")
            duration_seconds = int(video_details.get("lengthSeconds", 0))

            # Try to get album/thumbnail from microformat
            microformat = track_info.get("microformat", {}).get(
                "microformatDataRenderer", {}
            )
            thumbnails = microformat.get("thumbnail", {}).get("thumbnails", [])
            cover_url = thumbnails[-1]["url"] if thumbnails else None

            # Simple track construction
            track = Track(
                name=title,
                artists=[author],
                album=None,  # get_song often lacks album info directly
                cover_url=cover_url,
                duration_ms=duration_seconds * 1000 if duration_seconds else None,
                release_date=microformat.get("uploadDate", "")[:4]
                if microformat.get("uploadDate")
                else None,
                track_number=None,
                artist_ids=[],
                genre=None,
                source_url=f"https://www.youtube.com/watch?v={video_id}",  # Direct video link for yt-dlp
            )
            return track

        except Exception as e:
            logger.error(f"Error fetching track from YouTube Music: {e}")
            return None

    def get_playlist(self, url: str) -> tuple[str, list[Track]]:
        """Fetch playlist/album from YouTube Music."""
        playlist_id = self._extract_id(url)
        if not playlist_id:
            logger.error(f"Could not extract playlist ID from URL: {url}")
            return "Unknown Playlist", []

        try:
            # Check if it's an album or playlist
            is_album = playlist_id.startswith("OLAK5uy_")

            if is_album:
                logger.info(f"Fetching album info for ID: {playlist_id}")
                data = self.yt.get_album(playlist_id)
                name = data.get("title", "Unknown Album")
                tracks_data = data.get("tracks", [])
                album_name = name
                # Album specific: thumbnails usually in data['thumbnails']
                thumbnails = data.get("thumbnails", [])
                cover_url = thumbnails[-1]["url"] if thumbnails else None
                year = data.get("year")
            else:
                logger.info(f"Fetching playlist info for ID: {playlist_id}")
                data = self.yt.get_playlist(playlist_id)
                name = data.get("title", "Unknown Playlist")
                tracks_data = data.get("tracks", [])
                album_name = None
                thumbnails = data.get("thumbnails", [])
                cover_url = thumbnails[-1]["url"] if thumbnails else None
                year = None

            tracks: list[Track] = []

            for t in tracks_data:
                # Video ID is required for download
                t_video_id = t.get("videoId")
                if not t_video_id:
                    continue

                t_title = t.get("title", "Unknown")

                # Artists list
                t_artists = []
                if "artists" in t:
                    t_artists = [a.get("name") for a in t["artists"]]
                elif "artist" in t:  # internal API variance
                    t_artists = (
                        [t["artist"].get("name")]
                        if isinstance(t["artist"], dict)
                        else []
                    )

                if not t_artists:
                    t_artists = [t.get("uploader", "Unknown Artist")]  # fallback

                # Duration
                t_duration = t.get("duration") or t.get("length")  # "3:45" or "3:45"
                t_duration_ms = (
                    self._parse_track_duration(t_duration)
                    if isinstance(t_duration, str)
                    else None
                )
                if not t_duration_ms and "lengthSeconds" in t:
                    t_duration_ms = int(t["lengthSeconds"]) * 1000

                # Thumbnail (track specific or album fallback)
                t_thumbnails = t.get("thumbnails", [])
                t_cover = t_thumbnails[-1]["url"] if t_thumbnails else cover_url

                # Album name (track specific or album fallback)
                t_album = (
                    t.get("album", {}).get("name") if t.get("album") else album_name
                )

                track = Track(
                    name=t_title,
                    artists=t_artists,
                    album=t_album,
                    cover_url=t_cover,
                    duration_ms=t_duration_ms,
                    release_date=str(year) if year else None,
                    track_number=None,  # Could infer from index
                    artist_ids=[],
                    genre=None,
                    source_url=f"https://www.youtube.com/watch?v={t_video_id}",
                )
                tracks.append(track)

            logger.info(f"Found {len(tracks)} tracks in '{name}'")
            return name, tracks

        except Exception as e:
            logger.error(f"Error fetching playlist from YouTube Music: {e}")
            return "Unknown Playlist", []

    def search_video(self, artist: str, title: str) -> str | None:
        """Search for a video using YTMusic API and return video URL.

        This is more reliable than yt-dlp search which often gets 403 errors.

        Args:
            artist: Artist name
            title: Track title

        Returns:
            YouTube video URL or None if not found
        """
        try:
            query = f"{artist} {title}"
            logger.debug(f"Searching YTMusic for: {query}")

            results = self.yt.search(query, filter="songs", limit=5)

            for result in results:
                if result.get("videoId"):
                    video_id = result["videoId"]
                    result_title = result.get("title", "")
                    result_artists = [
                        a.get("name", "") for a in result.get("artists", [])
                    ]

                    logger.debug(
                        f"Found YTMusic result: '{result_title}' by {result_artists}"
                    )

                    # Simple validation - just check if artist name appears in result
                    artist_match = any(
                        artist.lower() in ra.lower() for ra in result_artists
                    )
                    title_match = (
                        title.lower() in result_title.lower()
                        or result_title.lower() in title.lower()
                    )

                    if artist_match or title_match:
                        video_url = f"https://www.youtube.com/watch?v={video_id}"
                        logger.info(f"Found matching video via YTMusic: {video_url}")
                        return video_url

            logger.debug(f"No good YTMusic match found for: {artist} - {title}")
            return None

        except Exception as e:
            logger.debug(f"YTMusic search failed: {e}")
            return None
