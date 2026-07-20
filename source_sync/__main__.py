from __future__ import annotations

import argparse
from dataclasses import asdict
import json

from .registry import SourceRegistry
from .server import serve
from .service import SourceSyncService
from .worker import Worker
from .runtime import Runtime


def main():
    parser = argparse.ArgumentParser(prog="source-sync")
    parser.add_argument("--db", default="~/.info-collector/source-sync/source-sync.db")
    sub = parser.add_subparsers(dest="command", required=True)
    dashboard = sub.add_parser("dashboard")
    dashboard.add_argument("--port", type=int, default=8787)
    add = sub.add_parser("add")
    add.add_argument("url")
    add.add_argument("--initial-sync", type=int, default=5)
    scan = sub.add_parser("scan")
    scan.add_argument("source_id", type=int, nargs="?")
    scan.add_argument("--platform", choices=("youtube", "x"))
    scan.add_argument("--maintenance", action="store_true")
    sub.add_parser("worker")
    sub.add_parser("maintenance")
    args = parser.parse_args()

    if args.command == "dashboard":
        return serve(port=args.port, db_path=args.db)
    registry = SourceRegistry(args.db)
    try:
        service = SourceSyncService(registry)
        if args.command == "add":
            result = service.add_source(
                service.inspect(args.url), None if args.initial_sync == -1 else args.initial_sync
            )
        elif args.command == "scan":
            result = service.run_cycle(args.source_id, args.platform, args.maintenance)
        elif args.command == "maintenance":
            result = {"backup": str(Runtime(registry.path.parent).maintenance(registry.db, registry.path))}
        else:
            result = {"completed": Worker(registry).run()}
        print(json.dumps(asdict(result) if hasattr(result, "__dataclass_fields__") else result, ensure_ascii=False, indent=2))
    finally:
        registry.close()


if __name__ == "__main__":
    main()
