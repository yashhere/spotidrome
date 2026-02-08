"""CLI utility to retag and rename downloaded files."""

from __future__ import annotations

import argparse
import os
import urllib.request
from pathlib import Path

from mediafile import Image, ImageType, MediaFile

from .providers.spotify import SpotifyProvider
from .providers.ytmusic import YTMusicProvider
from .tagging_utils import format_display_artist, normalize_artists, sanitize_filename


def retag_library(
    music_dir: Path,
    dry_run: bool = False,
    update_tags: bool = True,
    rename_files: bool = True,
    fetch_metadata: str = "missing",
    metadata_source: str = "spotify",
) -> int:
    if not music_dir.exists():
        print(f"Music directory not found: {music_dir}")
        return 1

    spotify_provider = None
    ytmusic_provider = None
    if fetch_metadata != "never":
        if metadata_source in {"spotify", "auto"}:
            client_id = os.getenv("SPOTIFY_CLIENT_ID")
            client_secret = os.getenv("SPOTIFY_CLIENT_SECRET")
            if client_id and client_secret:
                spotify_provider = SpotifyProvider(client_id, client_secret)
            elif metadata_source == "spotify":
                ytmusic_provider = YTMusicProvider()
                print("Spotify credentials missing; using YTMusic for metadata fetch")
        if metadata_source == "ytmusic" and not ytmusic_provider:
            ytmusic_provider = YTMusicProvider()

    total = 0
    tagged = 0
    renamed = 0
    skipped = 0
    enriched = 0

    for mp3 in music_dir.rglob("*.mp3"):
        total += 1
        try:
            mf = MediaFile(mp3)
        except Exception as e:
            print(f"Skip unreadable file: {mp3} ({e})")
            skipped += 1
            continue

        title = mf.title or mp3.stem.replace("_", " ")
        parent_artist = mp3.parent.name if mp3.parent != music_dir else ""
        artists_value: str | list[str] | None = None
        if mf.artists:
            artists_value = [str(a) for a in mf.artists if a]
        elif mf.artist:
            artists_value = str(mf.artist)
        elif parent_artist:
            artists_value = parent_artist

        artists = normalize_artists(artists_value)
        if not artists:
            artists = ["Unknown"]

        if (spotify_provider or ytmusic_provider) and title:
            should_fetch_metadata = fetch_metadata == "always" or any(
                [
                    not mf.album,
                    mf.year is None,
                    not mf.genre,
                    not mf.images,
                ]
            )
            should_fetch_cover = fetch_metadata != "never"
            should_query = should_fetch_metadata or should_fetch_cover
            if should_query:
                query = f"{format_display_artist(artists)} {title}"
                track = None
                if spotify_provider:
                    track = spotify_provider.search_track(query)
                elif ytmusic_provider:
                    results = ytmusic_provider.yt.search(query, filter="songs", limit=1)
                    if results:
                        result = results[0]
                        track = {
                            "name": result.get("title"),
                            "artists": [
                                a.get("name", "") for a in result.get("artists", [])
                            ],
                            "album": result.get("album", {}).get("name"),
                            "release_date": result.get("year"),
                            "cover_url": (
                                result.get("thumbnails", [])[-1]["url"]
                                if result.get("thumbnails")
                                else None
                            ),
                            "genre": None,
                        }

                if track:
                    if not mf.album or fetch_metadata == "always":
                        mf.album = track.get("album") or mf.album
                    if mf.year is None or fetch_metadata == "always":
                        release_date = track.get("release_date")
                        if release_date:
                            mf.year = int(str(release_date).split("-")[0])
                    if not mf.genre or fetch_metadata == "always":
                        mf.genre = track.get("genre") or mf.genre
                    upstream_artists = normalize_artists(track.get("artists"))
                    if upstream_artists and fetch_metadata == "always":
                        artists = upstream_artists
                    cover_url = track.get("cover_url")
                    if cover_url and should_fetch_cover:
                        try:
                            req = urllib.request.Request(
                                cover_url, headers={"User-Agent": "Mozilla/5.0"}
                            )
                            with urllib.request.urlopen(req, timeout=10) as response:
                                cover_data = response.read()
                            mf.images = [
                                Image(
                                    data=cover_data,
                                    desc="Cover",
                                    type=ImageType.front,
                                )
                            ]
                        except Exception:
                            pass
                    enriched += 1

        if update_tags:
            mf.title = title
            mf.artist = format_display_artist(artists)
            mf.artists = artists
            if not dry_run:
                mf.save()
            tagged += 1

        if rename_files:
            target_dir = music_dir / sanitize_filename(str(artists[0]))
            target_name = f"{sanitize_filename(str(title))}.mp3"
            target_path = target_dir / target_name

            if target_path != mp3:
                if target_path.exists():
                    print(f"Skip rename (exists): {target_path}")
                    skipped += 1
                else:
                    if dry_run:
                        print(f"Rename: {mp3} -> {target_path}")
                    else:
                        target_dir.mkdir(parents=True, exist_ok=True)
                        lrc_path = mp3.with_suffix(".lrc")
                        if lrc_path.exists():
                            lrc_path.rename(target_path.with_suffix(".lrc"))
                        mp3.rename(target_path)
                    renamed += 1

    print(
        "Retag complete: "
        f"total={total} tagged={tagged} renamed={renamed} "
        f"enriched={enriched} skipped={skipped}"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Retag and rename downloaded music files"
    )
    parser.add_argument(
        "--music-dir",
        default=os.getenv("MUSIC_DIR", "/music"),
        help="Root music directory (default: MUSIC_DIR env or /music)",
    )
    parser.add_argument("--dry-run", action="store_true", help="No changes")
    parser.add_argument(
        "--skip-tags", action="store_true", help="Do not update metadata tags"
    )
    parser.add_argument(
        "--skip-rename", action="store_true", help="Do not rename files"
    )
    parser.add_argument(
        "--fetch-metadata",
        choices=["missing", "always", "never"],
        default="missing",
        help="Fetch metadata from Spotify (default: missing)",
    )
    parser.add_argument(
        "--metadata-source",
        choices=["spotify", "ytmusic", "auto"],
        default="spotify",
        help="Metadata source (default: spotify)",
    )

    args = parser.parse_args()
    return retag_library(
        Path(args.music_dir),
        dry_run=args.dry_run,
        update_tags=not args.skip_tags,
        rename_files=not args.skip_rename,
        fetch_metadata=args.fetch_metadata,
        metadata_source=args.metadata_source,
    )


if __name__ == "__main__":
    raise SystemExit(main())
