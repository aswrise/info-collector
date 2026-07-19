#!/usr/bin/env python3
# Shared Podcast Processor implementation and legacy queue runner.
"""
播客流（podcast）：消费 Info Collector 的 podcast outbox，找到既有文稿，
再交给 pi 的 podcast-digest skill 生成 TLDR / 1000 字总结 / 可选 7000 字总结 / 全文稿。

flow 只做确定性工作：spool、锁、网页/字幕提取、语言启发式、workdir、报告。
不做音频转写，不在 Python 里翻译、总结或写 Obsidian 正文。
"""

import fcntl
from html.parser import HTMLParser
import hashlib
import html
import json
import os
from pathlib import Path
import random
import re
import shutil
import string
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from urllib.parse import parse_qs, urlencode, urlparse
import urllib.error
import urllib.request

from processors.paths import collection_path


SPOOL = os.path.expanduser("~/.info-collector")
OUTBOX_FILE = os.path.join(SPOOL, "outbox", "podcast.json")
INBOX_DIR = os.path.join(SPOOL, "inbox")
STATE_FILE = os.path.join(SPOOL, "state", "podcast-reported.json")
STATUS_FILE = os.path.join(SPOOL, "state", "podcast-status.json")
WORK_ROOT = os.path.join(SPOOL, "work", "podcast")
LOG_FILE = os.path.expanduser("~/.pi/logs/podcast-bookmarks.log")
LOCK_FILE = os.path.expanduser("~/.pi/logs/podcast-bookmarks.lock")
DEFAULT_OUTPUT_DIR = os.path.expanduser(
    os.environ.get(
        "INFO_COLLECTOR_PODCAST_OUTPUT_DIR",
        "~/D/Documents/ob/obsidian-sync-win-v1/播客收集",
    )
)

MAX_PER_RUN = 2
PI_TIMEOUT = 2700
DIGEST_VERSION = 2
MIN_TRANSCRIPT_CHARS = 2000
FIRECRAWL_TIMEOUT = 120
DEFUDDLE_TIMEOUT = 120
FETCH_TIMEOUT = 45
MAX_FETCH_BYTES = 2_000_000
YT_COOKIES_FILE = os.environ.get("INFO_COLLECTOR_YT_COOKIES_FILE") or None
YT_COOKIES_BROWSER = os.environ.get("INFO_COLLECTOR_YT_COOKIES_BROWSER") or None

TRIGGER = "manual" if "--manual" in sys.argv else "scheduled"


class ResolutionError(Exception):
    def __init__(self, code, message=None, meta=None):
        super().__init__(message or code)
        self.code = code
        self.meta = meta or {}


def log(msg):
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{ts}] {msg}\n")
    print(f"[{ts}] {msg}", flush=True)


def atomic_write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return default
    return default


def local_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def utc_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def update_status(patch):
    status = load_json(STATUS_FILE, {})
    status.update(patch)
    atomic_write_json(STATUS_FILE, status)


def acquire_lock():
    os.makedirs(os.path.dirname(LOCK_FILE), exist_ok=True)
    fd = os.open(LOCK_FILE, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except BlockingIOError:
        os.close(fd)
        return None


def write_report(results):
    rid = "podcast-%s-%s" % (
        time.strftime("%Y%m%dT%H%M%S"),
        "".join(random.choices(string.ascii_lowercase + string.digits, k=4)),
    )
    atomic_write_json(os.path.join(INBOX_DIR, rid + ".json"), {
        "reportId": rid,
        "processingType": "podcast",
        "results": results,
    })
    return rid


class TextHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip_depth = 0

    def handle_starttag(self, tag, _attrs):
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "svg"}:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if tag in {"p", "div", "section", "article", "br", "li", "tr",
                   "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "svg"} and self.skip_depth:
            self.skip_depth -= 1
            return
        if not self.skip_depth and tag in {"p", "div", "section", "article", "li",
                                           "h1", "h2", "h3", "h4", "h5", "h6",
                                           "blockquote", "pre"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip_depth and data.strip():
            self.parts.append(data.strip())

    def text(self):
        return clean_text(" ".join(self.parts))


def clean_text(text):
    text = html.unescape(text or "")
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def content_from_defuddle(meta):
    for key in ("markdown", "content", "text", "article"):
        val = meta.get(key)
        if isinstance(val, str) and val.strip():
            return clean_text(val)
    raw_html = meta.get("html")
    if isinstance(raw_html, str) and raw_html.strip():
        parser = TextHTMLParser()
        parser.feed(raw_html)
        return parser.text()
    return ""


def run_defuddle(url):
    exe = shutil.which("defuddle")
    if not exe:
        raise ResolutionError("fetch-failed", "defuddle not found")
    try:
        proc = subprocess.run(
            [exe, "parse", url, "--json"],
            capture_output=True,
            text=True,
            timeout=DEFUDDLE_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise ResolutionError("fetch-failed", str(e)) from e
    if proc.returncode != 0:
        raise ResolutionError("fetch-failed", (proc.stderr or proc.stdout)[:300])
    try:
        meta = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise ResolutionError("fetch-failed", f"defuddle json: {e}") from e
    return meta, content_from_defuddle(meta)


def fetch_raw_html(url):
    req = urllib.request.Request(
        url,
        headers={
            "user-agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/126.0 Safari/537.36 InfoCollector/1.0"),
            "accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.5",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
            charset = resp.headers.get_content_charset() or "utf-8"
            raw = resp.read(MAX_FETCH_BYTES + 1)
    except (urllib.error.URLError, OSError):
        return ""
    if len(raw) > MAX_FETCH_BYTES:
        return ""
    return raw.decode(charset, errors="replace")


def is_youtube_url(url):
    host = urlparse(url).netloc.lower().removeprefix("www.").removeprefix("m.")
    return host in {"youtube.com", "youtu.be"}


def normalize_youtube_url(url):
    parsed = urlparse(html.unescape(url))
    host = parsed.netloc.lower().removeprefix("www.").removeprefix("m.")
    video_id = ""
    if host == "youtu.be":
        video_id = parsed.path.strip("/").split("/")[0]
    elif host == "youtube.com":
        if parsed.path == "/watch":
            video_id = (parse_qs(parsed.query).get("v") or [""])[0]
        else:
            m = re.match(r"^/(embed|shorts|live)/([^/?#]+)", parsed.path)
            if m:
                video_id = m.group(2)
    video_id = re.sub(r"[^A-Za-z0-9_-]", "", video_id)
    return f"https://www.youtube.com/watch?v={video_id}" if video_id else None


YOUTUBE_URL_RE = re.compile(
    r"https?://(?:www\.|m\.)?(?:youtube\.com/(?:watch\?[^\"'<>\s]+|embed/[^\"'<>\s]+|shorts/[^\"'<>\s]+|live/[^\"'<>\s]+)|youtu\.be/[^\"'<>\s]+)",
    re.I,
)


def extract_youtube_url(text):
    for m in YOUTUBE_URL_RE.finditer(text or ""):
        normalized = normalize_youtube_url(m.group(0))
        if normalized:
            return normalized
    return None


def looks_blocked(text):
    lowered = (text or "").lower()
    blocked = [
        "403 forbidden", "sign in to confirm", "login_required", "please sign in",
        "confirm you're not a bot", "enable javascript", "access denied",
    ]
    return any(s in lowered for s in blocked)


def extract_firecrawl_transcript(markdown, min_chars=None):
    if looks_blocked(markdown):
        return ""
    match = re.search(r"(?im)^##\s+Transcript\s*$", markdown or "")
    if not match:
        return ""
    text = clean_text((markdown or "")[match.end():])
    return text if len(text) >= (min_chars or MIN_TRANSCRIPT_CHARS) else ""


def firecrawl_transcript(watch_url):
    npx = shutil.which("npx")
    if not npx:
        return None
    try:
        proc = subprocess.run(
            [
                npx, "-y", "firecrawl-cli@latest", "scrape", watch_url,
                "--format", "markdown", "--country", "US", "--wait-for", "5000",
                "--max-age", "0",
            ],
            capture_output=True,
            text=True,
            timeout=FIRECRAWL_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        log(f"Firecrawl 不可用: {e}")
        return None
    text = extract_firecrawl_transcript(proc.stdout)
    if proc.returncode == 0 and text:
        return {
            "text": text,
            "transcriptSource": "youtube_unknown_caption",
            "transcriptProvider": "firecrawl_youtube",
            "captionKind": "unknown",
            "sourceReliability": "unknown_caption",
            "captionLanguage": "",
            "mediaSource": watch_url,
        }
    if proc.stderr:
        log(f"Firecrawl 未拿到 transcript: {proc.stderr[:240]}")
    return None


def normalize_publish_date(value):
    value = clean_text(value)
    if not value:
        return ""
    match = re.match(r"^(\d{4}-\d{2}-\d{2})", value)
    if match:
        return match.group(1)
    value = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", value, flags=re.I)
    for fmt in ("%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return ""


def parse_youtube_publish_date(page_html):
    patterns = [
        r'"publishDate"\s*:\s*\{\s*"simpleText"\s*:\s*"([^"]+)"',
        r'"(?:uploadDate|datePublished|publishDate)"\s*:\s*"([^"]+)"',
        r'"dateText"\s*:\s*\{\s*"simpleText"\s*:\s*"([^"]+)"',
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, page_html or ""):
            published = normalize_publish_date(match.group(1))
            if published:
                return published
    return ""


def fetch_youtube_metadata(watch_url):
    published = parse_youtube_publish_date(fetch_raw_html(watch_url))
    query = urlencode({"url": watch_url, "format": "json"})
    req = urllib.request.Request(
        f"https://www.youtube.com/oembed?{query}",
        headers={"user-agent": "InfoCollector/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
            meta = json.loads(resp.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        meta = {}
    return {
        "title": meta.get("title") or "",
        "channel": meta.get("author_name") or "",
        "channelUrl": meta.get("author_url") or "",
        "published": published,
    }


def parse_json3(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    lines = []
    for event in data.get("events") or []:
        segs = event.get("segs") or []
        line = "".join(seg.get("utf8", "") for seg in segs if isinstance(seg, dict))
        line = clean_text(line)
        if line and (not lines or lines[-1] != line):
            lines.append(line)
    return clean_text("\n".join(lines))


def parse_vtt(path):
    lines = []
    for raw in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line == "WEBVTT" or line.startswith(("NOTE", "STYLE", "REGION")):
            continue
        if "-->" in line or re.match(r"^\d+$", line):
            continue
        line = re.sub(r"<[^>]+>", "", line)
        line = clean_text(line)
        if line and (not lines or lines[-1] != line):
            lines.append(line)
    return clean_text("\n".join(lines))


def caption_language(path):
    name = Path(path).name
    parts = name.split(".")
    return parts[-2] if len(parts) >= 3 else ""


def caption_sort_key(path):
    lang = caption_language(path)
    ext_score = 0 if str(path).endswith(".json3") else 1
    if lang == "en":
        lang_score = 0
    elif lang.startswith("en"):
        lang_score = 1
    elif lang.startswith("zh"):
        lang_score = 2
    else:
        lang_score = 3
    return (lang_score, ext_score, str(path))


def parse_caption_file(path):
    return parse_json3(path) if str(path).endswith(".json3") else parse_vtt(path)


def yt_dlp_once(watch_url, workdir, auto=False):
    uvx = shutil.which("uvx")
    if not uvx:
        return None
    before = set(Path(workdir).glob("*"))
    args = [
        uvx, "--from", "yt-dlp", "yt-dlp",
        "--skip-download",
        "--write-auto-subs" if auto else "--write-subs",
        "--sub-langs", "en,en-orig,en.*,zh.*",
        "--sub-format", "json3/vtt",
        "--ignore-no-formats-error",
        "-o", os.path.join(workdir, "%(id)s.%(ext)s"),
        watch_url,
    ]
    if YT_COOKIES_FILE:
        args[-1:-1] = ["--cookies", os.path.expanduser(YT_COOKIES_FILE)]
    elif YT_COOKIES_BROWSER:
        args[-1:-1] = ["--cookies-from-browser", YT_COOKIES_BROWSER]
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=FIRECRAWL_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as e:
        log(f"yt-dlp 不可用: {e}")
        return None
    files = [
        p for p in Path(workdir).glob("*")
        if p not in before and p.suffix in {".json3", ".vtt"}
    ]
    if not files:
        if proc.stderr:
            log(f"yt-dlp 未拿到字幕: {proc.stderr[:240]}")
        return None
    for path in sorted(files, key=caption_sort_key):
        text = parse_caption_file(path)
        if len(text) >= MIN_TRANSCRIPT_CHARS:
            return {
                "text": text,
                "transcriptSource": "youtube_auto_caption" if auto else "youtube_manual_caption",
                "transcriptProvider": "yt_dlp",
                "captionKind": "auto" if auto else "manual",
                "sourceReliability": "auto_caption" if auto else "manual_caption",
                "captionLanguage": caption_language(path),
                "mediaSource": watch_url,
            }
    return None


def yt_dlp_transcript(watch_url, workdir):
    return yt_dlp_once(watch_url, workdir, auto=False) or yt_dlp_once(watch_url, workdir, auto=True)


def resolve_youtube_transcript(url, workdir):
    watch_url = normalize_youtube_url(url)
    if not watch_url:
        raise ResolutionError("youtube-transcript-unavailable", "invalid YouTube URL")
    video_meta = fetch_youtube_metadata(watch_url)
    resolved = firecrawl_transcript(watch_url) or yt_dlp_transcript(watch_url, workdir)
    if resolved:
        resolved.update({k: v for k, v in video_meta.items() if v and not resolved.get(k)})
        return resolved
    raise ResolutionError(
        "youtube-transcript-unavailable",
        "Firecrawl and yt-dlp found no transcript",
        {"mediaSource": watch_url, **{k: v for k, v in video_meta.items() if v}},
    )


def resolve_article(article, workdir):
    url = article["url"]
    if is_youtube_url(url):
        return resolve_youtube_transcript(url, workdir)

    meta, content = run_defuddle(url)
    if len(content) >= MIN_TRANSCRIPT_CHARS:
        return {
            "text": content,
            "transcriptSource": "webpage_transcript",
            "transcriptProvider": "defuddle",
            "captionKind": None,
            "sourceReliability": "edited",
            "captionLanguage": "",
            "title": meta.get("title") or article.get("title") or "",
            "author": meta.get("author") or "",
            "published": meta.get("published") or meta.get("date") or "",
            "duration": meta.get("duration") or "",
        }

    haystack = "\n".join([content, json.dumps(meta, ensure_ascii=False), fetch_raw_html(url)])
    youtube_url = extract_youtube_url(haystack)
    if youtube_url:
        return resolve_youtube_transcript(youtube_url, workdir)
    raise ResolutionError("no-existing-transcript", "page has no long transcript or YouTube embed")


def detect_language(text, caption_lang=""):
    lang = (caption_lang or "").lower()
    if lang.startswith("zh"):
        return "zh"
    if lang:
        return "en"
    sample = (text or "")[:4000]
    if re.search(r"[\u3040-\u30ff]", sample):
        return "en"
    chars = [c for c in sample if not c.isspace()]
    if not chars:
        return "en"
    cjk = sum(1 for c in chars if "\u4e00" <= c <= "\u9fff")
    return "zh" if cjk / len(chars) > 0.25 else "en"


def workdir_for(article):
    key = article.get("articleKey") or article["url"]
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    return os.path.join(WORK_ROOT, digest)


def prepare_workdir(article, resolved, output_dir=DEFAULT_OUTPUT_DIR):
    workdir = workdir_for(article)
    os.makedirs(workdir, exist_ok=True)
    result_file = os.path.join(workdir, "result.json")
    source_file = os.path.join(workdir, "source.md")
    for path in (result_file,):
        if os.path.exists(path):
            os.unlink(path)
    if resolved.get("text"):
        Path(source_file).write_text(resolved["text"], encoding="utf-8")
    elif os.path.exists(source_file):
        os.unlink(source_file)

    transcript_language = detect_language(resolved.get("text", ""), resolved.get("captionLanguage", ""))
    meta = {
        "url": article["url"],
        "title": resolved.get("title") or article.get("title") or "",
        "author": resolved.get("author") or "",
        "channel": resolved.get("channel") or "",
        "channelUrl": resolved.get("channelUrl") or "",
        "published": resolved.get("published") or "",
        "duration": resolved.get("duration") or "",
        "transcriptSource": resolved.get("transcriptSource"),
        "transcriptProvider": resolved.get("transcriptProvider"),
        "captionKind": resolved.get("captionKind"),
        "transcriptLanguage": transcript_language,
        "sourceReliability": resolved.get("sourceReliability"),
        "digestVersion": DIGEST_VERSION,
        "outputDir": str(output_dir),
        "resultFile": result_file,
    }
    for key in ("resolutionStatus", "mediaSource"):
        if resolved.get(key):
            meta[key] = resolved[key]
    atomic_write_json(os.path.join(workdir, "meta.json"), meta)
    return workdir, meta


def call_pi(workdir):
    source_file = os.path.join(workdir, "source.md")
    if not os.path.isfile(source_file):
        raise ResolutionError(
            "youtube-transcript-unavailable",
            "refusing to start pi without source.md",
        )
    skill = os.path.expanduser("~/.claude/skills/podcast-digest/SKILL.md")
    current_time = datetime.now().astimezone().isoformat(timespec="minutes")
    prompt = (
        "The podcast-digest skill is already loaded. Do not search for skills or run shell commands.\n\n"
        f"Current local time: {current_time}. Use it for the frontmatter date.\n\n"
        "Read source.md and meta.json in the current directory. Do not open or control a browser, "
        "use browser cookies, or fetch a replacement transcript. "
        "Write only the result JSON to meta.json.resultFile."
    )
    try:
        proc = subprocess.run(
            [
                "pi", "--no-session", "--no-skills", "--skill", skill,
                "--no-context-files", "--tools", "read,write", "-p", prompt,
            ],
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=PI_TIMEOUT,
            env={**os.environ, "HOME": os.path.expanduser("~")},
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise ResolutionError("pi-failed", str(e)) from e
    for line in (proc.stdout or "").strip().splitlines()[-8:]:
        log(f"  | {line[:300]}")
    if proc.stderr:
        log(f"pi 错误: {proc.stderr[:500]}")
    if proc.returncode != 0:
        raise ResolutionError("pi-failed", f"pi exit {proc.returncode}")


def validate_result(result_file, digest_version=None):
    result = load_json(result_file, None)
    if not isinstance(result, dict):
        raise ResolutionError("pi-failed", "result.json missing or invalid")
    if result.get("status") == "failed":
        raise ResolutionError(result.get("error") or "pi-failed")
    if result.get("status") != "ok":
        raise ResolutionError("pi-failed", "result.json status is not ok")
    current = digest_version == DIGEST_VERSION or result.get("digestVersion") == DIGEST_VERSION
    required = (
        ("tldrFile", "summary1000File", "transcriptFile")
        if current else
        ("tldrFile", "deepSummaryFile", "transcriptFile")
    )
    for key in required:
        path = result.get(key)
        if not path or not os.path.exists(os.path.expanduser(path)):
            raise ResolutionError("pi-failed", f"{key} missing")
    summary_7000 = result.get("summary7000File")
    if summary_7000 and not os.path.exists(os.path.expanduser(summary_7000)):
        raise ResolutionError("pi-failed", "summary7000File missing")
    if current:
        transcript = Path(result["transcriptFile"]).read_text(encoding="utf-8")
        if transcript.startswith("---"):
            parts = transcript.split("---", 2)
            if len(parts) == 3:
                transcript = parts[2]
        needs_7000 = len(re.sub(r"\s+", "", transcript)) >= 7000
        if needs_7000 != bool(summary_7000):
            requirement = "required" if needs_7000 else "must be omitted"
            raise ResolutionError("pi-failed", f"summary7000File {requirement}")
    return result


def done_meta(result, meta):
    current_meta = load_json(os.path.join(os.path.dirname(meta["resultFile"]), "meta.json"), meta)
    output = {
        "tldrFile": result["tldrFile"],
        "transcriptFile": result["transcriptFile"],
        "transcriptSource": current_meta.get("transcriptSource"),
        "transcriptProvider": current_meta.get("transcriptProvider"),
        "captionKind": current_meta.get("captionKind"),
        "transcriptLanguage": current_meta.get("transcriptLanguage"),
        "sourceReliability": current_meta.get("sourceReliability"),
        "channel": current_meta.get("channel"),
        "channelUrl": current_meta.get("channelUrl"),
        "published": current_meta.get("published"),
    }
    if result.get("summary1000File"):
        output["summary1000File"] = result["summary1000File"]
    if result.get("summary7000File"):
        output["summary7000File"] = result["summary7000File"]
    if result.get("deepSummaryFile"):
        output["deepSummaryFile"] = result["deepSummaryFile"]
    return output


def migrate_public_outputs(output_root=DEFAULT_OUTPUT_DIR, work_root=WORK_ROOT):
    root = Path(output_root).expanduser().resolve()
    public = collection_path(root, "公共")
    moved = 0
    for result_file in Path(work_root).expanduser().glob("*/result.json"):
        result = load_json(result_file, None)
        if not isinstance(result, dict):
            continue
        changed = False
        for key in (
            "tldrFile", "summary1000File", "summary7000File",
            "deepSummaryFile", "transcriptFile",
        ):
            value = result.get(key)
            if not value:
                continue
            source = Path(value).expanduser().resolve()
            if source.parent != root:
                continue
            destination = public / source.name
            if source.exists():
                if destination.exists():
                    raise RuntimeError(f"refusing to overwrite existing product: {destination}")
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(source, destination)
                moved += 1
            elif not destination.exists():
                continue
            result[key] = str(destination)
            changed = True
        if changed:
            atomic_write_json(result_file, result)
    return moved


class PodcastProcessor:
    """Shared idempotent processor used by both queue systems."""

    def __init__(self, output_dir=DEFAULT_OUTPUT_DIR, resolver=None, pi=None):
        self.output_dir = Path(output_dir).expanduser()
        self.resolver = resolver or resolve_article
        self.pi = pi or call_pi

    def lookup(self, canonical_youtube_url):
        direct = workdir_for({"url": canonical_youtube_url, "articleKey": canonical_youtube_url})
        match = self._completed_result(direct)
        if match:
            return match
        target = normalize_youtube_url(canonical_youtube_url) or canonical_youtube_url
        for meta_file in Path(WORK_ROOT).glob("*/meta.json"):
            meta = load_json(meta_file, None)
            if not isinstance(meta, dict):
                continue
            candidate = normalize_youtube_url(meta.get("url", "")) or meta.get("url")
            if candidate == target:
                match = self._completed_result(str(meta_file.parent), meta)
                if match:
                    return match
        return None

    @staticmethod
    def _completed_result(workdir, meta=None):
        meta = meta or load_json(os.path.join(workdir, "meta.json"), None)
        if not isinstance(meta, dict):
            return None
        try:
            result = validate_result(
                meta.get("resultFile") or os.path.join(workdir, "result.json"),
                meta.get("digestVersion"),
            )
        except ResolutionError:
            return None
        return done_meta(result, meta)

    def process(self, canonical_youtube_url, metadata=None, collection_subdir=None):
        existing = self.lookup(canonical_youtube_url)
        if existing:
            return existing
        metadata = metadata or {}
        article = {
            "url": canonical_youtube_url,
            "articleKey": canonical_youtube_url,
            "title": metadata.get("title") or metadata.get("title_or_text") or "",
        }
        workdir = workdir_for(article)
        resolved = self.resolver(article, workdir)
        output_dir = collection_path(self.output_dir, collection_subdir or "公共")
        workdir, meta = prepare_workdir(article, resolved, output_dir)
        log(f"🚀 启动 pi -p: {article.get('title') or article['url']}")
        self.pi(workdir)
        return done_meta(validate_result(meta["resultFile"], meta.get("digestVersion")), meta)


def process_article(article):
    try:
        return {
            "url": article["url"],
            "status": "done",
            "processedAt": utc_iso(),
            "meta": PodcastProcessor().process(article["url"], article),
        }
    except ResolutionError as e:
        return {
            "url": article["url"],
            "status": "failed",
            "processedAt": utc_iso(),
            "meta": {"error": e.code, **e.meta},
        }
    except Exception as e:
        return {
            "url": article["url"],
            "status": "failed",
            "processedAt": utc_iso(),
            "meta": {"error": "pi-failed", "detail": str(e)[:240]},
        }


def main():
    log("=" * 50)
    log(f"开始巡检播客队列（{TRIGGER}）")

    lock_fd = acquire_lock()
    if lock_fd is None:
        log("另有进程在运行，跳过")
        return

    last_run = {"startedAt": local_iso(), "trigger": TRIGGER, "outcome": "running"}
    patch = {"lastRun": last_run}
    if TRIGGER == "scheduled":
        patch["lastScheduledStartAt"] = last_run["startedAt"]
    update_status(patch)

    def finish(outcome, **extra):
        last_run.update({"outcome": outcome, "finishedAt": local_iso(), **extra})
        update_status({"lastRun": last_run})

    try:
        if not os.path.exists(OUTBOX_FILE):
            log("outbox 不存在——扩展尚未完成首次桥接，跳过")
            finish("no-outbox")
            return
        outbox = load_json(OUTBOX_FILE, {})
        articles = outbox.get("articles") or []
        state = load_json(STATE_FILE, {})
        pending = [
            a for a in articles
            if a.get("url") and a.get("articleKey") not in state and a.get("url") not in state
        ][:MAX_PER_RUN]
        log(f"outbox 共 {len(articles)} 条，本次处理 {len(pending)} 条")
        if not pending:
            log("✅ 无需处理")
            finish("empty")
            return

        claim_rid = write_report([{"url": a["url"], "status": "processing"} for a in pending])
        log(f"📌 已认领 {len(pending)} 篇（{claim_rid}）")

        done = 0
        last_report = claim_rid
        for article in pending:
            result = process_article(article)
            last_report = write_report([result])
            if result["status"] == "done":
                done += 1
                state[article.get("articleKey") or article["url"]] = result["processedAt"]
                atomic_write_json(STATE_FILE, state)
                log(f"✅ 已完成: {article.get('title') or article['url']}（{last_report}）")
            else:
                log(f"❌ 失败: {article.get('title') or article['url']} — {result['meta'].get('error')}")

        finish("success" if done == len(pending) else "failed", count=done, reportId=last_report)
    except Exception as e:
        finish("error", error=str(e)[:300])
        raise
    finally:
        os.close(lock_fd)


if __name__ == "__main__":
    main()
