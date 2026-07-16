import importlib.util
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FLOW_PATH = ROOT / "processors" / "podcast.py"


def import_flow(home):
    old_home = os.environ.get("HOME")
    old_argv = sys.argv[:]
    os.environ["HOME"] = str(home)
    sys.argv = ["podcast-bookmarks.py"]
    try:
        spec = importlib.util.spec_from_file_location("podcast_flow_under_test", FLOW_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.argv = old_argv
        if old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = old_home


def install_fake_defuddle(home, payload):
    bin_dir = home / "bin"
    bin_dir.mkdir(exist_ok=True)
    script = bin_dir / "defuddle"
    script.write_text(f"""#!/usr/bin/env python3
import json
print(json.dumps({payload!r}, ensure_ascii=False))
""", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return bin_dir


class PodcastFlowTest(unittest.TestCase):
    def test_manual_installer_uses_supported_python_and_enables_agent(self):
        setup = (ROOT / "scripts" / "setup-pi-podcast-flow.sh").read_text(encoding="utf-8")
        plist = (ROOT / "templates" / "com.pi.podcast-bookmarks.plist").read_text(
            encoding="utf-8"
        )

        self.assertIn("<string>__PYTHON__</string>", plist)
        self.assertIn('PYTHON="$(command -v python3)"', setup)
        self.assertIn("requires Python 3.10+", setup)
        self.assertIn("migrate_public_outputs", setup)
        self.assertIn('launchctl enable "gui/$(id -u)/$PLIST_LABEL"', setup)

    def test_manual_processor_writes_product_under_public_collection(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            flow = import_flow(home)
            flow.WORK_ROOT = str(home / "work")

            def resolve(_article, _workdir):
                return {
                    "text": "transcript",
                    "title": "Standalone",
                    "transcriptSource": "webpage_transcript",
                    "transcriptProvider": "defuddle",
                    "sourceReliability": "edited",
                }

            def write_product(workdir):
                meta = json.loads((Path(workdir) / "meta.json").read_text(encoding="utf-8"))
                output = Path(meta["outputDir"])
                output.mkdir(parents=True)
                files = []
                for name in ("Standalone（TLDR）.md", "Standalone（深度总结）.md", "Standalone（全文稿）.md"):
                    path = output / name
                    path.write_text(name, encoding="utf-8")
                    files.append(str(path))
                Path(meta["resultFile"]).write_text(json.dumps({
                    "status": "ok", "tldrFile": files[0],
                    "deepSummaryFile": files[1], "transcriptFile": files[2],
                }), encoding="utf-8")

            result = flow.PodcastProcessor(
                output_dir=home / "播客收集", resolver=resolve, pi=write_product
            ).process("https://example.test/standalone")

            self.assertEqual(Path(result["tldrFile"]).parent, home / "播客收集" / "公共")

    def test_existing_products_move_to_public_and_keep_base_at_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            flow = import_flow(home)
            output = home / "播客收集"
            workdir = home / "work" / "one"
            output.mkdir()
            workdir.mkdir(parents=True)
            base = output / "播客收集.base"
            base.write_text("views: []", encoding="utf-8")
            product = output / "Existing（TLDR）.md"
            product.write_text("existing", encoding="utf-8")
            result_file = workdir / "result.json"
            result_file.write_text(json.dumps({
                "status": "ok", "tldrFile": str(product),
            }), encoding="utf-8")

            moved = flow.migrate_public_outputs(output, home / "work")

            migrated = json.loads(result_file.read_text(encoding="utf-8"))
            self.assertEqual(moved, 1)
            self.assertEqual(
                Path(migrated["tldrFile"]), (output / "公共" / product.name).resolve()
            )
            self.assertTrue((output / "公共" / product.name).is_file())
            self.assertTrue(base.is_file())
            self.assertEqual(flow.migrate_public_outputs(output, home / "work"), 0)

    def test_defuddle_long_webpage_resolves_as_webpage_transcript(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            old_path = os.environ.get("PATH", "")
            bin_dir = install_fake_defuddle(home, {
                "title": "Interview",
                "author": "Ada",
                "published": "2026-07-06",
                "markdown": "中文访谈内容。" * 80,
            })
            os.environ["PATH"] = f"{bin_dir}{os.pathsep}{old_path}"
            try:
                flow = import_flow(home)
                flow.MIN_TRANSCRIPT_CHARS = 20
                got = flow.resolve_article(
                    {"url": "https://example.test/interview", "title": "Old"},
                    str(home / "work"),
                )
            finally:
                os.environ["PATH"] = old_path

            self.assertEqual(got["transcriptSource"], "webpage_transcript")
            self.assertEqual(got["transcriptProvider"], "defuddle")
            self.assertEqual(got["sourceReliability"], "edited")
            self.assertEqual(got["title"], "Interview")

    def test_short_webpage_with_youtube_embed_uses_youtube_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            old_path = os.environ.get("PATH", "")
            bin_dir = install_fake_defuddle(home, {
                "markdown": "short",
                "html": '<iframe src="https://www.youtube.com/embed/abc123"></iframe>',
            })
            os.environ["PATH"] = f"{bin_dir}{os.pathsep}{old_path}"
            try:
                flow = import_flow(home)
                flow.MIN_TRANSCRIPT_CHARS = 2000
                flow.resolve_youtube_transcript = lambda url, _workdir: {
                    "text": "caption text",
                    "transcriptSource": "youtube_unknown_caption",
                    "transcriptProvider": "firecrawl_youtube",
                    "captionKind": "unknown",
                    "sourceReliability": "unknown_caption",
                    "captionLanguage": "en",
                    "mediaSource": url,
                }
                got = flow.resolve_article({"url": "https://example.test/post"}, str(home / "work"))
            finally:
                os.environ["PATH"] = old_path

            self.assertEqual(got["mediaSource"], "https://www.youtube.com/watch?v=abc123")
            self.assertEqual(got["transcriptSource"], "youtube_unknown_caption")

    def test_youtube_metadata_is_written_to_workdir_meta(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            flow = import_flow(home)
            flow.WORK_ROOT = str(home / "work")
            flow.firecrawl_transcript = lambda _url: {
                "text": "caption " * 400,
                "transcriptSource": "youtube_unknown_caption",
                "transcriptProvider": "firecrawl_youtube",
                "captionKind": "unknown",
                "sourceReliability": "unknown_caption",
                "captionLanguage": "en",
                "mediaSource": "https://www.youtube.com/watch?v=abc123",
            }
            flow.yt_dlp_transcript = lambda _url, _workdir: None
            flow.fetch_youtube_metadata = lambda _url: {
                "title": "Video Title",
                "channel": "Sequoia Capital",
                "channelUrl": "https://www.youtube.com/@sequoiacapital",
                "published": "2026-06-16",
            }

            resolved = flow.resolve_youtube_transcript("https://youtu.be/abc123", str(home / "tmp"))
            workdir, _meta = flow.prepare_workdir({
                "articleKey": "https://www.youtube.com/watch?v=abc123",
                "url": "https://www.youtube.com/watch?v=abc123",
                "title": "Bookmark Title",
            }, resolved)
            meta = json.loads((Path(workdir) / "meta.json").read_text(encoding="utf-8"))

            self.assertEqual(meta["title"], "Video Title")
            self.assertEqual(meta["channel"], "Sequoia Capital")
            self.assertEqual(meta["channelUrl"], "https://www.youtube.com/@sequoiacapital")
            self.assertEqual(meta["published"], "2026-06-16")

    def test_youtube_without_transcript_fails_before_pi_or_browser_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            flow = import_flow(Path(tmp))
            flow.firecrawl_transcript = lambda _url: None
            flow.yt_dlp_transcript = lambda _url, _workdir: None
            flow.fetch_youtube_metadata = lambda _url: {
                "title": "Video Title",
                "channel": "Channel",
            }

            with self.assertRaises(flow.ResolutionError) as ctx:
                flow.resolve_youtube_transcript(
                    "https://youtu.be/abc123",
                    str(Path(tmp) / "work"),
                )

            self.assertEqual(ctx.exception.code, "youtube-transcript-unavailable")

    def test_pi_refuses_workdir_without_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            flow = import_flow(Path(tmp))
            with self.assertRaises(flow.ResolutionError) as ctx:
                flow.call_pi(tmp)

            self.assertEqual(ctx.exception.code, "youtube-transcript-unavailable")

    def test_youtube_publish_date_parsing(self):
        with tempfile.TemporaryDirectory() as tmp:
            flow = import_flow(Path(tmp))

            self.assertEqual(
                flow.parse_youtube_publish_date(
                    '"publishDate":{"simpleText":"Jun 16, 2026"}'
                ),
                "2026-06-16",
            )
            self.assertEqual(
                flow.parse_youtube_publish_date(
                    '"uploadDate":"2026-06-16T10:30:00-07:00"'
                ),
                "2026-06-16",
            )
            self.assertEqual(flow.parse_youtube_publish_date('"publishedTimeText":"12 days ago"'), "")

    def test_short_webpage_without_media_fails_no_existing_transcript(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            old_path = os.environ.get("PATH", "")
            bin_dir = install_fake_defuddle(home, {"markdown": "short"})
            os.environ["PATH"] = f"{bin_dir}{os.pathsep}{old_path}"
            try:
                flow = import_flow(home)
                flow.MIN_TRANSCRIPT_CHARS = 2000
                flow.fetch_raw_html = lambda _url: ""
                with self.assertRaises(flow.ResolutionError) as ctx:
                    flow.resolve_article({"url": "https://example.test/post"}, str(home / "work"))
            finally:
                os.environ["PATH"] = old_path

            self.assertEqual(ctx.exception.code, "no-existing-transcript")

    def test_firecrawl_transcript_extraction_and_blocked_rejection(self):
        with tempfile.TemporaryDirectory() as tmp:
            flow = import_flow(Path(tmp))
            flow.MIN_TRANSCRIPT_CHARS = 10
            self.assertEqual(
                flow.extract_firecrawl_transcript("# Title\n\n## Transcript\n\nhello world"),
                "hello world",
            )
            self.assertEqual(
                flow.extract_firecrawl_transcript("403 Forbidden\n\n## Transcript\n\nhello world"),
                "",
            )

    def test_json3_vtt_and_language_helpers(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            flow = import_flow(home)
            json3 = home / "x.en.json3"
            json3.write_text(json.dumps({
                "events": [
                    {"segs": [{"utf8": "Hello"}, {"utf8": " world"}]},
                    {"segs": [{"utf8": "Hello world"}]},
                    {"segs": [{"utf8": "Next line"}]},
                ],
            }), encoding="utf-8")
            vtt = home / "x.zh.vtt"
            vtt.write_text("WEBVTT\n\n1\n00:00:00.000 --> 00:00:01.000\n你好\n你好\n", encoding="utf-8")

            self.assertEqual(flow.parse_json3(json3), "Hello world\nNext line")
            self.assertEqual(flow.parse_vtt(vtt), "你好")
            self.assertEqual(flow.detect_language("这是中文" * 50), "zh")
            self.assertEqual(flow.detect_language("これは日本語です"), "en")
            self.assertEqual(flow.detect_language("hello", "en"), "en")

    def test_processor_finds_existing_output_from_parameterized_youtube_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            flow = import_flow(home)
            flow.WORK_ROOT = str(home / "work")
            old_url = "https://www.youtube.com/watch?v=abc123&list=PL123&index=1"
            workdir = Path(flow.workdir_for({"url": old_url, "articleKey": old_url}))
            workdir.mkdir(parents=True)
            outputs = []
            for name in ("tldr.md", "deep.md", "transcript.md"):
                output = home / name
                output.write_text(name, encoding="utf-8")
                outputs.append(str(output))
            result_file = workdir / "result.json"
            result_file.write_text(json.dumps({
                "status": "ok", "tldrFile": outputs[0],
                "deepSummaryFile": outputs[1], "transcriptFile": outputs[2],
            }), encoding="utf-8")
            (workdir / "meta.json").write_text(json.dumps({
                "url": old_url, "resultFile": str(result_file),
            }), encoding="utf-8")

            result = flow.PodcastProcessor().lookup(
                "https://www.youtube.com/watch?v=abc123"
            )

            self.assertEqual(result["tldrFile"], outputs[0])


if __name__ == "__main__":
    unittest.main()
