from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class DiscoveredItem:
    content_key: str
    canonical_url: str
    title_or_text: str
    author: str
    published_at: str | None = None
    position: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ScanPage:
    items: list[DiscoveredItem]
    next_checkpoint: str | None = None
    exhausted: bool = True
    is_snapshot: bool = False


@dataclass(frozen=True)
class SourcePreview:
    source_type: str
    canonical_url: str
    external_id: str
    display_name: str
    item_count: int | None
    recent_items: list[DiscoveredItem]


@dataclass(frozen=True)
class Source:
    id: int
    source_type: str
    canonical_url: str
    external_id: str
    display_name: str
    enabled: bool
    auto_sync_new: bool
    collection_policy: str
    settings: dict[str, Any]
    last_scan_at: str | None = None
    last_scan_status: str | None = None
    last_scan_error: str | None = None


@dataclass(frozen=True)
class DiscoveryResult:
    discovered: int
    new_items: int
    removed: int = 0
    unavailable: int = 0
    overlap_hit: bool = False


@dataclass(frozen=True)
class EnqueueResult:
    queued: int = 0
    skipped_synced: int = 0
    skipped_active: int = 0
    skipped_disabled: int = 0


@dataclass(frozen=True)
class Job:
    id: int
    content_key: str
    processor: str
    status: str
    trigger: str
    attempts: int
    canonical_url: str
    metadata: dict[str, Any]
    collection_subdir: str | None = None


@dataclass(frozen=True)
class ValueDecision:
    content_key: str
    relevance: int
    information_gain: int
    usefulness: int
    evidence: int
    total: int
    decision: str
    reason_code: str | None = None
    explanation: str | None = None
    confidence: float | None = None


class SourceAdapter(Protocol):
    def inspect(self, url: str) -> SourcePreview: ...

    def scan(self, source: Source, checkpoint: str | None) -> ScanPage: ...
