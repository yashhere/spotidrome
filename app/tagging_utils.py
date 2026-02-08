"""Helpers for artist metadata formatting."""

from __future__ import annotations

import re

SPLIT_PATTERN = re.compile(r" / | feat\. | feat | ft\. | ft |; |, ")


def normalize_artists(value: str | list[str] | None) -> list[str]:
    """Normalize artist input into a list of names."""
    if not value:
        return []

    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if not item:
                continue
            parts.extend(_split_artist_string(str(item)))
        return _dedupe_preserve_order(parts)

    return _split_artist_string(str(value))


def format_display_artist(artists: list[str]) -> str:
    """Format display artist string from list."""
    if not artists:
        return "Unknown"
    if len(artists) == 1:
        return artists[0]
    return " / ".join(artists)


def _split_artist_string(value: str) -> list[str]:
    parts = [p.strip() for p in SPLIT_PATTERN.split(value) if p.strip()]
    return _dedupe_preserve_order(parts)


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for item in values:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result
