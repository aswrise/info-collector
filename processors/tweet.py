from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from processors.paths import collection_path


DEFAULT_OUTPUT_DIR = Path(os.environ.get(
    "INFO_COLLECTOR_TWEET_OUTPUT_DIR",
    "/Users/aqua/D/Documents/ob/obsidian-sync-win-v1/文章收集/tweet 整理",
)).expanduser()
WORK_ROOT = Path("~/.info-collector/source-sync/work/tweets").expanduser()
BASE_TEMPLATE = Path(__file__).parents[1] / "templates" / "tweet-organizer.base"


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write(text)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def safe_filename(title: str) -> str:
    name = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "-", title).strip(" .")
    return re.sub(r"\s+", " ", name)[:120] or "Tweet"


def render_markdown(result: dict, tweet: dict, context: dict, collected_at: str) -> str:
    tweet_id = str(tweet["id"])
    handle = str(tweet.get("author") or "unknown").lstrip("@")
    published = str(tweet.get("created_at") or tweet.get("createdAt") or "")[:10]
    origin = context.get("origin") or "list"
    lists = context.get("lists") or (["Likes"] if origin == "likes" else [])
    tags = result.get("tags") or []
    fields = [
        ("title", result["title"]),
        ("source", tweet.get("url") or f"https://x.com/{handle}/status/{tweet_id}"),
        ("published", published),
        ("date", collected_at),
        ("authors", result.get("authors") or f"@{handle}"),
        ("platform", "x"),
        ("tweet_id", tweet_id),
        ("language", result["language"]),
        ("translation_status", result["translation_status"]),
        ("origin", origin),
    ]
    yaml = ["---", *(f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in fields)]
    yaml += [
        f"lists: {json.dumps(lists, ensure_ascii=False)}",
        f"tags: {json.dumps(tags, ensure_ascii=False)}",
        "---",
        "",
        result["body"].strip(),
        "",
    ]
    return "\n".join(yaml)


class TweetOrganizer:
    def __init__(self, output_dir: str | Path = DEFAULT_OUTPUT_DIR, work_root: str | Path = WORK_ROOT, call_pi=None):
        self.output_dir = Path(output_dir).expanduser()
        self.work_root = Path(work_root).expanduser()
        self.call_pi = call_pi or self._call_pi

    def process(
        self, tweet_record: dict, context: dict | None = None,
        collection_subdir: str | None = None,
    ) -> dict:
        tweet_id = str(tweet_record.get("id") or "")
        if not tweet_id.isdigit():
            raise ValueError("Tweet record requires a numeric id")
        workdir = self.work_root / tweet_id
        result_file = workdir / "result.json"
        previous = self._valid_result(result_file)
        if previous and Path(previous.get("outputFile", "")).is_file():
            return previous

        workdir.mkdir(parents=True, exist_ok=True)
        atomic_write(workdir / "tweet.json", json.dumps(tweet_record, ensure_ascii=False, indent=2))
        atomic_write(workdir / "meta.json", json.dumps({"resultFile": str(result_file)}, ensure_ascii=False, indent=2))
        if result_file.exists():
            result_file.unlink()
        self.call_pi(workdir)
        result = self._valid_result(result_file)
        if not result:
            raise RuntimeError("tweet-organizer returned no valid result")

        context = context or {}
        folder = collection_subdir or (
            "likes" if context.get("origin") == "likes"
            else next(iter(context.get("lists") or []), "公共")
        )
        output_dir = collection_path(self.output_dir, folder)
        collected_at = datetime.now().astimezone().isoformat(timespec="minutes")
        title = safe_filename(f"{result['title']}（@{str(tweet_record.get('author') or 'unknown').lstrip('@')}）")
        output = output_dir / f"{title}.md"
        if output.exists():
            output = output_dir / f"{title}-{tweet_id[-6:]}.md"
        atomic_write(output, render_markdown(result, tweet_record, context, collected_at))
        self._ensure_base()
        result.update({"sync_status": "synced", "outputFile": str(output)})
        atomic_write(result_file, json.dumps(result, ensure_ascii=False, indent=2))
        return result

    @staticmethod
    def _valid_result(path: Path) -> dict | None:
        try:
            result = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        required = {"title", "body", "language", "translation_status"}
        if result.get("status") != "ok" or not required.issubset(result):
            return None
        if result["language"] not in {"zh", "en", "mixed"}:
            return None
        if result["translation_status"] not in {"not_needed", "translated", "partially_translated"}:
            return None
        return result

    def _ensure_base(self) -> None:
        base = self.output_dir / "tweet 整理.base"
        if not base.exists():
            atomic_write(base, BASE_TEMPLATE.read_text(encoding="utf-8"))

    @staticmethod
    def _call_pi(workdir: Path) -> None:
        prompt = (
            "Use the tweet-organizer skill.\n\n"
            f"Workdir: {workdir}\n\n"
            "Read tweet.json and meta.json. Do not open a browser or fetch URLs. "
            "Write only meta.json.resultFile."
        )
        try:
            proc = subprocess.run(["pi", "-p", prompt], text=True, capture_output=True, timeout=900)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise RuntimeError(f"tweet-organizer failed: {error}") from error
        if proc.returncode:
            raise RuntimeError(f"tweet-organizer failed: {(proc.stderr or '').strip()[:500]}")
