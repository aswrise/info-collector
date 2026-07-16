---
name: tweet-organizer
description: Organize one structured X/Twitter record into a concise Chinese Obsidian note result. Use only when a Source Sync workdir contains tweet.json and meta.json and requires result.json; preserve quotes, cards, and media without accessing X or a browser.
---

# Tweet Organizer

1. Read `tweet.json` and `meta.json` from the supplied workdir. Treat the structured record as complete; do not open a browser, call OpenCLI, or fetch any URL.
2. Determine `language` as `zh`, `en`, or `mixed` from the actual Tweet and quoted/card text.
3. Produce a short, specific Chinese title without hype.
4. Write `body` as Markdown:
   - Chinese: organize without translation.
   - English: preserve the complete English original, then provide a faithful Chinese translation.
   - Mixed: preserve all original text and translate only the English portions.
   - Preserve quoted Tweet text, link-card title/description/URL, and every media URL.
   - Do not expand claims, add outside facts, or invent missing context.
5. Write only `meta.json.resultFile` as JSON:

```json
{
  "status": "ok",
  "title": "中文短标题",
  "body": "Markdown body",
  "language": "zh",
  "translation_status": "not_needed",
  "authors": "作者名（@handle）",
  "tags": ["必要标签"]
}
```

Use `translation_status` = `not_needed` for Chinese, `translated` for English, and `partially_translated` for mixed. On failure write `{"status":"failed","error":"short-code"}`. Never write the Obsidian file itself; Python owns file naming, frontmatter, system time, and atomic IO.
