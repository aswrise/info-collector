# 7. 播客字幕不再使用浏览器自动化

日期：2026-07-12

## 状态

已接受

## 背景

原来的 YouTube 字幕顺序是 Firecrawl → yt-dlp → Pi 绑定用户现有 Chrome 读取
Transcript UI。这个设计把“字幕获取”和“内容生成”都交给了同一个 Pi 进程：当
`source.md` 不存在时，Pi 可以自行选择拥有的浏览器 skill。实际运行中，Pi 发现
OpenCLI browser bridge 可用，执行了 `opencli browser ...`，并通过用户的 Chrome
会话尝试读取字幕。流程没有代码级的工具白名单，skill 里的“使用 Browser Use / Chrome”
也没有阻止 Pi 选择 OpenCLI。

这条边界不适合无人值守的 launchd 任务：字幕失败不应升级成对用户浏览器、Cookie、
账号状态或标签页的自动化操作。

## 决策

YouTube 字幕只允许以下确定性路径：

1. Firecrawl 获取页面中的 transcript；
2. yt-dlp 获取手动或自动字幕；
3. 两者都失败时报告 `youtube-transcript-unavailable`。

只有存在 `source.md` 时才启动 `pi -p` 做摘要、翻译和 Obsidian 导出。
`podcast-digest` skill 不打开或控制浏览器、不使用 OpenCLI、不读取浏览器 Cookie，
`flows/podcast-bookmarks.py` 也不再创建 `needs_browser_resolution` 交接状态。

## 后果

- 无字幕的 YouTube 视频会明确失败，需要用户提供已有文稿/字幕来源后重试。
- 播客后台流不再触碰用户 Chrome，也不依赖 OpenCLI、BrowserBridge 或 Chrome 登录态。
- 保留 Firecrawl/yt-dlp 的 provider 元数据和现有失败报告语义。
- ADR 0005 的“Chrome Transcript UI 兜底”和 ADR 0006 的旧 provider 顺序不再适用于当前实现。
