from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import asdict
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .registry import SourceRegistry
from .service import SourceSyncService
from .worker import Worker
from .runtime import Runtime


STATIC = Path(__file__).with_name("static")


class SourceSyncHandler(SimpleHTTPRequestHandler):
    db_path = "~/.info-collector/source-sync/source-sync.db"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC), **kwargs)

    def do_GET(self):
        if not self.path.startswith("/api/"):
            return super().do_GET()
        with self._request_context():
            return self._get_api()

    def _get_api(self):
        if self.path == "/api/state":
            sources = [asdict(source) for source in self.registry.list_sources()]
            items = self.registry.list_items()
            counts = {status: 0 for status in ("queued", "syncing", "synced", "failed")}
            for item in items:
                if item["sync_status"] in counts:
                    counts[item["sync_status"]] += 1
            return self._json({
                "sources": sources, "items": items, "counts": counts,
                "runtime": Runtime(self.registry.path.parent).status(),
            })
        source_id = self._source_action("shadow")
        if source_id is not None:
            return self._json({"samples": self.registry.shadow_samples(source_id)})
        return self.send_error(404)

    def do_POST(self):
        with self._request_context():
            return self._post_api()

    def _post_api(self):
        try:
            body = self._body()
            if self.path == "/api/inspect":
                return self._json(asdict(self.service.inspect(self._required(body, "url"))))
            if self.path == "/api/sources":
                preview = self.service.inspect(self._required(body, "url"))
                initial = int(body.get("initial_sync_count", 5))
                if initial < -1:
                    raise ValueError("initial_sync_count must be -1 or greater")
                defaults = (
                    {"scan_depth": 5} if preview.source_type == "youtube_channel" else
                    {"page_interval_seconds": [5, 10], "backfill_pages_per_run": 15}
                    if preview.source_type == "x_likes" else
                    {
                        "topics": body.get("topics") or [], "minimum_collect_score": 7,
                        "auto_sync_accepted": False, "send_borderline_to_review": True,
                        "shadow_mode": True, "page_interval_seconds": [5, 10],
                        "backfill_pages_per_run": 15,
                    } if preview.source_type == "x_list" else {}
                )
                settings = body.get("settings") or defaults
                if preview.source_type == "x_list" and not settings.get("topics"):
                    raise ValueError("X List requires at least one topic")
                source = self.service.add_source(
                    preview,
                    initial_sync_count=None if initial == -1 else initial,
                    auto_sync_new=bool(body.get("auto_sync_new", False)),
                    settings=settings,
                )
                return self._json(asdict(source), 201)
            if self.path == "/api/enqueue":
                items = body.get("items")
                if not isinstance(items, list) or not items:
                    raise ValueError("items must be a non-empty list")
                groups = {}
                for item in items:
                    key = item.get("content_key") if isinstance(item, dict) else None
                    source_id = item.get("source_id") if isinstance(item, dict) else None
                    platform = key.split(":", 1)[0] if isinstance(key, str) and ":" in key else None
                    if platform not in {"youtube", "x"} or not isinstance(source_id, int):
                        raise ValueError("each item requires a supported content_key and source_id")
                    processor = "tweet_organizer" if platform == "x" else "podcast"
                    folder = self.registry.collection_name(key, source_id)
                    groups.setdefault((processor, folder), []).append(key)
                totals = {"queued": 0, "skipped_synced": 0, "skipped_active": 0}
                for (processor, folder), keys in groups.items():
                    result = asdict(self.registry.enqueue(keys, processor, collection_subdir=folder))
                    for name in totals:
                        totals[name] += result[name]
                return self._json(totals)
            if self.path == "/api/worker":
                return self._json({"completed": Worker(self.registry).run(2)})
            if self.path == "/api/decisions":
                keys = body.get("content_keys")
                source_id = int(body.get("source_id"))
                override = body.get("override")
                if not isinstance(keys, list) or not keys:
                    raise ValueError("content_keys must be a non-empty list")
                for key in keys:
                    self.registry.override_decision(key, source_id, override)
                queued = None
                if override == "user_collect":
                    folder = self.registry.collection_name(keys[0], source_id)
                    queued = asdict(self.registry.enqueue(
                        keys, "tweet_organizer", "user", folder
                    ))
                return self._json({"updated": len(keys), "enqueue": queued})
            match = self._source_action("scan")
            if match is not None:
                return self._json(asdict(self.service.scan(match)))
            return self.send_error(404)
        except (KeyError, ValueError) as error:
            return self._json({"error": str(error)}, 400)
        except Exception as error:
            return self._json({"error": str(error)}, 500)

    def do_DELETE(self):
        with self._request_context():
            return self._delete_api()

    def _delete_api(self):
        try:
            source_id = self._source_action(None)
            if source_id is None:
                return self.send_error(404)
            self.registry.delete_source(source_id)
            return self._json({"ok": True})
        except KeyError:
            return self._json({"error": "source not found"}, 404)

    @contextmanager
    def _request_context(self):
        registry = SourceRegistry(type(self).db_path)
        self.registry = registry
        self.service = SourceSyncService(registry)
        try:
            yield
        finally:
            registry.close()

    def _source_action(self, action):
        parts = urlparse(self.path).path.strip("/").split("/")
        expected = ["api", "sources", "<id>"] + ([action] if action else [])
        if len(parts) != len(expected) or parts[:2] != expected[:2] or (action and parts[-1] != action):
            return None
        try:
            return int(parts[2])
        except ValueError:
            return None

    def _body(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length > 1_000_000:
            raise ValueError("request body too large")
        data = self.rfile.read(length)
        return json.loads(data) if data else {}

    @staticmethod
    def _required(body, key):
        value = body.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{key} is required")
        return value

    def _json(self, data, status=200):
        payload = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format, *args):
        return


def serve(host="127.0.0.1", port=8787, db_path="~/.info-collector/source-sync/source-sync.db"):
    SourceSyncHandler.db_path = db_path
    server = ThreadingHTTPServer((host, port), SourceSyncHandler)
    try:
        server.serve_forever()
    finally:
        server.server_close()
