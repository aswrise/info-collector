import json
import plistlib
import subprocess
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from source_sync.adapters.youtube import (
    YouTubeChannelAdapter,
    content_key,
    normalize_youtube_source,
)
from source_sync.adapters.x import OpenCLIPageClient, XScanStopped, normalize_x_source
from source_sync.registry import SourceRegistry
from source_sync.service import SourceSyncService
from source_sync.types import DiscoveredItem, ScanPage, SourcePreview
from source_sync.worker import Worker
from source_sync.runtime import Runtime
from source_sync.server import SourceSyncHandler
from processors.tweet import TweetOrganizer
from processors.value import ValueEvaluator, decide
from source_sync.types import ValueDecision


def item(video_id, position=0):
    return DiscoveredItem(
        f"youtube:{video_id}",
        f"https://www.youtube.com/watch?v={video_id}",
        f"Video {video_id}",
        "Channel",
        "2026-07-16",
        position,
        {"id": video_id},
    )


class SourceSyncTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.registry = SourceRegistry(Path(self.tmp.name) / "source-sync.db")

    def tearDown(self):
        self.registry.close()
        self.tmp.cleanup()

    def add_channel(self, **options):
        return self.registry.add_source(SourcePreview(
            "youtube_channel",
            "https://www.youtube.com/@example",
            "@example",
            "Example",
            None,
            [],
        ), settings={"scan_depth": 5}, **options)

    def add_likes(self, pages=15):
        return self.registry.add_source(SourcePreview(
            "x_likes", "https://x.com/example/likes", "example", "@example Likes",
            None, [],
        ), settings={"page_interval_seconds": [5, 10], "backfill_pages_per_run": pages})

    def add_list(self, **settings):
        policy = {
            "topics": ["AI products"], "minimum_collect_score": 7,
            "auto_sync_accepted": False, "shadow_mode": True,
            "page_interval_seconds": [5, 10], "backfill_pages_per_run": 15,
            **settings,
        }
        return self.registry.add_source(SourcePreview(
            "x_list", "https://x.com/i/lists/2058340249626128801",
            "2058340249626128801", "AI Products", None, [],
        ), settings=policy)

    def test_schema_matches_current_contract(self):
        tables = {
            row[0] for row in self.registry.db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        self.assertTrue({
            "meta", "sources", "content_items", "source_relationships",
            "sync_jobs", "value_decisions", "source_checkpoints",
        } <= tables)
        self.assertEqual(
            self.registry.db.execute(
                "SELECT value FROM meta WHERE key='schema_version'"
            ).fetchone()[0],
            "3",
        )

    def test_background_launch_agents_deny_user_library(self):
        templates = Path(__file__).parents[1] / "templates"
        for name in (
            "com.info-collector.source-sync-worker.plist",
            "com.info-collector.source-sync-x.plist",
            "com.info-collector.source-sync-youtube.plist",
            "com.pi.podcast-bookmarks.plist",
        ):
            with self.subTest(name=name):
                arguments = plistlib.loads((templates / name).read_bytes())["ProgramArguments"]
                self.assertEqual(arguments[:2], ["/usr/bin/sandbox-exec", "-p"])
                self.assertIn(
                    '(deny file-read* file-write* (subpath "__HOME__/Library"))',
                    arguments[2],
                )

        for name in ("setup-pi-flow.sh", "setup-pi-podcast-flow.sh"):
            with self.subTest(name=name):
                setup = (Path(__file__).parents[1] / "scripts" / name).read_text()
                self.assertIn('"/usr/bin/sandbox-exec", "-p", sandbox', setup)
                self.assertIn("deny file-read* file-write*", setup)

    def test_dashboard_state_uses_request_local_sqlite_connection(self):
        source = self.add_channel()
        self.registry.record_scan(source.id, ScanPage([item("dashboard")]))
        SourceSyncHandler.db_path = self.registry.path
        server = ThreadingHTTPServer(("127.0.0.1", 0), SourceSyncHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{server.server_port}/api/state", timeout=5
            ) as response:
                state = json.load(response)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        self.assertEqual(state["sources"][0]["id"], source.id)
        self.assertEqual(state["items"][0]["content_key"], "youtube:dashboard")

    def test_dashboard_static_assets_disable_caching(self):
        SourceSyncHandler.db_path = self.registry.path
        server = ThreadingHTTPServer(("127.0.0.1", 0), SourceSyncHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{server.server_port}/app.js", timeout=5
            ) as response:
                cache_control = response.headers.get("Cache-Control")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        self.assertEqual(cache_control, "no-store")

    def test_dashboard_enqueue_uses_the_selected_source_collection(self):
        playlist = self.registry.add_source(SourcePreview(
            "youtube_playlist", "https://www.youtube.com/playlist?list=PL1", "PL1",
            "Training Data", 1, [],
        ))
        self.registry.record_scan(playlist.id, ScanPage([item("shared")], is_snapshot=True))
        SourceSyncHandler.db_path = self.registry.path
        server = ThreadingHTTPServer(("127.0.0.1", 0), SourceSyncHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/api/enqueue",
                data=json.dumps({"items": [{
                    "content_key": "youtube:shared", "source_id": playlist.id,
                }]}).encode(),
                headers={"Content-Type": "application/json"}, method="POST",
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                result = json.load(response)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        self.assertEqual(result["queued"], 1)
        self.assertEqual(self.registry.db.execute(
            "SELECT collection_subdir FROM sync_jobs WHERE content_key='youtube:shared'"
        ).fetchone()[0], "Training Data")

    def test_youtube_source_normalization_and_content_key(self):
        self.assertEqual(normalize_youtube_source("youtube.com/@Example/videos"), (
            "youtube_channel", "https://www.youtube.com/@Example", "@Example"
        ))
        self.assertEqual(normalize_youtube_source(
            "https://youtube.com/watch?v=x&list=PL123"
        ), (
            "youtube_playlist", "https://www.youtube.com/playlist?list=PL123", "PL123"
        ))
        self.assertEqual(content_key("abc123"), "youtube:abc123")
        self.assertEqual(normalize_x_source("@example"), (
            "x_likes", "https://x.com/example/likes", "example"
        ))
        self.assertEqual(normalize_x_source("https://x.com/i/lists/2058340249626128801"), (
            "x_list", "https://x.com/i/lists/2058340249626128801", "2058340249626128801"
        ))

    def test_channel_adapter_never_downloads_media_and_limits_depth(self):
        calls = []

        def runner(command):
            calls.append(command)
            rows = [json.dumps({
                "id": "abc123", "title": "Interview", "playlist_channel": "Example"
            })]
            return subprocess.CompletedProcess(command, 0, "\n".join(rows), "")

        preview = YouTubeChannelAdapter(runner).inspect("https://youtube.com/@example")
        self.assertEqual(preview.recent_items[0].content_key, "youtube:abc123")
        self.assertEqual(preview.display_name, "Example")
        self.assertEqual(calls[0], [
            "yt-dlp", "--flat-playlist", "--playlist-end", "5", "--dump-json",
            "https://www.youtube.com/@example/videos",
        ])

    def test_channel_history_fetches_one_bounded_page(self):
        calls = []

        def runner(command):
            calls.append(command)
            rows = [json.dumps({"id": str(index), "title": f"Video {index}"})
                    for index in range(21)]
            return subprocess.CompletedProcess(command, 0, "\n".join(rows), "")

        source = self.add_channel()
        page = YouTubeChannelAdapter(runner).history(source, 3)

        self.assertEqual(len(page.items), 20)
        self.assertEqual((page.items[0].position, page.items[-1].position), (40, 59))
        self.assertEqual(page.next_checkpoint, "4")
        self.assertFalse(page.exhausted)
        self.assertEqual(calls[0], [
            "yt-dlp", "--flat-playlist", "--playlist-start", "41",
            "--playlist-end", "61", "--dump-json",
            "https://www.youtube.com/@example/videos",
        ])

    def test_browsing_channel_history_records_items_without_queueing_them(self):
        source = self.add_channel()

        class Adapter:
            def history(self, _source, page, page_size):
                self.request = (page, page_size)
                return ScanPage([item("older", 20)], next_checkpoint="3", exhausted=False)

        adapter = Adapter()
        class Podcast:
            def lookup(self, _url):
                return None

        service = SourceSyncService(
            self.registry, adapters={"youtube_channel": adapter}, podcast=Podcast()
        )

        result = service.youtube_history(source.id, 2)

        self.assertEqual(adapter.request, (2, 20))
        self.assertEqual(result["items"][0]["content_key"], "youtube:older")
        self.assertTrue(result["has_previous"])
        self.assertTrue(result["has_next"])
        self.assertIsNone(self.registry.db.execute(
            "SELECT 1 FROM sync_jobs WHERE content_key='youtube:older'"
        ).fetchone())

    def test_snapshot_diff_marks_removed_and_readding_restores_present(self):
        source = self.add_channel()
        self.registry.record_scan(source.id, ScanPage([item("a"), item("b", 1)], is_snapshot=True))
        result = self.registry.record_scan(source.id, ScanPage([item("b")], is_snapshot=True))
        self.assertEqual(result.removed, 1)
        status = self.registry.db.execute(
            "SELECT status FROM source_relationships WHERE source_id=? AND content_key='youtube:a'",
            (source.id,),
        ).fetchone()[0]
        self.assertEqual(status, "removed")

        self.registry.record_scan(source.id, ScanPage([item("a")], is_snapshot=True))
        status = self.registry.db.execute(
            "SELECT status FROM source_relationships WHERE source_id=? AND content_key='youtube:a'",
            (source.id,),
        ).fetchone()[0]
        self.assertEqual(status, "present")

    def test_snapshot_updates_positions_and_marks_unavailable(self):
        source = self.add_channel()
        unavailable = DiscoveredItem(
            "youtube:a", "https://www.youtube.com/watch?v=a", "Private video", "",
            None, 1, {"availability": "private"},
        )
        self.registry.record_scan(source.id, ScanPage([item("b"), unavailable], is_snapshot=True))
        rows = self.registry.db.execute(
            "SELECT content_key, status, position FROM source_relationships ORDER BY position"
        ).fetchall()
        self.assertEqual([tuple(row) for row in rows], [
            ("youtube:b", "present", 0), ("youtube:a", "unavailable", 1)
        ])

    def test_enqueue_is_idempotent_and_worker_recovers(self):
        source = self.add_channel()
        self.registry.record_scan(source.id, ScanPage([item("a")]))
        first = self.registry.enqueue(["youtube:a", "youtube:a"], "podcast")
        second = self.registry.enqueue(["youtube:a"], "podcast")
        self.assertEqual((first.queued, first.skipped_active), (1, 0))
        self.assertEqual((second.queued, second.skipped_active), (0, 1))

        job = self.registry.claim_jobs(1)[0]
        self.assertEqual(job.status, "syncing")
        self.registry.finish_job(job.id, {"tldrFile": "/tmp/tldr.md"})
        third = self.registry.enqueue(["youtube:a"], "podcast")
        self.assertEqual(third.skipped_synced, 1)

    def test_cancel_jobs_deletes_only_queued_requests(self):
        source = self.add_channel()
        self.registry.record_scan(source.id, ScanPage([item("cancel")]))
        self.registry.enqueue(["youtube:cancel"], "podcast")
        job_id = self.registry.db.execute(
            "SELECT id FROM sync_jobs WHERE content_key='youtube:cancel'"
        ).fetchone()[0]

        self.registry.cancel_jobs([job_id])

        self.assertIsNone(self.registry.db.execute(
            "SELECT 1 FROM sync_jobs WHERE id=?", (job_id,)
        ).fetchone())
        self.assertIsNotNone(self.registry.db.execute(
            "SELECT 1 FROM content_items WHERE content_key='youtube:cancel'"
        ).fetchone())
        self.assertIsNotNone(self.registry.db.execute(
            "SELECT 1 FROM source_relationships WHERE content_key='youtube:cancel'"
        ).fetchone())

        self.registry.enqueue(["youtube:cancel"], "podcast")
        syncing = self.registry.claim_jobs(1)[0]
        with self.assertRaisesRegex(ValueError, "queued"):
            self.registry.cancel_jobs([syncing.id])

    def test_dashboard_can_batch_cancel_queued_jobs(self):
        source = self.add_channel()
        self.registry.record_scan(source.id, ScanPage([item("dashboard-cancel")]))
        self.registry.enqueue(["youtube:dashboard-cancel"], "podcast")
        job_id = self.registry.db.execute(
            "SELECT id FROM sync_jobs WHERE content_key='youtube:dashboard-cancel'"
        ).fetchone()[0]
        SourceSyncHandler.db_path = self.registry.path
        server = ThreadingHTTPServer(("127.0.0.1", 0), SourceSyncHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/api/jobs/cancel",
                data=json.dumps({"job_ids": [job_id]}).encode(),
                headers={"Content-Type": "application/json"}, method="POST",
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                result = json.load(response)
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/api/items/disabled",
                data=json.dumps({
                    "content_keys": ["youtube:dashboard-cancel"], "disabled": True,
                }).encode(),
                headers={"Content-Type": "application/json"}, method="POST",
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                disabled = json.load(response)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        self.assertEqual(result, {"cancelled": 1})
        self.assertIsNone(self.registry.db.execute(
            "SELECT 1 FROM sync_jobs WHERE id=?", (job_id,)
        ).fetchone())

        self.assertEqual(disabled, {"updated": 1})
        self.assertEqual(self.registry.db.execute(
            "SELECT sync_disabled FROM content_items WHERE content_key='youtube:dashboard-cancel'"
        ).fetchone()[0], 1)

    def test_disabled_items_cannot_be_requeued(self):
        source = self.add_channel()
        self.registry.record_scan(source.id, ScanPage([item("disabled")]))

        self.registry.set_disabled(["youtube:disabled"], True)
        result = self.registry.enqueue(["youtube:disabled"], "podcast")

        self.assertEqual(result.skipped_disabled, 1)
        self.assertIsNone(self.registry.db.execute(
            "SELECT 1 FROM sync_jobs WHERE content_key='youtube:disabled'"
        ).fetchone())
        self.assertEqual(self.registry.list_items()[0]["sync_disabled"], 1)

        self.registry.set_disabled(["youtube:disabled"], False)
        self.assertEqual(
            self.registry.enqueue(["youtube:disabled"], "podcast").queued, 1
        )

    def test_first_enqueued_collection_owns_the_single_job(self):
        source = self.add_channel()
        self.registry.record_scan(source.id, ScanPage([item("owned")]))

        self.registry.enqueue(
            ["youtube:owned"], "podcast", collection_subdir="Training Data"
        )
        self.registry.enqueue(
            ["youtube:owned"], "podcast", collection_subdir="Lenny's Podcast"
        )

        job = self.registry.claim_jobs(1)[0]
        self.assertEqual(job.collection_subdir, "Training Data")

        self.registry.finish_job(job.id, error="temporary failure")
        self.registry.enqueue(
            ["youtube:owned"], "podcast", collection_subdir="Lenny's Podcast"
        )
        retry = self.registry.claim_jobs(1)[0]
        self.assertEqual(retry.collection_subdir, "Training Data")

    def test_worker_passes_primary_collection_to_processor(self):
        source = self.add_channel()
        self.registry.record_scan(source.id, ScanPage([item("worker-folder")]))
        self.registry.enqueue(
            ["youtube:worker-folder"], "podcast", collection_subdir="Lenny's Podcast"
        )

        class Podcast:
            def process(self, _url, _metadata, collection_subdir=None):
                self.collection_subdir = collection_subdir
                return {"tldrFile": "/tmp/tldr.md"}

        podcast = Podcast()
        Worker(self.registry, podcast=podcast).run()

        self.assertEqual(podcast.collection_subdir, "Lenny's Podcast")

    def test_deleting_source_preserves_content_and_jobs(self):
        source = self.add_channel()
        self.registry.record_scan(source.id, ScanPage([item("a")]))
        self.registry.enqueue(["youtube:a"], "podcast")
        self.registry.delete_source(source.id)
        self.assertIsNotNone(self.registry.db.execute(
            "SELECT 1 FROM content_items WHERE content_key='youtube:a'"
        ).fetchone())
        self.assertIsNotNone(self.registry.db.execute(
            "SELECT 1 FROM sync_jobs WHERE content_key='youtube:a'"
        ).fetchone())

    def test_add_source_marks_existing_outputs_and_queues_only_missing(self):
        class Podcast:
            def lookup(self, url):
                return {"tldrFile": "/existing.md"} if url.endswith("=a") else None

        preview = SourcePreview(
            "youtube_channel", "https://www.youtube.com/@example", "@example",
            "Example", None, [item("a"), item("b", 1)],
        )
        service = SourceSyncService(self.registry, adapters={}, podcast=Podcast())
        service.add_source(preview, initial_sync_count=5)
        statuses = dict(self.registry.db.execute(
            "SELECT content_key, status FROM sync_jobs ORDER BY content_key"
        ).fetchall())
        self.assertEqual(statuses, {"youtube:a": "synced", "youtube:b": "queued"})
        self.assertEqual(self.registry.db.execute(
            "SELECT collection_subdir FROM sync_jobs WHERE content_key='youtube:b'"
        ).fetchone()[0], "Example")

    def test_auto_sync_queues_the_actual_new_item_and_worker_finishes_it(self):
        source = self.add_channel(auto_sync_new=True)
        self.registry.record_scan(source.id, ScanPage([item("old")]))

        class Adapter:
            def scan(self, _source, _checkpoint):
                return ScanPage([item("old"), item("new", 1)])

        service = SourceSyncService(
            self.registry, adapters={"youtube_channel": Adapter()}, podcast=object()
        )
        service.scan(source.id)
        job = self.registry.db.execute(
            "SELECT content_key, collection_subdir FROM sync_jobs WHERE status='queued'"
        ).fetchone()
        self.assertEqual(job[0], "youtube:new")
        self.assertEqual(job[1], "Example")

        class Podcast:
            def process(self, url, metadata, collection_subdir=None):
                return {"tldrFile": url, "metadata": metadata}

        self.assertEqual(Worker(self.registry, Podcast()).run(), 1)
        self.assertEqual(self.registry.db.execute(
            "SELECT status FROM sync_jobs WHERE content_key='youtube:new'"
        ).fetchone()[0], "synced")

    def test_opencli_x_page_is_exactly_twenty_and_preserves_cursor(self):
        calls = []
        payload = {"items": [
            {"id": str(index), "author": "a", "text": "text"} for index in range(20)
        ], "next_cursor": "next", "exhausted": False}

        def runner(command):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

        page = OpenCLIPageClient(runner).page("likes-page", "example", "before")
        self.assertEqual(len(page.items), 20)
        self.assertEqual(page.next_checkpoint, "next")
        self.assertEqual(calls[0], [
            "opencli", "twitter", "likes-page", "example", "--count", "20",
            "-f", "json", "--cursor", "before",
        ])

    def test_likes_initial_scan_stops_after_latest_twenty(self):
        source = self.add_likes(pages=15)

        class Adapter:
            def __init__(self):
                self.run = 0
                self.cursors = []
            def scan(self, _source, cursor):
                self.cursors.append(cursor)
                if self.run == 0:
                    return ScanPage(
                        [x_item(str(tweet_id), position) for position, tweet_id in enumerate(range(120, 100, -1))],
                        "older", False,
                    )
                return ScanPage([x_item("121"), x_item("120", 1)], "older-again", False)

        adapter = Adapter()
        service = SourceSyncService(
            self.registry, adapters={"x_likes": adapter}, podcast=object(),
            sleeper=lambda _seconds: None, interval=lambda low, _high: low,
        )
        service.scan(source.id)
        checkpoint = self.registry.get_checkpoint(source.id)
        self.assertIsNone(checkpoint["pending_cursor"])
        self.assertIsNone(checkpoint["pending_stop_reason"])
        self.assertEqual(checkpoint["overlap_ids"], ["120", "119", "118", "117", "116"])
        self.assertEqual(adapter.cursors, [None])
        self.assertEqual(self.registry.db.execute(
            "SELECT COUNT(*) FROM sync_jobs WHERE processor='tweet_organizer'"
        ).fetchone()[0], 20)

        adapter.run = 1
        service.scan(source.id)
        checkpoint = self.registry.get_checkpoint(source.id)
        self.assertIsNone(checkpoint["pending_cursor"])
        self.assertEqual(checkpoint["overlap_ids"], ["121", "120"])
        self.assertEqual(adapter.cursors, [None, None])
        queued = self.registry.db.execute(
            "SELECT COUNT(*) FROM sync_jobs WHERE processor='tweet_organizer'"
        ).fetchone()[0]
        self.assertEqual(queued, 21)
        self.assertEqual({row[0] for row in self.registry.db.execute(
            "SELECT collection_subdir FROM sync_jobs WHERE processor='tweet_organizer'"
        )}, {"likes"})

    def test_x_risk_stop_does_not_advance_watermark(self):
        source = self.add_likes()
        self.registry.finish_x_scan(source.id, complete=True, pages=1, overlap_ids=["old"])

        class Adapter:
            def scan(self, _source, _cursor):
                raise XScanStopped("auth", "login required")

        service = SourceSyncService(
            self.registry, adapters={"x_likes": Adapter()}, podcast=object(),
            sleeper=lambda _seconds: None,
        )
        with self.assertRaises(XScanStopped):
            service.scan(source.id)
        checkpoint = self.registry.get_checkpoint(source.id)
        self.assertEqual(checkpoint["overlap_ids"], ["old"])
        self.assertEqual(self.registry.get_source(source.id).last_scan_status, "paused_risk")

    def test_tweet_organizer_writes_compliant_note_and_base(self):
        root = Path(self.tmp.name)

        def fake_pi(workdir):
            meta = json.loads((workdir / "meta.json").read_text(encoding="utf-8"))
            Path(meta["resultFile"]).write_text(json.dumps({
                "status": "ok",
                "title": "Agent 评测的新方法",
                "body": "## 原文\n\nEvaluation harness.\n\n## 中文翻译\n\n评测工具链。",
                "language": "en",
                "translation_status": "translated",
                "authors": "Alice（@alice）",
                "tags": ["AI", "评测"],
            }, ensure_ascii=False), encoding="utf-8")

        organizer = TweetOrganizer(root / "out", root / "work", fake_pi)
        result = organizer.process({
            "id": "123456789", "author": "alice", "name": "Alice",
            "text": "Evaluation harness.", "created_at": "Wed Jul 16 10:00:00 +0000 2026",
            "url": "https://x.com/alice/status/123456789",
        }, {"origin": "likes", "lists": ["Likes"]})
        self.assertEqual(Path(result["outputFile"]).parent, root / "out" / "likes")
        note = Path(result["outputFile"]).read_text(encoding="utf-8")
        self.assertIn('tweet_id: "123456789"', note)
        self.assertIn('translation_status: "translated"', note)
        self.assertIn('origin: "likes"', note)
        self.assertRegex(note, r'date: "\d{4}-\d{2}-\d{2}T\d{2}:\d{2}[+-]\d{2}:\d{2}"')
        self.assertTrue((root / "out" / "tweet 整理.base").is_file())

        again = organizer.process({
            "id": "123456789", "author": "alice", "text": "ignored"
        })
        self.assertEqual(again["outputFile"], result["outputFile"])

    def test_tweet_organizer_uses_sanitized_list_collection(self):
        root = Path(self.tmp.name)

        def fake_pi(workdir):
            meta = json.loads((workdir / "meta.json").read_text(encoding="utf-8"))
            Path(meta["resultFile"]).write_text(json.dumps({
                "status": "ok", "title": "List item", "body": "正文",
                "language": "zh", "translation_status": "not_needed",
            }), encoding="utf-8")

        result = TweetOrganizer(root / "out", root / "work", fake_pi).process(
            {"id": "987654321", "author": "alice", "text": "正文"},
            {"origin": "list", "lists": ["AI/Product"]},
            collection_subdir="AI/Product",
        )

        self.assertEqual(Path(result["outputFile"]).parent, root / "out" / "AI-Product")
        self.assertTrue((root / "out" / "tweet 整理.base").is_file())

    def test_tweet_pi_calls_load_fixed_skills_without_shell_access(self):
        root = Path(self.tmp.name)
        calls = [
            (TweetOrganizer._call_pi, "processors.tweet.subprocess.run", "tweet-organizer"),
            (ValueEvaluator._call_pi, "processors.value.subprocess.run", "tweet-value-evaluator"),
        ]
        for call_pi, target, skill in calls:
            with self.subTest(skill=skill), patch(target) as run:
                run.return_value = subprocess.CompletedProcess([], 0, "", "")
                call_pi(root)

                command = run.call_args.args[0]
                self.assertEqual(run.call_args.kwargs["cwd"], root)
                self.assertIn("--no-skills", command)
                self.assertIn("--no-context-files", command)
                self.assertEqual(command[command.index("--skill") + 1], str(
                    Path.home() / ".claude" / "skills" / skill / "SKILL.md"
                ))
                self.assertEqual(command[command.index("--tools") + 1], "read,write")

    def test_value_thresholds_and_insufficient_context_are_deterministic(self):
        collected = decide("x:1", {
            "relevance": 2, "information_gain": 2, "usefulness": 2, "evidence": 1,
            "reason_code": None,
        })
        borderline = decide("x:2", {
            "relevance": 3, "information_gain": 3, "usefulness": 2, "evidence": 1,
            "reason_code": "insufficient_context",
        })
        noise = decide("x:3", {
            "relevance": 0, "information_gain": 1, "usefulness": 1, "evidence": 1,
            "reason_code": "off_topic",
        })
        self.assertEqual((collected.total, collected.decision), (7, "collect"))
        self.assertEqual(borderline.decision, "review")
        self.assertEqual(noise.decision, "noise")

    def test_list_shadow_mode_records_decisions_without_enqueueing(self):
        source = self.add_list()

        class Adapter:
            def scan(self, _source, _cursor):
                return ScanPage([x_item("500")], None, True)

        class Evaluator:
            def evaluate(self, _policy, records):
                self.records = records
                return [ValueDecision("x:500", 2, 2, 2, 1, 7, "collect")]

        evaluator = Evaluator()
        SourceSyncService(
            self.registry, adapters={"x_list": Adapter()}, podcast=object(),
            evaluator=evaluator, sleeper=lambda _seconds: None,
        ).scan(source.id)
        self.assertEqual(evaluator.records[0]["id"], "500")
        self.assertEqual(self.registry.shadow_samples(source.id)[0]["decision"], "collect")
        self.assertEqual(self.registry.db.execute("SELECT COUNT(*) FROM sync_jobs").fetchone()[0], 0)

        self.registry.override_decision("x:500", source.id, "user_collect")
        row = self.registry.db.execute(
            "SELECT relevance, total, decision, overridden_by FROM value_decisions"
        ).fetchone()
        self.assertEqual(tuple(row), (2, 7, "collect", "user_collect"))

    def test_auto_collected_list_tweet_keeps_list_as_primary_collection(self):
        source = self.add_list(shadow_mode=False, auto_sync_accepted=True)

        class Adapter:
            def scan(self, _source, _cursor):
                return ScanPage([x_item("501")], None, True)

        class Evaluator:
            def evaluate(self, _policy, _records):
                return [ValueDecision("x:501", 2, 2, 2, 1, 7, "collect")]

        SourceSyncService(
            self.registry, adapters={"x_list": Adapter()}, podcast=object(),
            evaluator=Evaluator(), sleeper=lambda _seconds: None,
        ).scan(source.id)

        self.assertEqual(self.registry.db.execute(
            "SELECT collection_subdir FROM sync_jobs WHERE content_key='x:501'"
        ).fetchone()[0], "AI Products")

    def test_likes_priority_keeps_noise_tweet_queued(self):
        likes = self.add_likes()
        list_source = self.add_list()
        self.registry.record_scan(likes.id, ScanPage([x_item("700")]), complete=False)
        self.registry.enqueue(["x:700"], "tweet_organizer", "auto")
        self.registry.record_scan(list_source.id, ScanPage([x_item("700")]), complete=False)
        self.registry.record_decisions(list_source.id, [
            ValueDecision("x:700", 0, 0, 0, 0, 0, "noise", "off_topic")
        ])
        self.assertEqual(self.registry.db.execute(
            "SELECT status FROM sync_jobs WHERE content_key='x:700'"
        ).fetchone()[0], "queued")

    def test_likes_onboarding_imports_existing_tweet_baseline(self):
        output = Path(self.tmp.name) / "tweets"
        likes_note = output / "likes" / "existing.md"
        list_note = output / "AI Products" / "listed.md"
        likes_note.parent.mkdir(parents=True)
        list_note.parent.mkdir(parents=True)
        likes_note.write_text('''---
title: "Existing"
source: "https://x.com/alice/status/987654321"
tweet_id: "987654321"
---
''', encoding="utf-8")
        list_note.write_text('''---
title: "Listed"
source: "https://x.com/bob/status/123456789"
tweet_id: "123456789"
---
''', encoding="utf-8")
        preview = SourcePreview(
            "x_likes", "https://x.com/example/likes", "example", "@example Likes",
            None, [],
        )
        service = SourceSyncService(
            self.registry, adapters={}, podcast=object(), tweet_output_dir=output
        )
        source = service.add_source(preview, settings={
            "page_interval_seconds": [5, 10], "backfill_pages_per_run": 15,
        })
        self.assertEqual(
            set(self.registry.get_checkpoint(source.id)["overlap_ids"]),
            {"987654321", "123456789"},
        )
        jobs = self.registry.db.execute(
            "SELECT content_key, status, result_json FROM sync_jobs ORDER BY content_key"
        ).fetchall()
        self.assertEqual({job["content_key"] for job in jobs}, {"x:987654321", "x:123456789"})
        self.assertTrue(all(job["status"] == "synced" for job in jobs))
        self.assertIn(str(likes_note), next(
            job["result_json"] for job in jobs if job["content_key"] == "x:987654321"
        ))
        self.assertIn(str(list_note), next(
            job["result_json"] for job in jobs if job["content_key"] == "x:123456789"
        ))

    def test_stale_worker_recovery_and_daily_backup(self):
        source = self.add_channel()
        self.registry.record_scan(source.id, ScanPage([item("stale")]))
        self.registry.enqueue(["youtube:stale"], "podcast")
        job = self.registry.claim_jobs(1)[0]
        self.registry.db.execute(
            "UPDATE sync_jobs SET started_at='2000-01-01T00:00:00Z' WHERE id=?", (job.id,)
        )
        self.registry.db.commit()
        self.assertEqual(self.registry.recover_stale_jobs(), 1)

        runtime = Runtime(Path(self.tmp.name) / "runtime")
        backup = runtime.maintenance(self.registry.db, self.registry.path)
        self.assertTrue(backup.is_file())
        self.assertEqual(runtime.status()["maintenance"]["daily"]["outcome"], "ok")

    def test_runtime_lock_rejects_concurrent_owner(self):
        runtime = Runtime(Path(self.tmp.name) / "runtime")
        with runtime.lock("x-global"):
            with self.assertRaises(RuntimeError):
                with runtime.lock("x-global"):
                    pass


def x_item(tweet_id, position=0):
    return DiscoveredItem(
        f"x:{tweet_id}", f"https://x.com/a/status/{tweet_id}", "tweet", "a",
        "2026-07-16", position, {"id": tweet_id},
    )


if __name__ == "__main__":
    unittest.main()
