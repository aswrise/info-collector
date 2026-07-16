from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from .db import connect
from .types import DiscoveryResult, EnqueueResult, Job, ScanPage, Source, SourcePreview, ValueDecision


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class SourceRegistry:
    def __init__(self, path: str | Path = "~/.info-collector/source-sync/source-sync.db"):
        self.path = Path(path).expanduser()
        self.db = connect(self.path)

    def close(self) -> None:
        self.db.close()

    def add_source(
        self,
        preview: SourcePreview,
        initial_policy: str | None = None,
        *,
        auto_sync_new: bool = False,
        settings: dict | None = None,
    ) -> Source:
        policy = initial_policy or (
            "always_collect" if preview.source_type == "x_likes" else
            "value_filter" if preview.source_type == "x_list" else
            "auto_sync_new"
        )
        with self.db:
            cursor = self.db.execute(
                """INSERT INTO sources(
                     source_type, canonical_url, external_id, display_name, enabled,
                     auto_sync_new, collection_policy, settings_json, created_at
                   ) VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?)
                   ON CONFLICT(canonical_url) DO NOTHING""",
                (
                    preview.source_type, preview.canonical_url, preview.external_id,
                    preview.display_name, int(auto_sync_new), policy,
                    json.dumps(settings or {}, ensure_ascii=False), utc_now(),
                ),
            )
            if not cursor.rowcount:
                raise ValueError("source already exists")
            source_id = cursor.lastrowid
            self.db.execute(
                "INSERT INTO source_checkpoints(source_id) VALUES (?)", (source_id,)
            )
        return self.get_source(source_id)

    def get_source(self, source_id: int) -> Source:
        row = self.db.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
        if not row:
            raise KeyError(source_id)
        return self._source(row)

    def list_sources(self) -> list[Source]:
        return [self._source(row) for row in self.db.execute("SELECT * FROM sources ORDER BY id")]

    def collection_name(self, content_key: str, source_id: int) -> str:
        row = self.db.execute(
            """SELECT s.source_type, s.display_name FROM source_relationships r
               JOIN sources s ON s.id=r.source_id
               WHERE r.content_key=? AND r.source_id=?""",
            (content_key, source_id),
        ).fetchone()
        if not row:
            raise KeyError((content_key, source_id))
        return "likes" if row["source_type"] == "x_likes" else row["display_name"]

    def delete_source(self, source_id: int) -> None:
        with self.db:
            if not self.db.execute("DELETE FROM sources WHERE id = ?", (source_id,)).rowcount:
                raise KeyError(source_id)

    def record_scan(self, source_id: int, page: ScanPage, *, complete: bool = True) -> DiscoveryResult:
        now = utc_now()
        existing = {
            row[0] for row in self.db.execute(
                "SELECT content_key FROM source_relationships WHERE source_id = ?",
                (source_id,),
            )
        }
        seen = {item.content_key for item in page.items}
        unavailable_keys = {
            item.content_key for item in page.items
            if item.metadata.get("availability") in {
                "private", "subscriber_only", "needs_auth", "premium_only"
            }
        }
        with self.db:
            for item in page.items:
                platform = item.content_key.split(":", 1)[0]
                self.db.execute(
                    """INSERT INTO content_items(
                         content_key, platform, canonical_url, title_or_text, author,
                         published_at, metadata_json, created_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(content_key) DO UPDATE SET
                         canonical_url=excluded.canonical_url,
                         title_or_text=excluded.title_or_text,
                         author=excluded.author,
                         published_at=COALESCE(excluded.published_at, content_items.published_at),
                         metadata_json=excluded.metadata_json""",
                    (
                        item.content_key, platform, item.canonical_url, item.title_or_text,
                        item.author, item.published_at,
                        json.dumps(item.metadata, ensure_ascii=False), now,
                    ),
                )
                self.db.execute(
                    """INSERT INTO source_relationships(
                         source_id, content_key, status, first_seen_at, last_seen_at, position
                       ) VALUES (?, ?, ?, ?, ?, ?)
                       ON CONFLICT(source_id, content_key) DO UPDATE SET
                         status=excluded.status, last_seen_at=excluded.last_seen_at,
                         position=excluded.position""",
                    (
                        source_id, item.content_key,
                        "unavailable" if item.content_key in unavailable_keys else "present",
                        now, now, item.position,
                    ),
                )

            removed = 0
            if page.is_snapshot:
                missing = existing - seen
                if missing:
                    marks = ",".join("?" for _ in missing)
                    removed = self.db.execute(
                        f"""UPDATE source_relationships SET status='removed'
                            WHERE source_id=? AND content_key IN ({marks})""",
                        (source_id, *missing),
                    ).rowcount
                self.db.execute(
                    "UPDATE source_checkpoints SET snapshot_json=? WHERE source_id=?",
                    (json.dumps([item.content_key for item in page.items]), source_id),
                )

            if complete:
                self.db.execute(
                    """UPDATE sources SET last_scan_at=?, last_scan_status='complete',
                       last_scan_error=NULL WHERE id=?""",
                    (now, source_id),
                )
        return DiscoveryResult(
            len(page.items), len(seen - existing), removed, len(unavailable_keys)
        )

    def mark_scan_failed(self, source_id: int, error: str, status: str = "failed") -> None:
        with self.db:
            self.db.execute(
                "UPDATE sources SET last_scan_at=?, last_scan_status=?, last_scan_error=? WHERE id=?",
                (utc_now(), status, error[:500], source_id),
            )

    def get_checkpoint(self, source_id: int) -> dict:
        row = self.db.execute(
            "SELECT * FROM source_checkpoints WHERE source_id=?", (source_id,)
        ).fetchone()
        if not row:
            raise KeyError(source_id)
        data = dict(row)
        data["overlap_ids"] = json.loads(data.pop("overlap_ids_json") or "[]")
        return data

    def seed_overlap(self, source_id: int, tweet_ids: list[str]) -> None:
        with self.db:
            self.db.execute(
                "UPDATE source_checkpoints SET overlap_ids_json=? WHERE source_id=?",
                (json.dumps(tweet_ids[:5]), source_id),
            )

    def finish_x_scan(
        self,
        source_id: int,
        *,
        complete: bool,
        pages: int,
        overlap_ids: list[str] | None = None,
        pending_cursor: str | None = None,
        stop_reason: str | None = None,
        error: str | None = None,
    ) -> None:
        now = utc_now()
        status = "complete" if complete else (
            "paused_risk" if stop_reason in {"rate_limited", "auth", "challenge"} else "incomplete"
        )
        with self.db:
            self.db.execute(
                """UPDATE source_checkpoints SET
                     overlap_ids_json=CASE WHEN ? THEN ? ELSE overlap_ids_json END,
                     pending_cursor=?, pending_stop_reason=?,
                     last_complete_at=CASE WHEN ? THEN ? ELSE last_complete_at END,
                     last_complete_pages=CASE WHEN ? THEN ? ELSE last_complete_pages END
                   WHERE source_id=?""",
                (
                    int(complete), json.dumps(overlap_ids or []), pending_cursor, stop_reason,
                    int(complete), now, int(complete), pages, source_id,
                ),
            )
            self.db.execute(
                """UPDATE sources SET last_scan_at=?, last_scan_status=?, last_scan_error=?
                   WHERE id=?""",
                (now, status, error[:500] if error else None, source_id),
            )

    def undecided_items(self, source_id: int, items: list) -> list:
        decided = {
            row[0] for row in self.db.execute(
                "SELECT content_key FROM value_decisions WHERE source_id=?", (source_id,)
            )
        }
        return [item for item in items if item.content_key not in decided]

    def record_decisions(self, source_id: int, decisions: Iterable[ValueDecision]) -> None:
        now = utc_now()
        with self.db:
            for decision in decisions:
                self.db.execute(
                    """INSERT INTO value_decisions(
                         content_key, source_id, relevance, information_gain, usefulness,
                         evidence, total, decision, reason_code, explanation, confidence, decided_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(content_key, source_id) DO UPDATE SET
                         relevance=excluded.relevance, information_gain=excluded.information_gain,
                         usefulness=excluded.usefulness, evidence=excluded.evidence,
                         total=excluded.total, decision=excluded.decision,
                         reason_code=excluded.reason_code, explanation=excluded.explanation,
                         confidence=excluded.confidence, decided_at=excluded.decided_at""",
                    (
                        decision.content_key, source_id, decision.relevance,
                        decision.information_gain, decision.usefulness, decision.evidence,
                        decision.total, decision.decision, decision.reason_code,
                        decision.explanation, decision.confidence, now,
                    ),
                )

    def override_decision(self, content_key: str, source_id: int, override: str) -> None:
        if override not in {"user_collect", "user_noise"}:
            raise ValueError("invalid override")
        with self.db:
            if not self.db.execute(
                """UPDATE value_decisions SET overridden_by=?, overridden_at=?
                   WHERE content_key=? AND source_id=?""",
                (override, utc_now(), content_key, source_id),
            ).rowcount:
                raise KeyError((content_key, source_id))

    def shadow_samples(self, source_id: int) -> list[dict]:
        return [dict(row) for row in self.db.execute(
            """SELECT d.*, i.canonical_url, i.title_or_text, i.author, i.metadata_json
               FROM value_decisions d JOIN content_items i USING(content_key)
               WHERE d.source_id=? ORDER BY d.decided_at""",
            (source_id,),
        )]

    def enqueue(
        self,
        content_keys: Iterable[str],
        processor: str,
        trigger: str = "user",
        collection_subdir: str | None = None,
    ) -> EnqueueResult:
        result = EnqueueResult()
        with self.db:
            for key in dict.fromkeys(content_keys):
                item = self.db.execute(
                    "SELECT sync_disabled FROM content_items WHERE content_key=?", (key,)
                ).fetchone()
                if not item:
                    raise KeyError(key)
                if item["sync_disabled"]:
                    result = EnqueueResult(
                        result.queued, result.skipped_synced, result.skipped_active,
                        result.skipped_disabled + 1,
                    )
                    continue
                owner = self.db.execute(
                    """SELECT collection_subdir FROM sync_jobs
                       WHERE content_key=? AND processor=? AND collection_subdir IS NOT NULL
                       ORDER BY id LIMIT 1""",
                    (key, processor),
                ).fetchone()
                folder = owner[0] if owner else collection_subdir
                cursor = self.db.execute(
                    """INSERT INTO sync_jobs(
                         content_key, processor, status, trigger, collection_subdir, created_at
                       ) VALUES (?, ?, 'queued', ?, ?, ?) ON CONFLICT DO NOTHING""",
                    (key, processor, trigger, folder, utc_now()),
                )
                if cursor.rowcount:
                    result = EnqueueResult(
                        result.queued + 1, result.skipped_synced,
                        result.skipped_active, result.skipped_disabled,
                    )
                    continue
                status = self.db.execute(
                    """SELECT status FROM sync_jobs WHERE content_key=? AND processor=?
                       AND status IN ('queued','syncing','synced')""",
                    (key, processor),
                ).fetchone()
                if not status:
                    raise KeyError(key)
                result = EnqueueResult(
                    result.queued,
                    result.skipped_synced + (status[0] == "synced"),
                    result.skipped_active + (status[0] != "synced"),
                    result.skipped_disabled,
                )
        return result

    def record_synced(self, content_key: str, processor: str, result: dict) -> None:
        with self.db:
            self.db.execute(
                """INSERT INTO sync_jobs(
                     content_key, processor, status, trigger, result_json, created_at, finished_at
                   ) VALUES (?, ?, 'synced', 'initial_import', ?, ?, ?)
                   ON CONFLICT DO NOTHING""",
                (content_key, processor, json.dumps(result, ensure_ascii=False), utc_now(), utc_now()),
            )

    def cancel_jobs(self, job_ids: Iterable[int]) -> int:
        ids = list(dict.fromkeys(job_ids))
        with self.db:
            for job_id in ids:
                if self.db.execute(
                    "DELETE FROM sync_jobs WHERE id=? AND status='queued'", (job_id,)
                ).rowcount:
                    continue
                if self.db.execute("SELECT 1 FROM sync_jobs WHERE id=?", (job_id,)).fetchone():
                    raise ValueError("only queued jobs can be cancelled")
                raise KeyError(job_id)
        return len(ids)

    def set_disabled(self, content_keys: Iterable[str], disabled: bool) -> int:
        keys = list(dict.fromkeys(content_keys))
        with self.db:
            for key in keys:
                if not self.db.execute(
                    "UPDATE content_items SET sync_disabled=? WHERE content_key=?",
                    (int(disabled), key),
                ).rowcount:
                    raise KeyError(key)
        return len(keys)

    def recover_stale_jobs(self, minutes: int = 30) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat(
            timespec="seconds"
        ).replace("+00:00", "Z")
        with self.db:
            return self.db.execute(
                """UPDATE sync_jobs SET status='queued', started_at=NULL
                   WHERE status='syncing' AND started_at < ?""",
                (cutoff,),
            ).rowcount

    def claim_jobs(self, limit: int = 2, processor: str | None = None) -> list[Job]:
        now = utc_now()
        processor_filter = " AND processor=?" if processor else ""
        params = (processor, limit) if processor else (limit,)
        with self.db:
            rows = self.db.execute(
                f"""UPDATE sync_jobs SET status='syncing', started_at=?, attempts=attempts+1
                    WHERE id IN (
                      SELECT id FROM sync_jobs WHERE status='queued'{processor_filter}
                      ORDER BY created_at LIMIT ?
                    ) RETURNING *""",
                (now, *params),
            ).fetchall()
        jobs = []
        for row in rows:
            item = self.db.execute(
                "SELECT canonical_url, metadata_json FROM content_items WHERE content_key=?",
                (row["content_key"],),
            ).fetchone()
            metadata = json.loads(item["metadata_json"])
            relationships = self.db.execute(
                """SELECT s.source_type, s.display_name FROM source_relationships r
                   JOIN sources s ON s.id=r.source_id
                   WHERE r.content_key=? AND r.status='present'""",
                (row["content_key"],),
            ).fetchall()
            if row["processor"] == "tweet_organizer":
                likes = any(rel["source_type"] == "x_likes" for rel in relationships)
                metadata["origin"] = "likes" if likes else "list"
                metadata["lists"] = [
                    "Likes" if rel["source_type"] == "x_likes" else rel["display_name"]
                    for rel in relationships
                ]
            jobs.append(Job(
                row["id"], row["content_key"], row["processor"], row["status"],
                row["trigger"], row["attempts"], item["canonical_url"],
                metadata, row["collection_subdir"],
            ))
        return jobs

    def finish_job(self, job_id: int, result: dict | None = None, error: str | None = None) -> str:
        status = "failed" if error else "synced"
        with self.db:
            if not self.db.execute(
                """UPDATE sync_jobs SET status=?, last_error=?, result_json=?, finished_at=?
                   WHERE id=? AND status='syncing'""",
                (
                    status, error[:500] if error else None,
                    json.dumps(result, ensure_ascii=False) if result is not None else None,
                    utc_now(), job_id,
                ),
            ).rowcount:
                raise ValueError("job is not syncing")
            if result and result.get("language") in {"zh", "en", "mixed", "unknown"}:
                self.db.execute(
                    """UPDATE content_items SET language=? WHERE content_key=(
                         SELECT content_key FROM sync_jobs WHERE id=?
                       )""",
                    (result["language"], job_id),
                )
        return status

    def list_items(self, source_id: int | None = None) -> list[dict]:
        where = "WHERE r.source_id=?" if source_id is not None else ""
        params = (source_id,) if source_id is not None else ()
        rows = self.db.execute(
            f"""SELECT i.*, r.source_id, r.status AS relationship_status,
                       j.id AS job_id, j.status AS sync_status, j.last_error, j.result_json,
                       d.relevance, d.information_gain, d.usefulness, d.evidence,
                       d.total, d.decision, d.reason_code, d.explanation, d.confidence,
                       d.overridden_by,
                       CASE d.overridden_by WHEN 'user_collect' THEN 'collect'
                         WHEN 'user_noise' THEN 'noise' ELSE d.decision END AS effective_decision
                FROM content_items i
                JOIN source_relationships r ON r.content_key=i.content_key
                LEFT JOIN value_decisions d ON d.content_key=i.content_key AND d.source_id=r.source_id
                LEFT JOIN sync_jobs j ON j.id=(
                  SELECT id FROM sync_jobs WHERE content_key=i.content_key
                  ORDER BY id DESC LIMIT 1
                )
                {where}
                ORDER BY COALESCE(i.published_at, i.created_at) DESC""",
            params,
        ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _source(row: sqlite3.Row) -> Source:
        return Source(
            row["id"], row["source_type"], row["canonical_url"], row["external_id"],
            row["display_name"], bool(row["enabled"]), bool(row["auto_sync_new"]),
            row["collection_policy"], json.loads(row["settings_json"]),
            row["last_scan_at"], row["last_scan_status"], row["last_scan_error"],
        )
