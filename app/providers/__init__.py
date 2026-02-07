"""
Provider registry - auto-detects URL type and returns appropriate provider.
"""

from .base import PlaylistProvider, Track
from .spotify import SpotifyProvider
from .youtube import CookieExpiredError, YouTubeProvider

__all__ = [
    "CookieExpiredError",
    "PlaylistProvider",
    "Track",
    "SpotifyProvider",
    "YouTubeProvider",
    "get_provider",
]

# Provider instances (created lazily)
_providers: list[PlaylistProvider] | None = None


def get_providers() -> list[PlaylistProvider]:
    """Get list of all available providers."""
    global _providers
    if _providers is None:
        _providers = [
            SpotifyProvider(),
            YouTubeProvider(),
        ]
    return _providers


def get_provider(url: str) -> PlaylistProvider | None:
    """Get the appropriate provider for a URL.

    Returns:
        Provider that can handle the URL, or None if no provider supports it.
    """
    for provider in get_providers():
        if provider.supports_url(url):
            return provider
    return None
