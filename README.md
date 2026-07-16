# Info Collector

把 Chrome 书签变成本机处理流水线：文章放进「收藏文章」自动翻译成中文
Markdown；播客 / YouTube 访谈放进「收藏播客」自动生成 TLDR、深度总结和
全文稿。处理状态在扩展的 Dashboard 里一目了然（待处理 → 处理中 → 已完成），
跨设备同步。

Dashboard 长这样：状态卡片、流水线运行时间、逐篇文章的状态与操作按钮：

![Info Collector Dashboard：文章处理台账，显示待处理/已完成状态、来源、更新时间和操作按钮](docs/images/dashboard.png)

## 快速上手（普通用户）

文章翻译只需要一个 DeepSeek API Key（也支持 Anthropic API Key），三步装好，全程约 10 分钟：

1. 获取 API Key
2. Chrome 加载扩展
3. 终端跑 `bash scripts/setup.sh`

**手把手图文指南（写给非技术用户）：[SETUP.md](SETUP.md)**

也可以让 AI 替你装：把项目文件夹交给 AI 编程助手，说「按 SETUP.md 装好」。
安装脚本支持非交互模式（`INFO_COLLECTOR_ENGINE=deepseek INFO_COLLECTOR_API_KEY=sk-... bash scripts/setup.sh`），
装完可用 `translate-flow.py --check` 自检。

播客流是可选的，需要本机已安装 `pi` CLI。装好文章流后再运行：

```bash
bash scripts/setup-pi-podcast-flow.sh
```

之后把 YouTube / 播客页面收藏到 Chrome 书签文件夹「收藏播客」，Dashboard 里选
`播客 (podcast)` 可以查看状态并点「立即处理」。

## Source Sync（自动订阅源，可选）

Source Sync 独立于浏览器扩展的手动收藏队列，用本地 SQLite 监控 YouTube
Channel / Playlist 与 X Likes / Lists，并复用同一个 Podcast Processor 与 Tweet
Organizer。安装需要 `pi`、`yt-dlp` 和已可用的 `opencli`：

```bash
bash scripts/setup-source-sync.sh
```

打开本地 Dashboard：

```bash
cd ~/.info-collector/source-sync/app
python3 -m source_sync dashboard
```

然后访问 <http://127.0.0.1:8787>。数据、日志和状态分别位于
`~/.info-collector/source-sync/{source-sync.db,logs/,status.json}`。卸载调度与代码但保留数据库及产物：

```bash
bash scripts/setup-source-sync.sh --uninstall
```

定时策略：YouTube 每天 06:00；X 每天 08:00 / 20:00；Worker 每 30 分钟兜底，
并在扫描结束后立即运行。X 每页固定最多 20 条、页间随机等待 5–10 秒，认证、
429 或验证挑战会立即暂停，不在同次运行重试。X Likes 首次只同步最新 20 条，
随后仅同步水位之后的新 Likes。

产物按首次入队来源只保存一份：YouTube Channel / Playlist 写入
`播客收集/<频道或列表名>/`，手动收藏写入 `播客收集/公共/`；X Likes 写入
`文章收集/tweet 整理/likes/`，X List 写入 `文章收集/tweet 整理/<List 名>/`。
后续在其他来源发现同一内容时不会复制或移动文件；两个 `.base` 文件仍留在各自集合根目录。
Dashboard 可逐条或批量取消 queued 任务；禁用内容后，后续扫描不会再将它自动入队。

## 工作原理

```
Chrome 书签「收藏文章」 → 扩展（队列 + Dashboard） ⇄ 文件桥 ⇄ 翻译流（DeepSeek/Claude API）
                                                              ↓
                                                     ~/Documents/InfoCollector/*.md

Chrome 书签「收藏播客」 → 扩展（队列 + Dashboard） ⇄ 文件桥 ⇄ 播客流（transcript + pi）
                                                              ↓
                                                     Obsidian「播客收集/公共」三件套
```

- **扩展**（MV3）是唯一事实来源：从书签只读导入，状态存 `chrome.storage.sync`
  跨设备同步；书签本身永远不会被修改。
- **翻译流**是普通本机脚本，经 `~/.info-collector/` 下的文件契约与扩展
  交换数据（outbox 待处理清单 / inbox 完成报告），由 launchd 每小时调度，
  也可在 Dashboard 里点「▶ 立即处理」立刻触发。
- 内置翻译流支持 DeepSeek API 和 Claude API。DeepSeek 路径会优先用本机
  `defuddle parse --json` 提取正文与 metadata，再调 OpenAI-compatible
  `/chat/completions`；未安装 defuddle 时退回内置简易抓取器。Claude 路径继续
  使用 Anthropic Messages API 的服务端 `web_fetch`。
- 播客流由 `scripts/setup-pi-podcast-flow.sh` 注册，消费 `podcast` 队列；
  YouTube 会优先复用已有字幕 / transcript，记录频道、频道链接和发布日期，再交给
  `podcast-digest` Pi skill 写入 Obsidian「播客收集/公共」。

## 开发者

- 领域语言：[CONTEXT.md](CONTEXT.md) · 总体设计：[docs/design.md](docs/design.md)
  · 关键决策：[docs/adr/](docs/adr/)
- 目录：`extension/`（MV3 扩展）· `host/`（native messaging 文件桥）·
  `flows/`（手动队列薄壳）· `processors/`（共享处理器）· `source_sync/`（自动订阅源）·
  `templates/`（launchd 模板）· `scripts/`（安装）·
  `test/`（`npm test`，node --test）
- 配置文件：`~/.info-collector/config.json`（provider / apiKey / baseUrl / model / outputDir）·
  `~/.info-collector/flows.json`（流程注册表，见 ADR 0004）
- 内置处理类型：`translate`（书签「收藏文章」，自动入队）· `podcast`
  （书签「收藏播客」，去重后入队）。

### 新增一条处理流（文稿、书籍……）

1. Dashboard → 设置 → 新增处理类型（如 `transcript`）。
2. 写脚本：读 `~/.info-collector/outbox/transcript.json`，处理完写报告到
   `~/.info-collector/inbox/<reportId>.json`（原子写：临时文件 + rename）：

```json
{ "reportId": "transcript-20260705T120000-ab12", "processingType": "transcript",
  "results": [ { "url": "…", "status": "done", "processedAt": "…" } ] }
```

`status` 支持 `processing`（认领，显示「处理中」）/ `done` / `failed`。
`flows/translate-claude-api.py` 是完整参考实现（含状态文件、锁、认领报告）。

3. 想要 Dashboard 的「▶ 立即处理」按钮和运行状态，往
   `~/.info-collector/flows.json` 加一条注册：
   `{"command": [...启动命令, "--manual"], "lockFile": "…", "intervalSeconds": 3600}`。

## 平台支持

目前仅 macOS（launchd、Chrome native messaging 路径）。Chrome 需要
以「加载已解压的扩展程序」方式安装（manifest 内置固定 key，所有设备
上扩展 ID 一致：`fmdbamjmoabmcggjfgeopaijnbjkjbhm`）。
