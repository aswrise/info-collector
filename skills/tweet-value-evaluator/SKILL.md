---
name: tweet-value-evaluator
description: Score a structured batch of X List posts against configured topics for Source Sync. Use when a workdir contains input.json and meta.json and requires four numeric dimensions plus a conservative reason code; never fetch X or enqueue work.
---

# Tweet Value Evaluator

Read `input.json` and `meta.json` from the supplied workdir. Do not browse, call OpenCLI, or fetch URLs.

For every Tweet, score only against `policy.topics` and the supplied structured context:

- `relevance`: 0–3
- `information_gain`: 0–3
- `usefulness`: 0–2
- `evidence`: 0–2

Use a `reason_code` only when it explains a weak item: `social_chatter`, `contextless_reaction`, `pure_promotion`, `engagement_bait`, `repetition`, `off_topic`, or `insufficient_context`. Missing thread context, an unexplained quote, or an assertion whose evidence is not included must use `insufficient_context`. Be conservative; do not infer missing facts.

Write only `meta.json.resultFile`:

```json
{
  "status": "ok",
  "decisions": [{
    "tweet_id": "123",
    "relevance": 2,
    "information_gain": 2,
    "usefulness": 1,
    "evidence": 1,
    "reason_code": null,
    "explanation": "one concise sentence",
    "confidence": 0.8
  }]
}
```

Return exactly one decision per input Tweet. Do not calculate the final collect/review/noise state; Python owns thresholds, Likes priority, shadow mode, and enqueueing.
