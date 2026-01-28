"""
Spotify provider - fetches playlists/tracks from Spotify API.
"""

import base64
import json
import re
import urllib.parse
import urllib.request

from .base import Track


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
            raise ValueError("Spotify credentials not configured")

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

        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read())
            self._token = data["access_token"]
            return self._token

    def _api_request(self, endpoint: str) -> dict:
        """Make authenticated API request."""
        token = self._get_token()
        url = f"https://api.spotify.com/v1/{endpoint}"

        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})

        with urllib.request.urlopen(req) as response:
            return json.loads(response.read())

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

    def _parse_track(self, track_data: dict, album_data: dict | None = None) -> Track:
        """Parse Spotify track data into common Track format."""
        if album_data is None:
            album_data = track_data.get("album", {})

        artists = [a["name"] for a in track_data.get("artists", [])]
        artist_ids = [a["id"] for a in track_data.get("artists", []) if a.get("id")]

        return Track(
            name=track_data.get("name", ""),
            artists=artists,
            album=album_data.get("name"),
            cover_url=album_data.get("images", [{}])[0].get("url")
            if album_data.get("images")
            else None,
            duration_ms=track_data.get("duration_ms"),
            release_date=album_data.get("release_date"),
            track_number=track_data.get("track_number"),
            artist_ids=artist_ids,
            source_url=None,  # Will search YouTube
        )

    def get_track(self, url: str) -> Track | None:
        """Fetch single track from Spotify URL."""
        track_id = self._extract_id(url, "track")
        if not track_id:
            return None

        try:
            data = self._api_request(f"tracks/{track_id}")
            return self._parse_track(data)
        except Exception:
            return None

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
        tracks: list[Track] = []

        items = playlist.get("tracks", {}).get("items", [])
        for item in items:
            track = item.get("track")
            if track:
                tracks.append(self._parse_track(track))

        # Handle pagination
        next_url = playlist.get("tracks", {}).get("next")
        while next_url:
            endpoint = next_url.replace("https://api.spotify.com/v1/", "")
            data = self._api_request(endpoint)
            for item in data.get("items", []):
                track = item.get("track")
                if track:
                    tracks.append(self._parse_track(track))
            next_url = data.get("next")

        return playlist.get("name", "Spotify Playlist"), tracks

    def _get_album(self, album_id: str) -> tuple[str, list[Track]]:
        """Fetch album tracks."""
        album = self._api_request(f"albums/{album_id}")
        tracks: list[Track] = []

        for track in album.get("tracks", {}).get("items", []):
            tracks.append(self._parse_track(track, album))

        return album.get("name", "Spotify Album"), tracks

    def search_track(self, query: str) -> Track | None:
        """Search for a track by query string."""
        try:
            params = urllib.parse.urlencode({"q": query, "type": "track", "limit": 1})
            data = self._api_request(f"search?{params}")

            items = data.get("tracks", {}).get("items", [])
            if items:
                return self._parse_track(items[0])
        except Exception:
            pass
        return None
