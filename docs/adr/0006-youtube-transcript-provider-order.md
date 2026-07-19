# 6. YouTube transcript 先用 Firecrawl，再用 yt-dlp，最后读 Chrome UI

日期：2026-07-06

## 状态

已被 ADR 0007 取代

## 背景

`podcast` 流需要从 YouTube 拿到既有 transcript，但本机实测显示几条看似直接的路径并不稳定：`summarize` 的 `youtubei/get_transcript` 会返回 `400 failedPrecondition`，`captionTracks.baseUrl` 可能返回 200 空体，OpenCLI 的 `youtube transcript` 当前会因 cross-origin frame 抛 `SecurityError`。另一方面，Firecrawl 能在不使用用户 YouTube cookie 的情况下抓到 YouTube markdown transcript，yt-dlp 能在本地用字幕轨道拿到 json3，用户已登录 Chrome 的页面 UI 也能显示 Transcript panel。

## 决策

YouTube transcript provider 顺序固定为：Firecrawl scrape 标准 watch URL → yt-dlp 只下载字幕 → pi 绑定用户现有已登录 Chrome 读取 Transcript panel / `Copy Transcript`。不把 summarize 的低层 YouTube web API 或 OpenCLI adapter 放进 v1 主路径；它们以后只能作为可选实验 provider。

## 后果

- 默认路径不消耗用户 YouTube 账号 cookie，降低账号风控风险；Firecrawl 是云端服务，所以失败必须继续 fallback，不能当唯一依赖。
- yt-dlp 保留为本地可控路径，优先手动字幕，必要时接受自动字幕；后台默认不使用 `--cookies-from-browser chrome`。
- Chrome UI fallback 覆盖“页面明明有 transcript，但低层 API 拉不到”的情况；它依赖用户已有登录态，不适合纯无人值守后台。
