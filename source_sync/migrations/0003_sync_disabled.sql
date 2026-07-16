ALTER TABLE content_items ADD COLUMN sync_disabled INTEGER NOT NULL DEFAULT 0
  CHECK (sync_disabled IN (0, 1));
