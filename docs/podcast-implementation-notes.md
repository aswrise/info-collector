# Podcast Implementation Notes

## Status

- Added `flows/podcast-bookmarks.py` for the `podcast` spool contract.
- Added `skills/podcast-digest/SKILL.md` for Pi-side export of TLDR, 1000-character summary, optional 7000-character summary, and transcript files.
- Added setup script and launchd template for the Pi podcast flow.
- Added popup `保存为播客` and Dashboard `加入队列` entry points.
- Added bookmark-folder routing: links under `收藏播客` import as `podcast` jobs.
- Added focused Node/Python tests for queue semantics and deterministic transcript parsing.

## Export Notes

- The flow writes `source.md`, `meta.json`, and expects Pi to write `result.json` in `~/.info-collector/work/podcast/<hash>/`.
- The Pi skill owns final Obsidian export into `播客收集/`.
- Current flow success is only `result.json` with existing `tldrFile`, `summary1000File`, and `transcriptFile`; `summary7000File` is optional but must exist when present. Legacy three-file results remain readable for idempotency.
- YouTube `published` is parsed from the watch page's structured date fields; oEmbed only provides title/channel metadata.
- YouTube transcript fallback is intentionally limited to Firecrawl and yt-dlp. Missing captions fail with `youtube-transcript-unavailable`; Pi never opens Chrome or invokes OpenCLI.

## Deviations

- `yt-dlp` manual captions and auto captions are checked in two passes instead of one combined command. This keeps `captionKind` reliable without guessing from ambiguous output filenames.
- User feedback changed the entry model from button-first to folder-first. The conservative implementation keeps the direct `保存为播客` shortcut, but the default path is now `收藏播客` bookmark folder -> `podcast` job.
