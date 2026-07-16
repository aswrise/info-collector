# Source Sync implementation notes

Source of truth: `docs/source-sync-implementation-plan.html` (final, 2026-07-16).

## Folder ownership change (2026-07-17)

Status: implemented, migrated, and deployed.

- Manual podcast collection keeps its existing queue and processing behavior, but standalone outputs live under `播客收集/公共/`.
- Source Sync YouTube outputs live under a direct child folder named after the Channel or Playlist.
- X Likes outputs live under `文章收集/tweet 整理/likes/`; X List outputs live under a direct child folder named after the List.
- A Content Item has one stable product copy. The first source that queues processing becomes its Primary Collection Source, including across failed retries; later Source Relationships add metadata but never copy or relocate the product.
- `播客收集.base` and `tweet 整理.base` remain at their collection roots and cover descendant folders.

## Implemented

- Phase 1: schema v3 migrations, Source Registry, YouTube Channel inspection/scan, idempotent queueing, shared `PodcastProcessor`, Worker, and local no-framework Dashboard/API.
- Phase 2: YouTube Playlist normalization, complete snapshot diff, present/removed/unavailable relationships, position updates, initial sync none/N/all, and `auto_sync_new`.
- Phase 3: private OpenCLI `likes-page` / `list-page` sources, strict 20-item pages, 5–10 second serial paging, 15-page budget and cursor continuation, overlap watermarks, risk stop, all-Likes enqueueing, Tweet Organizer, compliant Obsidian Markdown, and `tweet 整理.base`.
- Phase 4: X List topics and threshold 7, four score dimensions, collect/review/noise, `insufficient_context` review guard, shadow mode, sample export, human overrides, and Likes priority.
- Phase 5: per-source and X-global locks, serial Worker with stale recovery, atomic `status.json`, logs, daily SQLite integrity check and backup, three launchd jobs, setup/uninstall, README, domain language, and temporary-note replacement markers.

## Verification

- `test/source_sync_test.py` covers schema, normalization, Channel command safety, Playlist diff/reorder/unavailable, queue idempotency, queued-only cancellation, persistent disable/re-enable behavior, first-source folder ownership across retries, selected-source Dashboard enqueueing, source-folder propagation through the Worker, stale-safe Worker behavior, request-local Dashboard/SQLite consistency, X page limit/cursor/budget/resume/risk stop, Tweet Markdown/Base output, value thresholds, shadow mode, override preservation, and Likes priority.
- `test/x_page_adapter.test.js` covers the committed OpenCLI page parser, structured media, cursor, and the 20-item ceiling.
- Existing `test/podcast_flow_test.py` remains the compatibility gate for the Processor extraction and now proves Public Collection routing, conservative product migration, and recognition of parameterized legacy YouTube workdirs from canonical `watch?v=` URLs.
- Plists are checked with `plutil -lint`; Base and skill YAML are parsed locally.
- The complete local suite passes: 34 Node tests and 45 Python tests. The manual podcast installer has a regression test for selecting Python 3.10+ and re-enabling its LaunchAgent.
- Live migration moved all 9 `result.json`-referenced podcast Markdown products into `播客收集/公共/`, updated all 9 stored paths, kept `播客收集.base` at the root, and returned 0 on an idempotency rerun.
- The installed Source Sync database is at schema version 3 with `sync_jobs.collection_subdir` and `content_items.sync_disabled`; the updated Dashboard responds at `127.0.0.1:8787`, and the manual podcast LaunchAgent runs with the selected Homebrew Python.
- X Likes baseline recovery scans all descendant collection folders, so re-onboarding after database/work-state loss recognizes notes already stored under `likes/` or any List folder instead of creating duplicates.
- A newly added X Likes source completes its initial scan after the first 20-item page and records that page as its overlap watermark; later scheduled scans collect only newer Likes.
- Read-only live `yt-dlp` inspection passed through the committed adapters for Lenny's Podcast (five current `/videos` entries) and the complete Training Data baseline (91 items).
- `opencli doctor -v` passed with the daemon, extension, and connectivity green. Read-only X checks confirmed safe-stop behavior: `Tenitsugunsmith` is currently protected and List `2058340249626128801` currently resolves to X's “page does not exist” state, so both page adapters return typed command errors instead of treating inaccessible data as an exhausted empty source.

## Deviations

- The one-time podcast migration only moves Markdown files referenced by an existing podcast `result.json`. It deliberately leaves unrelated root-level Markdown untouched and stops rather than overwriting a same-named file already present in `公共/`.
- `source_relationships.source_id` and `source_checkpoints.source_id` use `ON DELETE CASCADE`. The final DDL omitted cascade, but §3.4 explicitly requires deleting a Monitored Source and its relationships while preserving Content Items and Sync Jobs; cascade is the smallest FK-safe implementation of that behavior.
- YouTube Channel scans are incremental, not authoritative snapshots. A depth-5 `/videos` response cannot prove that older Channel videos were removed; treating it as a complete snapshot would incorrectly mark every older Content Item `removed`. Playlist scans remain authoritative snapshots.
- The OpenCLI adapter declaration uses `Strategy.COOKIE` because that is the installed OpenCLI enum, while `opencli/twitter/STRATEGY.md` records the more precise maintenance class as `PAGE_FETCH`.
- The source-sync CLI runs the Worker immediately after scheduled scans; the standalone 30-minute Worker launchd job is the fallback. Dashboard batch enqueueing remains non-blocking instead of holding an HTTP request open for a potentially 45-minute Pi run.

## Live-source status

- The named X fixtures cannot currently produce a positive 20-item sample because of their upstream visibility state, not because BrowserBridge is unavailable. The verification still exercises the plan's required “异常即停” path, and no write action exists in either adapter.
