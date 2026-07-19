# 5. Podcast 流只用既有文稿，不做音频转写

日期：2026-07-06

## 状态

已被 ADR 0007 取代

## 背景

`podcast` 处理类型要把长播客/访谈内容加工成摘要与中文全文稿，第一步是拿到
文稿。候选来源有四类：网页上已存在的成熟文稿（编辑过的访谈文章、show notes、
博客整理稿等）、YouTube 字幕（可能是手动，也可能是自动或无法判断 kind）、
YouTube 登录态页面里的 Transcript UI、以及下载音频后用 Whisper/Groq/Deepgram
等做语音转写。

实际观察：用户投喂的链接大多**已经带文稿**——微信公众号的播客整理文（实测
样本约 9000 汉字编辑稿）、Substack/博客的访谈整理等。是否值得为剩余场景引入
音频下载 + 转写这条重链路，是真实权衡：转写路径带来 API Key、音频下载、
分钟级成本、长音频切分等一整层复杂度，而收益只覆盖「既无网页文稿又无字幕」
的少数情况。

自动字幕质量曾被质疑，参考工具（last30days-skill、steipete/summarize）均
接受自动字幕（后者默认 manual-first，仅可选过滤）。

## 决策

来源按优先级取第一个可用的。YouTube 先走 Firecrawl，再走 yt-dlp 字幕；这些路径
如果被 bot/auth/页面接口卡住，不直接失败，而是交给 `pi -p` 里的
`podcast-digest` skill 使用已登录 Chrome 的 Browser Use 能力读取右侧
Transcript panel 或 `Copy Transcript`；补取到的文本仍按下面的来源类型标注。
这个浏览器补取必须绑定用户现有的已登录 Chrome，会话不能通过杀/重启 Chrome 或
临时 `--user-data-dir` profile 伪造；实测临时 profile 会得到 YouTube
`LOGIN_REQUIRED`。
所有既有 transcript/caption 路径都取不到时才明确失败：

1. 网页成熟文稿（任意站点，通用判断，不做微信特判）
2. YouTube 手动字幕
3. YouTube 自动字幕（接受，但元数据必须标注 `source_reliability: auto_caption`）
4. YouTube 字幕但无法判断 manual/auto（接受，标注 `unknown_caption`）
5. 都没有 → 报 `youtube-transcript-unavailable` 或 `no-existing-transcript`
   失败，**不下载音频、不做语音转写**

每篇产物在 frontmatter 记录 `transcript_source` / `transcript_provider` /
`caption_kind` / `transcript_language` / `source_reliability`，让阅读者知道文稿的
可信度来源与获取机制。

## 后果

- 流程零音频依赖：无转写 API Key、无 ffmpeg、无大文件下载，flow 保持
  「defuddle + Firecrawl/yt-dlp 字幕 + pi」的轻结构；YouTube bot/auth 问题由
  pi 在已登录 Chrome 中补取 transcript，而不是引入语音转写链路。无法绑定现有
  登录态时报 `youtube-login-required`。
- 纯音频播客（无文稿页、无 YouTube 字幕，如部分小宇宙/Apple Podcasts 单集）
  v1 处理不了，会明确失败。如未来此类失败占比变高，再评估转写路径，
  届时本 ADR 需修订。
- 自动字幕的错别字/断句问题由下游 skill 的清理与摘要吸收，
  `auto_caption` 标注让用户对全文稿质量有预期。
