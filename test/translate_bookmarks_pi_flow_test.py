import importlib.util
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
FLOW_PATH = ROOT / "flows" / "translate-bookmarks.py"


def import_flow(home):
    old_home = os.environ.get("HOME")
    old_argv = sys.argv[:]
    os.environ["HOME"] = str(home)
    sys.argv = ["translate-bookmarks.py"]
    try:
        spec = importlib.util.spec_from_file_location("translate_bookmarks_under_test", FLOW_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.argv = old_argv
        if old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = old_home


class TranslateBookmarksPiFlowTest(unittest.TestCase):
    def test_successful_articles_are_reported_before_later_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            flow = import_flow(home)
            spool = home / ".info-collector"
            outbox = spool / "outbox"
            inbox = spool / "inbox"
            state_dir = spool / "state"
            logs = home / ".pi" / "logs"
            scripts = home / ".pi" / "scripts"
            for p in (outbox, inbox, state_dir, logs, scripts):
                p.mkdir(parents=True)

            flow.SPOOL = str(spool)
            flow.OUTBOX_FILE = str(outbox / "translate.json")
            flow.INBOX_DIR = str(inbox)
            flow.STATE_FILE = str(state_dir / "translate-reported.json")
            flow.STATUS_FILE = str(state_dir / "translate-status.json")
            flow.HISTORY_FILE = str(scripts / "action-history.json")
            flow.LOG_FILE = str(logs / "translate-bookmarks.log")
            flow.LOCK_FILE = str(logs / "translate-bookmarks.lock")

            (outbox / "translate.json").write_text(json.dumps({
                "processingType": "translate",
                "articles": [
                    {"articleKey": "https://example.test/ok", "url": "https://example.test/ok", "title": "ok"},
                    {"articleKey": "https://example.test/bad", "url": "https://example.test/bad", "title": "bad"},
                ],
            }), encoding="utf-8")

            calls = []

            def fake_run_pi(url):
                calls.append(url)
                if url.endswith("/ok"):
                    return True, SimpleNamespace(returncode=0, stdout="ALL_DONE", stderr=""), 1, None
                return False, SimpleNamespace(returncode=1, stdout="", stderr="boom"), 1, "pi exit 1"

            flow.run_pi = fake_run_pi

            with contextlib.redirect_stdout(io.StringIO()):
                flow.main()

            self.assertEqual(calls, ["https://example.test/ok", "https://example.test/bad"])
            state = json.loads((state_dir / "translate-reported.json").read_text(encoding="utf-8"))
            self.assertIn("https://example.test/ok", state)
            self.assertNotIn("https://example.test/bad", state)

            reports = [
                json.loads(p.read_text(encoding="utf-8"))
                for p in inbox.glob("translate-*.json")
            ]
            statuses = [
                item["status"]
                for report in reports
                for item in report.get("results", [])
                if item.get("url") in {"https://example.test/ok", "https://example.test/bad"}
            ]
            self.assertIn("done", statuses)
            self.assertIn("failed", statuses)

    def test_pi_env_adds_local_bin_for_ntn(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            flow = import_flow(home)
            old_home = os.environ.get("HOME")
            os.environ["HOME"] = str(home)
            try:
                self.assertIn(str(home / ".local" / "bin"), flow.pi_env()["PATH"].split(":"))
            finally:
                if old_home is None:
                    os.environ.pop("HOME", None)
                else:
                    os.environ["HOME"] = old_home


if __name__ == "__main__":
    unittest.main()
