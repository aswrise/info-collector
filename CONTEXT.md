# Article Queue Extension

This context describes a Chrome extension that turns saved article links into a durable processing queue. Chrome bookmarks may provide input, but the extension owns the processing state.

## Language

**Article**:
A URL saved by the user for later processing. An article may originate from a bookmark or from a direct save action.
_Avoid_: bookmark, page, link when referring to queued work

**Article Queue**:
The extension-owned collection of articles and their processing states.
_Avoid_: bookmark folder, reading list

**Bookmark Source**:
A Chrome bookmark folder used as an import source for articles. It is not the source of truth for processing state.
_Avoid_: queue, database, task list

**Queue Record**:
The extension-owned record for one article, including identity, source metadata, and processing state.
_Avoid_: bookmark item

**Article Key**:
The stable identity for an article across devices, derived from a normalized URL. Chrome bookmark IDs may be recorded as source metadata but are not article identity.
_Avoid_: bookmark id, tab id

**Processing State**:
The durable state for one article under one processing type. It reflects the extension's view of external processing, not the article's location in Chrome bookmarks.
_Avoid_: bookmark status

**Done Article**:
An article whose external processing flow completed successfully for a specific processing type and should not be processed again for that type.
_Avoid_: translated article, deleted article, read article

**External Processing Flow**:
A process outside the queue that performs work for an article and reports success back to the extension.
_Avoid_: translation when referring to processing in general

**Processing Type**:
A named category of external processing, such as translation, transcript extraction, or book acquisition. Each processing type has independent state for the same article, and each maps to one external processing flow. Types are registered data, not code; the first registered type is `translate`.
_Avoid_: global status, action when referring to durable processing identity

**Completion Report**:
A structured message from an external processing flow stating the outcome (done, failed, or a processing claim) for one or more articles under one processing type. Reports are the only way external flows change processing state.
_Avoid_: log, notification

**Processing Article**:
An article an external processing flow has claimed for one processing type but not yet reported on. The claim is only valid while the flow is running; if the flow stops without reporting, the claim lapses and the article returns to pending.
_Avoid_: running article, locked article

**Ignored Article**:
An article the user has explicitly excluded from a processing type. It is not offered to external flows and is not pending.
_Avoid_: deleted article, failed article

**Podcast Source**:
An article whose content is longform spoken-word material — a podcast episode, interview, or talk. Judged by content shape, not by media container: an edited interview article is a podcast source; a YouTube URL is only a podcast source when a transcript can be found. The `podcast` processing type digests these.
_Avoid_: video, audio, episode when referring to queued work

**Transcript**:
The full text of a podcast source, obtained from an existing webpage, video captions, or a platform transcript panel. The podcast flow never creates transcripts from audio; it only finds existing ones.
_Avoid_: subtitles when referring to the assembled text, transcription (implies speech-to-text)

**Transcript Source**:
What kind of text was found: an existing webpage (`webpage_transcript`), YouTube manual captions (`youtube_manual_caption`), YouTube auto-generated captions (`youtube_auto_caption`), or a YouTube caption whose manual/auto status is unknown (`youtube_unknown_caption`). Determines source reliability, which every digest records so readers know how trustworthy the text is.
_Avoid_: provider, origin URL

**Transcript Provider**:
The mechanism that retrieved the transcript, such as Defuddle, Firecrawl, yt-dlp, or the YouTube transcript panel in the user's logged-in Chrome. It is recorded separately from Transcript Source because a provider can return different source kinds.
_Avoid_: source when referring to retrieval mechanism

**Podcast Digest**:
The layered output product of the podcast flow for one article: TLDR, 1000-character summary, optional 7000-character summary when the full Chinese transcript is at least 7000 characters, and the full Chinese transcript itself (cleaned original if the source is Chinese, full translation if English).
_Avoid_: summary alone when referring to the whole product

**Monitored Source**:
A YouTube Channel, YouTube Playlist, X Likes timeline, or X List that Source Sync scans repeatedly. It owns discovery settings and health, not generated content.
_Avoid_: feed, bookmark folder

**Content Item**:
A globally identified YouTube video or Tweet discovered in one or more Monitored Sources. Its stable key is `youtube:<video_id>` or `x:<tweet_id>`.
_Avoid_: Queue Record, bookmark

**Source Relationship**:
The observation that a Content Item is present, removed, or unavailable in one Monitored Source, including first/last observation and Playlist position.
_Avoid_: source when referring to the Content Item itself

**Public Collection**:
The output grouping for standalone podcast or video items entering through the manual collection flow rather than through a Monitored Source. It is not itself a Monitored Source.
_Avoid_: default source, root folder

**Primary Collection Source**:
The Monitored Source or Public Collection that first causes a Content Item to be processed and owns its single stable product copy. Later Source Relationships do not duplicate or relocate that product.
_Avoid_: every source, current source

**Discovery State**:
The durable scan checkpoint for a Monitored Source: overlap IDs or Playlist snapshot, pending cursor, stop reason, and last complete scan.
_Avoid_: processing state

**Sync Job**:
Source Sync's durable request to run one shared Processor for a Content Item. Its states are `queued`, `syncing`, `synced`, and `failed`.
_Avoid_: Queue Record, scan

**Collection Policy**:
The source-specific rule that decides whether discovered content becomes a Sync Job: `auto_sync_new`, `always_collect`, or `value_filter`.
_Avoid_: filter when referring to the full policy

**Value Decision**:
An X List-specific four-dimension assessment stored per Content Item and Monitored Source. The original scores remain intact when a user overrides collect/noise.
_Avoid_: global Tweet score

**Organized Tweet**:
The Obsidian Markdown product created from a structured Tweet record, preserving original text, translation where needed, quotes, cards, and media URLs.
_Avoid_: translated article

**Archived Record**:
A queue record whose processing is finished and which has been moved out of the active queue. It still counts as processing history: re-importing the same article must not make it pending again.
_Avoid_: deleted record
