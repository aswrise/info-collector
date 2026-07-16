from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from urllib.parse import parse_qs, urlparse

from source_sync.types import DiscoveredItem, ScanPage, Source, SourcePreview


Runner = Callable[[list[str]], subprocess.CompletedProcess]


def _run(command: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(command, text=True, capture_output=True, timeout=120, check=False)


def content_key(video_id: str) -> str:
    if not video_id or ":" in video_id:
        raise ValueError("invalid YouTube video id")
    return f"youtube:{video_id}"


def normalize_youtube_source(url: str) -> tuple[str, str, str]:
    raw = url.strip()
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    host = parsed.netloc.lower().removeprefix("www.")
    if host not in {"youtube.com", "m.youtube.com"}:
        raise ValueError("expected a youtube.com channel or playlist URL")
    playlist_id = parse_qs(parsed.query).get("list", [None])[0]
    if playlist_id:
        return "youtube_playlist", f"https://www.youtube.com/playlist?list={playlist_id}", playlist_id
    path = parsed.path.rstrip("/")
    if path.endswith("/videos"):
        path = path[:-7]
    match = re.fullmatch(r"/(?:@[^/]+|channel/[^/]+|c/[^/]+|user/[^/]+)", path)
    if not match:
        raise ValueError("expected a YouTube channel root or playlist URL")
    external_id = path.split("/")[-1]
    return "youtube_channel", f"https://www.youtube.com{path}", external_id


def _items(stdout: str) -> list[DiscoveredItem]:
    found = []
    for position, line in enumerate(stdout.splitlines()):
        if not line.strip():
            continue
        data = json.loads(line)
        video_id = data.get("id")
        if not video_id:
            continue
        timestamp = data.get("timestamp") or data.get("release_timestamp")
        published = None
        if timestamp:
            from datetime import datetime, timezone
            published = datetime.fromtimestamp(timestamp, timezone.utc).isoformat().replace("+00:00", "Z")
        elif data.get("upload_date"):
            value = data["upload_date"]
            published = f"{value[:4]}-{value[4:6]}-{value[6:8]}" if len(value) >= 8 else None
        author = (
            data.get("channel")
            or data.get("uploader")
            or data.get("playlist_channel")
            or data.get("playlist_uploader")
            or ""
        )
        found.append(DiscoveredItem(
            content_key(video_id),
            f"https://www.youtube.com/watch?v={video_id}",
            data.get("title") or video_id,
            author,
            published,
            position,
            data,
        ))
    return found


class YouTubeChannelAdapter:
    def __init__(self, runner: Runner = _run):
        self.runner = runner

    def inspect(self, url: str) -> SourcePreview:
        source_type, canonical, external_id = normalize_youtube_source(url)
        if source_type != "youtube_channel":
            raise ValueError("not a YouTube channel URL")
        items = self._scan(canonical, 5)
        display = items[0].author if items and items[0].author else external_id
        return SourcePreview(source_type, canonical, external_id, display, None, items)

    def scan(self, source: Source, checkpoint: str | None = None) -> ScanPage:
        depth = int(source.settings.get("scan_depth", 5))
        return ScanPage(self._scan(source.canonical_url, depth), is_snapshot=False)

    def _scan(self, url: str, depth: int) -> list[DiscoveredItem]:
        proc = self.runner([
            "yt-dlp", "--flat-playlist", "--playlist-end", str(depth),
            "--dump-json", f"{url}/videos",
        ])
        if proc.returncode:
            raise RuntimeError((proc.stderr or "yt-dlp failed").strip()[:500])
        return _items(proc.stdout)


class YouTubePlaylistAdapter:
    def __init__(self, runner: Runner = _run):
        self.runner = runner

    def inspect(self, url: str) -> SourcePreview:
        source_type, canonical, external_id = normalize_youtube_source(url)
        if source_type != "youtube_playlist":
            raise ValueError("not a YouTube playlist URL")
        items = self._scan(canonical)
        display = items[0].metadata.get("playlist_title") if items else external_id
        return SourcePreview(source_type, canonical, external_id, display or external_id, len(items), items[:5])

    def scan(self, source: Source, checkpoint: str | None = None) -> ScanPage:
        return ScanPage(self._scan(source.canonical_url), is_snapshot=True)

    def _scan(self, url: str) -> list[DiscoveredItem]:
        proc = self.runner(["yt-dlp", "--flat-playlist", "--dump-json", url])
        if proc.returncode:
            raise RuntimeError((proc.stderr or "yt-dlp failed").strip()[:500])
        return _items(proc.stdout)
