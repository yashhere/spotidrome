"""
Lyrics provider - fetches lyrics from multiple sources.
"""

import json
import logging
import re
import urllib.parse
import urllib.request
from pathlib import Path

from langdetect import LangDetectException, detect
from mediafile import MediaFile

from .tagging_utils import format_display_artist, normalize_artists

logger = logging.getLogger(__name__)


def detect_language(text: str) -> str | None:
    """Detect language of text using langdetect library.

    Returns ISO 639-1 language code or None if detection fails.
    """
    if not text or len(text.strip()) < 20:
        return None
    try:
        return detect(text)
    except LangDetectException:
        return None


class LyricsProvider:
    """Fetches lyrics from multiple sources."""

    # ISO 639-1 language codes for filtering
    ALLOWED_LANGUAGES = {"en", "hi"}  # English and Hindi only

    def __init__(self, allowed_languages: set[str] | None = None):
        self.user_agent = (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        )
        self.mxm_token = None
        self.allowed_languages = allowed_languages or self.ALLOWED_LANGUAGES

    def get_lyrics(self, title: str, artist: str) -> tuple[str | None, str | None]:
        """
        Fetch lyrics from available sources.
        Returns (plain_lyrics, synced_lyrics_lrc) - either can be None.
        """
        logger.debug(f"Fetching lyrics for: {artist} - {title}")

        # Try Musixmatch first (best coverage, has synced lyrics)
        synced, plain = self._fetch_musixmatch(title, artist)
        if synced or plain:
            lyrics_to_check = plain or synced or ""
            if self._is_allowed_language(lyrics_to_check):
                logger.debug(
                    f"Found lyrics via Musixmatch (synced={bool(synced)}, plain={bool(plain)})"
                )
                return plain, synced

        # Try LRCLIB (has synced lyrics)
        synced, plain = self._fetch_lrclib(title, artist)
        if synced or plain:
            lyrics_to_check = plain or synced or ""
            if self._is_allowed_language(lyrics_to_check):
                logger.debug(
                    f"Found lyrics via LRCLIB (synced={bool(synced)}, plain={bool(plain)})"
                )
                return plain, synced

        # Fallback to lyrics.ovh for plain lyrics
        plain = self._fetch_lyrics_ovh(title, artist)
        if plain:
            if self._is_allowed_language(plain):
                logger.debug("Found plain lyrics via lyrics.ovh")
                return plain, None

        logger.debug("No lyrics found from any source (or filtered by language)")
        return None, None

    def _is_allowed_language(self, lyrics: str | None) -> bool:
        """Check if lyrics are in an allowed language."""
        if not lyrics:
            return True

        # Strip LRC timestamps if present
        clean_text = re.sub(r"\[\d{2}:\d{2}[.:]\d{2,3}\]", "", lyrics)
        detected_lang = detect_language(clean_text)

        if detected_lang:
            if detected_lang in self.allowed_languages:
                logger.debug(f"Lyrics language '{detected_lang}' is allowed")
                return True
            else:
                logger.debug(
                    f"Lyrics language '{detected_lang}' not in {self.allowed_languages}"
                )
                return False

        # Could not detect language, allow it
        logger.debug("Could not detect lyrics language, allowing")
        return True

    def _fetch_lrclib(self, title: str, artist: str) -> tuple[str | None, str | None]:
        """Fetch from LRCLIB (free, has synced lyrics)."""
        try:
            query = urllib.parse.urlencode({"track_name": title, "artist_name": artist})
            url = f"https://lrclib.net/api/search?{query}"

            req = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
            with urllib.request.urlopen(req, timeout=10) as response:
                results = json.loads(response.read())

            if results:
                best = results[0]
                return best.get("syncedLyrics"), best.get("plainLyrics")
        except Exception as e:
            logger.debug(f"LRCLIB failed: {e}")
        return None, None

    def _fetch_lyrics_ovh(self, title: str, artist: str) -> str | None:
        """Fetch from lyrics.ovh (plain lyrics only)."""
        try:
            artist_enc = urllib.parse.quote(artist)
            title_enc = urllib.parse.quote(title)
            url = f"https://api.lyrics.ovh/v1/{artist_enc}/{title_enc}"

            req = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read())
                return data.get("lyrics")
        except Exception as e:
            logger.debug(f"lyrics.ovh failed: {e}")
        return None

    def _get_mxm_token(self) -> str | None:
        """Get Musixmatch user token."""
        if self.mxm_token:
            return self.mxm_token

        try:
            url = "https://apic-desktop.musixmatch.com/ws/1.1/token.get?app_id=web-desktop-app-v1.0"
            req = urllib.request.Request(
                url,
                headers={"User-Agent": self.user_agent, "Cookie": "x-mxm-token-guid="},
            )
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read())
                self.mxm_token = (
                    data.get("message", {}).get("body", {}).get("user_token")
                )
                return self.mxm_token
        except Exception as e:
            logger.debug(f"Failed to get Musixmatch token: {e}")
        return None

    def _fetch_musixmatch(
        self, title: str, artist: str
    ) -> tuple[str | None, str | None]:
        """Fetch from Musixmatch (good coverage, has synced lyrics)."""
        token = self._get_mxm_token()
        if not token:
            return None, None

        try:
            # Search for track
            query = urllib.parse.urlencode(
                {
                    "q_track": title,
                    "q_artist": artist,
                    "app_id": "web-desktop-app-v1.0",
                    "usertoken": token,
                    "format": "json",
                }
            )
            search_url = (
                f"https://apic-desktop.musixmatch.com/ws/1.1/track.search?{query}"
            )

            req = urllib.request.Request(
                search_url, headers={"User-Agent": self.user_agent}
            )
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read())

            track_list = data.get("message", {}).get("body", {}).get("track_list", [])
            if not track_list:
                return None, None

            # Get lyrics from first track with lyrics
            for item in track_list:
                track = item.get("track", {})
                track_id = track.get("track_id")
                if not track_id:
                    continue

                synced_lyrics, plain_lyrics = self._fetch_mxm_lyrics(track_id, token)
                if synced_lyrics or plain_lyrics:
                    return synced_lyrics, plain_lyrics

            return None, None

        except Exception as e:
            logger.debug(f"Musixmatch failed: {e}")
        return None, None

    def _fetch_mxm_lyrics(
        self, track_id: int, token: str
    ) -> tuple[str | None, str | None]:
        """Fetch synced and plain lyrics for a Musixmatch track ID."""
        synced_lyrics = None
        plain_lyrics = None

        # Get synced lyrics
        try:
            subtitle_url = f"https://apic-desktop.musixmatch.com/ws/1.1/track.subtitle.get?track_id={track_id}&subtitle_format=lrc&app_id=web-desktop-app-v1.0&usertoken={token}"
            req = urllib.request.Request(
                subtitle_url, headers={"User-Agent": self.user_agent}
            )
            with urllib.request.urlopen(req, timeout=10) as response:
                subtitle_data = json.loads(response.read())
                subtitle_body = (
                    subtitle_data.get("message", {}).get("body", {}).get("subtitle", {})
                )
                synced_lyrics = subtitle_body.get("subtitle_body")
        except Exception:
            pass

        # Get plain lyrics
        try:
            lyrics_url = f"https://apic-desktop.musixmatch.com/ws/1.1/track.lyrics.get?track_id={track_id}&app_id=web-desktop-app-v1.0&usertoken={token}"
            req = urllib.request.Request(
                lyrics_url, headers={"User-Agent": self.user_agent}
            )
            with urllib.request.urlopen(req, timeout=10) as response:
                lyrics_data = json.loads(response.read())
                lyrics_body = (
                    lyrics_data.get("message", {}).get("body", {}).get("lyrics", {})
                )
                plain_lyrics = lyrics_body.get("lyrics_body")
        except Exception:
            pass

        return synced_lyrics, plain_lyrics


def embed_lyrics_in_file(
    file_path, plain_lyrics: str | None, synced_lyrics: str | None = None
) -> bool:
    """Embed lyrics into an audio file.

    Args:
        file_path: Path to the file
        plain_lyrics: Plain text lyrics to embed
        synced_lyrics: LRC format synced lyrics (not embedded, kept for .lrc)

    Returns:
        True if successful, False otherwise
    """

    file_path = Path(file_path)

    try:
        mf = MediaFile(file_path)
        mf.lyrics = plain_lyrics or None
        mf.save()
        logger.info(f"Embedded lyrics in: {file_path}")
        return True

    except Exception as e:
        logger.error(f"Failed to embed lyrics in {file_path}: {e}")
        return False


def update_metadata_in_file(file_path, metadata: dict) -> bool:
    """Update metadata tags.

    Args:
        file_path: Path to MP3 file
        metadata: Dictionary with keys: title, artist, album, date, track_number

    Returns:
        True if successful
    """

    file_path = Path(file_path)

    try:
        mf = MediaFile(file_path)

        if "title" in metadata and metadata["title"]:
            mf.title = metadata["title"]

        if "artist" in metadata and metadata["artist"]:
            artist_value = str(metadata["artist"])
            artists = normalize_artists(artist_value)
            if artists:
                mf.artist = format_display_artist(artists)
                mf.artists = artists
            else:
                mf.artist = artist_value

        if "album" in metadata and metadata["album"]:
            mf.album = metadata["album"]

        if "date" in metadata and metadata["date"]:
            mf.year = int(str(metadata["date"]).split("-")[0])

        if "track_number" in metadata and metadata["track_number"]:
            mf.track = int(metadata["track_number"])

        if "genre" in metadata and metadata["genre"]:
            mf.genre = metadata["genre"]

        mf.save()
        return True

    except Exception as e:
        logger.error(f"Failed to update metadata: {e}")
        return False
