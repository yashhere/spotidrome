"""
Spotify provider - fetches playlists/tracks from Spotify API.
"""

import base64
import json
import logging
import re
import urllib.parse
import urllib.request

from .base import Track

logger = logging.getLogger(__name__)


class SpotifyProvider:
    """Spotify playlist/track provider using Spotify Web API."""

    def __init__(self, client_id: str | None = None, client_secret: str | None = None):
        self.client_id = client_id
        self.client_secret = client_secret
        self._token: str | None = None

    def configure(self, client_id: str, client_secret: str) -> None:
        """Configure Spotify credentials."""
        self.client_id = client_id
        self.client_secret = client_secret
        self._token = None  # Reset token

    def supports_url(self, url: str) -> bool:
        """Check if URL is a Spotify URL."""
        return "spotify.com" in url or url.startswith("spotify:")

    def _get_token(self) -> str:
        """Get access token using client credentials flow."""
        if self._token:
            return self._token

        if not self.client_id or not self.client_secret:
            logger.error(
                "Spotify credentials not configured (missing client_id or client_secret)"
            )
            raise ValueError("Spotify credentials not configured")

        logger.debug("Fetching Spotify access token")
        auth_url = "https://accounts.spotify.com/api/token"
        auth_data = urllib.parse.urlencode(
            {"grant_type": "client_credentials"}
        ).encode()

        credentials = base64.b64encode(
            f"{self.client_id}:{self.client_secret}".encode()
        ).decode()

        req = urllib.request.Request(
            auth_url,
            data=auth_data,
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )

        try:
            with urllib.request.urlopen(req) as response:
                data = json.loads(response.read())
                self._token = data["access_token"]
                logger.debug("Successfully obtained Spotify access token")
                return self._token
        except urllib.error.HTTPError as e:
            logger.error(f"Failed to get Spotify token: HTTP {e.code} - {e.reason}")
            raise
        except Exception as e:
            logger.error(f"Failed to get Spotify token: {type(e).__name__}: {e}")
            raise

    def _api_request(self, endpoint: str) -> dict:
        """Make authenticated API request."""
        token = self._get_token()
        url = f"https://api.spotify.com/v1/{endpoint}"

        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})

        with urllib.request.urlopen(req) as response:
            return json.loads(response.read())

    def _get_artist_genres(self, artist_ids: list[str]) -> str | None:
        """Fetch genres from the first artist with genres.

        Returns:
            Comma-separated genre string, or None if no genres found
        """
        if not artist_ids:
            return None

        try:
            # Batch request up to 50 artists at once
            ids_param = ",".join(artist_ids[:50])
            data = self._api_request(f"artists?ids={ids_param}")

            for artist in data.get("artists", []):
                if artist and artist.get("genres"):
                    # Return first non-empty genre list, title-cased
                    genres = [g.title() for g in artist["genres"][:3]]
                    return ", ".join(genres)
        except Exception:
            pass

        return None

    def _extract_id(self, url: str, type_: str) -> str | None:
        """Extract Spotify ID from URL."""
        # Handle both URLs and URIs
        patterns = [
            rf"{type_}/([a-zA-Z0-9]+)",  # URL format
            rf"spotify:{type_}:([a-zA-Z0-9]+)",  # URI format
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None

    def _parse_track(
        self, track_data: dict, album_data: dict | None = None, genre: str | None = None
    ) -> Track:
        """Parse Spotify track data into common Track format."""
        if album_data is None:
            album_data = track_data.get("album", {})

        artists = [a["name"] for a in track_data.get("artists", [])]
        artist_ids = [a["id"] for a in track_data.get("artists", []) if a.get("id")]

        return Track(
            name=track_data.get("name") or "",
            artists=artists,
            album=album_data.get("name"),
            cover_url=album_data.get("images", [{}])[0].get("url")
            if album_data.get("images")
            else None,
            duration_ms=track_data.get("duration_ms"),
            release_date=album_data.get("release_date"),
            track_number=track_data.get("track_number"),
            artist_ids=artist_ids,
            genre=genre,
            source_url=None,  # Will search YouTube
        )

    def get_track(self, url: str) -> Track | None:
        """Fetch single track from Spotify URL."""
        logger.info(f"Fetching Spotify track from: {url}")
        track_id = self._extract_id(url, "track")
        if not track_id:
            logger.error(f"Could not extract track ID from URL: {url}")
            return None

        logger.debug(f"Extracted track ID: {track_id}")

        try:
            data = self._api_request(f"tracks/{track_id}")
            artist_ids = [a["id"] for a in data.get("artists", []) if a.get("id")]
            genre = self._get_artist_genres(artist_ids)
            track = self._parse_track(data, genre=genre)
            logger.info(
                f"Got Spotify track: {track.get('artists', ['?'])[0]} - {track.get('name', '?')}"
            )
            return track
        except urllib.error.HTTPError as e:
            logger.error(
                f"Spotify API error fetching track {track_id}: HTTP {e.code} - {e.reason}"
            )
            return None
        except Exception as e:
            logger.error(
                f"Error fetching Spotify track {track_id}: {type(e).__name__}: {e}"
            )
            return None

    def _fetch_genres_for_artists(self, artist_ids: list[str]) -> dict[str, str]:
        """Batch fetch genres for a list of artist IDs.

        Returns:
            Dict mapping artist_id to genre string
        """
        genres_map: dict[str, str] = {}
        if not artist_ids:
            return genres_map

        # Deduplicate
        unique_ids = list(dict.fromkeys(artist_ids))

        # Batch in groups of 50 (Spotify API limit)
        for i in range(0, len(unique_ids), 50):
            batch = unique_ids[i : i + 50]
            try:
                ids_param = ",".join(batch)
                data = self._api_request(f"artists?ids={ids_param}")

                for artist in data.get("artists", []):
                    if artist and artist.get("id") and artist.get("genres"):
                        genres = [g.title() for g in artist["genres"][:3]]
                        genres_map[artist["id"]] = ", ".join(genres)
            except Exception:
                pass

        return genres_map

    def get_playlist(self, url: str) -> tuple[str, list[Track]]:
        """Fetch playlist from Spotify URL."""
        playlist_id = self._extract_id(url, "playlist")
        if not playlist_id:
            # Maybe it's an album?
            album_id = self._extract_id(url, "album")
            if album_id:
                return self._get_album(album_id)
            raise ValueError(f"Could not extract playlist ID from URL: {url}")

        playlist = self._api_request(f"playlists/{playlist_id}")

        # Collect all raw track data first
        raw_tracks: list[dict] = []
        items = playlist.get("tracks", {}).get("items", [])
        for item in items:
            track = item.get("track")
            if track:
                raw_tracks.append(track)

        # Handle pagination
        next_url = playlist.get("tracks", {}).get("next")
        while next_url:
            endpoint = next_url.replace("https://api.spotify.com/v1/", "")
            data = self._api_request(endpoint)
            for item in data.get("items", []):
                track = item.get("track")
                if track:
                    raw_tracks.append(track)
            next_url = data.get("next")

        # Batch fetch genres for all artists
        all_artist_ids: list[str] = []
        for track in raw_tracks:
            for artist in track.get("artists", []):
                if artist.get("id"):
                    all_artist_ids.append(artist["id"])

        genres_map = self._fetch_genres_for_artists(all_artist_ids)

        # Parse tracks with genres
        tracks: list[Track] = []
        for track_data in raw_tracks:
            # Get genre from primary artist
            primary_artist_id = None
            if track_data.get("artists") and track_data["artists"][0].get("id"):
                primary_artist_id = track_data["artists"][0]["id"]
            genre = genres_map.get(primary_artist_id) if primary_artist_id else None
            tracks.append(self._parse_track(track_data, genre=genre))

        return playlist.get("name", "Spotify Playlist"), tracks

    def _get_album(self, album_id: str) -> tuple[str, list[Track]]:
        """Fetch album tracks."""
        album = self._api_request(f"albums/{album_id}")

        # Collect all artist IDs from album tracks
        raw_tracks = album.get("tracks", {}).get("items", [])
        all_artist_ids: list[str] = []
        for track in raw_tracks:
            for artist in track.get("artists", []):
                if artist.get("id"):
                    all_artist_ids.append(artist["id"])

        genres_map = self._fetch_genres_for_artists(all_artist_ids)

        tracks: list[Track] = []
        for track_data in raw_tracks:
            primary_artist_id = None
            if track_data.get("artists") and track_data["artists"][0].get("id"):
                primary_artist_id = track_data["artists"][0]["id"]
            genre = genres_map.get(primary_artist_id) if primary_artist_id else None
            tracks.append(self._parse_track(track_data, album, genre=genre))

        return album.get("name", "Spotify Album"), tracks

    def search_track(self, query: str) -> Track | None:
        """Search for a track by query string."""
        try:
            params = urllib.parse.urlencode({"q": query, "type": "track", "limit": 1})
            data = self._api_request(f"search?{params}")

            items = data.get("tracks", {}).get("items", [])
            if items:
                track_data = items[0]
                artist_ids = [
                    a["id"] for a in track_data.get("artists", []) if a.get("id")
                ]
                genre = self._get_artist_genres(artist_ids)
                return self._parse_track(track_data, genre=genre)
        except Exception:
            pass
        return None
