---
name: podcast-digest
description: 根据 Info Collector podcast flow 的 workdir 生成 TLDR、1000 字总结、按需生成的 7000 字总结和中文全文稿。
---

# Podcast Digest

输入只传一个 workdir 路径。不要重新询问。读取：

- `source.md`：既有网页文稿或 YouTube transcript 原文。可能不存在。
- `meta.json`：URL、标题、来源元数据、输出目录、`resultFile`。

输出只以 `meta.json` 里的 `resultFile` 为准。stdout 不算成功信号。

## 1. transcript 来源契约

`source.md` 必须由上游 flow 先写入既有网页文稿或字幕。

这个 skill 不打开或控制浏览器，不读取浏览器 Cookie，也不为补字幕调用其它抓取器。
若 `source.md` 不存在，直接写：

```json
{"status":"failed","error":"youtube-transcript-unavailable"}
```

## 2. 日期

运行：

```bash
date +%Y-%m-%dT%H:%M
```

frontmatter 的 `date` 必须使用这个真实输出。

## 3. 生成分层产物

输出目录是 `meta.json.outputDir`。确保目录存在。

始终生成三个独立 Markdown 文件：

- `<中文标题>（TLDR）.md`
- `<中文标题>（1000字总结）.md`
- `<中文标题>（全文稿）.md`

全文稿去掉 frontmatter 后的非空白正文字符数达到 7000 时，再生成：

- `<中文标题>（7000字总结）.md`

按上述口径少于 7000 字符时不得生成 7000 字总结；已有同名旧文件时不要把它写进本次
`result.json`。所有本次生成的文件都写 frontmatter：

```yaml
---
title: <中文标题>
source: <原文 URL>
published: <YYYY-MM-DD，可空>
date: <date 命令输出>
authors: <作者，可空>
channel: <YouTube 频道/Podcast 名称>
channel_url: <频道 URL，可空>
category: 播客
duration: <时长，可空>
transcript_source: webpage_transcript | youtube_manual_caption | youtube_auto_caption | youtube_unknown_caption
transcript_provider: defuddle | firecrawl_youtube | yt_dlp
caption_kind: manual | auto | unknown
transcript_language: zh | en
source_reliability: edited | manual_caption | auto_caption | unknown_caption
---
```

### TLDR

约 500 汉字（450-650），一段式核心结论。文件内链接：

```markdown
相关：[[<中文标题>（1000字总结）]] / [[<中文标题>（全文稿）]]
```

仅在本次生成 7000 字总结时，在相关链接中加入
`[[<中文标题>（7000字总结）]]`。

### 1000 字总结

约 1000 汉字（900-1100）。提炼核心观点、关键依据和重要细节，不按时间线复述。

### 7000 字总结

只在全文稿去掉 frontmatter 后的非空白正文字符数达到 7000 时生成。约 7000 汉字（6000-8000），保留论证链、
关键数据、金句引用，不按时间线复述。

### 全文稿

- 源是中文：清理原文，保留说话人结构，去广告推广，不改写不删减。
- 源是英文：完整翻译成中文。分段翻译、逐段追加，避免输出截断。
- 人名/术语首次出现保留英文原文。

### 频道信息

YouTube 来源必须记录频道账号：

- `channel` 写频道显示名，例如 `Sequoia Capital`。
- `channel_url` 写频道主页 URL，例如 `https://www.youtube.com/@sequoiacapital`。

非 YouTube 播客也要尽量记录 podcast / show 名称。找不到时才留空。

完成前自查：全文稿段落数与原文一致或有明确合并理由，无截断。

## 4. result.json

成功时写：

```json
{
  "status": "ok",
  "digestVersion": 2,
  "tldrFile": "/absolute/path/标题（TLDR）.md",
  "summary1000File": "/absolute/path/标题（1000字总结）.md",
  "summary7000File": "/absolute/path/标题（7000字总结）.md",
  "transcriptFile": "/absolute/path/标题（全文稿）.md"
}
```

全文稿按上述非空白正文字符口径少于 7000 时，省略 `summary7000File`。

任何失败都写：

```json
{"status":"failed","error":"<error-code>"}
```

允许的 error code：
`youtube-login-required`、`youtube-transcript-unavailable`、`pi-failed`。

v1 不同步 Notion，不下载音频，不做语音转写。
