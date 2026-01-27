"""
Lyrics provider - fetches lyrics from multiple sources.
"""

import json
import logging
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)


class LyricsProvider:
    """Fetches lyrics from multiple sources."""

    def __init__(self):
        self.user_agent = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        self.mxm_token = None

    def get_lyrics(self, title: str, artist: str) -> tuple[str | None, str | None]:
        """
        Fetch lyrics from available sources.
        Returns (plain_lyrics, synced_lyrics_lrc) - either can be None.
        """
        logger.debug(f"Fetching lyrics for: {artist} - {title}")

        # Try Musixmatch first (best coverage, has synced lyrics)
        synced, plain = self._fetch_musixmatch(title, artist)
        if synced or plain:
            logger.debug(f"Found lyrics via Musixmatch (synced={bool(synced)}, plain={bool(plain)})")
            return plain, synced

        # Try LRCLIB (has synced lyrics)
        synced, plain = self._fetch_lrclib(title, artist)
        if synced or plain:
            logger.debug(f"Found lyrics via LRCLIB (synced={bool(synced)}, plain={bool(plain)})")
            return plain, synced

        # Fallback to lyrics.ovh for plain lyrics
        plain = self._fetch_lyrics_ovh(title, artist)
        if plain:
            logger.debug("Found plain lyrics via lyrics.ovh")
        else:
            logger.debug("No lyrics found from any source")
        return plain, None

    def _fetch_lrclib(self, title: str, artist: str) -> tuple[str | None, str | None]:
        """Fetch from LRCLIB (free, has synced lyrics)."""
        try:
            query = urllib.parse.urlencode({
                "track_name": title,
                "artist_name": artist
            })
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
            req = urllib.request.Request(url, headers={
                "User-Agent": self.user_agent,
                "Cookie": "x-mxm-token-guid="
            })
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read())
                self.mxm_token = data.get("message", {}).get("body", {}).get("user_token")
                return self.mxm_token
        except Exception as e:
            logger.debug(f"Failed to get Musixmatch token: {e}")
        return None

    def _fetch_musixmatch(self, title: str, artist: str) -> tuple[str | None, str | None]:
        """Fetch from Musixmatch (good coverage, has synced lyrics)."""
        token = self._get_mxm_token()
        if not token:
            return None, None

        try:
            # Search for track
            query = urllib.parse.urlencode({
                "q_track": title,
                "q_artist": artist,
                "app_id": "web-desktop-app-v1.0",
                "usertoken": token,
                "format": "json"
            })
            search_url = f"https://apic-desktop.musixmatch.com/ws/1.1/track.search?{query}"

            req = urllib.request.Request(search_url, headers={"User-Agent": self.user_agent})
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read())

            track_list = data.get("message", {}).get("body", {}).get("track_list", [])
            if not track_list:
                return None, None

            track_id = track_list[0].get("track", {}).get("track_id")
            if not track_id:
                return None, None

            # Get synced lyrics
            synced_lyrics = None
            try:
                subtitle_url = f"https://apic-desktop.musixmatch.com/ws/1.1/track.subtitle.get?track_id={track_id}&subtitle_format=lrc&app_id=web-desktop-app-v1.0&usertoken={token}"
                req = urllib.request.Request(subtitle_url, headers={"User-Agent": self.user_agent})
                with urllib.request.urlopen(req, timeout=10) as response:
                    subtitle_data = json.loads(response.read())
                    subtitle_body = subtitle_data.get("message", {}).get("body", {}).get("subtitle", {})
                    synced_lyrics = subtitle_body.get("subtitle_body")
            except Exception:
                pass

            # Get plain lyrics
            plain_lyrics = None
            try:
                lyrics_url = f"https://apic-desktop.musixmatch.com/ws/1.1/track.lyrics.get?track_id={track_id}&app_id=web-desktop-app-v1.0&usertoken={token}"
                req = urllib.request.Request(lyrics_url, headers={"User-Agent": self.user_agent})
                with urllib.request.urlopen(req, timeout=10) as response:
                    lyrics_data = json.loads(response.read())
                    lyrics_body = lyrics_data.get("message", {}).get("body", {}).get("lyrics", {})
                    plain_lyrics = lyrics_body.get("lyrics_body")
            except Exception:
                pass

            return synced_lyrics, plain_lyrics

        except Exception as e:
            logger.debug(f"Musixmatch failed: {e}")
        return None, None
