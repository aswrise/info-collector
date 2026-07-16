from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

from source_sync.types import ValueDecision


WORK_ROOT = Path("~/.info-collector/source-sync/work/value-evaluator").expanduser()
REASONS = {
    "social_chatter", "contextless_reaction", "pure_promotion", "engagement_bait",
    "repetition", "off_topic", "insufficient_context",
}


def decide(content_key: str, row: dict, minimum_collect_score: int = 7) -> ValueDecision:
    ranges = {"relevance": 3, "information_gain": 3, "usefulness": 2, "evidence": 2}
    scores = {}
    for name, maximum in ranges.items():
        value = row.get(name)
        if not isinstance(value, int) or not 0 <= value <= maximum:
            raise ValueError(f"{name} must be between 0 and {maximum}")
        scores[name] = value
    reason = row.get("reason_code")
    if reason is not None and reason not in REASONS:
        raise ValueError("invalid reason_code")
    total = sum(scores.values())
    if reason == "insufficient_context":
        decision = "review"
    elif total >= minimum_collect_score and scores["relevance"] >= 1:
        decision = "collect"
    elif total >= 4:
        decision = "review"
    else:
        decision = "noise"
    confidence = row.get("confidence")
    if confidence is not None and (not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1):
        raise ValueError("confidence must be between 0 and 1")
    return ValueDecision(
        content_key, **scores, total=total, decision=decision, reason_code=reason,
        explanation=row.get("explanation"), confidence=confidence,
    )


class ValueEvaluator:
    def __init__(self, work_root: str | Path = WORK_ROOT, call_pi=None):
        self.work_root = Path(work_root).expanduser()
        self.call_pi = call_pi or self._call_pi

    def evaluate(self, list_policy: dict, tweet_records: list[dict]) -> list[ValueDecision]:
        if not list_policy.get("topics"):
            raise ValueError("X List requires at least one topic")
        run = self.work_root / f"{int(time.time() * 1000)}-{os.getpid()}"
        run.mkdir(parents=True, exist_ok=False)
        result_file = run / "result.json"
        self._write(run / "input.json", {"policy": list_policy, "tweets": tweet_records})
        self._write(run / "meta.json", {"resultFile": str(result_file)})
        self.call_pi(run)
        try:
            result = json.loads(result_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError("value evaluator returned no valid result") from error
        rows = result.get("decisions") if result.get("status") == "ok" else None
        if not isinstance(rows, list) or len(rows) != len(tweet_records):
            raise RuntimeError("value evaluator must return one decision per Tweet")
        by_id = {str(row.get("tweet_id")): row for row in rows}
        minimum = int(list_policy.get("minimum_collect_score", 7))
        decisions = []
        for tweet in tweet_records:
            tweet_id = str(tweet.get("id") or "")
            if tweet_id not in by_id:
                raise RuntimeError(f"missing decision for Tweet {tweet_id}")
            decisions.append(decide(f"x:{tweet_id}", by_id[tweet_id], minimum))
        return decisions

    @staticmethod
    def _write(path: Path, value: dict) -> None:
        fd, temp = tempfile.mkstemp(dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(value, file, ensure_ascii=False, indent=2)
            os.replace(temp, path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)

    @staticmethod
    def _call_pi(workdir: Path) -> None:
        prompt = (
            "Use the tweet-value-evaluator skill.\n\n"
            f"Workdir: {workdir}\n\n"
            "Read input.json and meta.json. Do not browse or fetch URLs. "
            "Write only meta.json.resultFile."
        )
        try:
            proc = subprocess.run(["pi", "-p", prompt], text=True, capture_output=True, timeout=900)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise RuntimeError(f"value evaluator failed: {error}") from error
        if proc.returncode:
            raise RuntimeError(f"value evaluator failed: {(proc.stderr or '').strip()[:500]}")
