CREATE TABLE meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
) STRICT;

CREATE TABLE sources (
  id                INTEGER PRIMARY KEY,
  source_type       TEXT NOT NULL CHECK (source_type IN
                      ('youtube_channel','youtube_playlist','x_likes','x_list')),
  canonical_url     TEXT NOT NULL UNIQUE,
  external_id       TEXT NOT NULL,
  display_name      TEXT NOT NULL,
  enabled           INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)),
  auto_sync_new     INTEGER NOT NULL DEFAULT 0 CHECK (auto_sync_new IN (0,1)),
  collection_policy TEXT NOT NULL CHECK (collection_policy IN
                      ('auto_sync_new','always_collect','value_filter')),
  settings_json     TEXT NOT NULL DEFAULT '{}',
  last_scan_at      TEXT,
  last_scan_status  TEXT CHECK (last_scan_status IN
                      ('complete','incomplete','failed','paused_risk') OR last_scan_status IS NULL),
  last_scan_error   TEXT,
  created_at        TEXT NOT NULL
) STRICT;

CREATE TABLE content_items (
  content_key    TEXT PRIMARY KEY,
  platform       TEXT NOT NULL CHECK (platform IN ('youtube','x')),
  canonical_url  TEXT NOT NULL,
  title_or_text  TEXT,
  author         TEXT,
  published_at   TEXT,
  language       TEXT CHECK (language IN ('zh','en','mixed','unknown') OR language IS NULL),
  metadata_json  TEXT NOT NULL DEFAULT '{}',
  created_at     TEXT NOT NULL
) STRICT;

CREATE INDEX idx_items_platform ON content_items(platform, published_at);

CREATE TABLE source_relationships (
  source_id     INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
  content_key   TEXT NOT NULL REFERENCES content_items(content_key),
  status        TEXT NOT NULL DEFAULT 'present'
                  CHECK (status IN ('present','removed','unavailable')),
  first_seen_at TEXT NOT NULL,
  last_seen_at  TEXT NOT NULL,
  position      INTEGER,
  PRIMARY KEY (source_id, content_key)
) STRICT;

CREATE INDEX idx_rel_content ON source_relationships(content_key);

CREATE TABLE sync_jobs (
  id           INTEGER PRIMARY KEY,
  content_key  TEXT NOT NULL REFERENCES content_items(content_key),
  processor    TEXT NOT NULL CHECK (processor IN ('podcast','tweet_organizer')),
  status       TEXT NOT NULL CHECK (status IN ('queued','syncing','synced','failed')),
  trigger      TEXT NOT NULL CHECK (trigger IN ('auto','user','initial_import')),
  attempts     INTEGER NOT NULL DEFAULT 0,
  last_error   TEXT,
  result_json  TEXT,
  created_at   TEXT NOT NULL,
  started_at   TEXT,
  finished_at  TEXT
) STRICT;

CREATE UNIQUE INDEX uq_active_job
  ON sync_jobs(content_key, processor)
  WHERE status IN ('queued','syncing','synced');

CREATE INDEX idx_jobs_status ON sync_jobs(status, created_at);

CREATE TABLE value_decisions (
  content_key      TEXT NOT NULL REFERENCES content_items(content_key),
  source_id        INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
  relevance        INTEGER NOT NULL CHECK (relevance BETWEEN 0 AND 3),
  information_gain INTEGER NOT NULL CHECK (information_gain BETWEEN 0 AND 3),
  usefulness       INTEGER NOT NULL CHECK (usefulness BETWEEN 0 AND 2),
  evidence         INTEGER NOT NULL CHECK (evidence BETWEEN 0 AND 2),
  total            INTEGER NOT NULL CHECK (total BETWEEN 0 AND 10),
  decision         TEXT NOT NULL CHECK (decision IN ('collect','review','noise')),
  reason_code      TEXT CHECK (reason_code IN ('social_chatter','contextless_reaction',
                     'pure_promotion','engagement_bait','repetition','off_topic',
                     'insufficient_context') OR reason_code IS NULL),
  explanation      TEXT,
  confidence       REAL,
  overridden_by    TEXT CHECK (overridden_by IN ('user_collect','user_noise') OR overridden_by IS NULL),
  overridden_at    TEXT,
  decided_at       TEXT NOT NULL,
  PRIMARY KEY (content_key, source_id)
) STRICT;

CREATE INDEX idx_decisions_review ON value_decisions(source_id, decision);

CREATE TABLE source_checkpoints (
  source_id            INTEGER PRIMARY KEY REFERENCES sources(id) ON DELETE CASCADE,
  overlap_ids_json     TEXT,
  pending_cursor       TEXT,
  pending_stop_reason  TEXT CHECK (pending_stop_reason IN
                         ('rate_limited','auth','challenge','error','page_budget')
                         OR pending_stop_reason IS NULL),
  snapshot_json        TEXT,
  last_complete_at     TEXT,
  last_complete_pages  INTEGER
) STRICT;
