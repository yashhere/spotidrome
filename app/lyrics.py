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





def _parse_lrc_timestamp(timestamp: str) -> int:
    """Convert [mm:ss.xx] timestamp to milliseconds."""
    try:
        minutes, seconds = timestamp.strip('[]').split(':')
        if '.' in seconds:
            secs, ms = seconds.split('.')
            if len(ms) == 2:
                ms += '0'  # 12.34 -> 12.340
        else:
            secs = seconds
            ms = '0'

        total_ms = (int(minutes) * 60 * 1000) + (int(secs) * 1000) + int(ms)
        return total_ms
    except Exception:
        return 0


def _parse_lrc(lrc_content: str) -> list[tuple[str, int]]:
    """Parse LRC content into list of (text, timestamp_ms)."""
    import re
    result = []
    # Regex to match timestamps like [00:12.34] or [00:12.345]
    pattern = re.compile(r'\[(\d{2}):(\d{2})[.:](\d{2,3})\]')

    for line in lrc_content.splitlines():
        line = line.strip()
        if not line:
            continue

        # Find all timestamps in the line (e.g. [00:12.00][00:15.00]Chorus)
        matches = list(pattern.finditer(line))
        if not matches:
            continue

        # The text is strictly after the last timestamp
        text = line[matches[-1].end():].strip()

        for match in matches:
            minutes = int(match.group(1))
            seconds = int(match.group(2))
            ms_part = match.group(3)

            # Normalize ms to milliseconds
            if len(ms_part) == 2:
                ms = int(ms_part) * 10
            else:
                ms = int(ms_part)

            total_ms = (minutes * 60 * 1000) + (seconds * 1000) + ms
            result.append((text, total_ms))

    # Sort by timestamp
    result.sort(key=lambda x: x[1])
    return result


def embed_lyrics_in_file(file_path, plain_lyrics: str | None, synced_lyrics: str | None = None) -> bool:
    """Embed lyrics into an MP3 file's ID3 tags.

    Args:
        file_path: Path to the MP3 file
        plain_lyrics: Plain text lyrics to embed as USLT
        synced_lyrics: LRC format synced lyrics to embed as SYLT (optional)

    Returns:
        True if successful, False otherwise
    """
    from pathlib import Path

    try:
        from mutagen.id3 import ID3, USLT, SYLT, Encoding
        from mutagen.id3._util import ID3NoHeaderError
    except ImportError:
        logger.warning("mutagen not installed, cannot embed lyrics")
        return False

    file_path = Path(file_path)

    try:
        try:
            audio = ID3(file_path)
        except ID3NoHeaderError:
            audio = ID3()
            audio.save(file_path)
            audio = ID3(file_path)

        # Remove existing lyrics
        audio.delall('USLT')
        audio.delall('SYLT')

        # Add plain lyrics
        if plain_lyrics:
            audio.add(USLT(
                encoding=Encoding.UTF8,
                lang='eng', # Use 'eng' instead of 'xxx' for better compatibility
                desc='',
                text=plain_lyrics
            ))

        # Add synced lyrics
        if synced_lyrics:
            parsed_lyrics = _parse_lrc(synced_lyrics)
            if parsed_lyrics:
                audio.add(SYLT(
                    encoding=Encoding.UTF8,
                    lang='eng',
                    format=2, # 2 = ms absolute timestamp
                    type=1, # 1 = Lyrics
                    desc='',
                    text=parsed_lyrics
                ))

        audio.save()
        logger.info(f"Embedded lyrics (synced={bool(synced_lyrics)}) in: {file_path}")
        return True

    except Exception as e:
        logger.error(f"Failed to embed lyrics in {file_path}: {e}")
        return False


def update_metadata_in_file(file_path, metadata: dict) -> bool:
    """Update ID3 metadata tags.

    Args:
        file_path: Path to MP3 file
        metadata: Dictionary with keys: title, artist, album, date, track_number

    Returns:
        True if successful
    """
    from pathlib import Path
    try:
        from mutagen.easyid3 import EasyID3
        from mutagen.id3 import ID3NoHeaderError, ID3
    except ImportError:
        logger.warning("mutagen not installed")
        return False

    file_path = Path(file_path)

    try:
        try:
            audio = EasyID3(file_path)
        except ID3NoHeaderError:
            try:
                # Try creating header
                meta = ID3()
                meta.save(file_path)
                audio = EasyID3(file_path)
            except Exception:
                return False

        if 'title' in metadata and metadata['title']:
            audio['title'] = metadata['title']

        if 'artist' in metadata and metadata['artist']:
            # Handle multiple artists separated by ;
            artists = [a.strip() for a in metadata['artist'].split(';') if a.strip()]
            audio['artist'] = artists

        if 'album' in metadata and metadata['album']:
            audio['album'] = metadata['album']

        if 'date' in metadata and metadata['date']:
            audio['date'] = str(metadata['date'])

        if 'track_number' in metadata and metadata['track_number']:
            audio['tracknumber'] = str(metadata['track_number'])

        audio.save()
        logger.info(f"Updated metadata for: {file_path}")
        return True

    except Exception as e:
        logger.error(f"Failed to update metadata: {e}")
        return False
