from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from datetime import datetime
from urllib.parse import urlparse

from source_sync.types import DiscoveredItem, ScanPage, Source, SourcePreview


Runner = Callable[[list[str]], subprocess.CompletedProcess]
RISK_PATTERNS = {
    "rate_limited": re.compile(r"\b429\b|rate.?limit", re.I),
    "auth": re.compile(r"auth|required|not logged|login|\b401\b|\b403\b", re.I),
    "challenge": re.compile(r"challenge|captcha|verify your identity|suspicious", re.I),
}


class XScanStopped(RuntimeError):
    def __init__(self, reason: str, detail: str):
        super().__init__(detail)
        self.reason = reason


def _run(command: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(command, text=True, capture_output=True, timeout=120, check=False)


def normalize_x_source(url: str) -> tuple[str, str, str]:
    raw = url.strip()
    if re.fullmatch(r"@?[A-Za-z0-9_]{1,15}", raw):
        handle = raw.lstrip("@")
        return "x_likes", f"https://x.com/{handle}/likes", handle
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    if parsed.netloc.lower().removeprefix("www.") not in {"x.com", "twitter.com"}:
        raise ValueError("expected an x.com Likes or List URL")
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) == 2 and parts[1] == "likes" and re.fullmatch(r"[A-Za-z0-9_]{1,15}", parts[0]):
        return "x_likes", f"https://x.com/{parts[0]}/likes", parts[0]
    if len(parts) >= 3 and parts[-2] in {"lists", "list"} and parts[-1].isdigit():
        return "x_list", f"https://x.com/i/lists/{parts[-1]}", parts[-1]
    raise ValueError("expected an x.com Likes or List URL")


def _published(value) -> str | None:
    if not value:
        return None
    for fmt in ("%a %b %d %H:%M:%S %z %Y", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(value, fmt).isoformat()
        except ValueError:
            pass
    return value


def _item(row: dict, position: int) -> DiscoveredItem:
    tweet_id = str(row.get("id") or row.get("tweetId") or "")
    if not tweet_id.isdigit():
        raise ValueError("OpenCLI returned a Tweet without a numeric id")
    author = str(row.get("author") or "unknown").lstrip("@")
    url = row.get("url") or f"https://x.com/{author}/status/{tweet_id}"
    return DiscoveredItem(
        f"x:{tweet_id}", url, row.get("text") or "", author,
        _published(row.get("created_at") or row.get("createdAt")), position, row,
    )


class OpenCLIPageClient:
    def __init__(self, runner: Runner = _run):
        self.runner = runner

    def page(self, command: str, subject: str, cursor: str | None) -> ScanPage:
        args = ["opencli", "twitter", command, subject, "--count", "20", "-f", "json"]
        if cursor:
            args += ["--cursor", cursor]
        proc = self.runner(args)
        if proc.returncode:
            detail = (proc.stderr or proc.stdout or "OpenCLI failed").strip()[:1000]
            for reason, pattern in RISK_PATTERNS.items():
                if pattern.search(detail):
                    raise XScanStopped(reason, detail)
            raise XScanStopped("error", detail)
        try:
            payload = json.loads(proc.stdout)
            if isinstance(payload, list) and len(payload) == 1 and "items" in payload[0]:
                payload = payload[0]
            rows = payload["items"]
        except (json.JSONDecodeError, KeyError, TypeError) as error:
            raise XScanStopped("error", "invalid OpenCLI page response") from error
        if not isinstance(rows, list) or len(rows) > 20:
            raise XScanStopped("error", "OpenCLI page exceeded the 20-item contract")
        items = [_item(row, index) for index, row in enumerate(rows)]
        next_cursor = payload.get("next_cursor") or payload.get("nextCursor")
        exhausted = bool(payload.get("exhausted", not next_cursor))
        return ScanPage(items, next_cursor, exhausted)


class XLikesAdapter:
    def __init__(self, client: OpenCLIPageClient | None = None):
        self.client = client or OpenCLIPageClient()

    def inspect(self, url: str) -> SourcePreview:
        source_type, canonical, handle = normalize_x_source(url)
        if source_type != "x_likes":
            raise ValueError("not an X Likes URL")
        return SourcePreview(source_type, canonical, handle, f"@{handle} Likes", None, [])

    def scan(self, source: Source, checkpoint: str | None) -> ScanPage:
        return self.client.page("likes-page", source.external_id, checkpoint)


class XListAdapter:
    def __init__(self, client: OpenCLIPageClient | None = None):
        self.client = client or OpenCLIPageClient()

    def inspect(self, url: str) -> SourcePreview:
        source_type, canonical, list_id = normalize_x_source(url)
        if source_type != "x_list":
            raise ValueError("not an X List URL")
        return SourcePreview(source_type, canonical, list_id, f"X List {list_id}", None, [])

    def scan(self, source: Source, checkpoint: str | None) -> ScanPage:
        return self.client.page("list-page", source.external_id, checkpoint)
