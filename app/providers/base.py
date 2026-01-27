"""
Base provider protocol and common types for Spotidrome.
"""

from typing import Protocol, TypedDict


class Track(TypedDict):
    """Common track format across all providers."""
    name: str
    artists: list[str]
    album: str | None
    cover_url: str | None
    duration_ms: int | None
    release_date: str | None
    track_number: int | None
    artist_ids: list[str]  # For Spotify genre lookup
    source_url: str | None  # Original URL (for YouTube direct download)


class PlaylistProvider(Protocol):
    """Common interface for playlist/track providers."""

    def get_playlist(self, url: str) -> tuple[str, list[Track]]:
        """Fetch playlist name and tracks from URL.

        Returns:
            Tuple of (playlist_name, list of tracks)
        """
        ...

    def get_track(self, url: str) -> Track | None:
        """Fetch single track from URL.

        Returns:
            Track dict or None if not found
        """
        ...

    def supports_url(self, url: str) -> bool:
        """Check if this provider can handle the URL.

        Returns:
            True if this provider can process the given URL
        """
        ...
