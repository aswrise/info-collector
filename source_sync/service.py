from __future__ import annotations

import random
import re
import time
from pathlib import Path

from processors.podcast import PodcastProcessor
from processors.value import ValueEvaluator
from processors.tweet import DEFAULT_OUTPUT_DIR as TWEET_OUTPUT_DIR

from .adapters import XListAdapter, XLikesAdapter, YouTubeChannelAdapter, YouTubePlaylistAdapter
from .adapters.x import XScanStopped
from .registry import SourceRegistry
from .runtime import Runtime, utc_iso
from .types import DiscoveredItem, DiscoveryResult, SourceAdapter, SourcePreview, ScanPage


class SourceSyncService:
    def __init__(
        self,
        registry: SourceRegistry,
        adapters: dict[str, SourceAdapter] | None = None,
        podcast: PodcastProcessor | None = None,
        evaluator: ValueEvaluator | None = None,
        runtime: Runtime | None = None,
        tweet_output_dir: str | Path = TWEET_OUTPUT_DIR,
        sleeper=time.sleep,
        interval=random.uniform,
    ):
        self.registry = registry
        self.podcast = podcast or PodcastProcessor()
        self.evaluator = evaluator or ValueEvaluator()
        self.runtime = runtime or Runtime(registry.path.parent)
        self.tweet_output_dir = Path(tweet_output_dir).expanduser()
        self.sleeper = sleeper
        self.interval = interval
        self.adapters = adapters or {
            "youtube_channel": YouTubeChannelAdapter(),
            "youtube_playlist": YouTubePlaylistAdapter(),
            "x_likes": XLikesAdapter(),
            "x_list": XListAdapter(),
        }

    def inspect(self, url: str) -> SourcePreview:
        errors = []
        for adapter in self.adapters.values():
            try:
                return adapter.inspect(url)
            except ValueError as error:
                errors.append(str(error))
        raise ValueError(errors[-1] if errors else "unsupported source URL")

    def add_source(self, preview: SourcePreview, initial_sync_count: int | None = 5, **options):
        source = self.registry.add_source(preview, **options)
        if preview.source_type == "x_likes":
            self._import_tweet_baseline(source.id)
        if preview.recent_items:
            self.registry.record_scan(source.id, ScanPage(preview.recent_items))
            unsynced = []
            for item in preview.recent_items:
                existing = self.podcast.lookup(item.canonical_url)
                if existing:
                    self.registry.record_synced(item.content_key, "podcast", existing)
                else:
                    unsynced.append(item.content_key)
            if initial_sync_count is None or initial_sync_count > 0:
                self.registry.enqueue(
                    unsynced if initial_sync_count is None else unsynced[:initial_sync_count],
                    "podcast", "initial_import", source.display_name,
                )
        return self.registry.get_source(source.id)

    def _import_tweet_baseline(self, source_id: int) -> int:
        found = []
        if not self.tweet_output_dir.is_dir():
            return 0
        for path in sorted(self.tweet_output_dir.rglob("*.md"), key=lambda item: item.stat().st_mtime, reverse=True):
            try:
                head = path.read_text(encoding="utf-8")[:5000]
            except OSError:
                continue
            match = re.search(r'^tweet_id:\s*["\']?(\d+)', head, re.M)
            if not match:
                continue
            tweet_id = match.group(1)
            source_match = re.search(r'^source:\s*["\']?(https://x\.com/[^\s"\']+)', head, re.M)
            title_match = re.search(r'^title:\s*["\']?([^\n"\']+)', head, re.M)
            url = source_match.group(1) if source_match else f"https://x.com/i/status/{tweet_id}"
            author_match = re.search(r"x\.com/([^/]+)/status/", url)
            item = DiscoveredItem(
                f"x:{tweet_id}", url, title_match.group(1) if title_match else path.stem,
                author_match.group(1) if author_match else "", metadata={"id": tweet_id},
            )
            found.append((item, path))
        if not found:
            return 0
        self.registry.record_scan(source_id, ScanPage([item for item, _path in found]), complete=False)
        for item, path in found:
            self.registry.record_synced(item.content_key, "tweet_organizer", {"outputFile": str(path), "imported": True})
        self.registry.seed_overlap(
            source_id, [item.content_key.split(":", 1)[1] for item, _path in found]
        )
        return len(found)

    def scan(self, source_id: int) -> DiscoveryResult:
        with self.runtime.lock(f"source-{source_id}"):
            source = self.registry.get_source(source_id)
            self.runtime.update_operation("scans", str(source_id), outcome="running", startedAt=utc_iso())
            try:
                if source.source_type.startswith("x_"):
                    with self.runtime.lock("x-global"):
                        result = self._scan_unlocked(source)
                else:
                    result = self._scan_unlocked(source)
                self.runtime.update_operation("scans", str(source_id), outcome="complete", finishedAt=utc_iso())
                return result
            except Exception as error:
                self.runtime.update_operation(
                    "scans", str(source_id), outcome="failed", finishedAt=utc_iso(), error=str(error)[:500]
                )
                self.runtime.log(f"scan {source_id} failed: {error}")
                raise

    def _scan_unlocked(self, source) -> DiscoveryResult:
        adapter = self.adapters[source.source_type]
        if source.source_type.startswith("x_"):
            return self._scan_x(source, adapter)
        try:
            page = adapter.scan(source, None)
            existing = {
                row[0] for row in self.registry.db.execute(
                    "SELECT content_key FROM source_relationships WHERE source_id=?",
                    (source.id,),
                )
            }
            result = self.registry.record_scan(source.id, page)
            if source.auto_sync_new and result.new_items:
                new_keys = [item.content_key for item in page.items if item.content_key not in existing]
                self.registry.enqueue(
                    new_keys, "podcast", "auto", source.display_name
                )
            return result
        except Exception as error:
            self.registry.mark_scan_failed(source.id, str(error))
            raise

    def _scan_x(self, source, adapter) -> DiscoveryResult:
        checkpoint = self.registry.get_checkpoint(source.id)
        overlap = set(checkpoint["overlap_ids"])
        pending = checkpoint["pending_cursor"]
        budget = int(source.settings.get("backfill_pages_per_run", 15))
        if budget < 1:
            raise ValueError("backfill_pages_per_run must be positive")
        pages = discovered = new_items = 0
        top_ids: list[str] = []
        cursor = None
        resuming = pending is not None
        try:
            while pages < budget:
                page = adapter.scan(source, cursor)
                pages += 1
                result = self.registry.record_scan(source.id, page, complete=False)
                discovered += result.discovered
                new_items += result.new_items
                ids = [item.content_key.split(":", 1)[1] for item in page.items]
                if not top_ids:
                    top_ids = ids[:5]
                if source.source_type == "x_likes":
                    self.registry.enqueue(
                        [item.content_key for item in page.items], "tweet_organizer", "auto",
                        "likes",
                    )
                else:
                    undecided = self.registry.undecided_items(source.id, page.items)
                    decisions = self.evaluator.evaluate(
                        source.settings, [item.metadata for item in undecided]
                    ) if undecided else []
                    self.registry.record_decisions(source.id, decisions)
                    if not source.settings.get("shadow_mode", True) and source.settings.get("auto_sync_accepted"):
                        self.registry.enqueue(
                            [decision.content_key for decision in decisions if decision.decision == "collect"],
                            "tweet_organizer", "auto", source.display_name,
                        )

                if (
                    source.source_type == "x_likes"
                    and checkpoint["last_complete_at"] is None
                    and pending is None
                ):
                    self.registry.finish_x_scan(
                        source.id, complete=True, pages=pages, overlap_ids=top_ids
                    )
                    return DiscoveryResult(discovered, new_items)

                hit_overlap = bool(overlap.intersection(ids))
                if hit_overlap and not (resuming and cursor is None):
                    self.registry.finish_x_scan(
                        source.id, complete=True, pages=pages, overlap_ids=top_ids
                    )
                    return DiscoveryResult(discovered, new_items, overlap_hit=True)
                if page.exhausted or not page.next_checkpoint:
                    self.registry.finish_x_scan(
                        source.id, complete=True, pages=pages, overlap_ids=top_ids
                    )
                    return DiscoveryResult(discovered, new_items)

                if resuming and cursor is None:
                    cursor = pending
                    resuming = False
                else:
                    cursor = page.next_checkpoint
                if pages < budget:
                    low, high = source.settings.get("page_interval_seconds", [5, 10])
                    self.sleeper(self.interval(float(low), float(high)))

            self.registry.finish_x_scan(
                source.id, complete=False, pages=pages, pending_cursor=cursor,
                stop_reason="page_budget",
            )
            return DiscoveryResult(discovered, new_items)
        except XScanStopped as error:
            self.registry.finish_x_scan(
                source.id, complete=False, pages=pages, pending_cursor=cursor or pending,
                stop_reason=error.reason, error=str(error),
            )
            raise
        except Exception as error:
            self.registry.finish_x_scan(
                source.id, complete=False, pages=pages, pending_cursor=cursor or pending,
                stop_reason="error", error=str(error),
            )
            raise
