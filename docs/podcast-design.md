# Podcast 流（podcast）设计

第二个 Processing Type：把长播客 / 访谈 / 文稿类内容加工成「TLDR + 1000 字总结 +
按需生成的 7000 字总结 + 中文全文稿」。领域语言见 `../CONTEXT.md`，来源边界见 `adr/0005`，YouTube
provider 顺序见 `adr/0006`。
总体架构（spool 契约、桥接、触发）不变，见 `design.md`。

## 目标与产物

输入：一个 URL（成熟文稿网页，或 YouTube 视频页）。产出分层文件，写入 Obsidian。
各层必须是**独立文件**：

| 产物 | 规格 |
|---|---|
| TLDR | 约 500 汉字（450–650），一段式核心结论 |
| 1000 字总结 | 约 1000 汉字（900–1100），提炼核心观点、关键依据和重要细节 |
| 7000 字总结 | 仅当全文稿去掉 frontmatter 后的非空白正文字符数达到 7000 时生成；约 7000 汉字（6000–8000），保留论证链、关键数据、金句引用 |
| 全文稿 | 源是中文 → 清理后的原文（保留说话人结构，去广告推广，不改写）；源是英文 → 完整中文翻译（分段进行，人名/术语首次出现保留原文） |

文件落在 Obsidian vault：`文章收集` 的兄弟目录 `播客收集/`：

- `播客收集/<中文标题>（TLDR）.md` —— 500 字摘要，链接到其他本次产物
- `播客收集/<中文标题>（1000字总结）.md` —— 必需的中等长度总结
- `播客收集/<中文标题>（7000字总结）.md` —— 全文稿非空白正文字符数达到 7000 时生成
- `播客收集/<中文标题>（全文稿）.md` —— 全文稿

所有生成文件都写 frontmatter（在 translate-article 惯例上追加播客字段）：

```yaml
title / source / published / date / authors / category: 播客
duration: <时长，可知时填>
transcript_source: webpage_transcript | youtube_manual_caption | youtube_auto_caption | youtube_unknown_caption
transcript_provider: defuddle | firecrawl_youtube | yt_dlp
caption_kind: manual | auto | unknown
transcript_language: zh | en
source_reliability: edited | manual_caption | auto_caption | unknown_caption
```

v1 不同步 Notion（translate-article 的 M4 不复用），列为后续增强。

## 决策一览

| 决策 | 结论 | 理由 |
|---|---|---|
| 类型命名 | `podcast`（不是 design.md 早前设想的 `transcript`） | 判据是内容形态（长口语/访谈内容），用户心智即「播客流」；`transcript` 只描述中间物不描述产物 |
| autoEnroll | `false` | 不是每篇收藏都是播客；误入队的代价（45 分钟 pi 运行）远高于 translate |
| 来源优先级 | 网页成熟文稿 > YouTube 字幕；YouTube 先 Firecrawl，再 yt-dlp，再 pi 读已登录 Chrome 的 Transcript UI；**不做音频转写** | ADR 0005 / ADR 0006 |
| 语言检测 | caption 语言标签 > 本地 CJK 占比启发式；不调 LLM 分类 | zh/en 二分启发式几乎不会错，省一次调用与失败面 |
| 生成引擎 | flow 抓取 + `pi -p` 调 `podcast-digest` skill 生成 | 与 translate 流对称；抓取放 flow 侧可控可测，生成放 skill 侧可版本管理 |
| 成功判据 | skill 写 `result.json`，flow 校验其内容与产物文件存在 | 单篇成本高，不能沿用 translate 的整批 ALL_DONE 粗粒度语义 |
| 处理粒度 | 逐篇处理、逐篇报告；每次运行最多 2 篇 | 单篇最长 45 分钟，限批防止运行数小时；逐篇报告让 Dashboard 跟进桥接尽快看到进度 |
| 失败重试 | 沿用 translate：state 只记 done，failed 留在 outbox 自然重试；YouTube provider 失败不在单次运行内反复轰炸 | `no-existing-transcript` 重试很便宜；YouTube 失败可能消耗 Firecrawl/账号风控预算，单次运行每 provider 最多试一次 |
| 误翻译竞态 | popup 新增「保存为播客」，只建 podcast job | 否则 saveTab 会按 autoEnroll 给播客页建 translate job，一小时内被翻译流误处理 |

## Processing Type 注册

在 Dashboard 设置区手工添加：id `podcast`，label `播客`，autoEnroll 不勾选。
（类型注册表在 chrome.storage 里，setup 脚本无法代劳。）

## Spool 契约实例

完全复用 ADR 0002 契约，只是多一个类型：

```
outbox/podcast.json              扩展写，flow 读
inbox/podcast-*.json             flow 写（claim + 逐篇 done/failed）
state/podcast-reported.json      flow 本地已报 done 清单（防 outbox 未刷新时重复处理）
state/podcast-status.json        flow 运行状态（Dashboard 状态区）
~/.pi/logs/podcast-bookmarks.lock   fcntl 锁（pi 个人流，与 translate 同风格）
~/.pi/logs/podcast-bookmarks.log    日志
```

flows.json 注册：

```json
"podcast": {
  "command": ["/usr/bin/python3", "~/.pi/scripts/podcast-bookmarks.py", "--manual"],
  "lockFile": "~/.pi/logs/podcast-bookmarks.lock",
  "intervalSeconds": 3600,
  "label": "com.pi.podcast-bookmarks"
}
```

done 报告的 meta 携带产物路径与来源元数据：

```json
{ "url": "…", "status": "done", "processedAt": "…",
  "meta": { "tldrFile": "…", "summary1000File": "…", "summary7000File": "…", "transcriptFile": "…",
            "transcriptSource": "webpage_transcript",
            "transcriptProvider": "defuddle", "captionKind": null,
            "transcriptLanguage": "zh", "sourceReliability": "edited" } }
```

## 流程内部（flows/podcast-bookmarks.py）

骨架与 `translate-bookmarks.py` 相同（锁 → status → 读 outbox → 过滤已报告 →
claim → 处理 → 报告 → finish），差异在处理循环：

职责边界：flow 只做确定性的队列、抓取、字幕解析、语言启发式与状态报告；凡是需要
判断、改写或生成的事都交给 `pi -p` 的 `podcast-digest` skill，包括标题中文化、
正文清理、全文翻译、TLDR、高密度总结、Obsidian 文件落盘与最终自检。

```
取未报告文章的前 MAX_PER_RUN(=2) 篇
写一份 claim 报告（这批全部 processing）
逐篇：
  1. resolve  → (transcript_text, transcript_source, metadata) / 失败错误码
  2. detect_language(transcript_text, caption_lang)
  3. 写工作目录 ~/.info-collector/work/podcast/<articleKey哈希>/
       source.md   文稿原文
       meta.json   {url,title,author,published,duration,transcriptSource,
                    transcriptProvider,captionKind,transcriptLanguage,
                    sourceReliability,outputDir,resultFile}
  4. pi -p 调 podcast-digest skill（timeout 2700s）
  5. 校验 result.json（status=ok，tldrFile/summary1000File/transcriptFile 存在；
     summary7000File 有值时也必须存在）
  6. 立即写单篇 done/failed 报告；done 时更新 state/podcast-reported.json
```

常量置于文件头（沿 translate 风格）：`MAX_PER_RUN=2`、`PI_TIMEOUT=2700`、
`MIN_TRANSCRIPT_CHARS=2000`、`FIRECRAWL_TIMEOUT=120`、`YT_COOKIES_FILE=None`、
`YT_COOKIES_BROWSER=None`。YouTube 本地快路径可使用 `YT_COOKIES_FILE`；如果
未配置或仍被 bot/auth 卡住，直接报告 `youtube-transcript-unavailable`，不启动 Pi
补取字幕，也不触碰浏览器。

### resolve：来源解析器

```
url 属于 youtube.com / youtu.be / m.youtube.com
  → YouTube transcript 路径
否则
  → defuddle parse <url> --json
     正文字符数 ≥ MIN_TRANSCRIPT_CHARS → webpage_transcript（reliability=edited）
     不足 → 在 defuddle 输出与原始 HTML 里找 YouTube 链接（iframe embed / watch URL）
        找到 → YouTube transcript 路径
        没有 → failed: no-existing-transcript
  defuddle 本身失败 → failed: fetch-failed
```

刻意宽松：不做说话人标记 / Q&A 结构等启发式判断——非 YouTube 的长文即使不是
严格意义的文稿，也值得按此流程摘要；「泛化网页文稿优先」是用户明确要求
（微信只是一个观察样本，不做站点特判）。

### YouTube transcript 路径

先把所有 YouTube URL 标准化为 `https://www.youtube.com/watch?v=<id>`。短链
`youtu.be` 在 Firecrawl 上实测更容易落到 403 页面壳。

Provider 顺序固定为：

1. **Firecrawl（默认省心路径）**

   ```
   npx -y firecrawl-cli@latest scrape "<watch-url>" \
       --format markdown --country US --wait-for 5000 --max-age 0
   ```

   从 markdown 的 `## Transcript` 之后抽正文，正文长度 ≥ `MIN_TRANSCRIPT_CHARS`
   且不包含 YouTube 403 / 登录提示时接受。metadata：
   `transcriptProvider=firecrawl_youtube`，`captionKind=unknown`，
   `transcriptSource=youtube_unknown_caption`，
   `sourceReliability=unknown_caption`。

   本机实测：
   - 自动字幕样本 `aiR7F4jqjXY`：约 48k 字符 / 8.9k 词。
   - 创作者字幕样本 `pv1TUJSEM2k`：约 212k 字符 / 38k 词，和
     `yt-dlp --write-subs` 的手动字幕几乎一致。

2. **yt-dlp（本地可控路径）**

   ```
   uvx --from yt-dlp yt-dlp --skip-download --write-subs --write-auto-subs \
       --sub-langs "en,en-orig,en.*,zh.*" --sub-format "json3/vtt" \
       --ignore-no-formats-error -o "<workdir>/%(id)s.%(ext)s" <watch-url>
   ```

   `--write-subs` 产物 = 手动字幕（`captionKind=manual`），
   `--write-auto-subs` 产物 = 自动字幕（`captionKind=auto`）；两者都有时取手动。
   json3 优先解析 `events[].segs[].utf8`，VTT 仅作兜底。caption 语言标签直接作为
   `transcript_language`。

   默认不在 launchd 后台使用 `--cookies-from-browser chrome`。如果配置了
   `YT_COOKIES_FILE`，追加 `--cookies <file>`；如果配置了 `YT_COOKIES_BROWSER`，
   仅用于手动调试，追加 `--cookies-from-browser <值>`。

3. **没有浏览器兜底**

   如果 Firecrawl 与 yt-dlp 都没拿到正文，直接报告
   `youtube-transcript-unavailable`。不启动 Pi 补取字幕，不打开或控制 Chrome，
   不读取浏览器 Cookie，不使用 OpenCLI，也不下载音频或做语音转写。

### detect_language

1. caption 语言标签可用 → 直接用。
2. 否则取文稿前 4000 字符：含日文假名 → 按 en 处理（走翻译路径，v1 不特判日语）；
   CJK 占比 > 25% → zh；否则 en。

### 失败错误码

`no-existing-transcript` | `fetch-failed` | `youtube-transcript-unavailable` |
`pi-failed`（pi 超时 / 非零退出 / result.json 缺失或校验不过）。
均写入 failed 报告的 `meta.error`，Dashboard chip title 可见。

## podcast-digest skill（skills/podcast-digest/SKILL.md）

源码进仓库 `skills/`，setup 复制到 `~/.claude/skills/podcast-digest/`。
输入约定：prompt 只传工作目录路径，skill 自己读 `source.md` 与 `meta.json`。

要点（吸收 translate-article 的教训）：

- `date` 字段必须来自 `date +%Y-%m-%dT%H:%M` 真实输出，禁止凭记忆填写。
- 全文翻译**分段进行、逐段追加写入**，规避单次输出长度上限；译完自查段落数
  与原文一致，无截断。
- `source.md` 必须由上游 flow 预先写入。skill 不打开或控制浏览器，不使用 OpenCLI，
  不读取浏览器 Cookie，也不抓取替代 transcript；缺少 `source.md` 时写
  `{"status":"failed","error":"youtube-transcript-unavailable"}`。
- 中文源：清理（合并破碎换行、去推广尾巴、保留说话人标记），不改写不删减内容。
- 产物结构：始终写 `（TLDR）`、`（1000字总结）`、`（全文稿）`；全文稿去掉
  frontmatter 后的非空白正文字符数达到 7000 时再写 `（7000字总结）`。各摘要链接到本次实际生成的其他产物。
- 完成后写 `meta.json` 里指定的 `resultFile`：
  `{"status":"ok","digestVersion":2,"tldrFile":"…","summary1000File":"…","summary7000File":"…","transcriptFile":"…"}`；短全文省略 `summary7000File`，任何一步失败写
  `{"status":"failed","error":"…"}`。**result.json 是唯一成功信号**，stdout
  不作数。

## 扩展改动

1. **Dashboard「加入队列」**（P0）：`renderTable` 中记录无当前类型 job 时
   （现在只给「删除」），补一个「加入队列」按钮 → `setJobStatus(key, type,
   'pending')`。`queue.js#setJobStatus` 已会为缺失 job 建新 job，后端零改动。
   归档记录不提供此操作（v1 已知限制，见下）。
2. **popup「保存为播客」**（P1）：popup 增加第二按钮；`saveTab` 命令接受可选
   `types: string[]`（校验存在于注册表），提供时**取代** autoEnroll 类型集。
   「保存为播客」= `saveTab({types: ['podcast']})`——不建 translate job，从根上
   消除误翻译竞态。默认按钮行为不变。

## 安装（scripts/setup-pi-podcast-flow.sh + templates/com.pi.podcast-bookmarks.plist）

镜像 `setup-pi-flow.sh`，多两步（skill 与 launchd 是新的，translate 当年是既有的）：

1. `flows/podcast-bookmarks.py` → `~/.pi/scripts/`（备份旧版）。
2. `skills/podcast-digest/` → `~/.claude/skills/podcast-digest/`。
3. 渲染 `templates/com.pi.podcast-bookmarks.plist`（interval 3600）→
   `~/Library/LaunchAgents/` 并 `launchctl load`。
4. 注册 flows.json `podcast` 条目。

无历史迁移（新流无存量）。

## 测试计划

**扩展（node:test）**

- `queue.test.js`：`setJobStatus` 对无该类型 job 的记录创建 pending job（覆盖
  Dashboard「加入队列」语义）。
- `saveTab` 的 `types` 覆盖逻辑（提供 types 时不建 autoEnroll job；types 含
  未注册类型时报错）。

**flow（python，镜像 translate_flow_backend_test.py）**

- resolve：微信样本 fixture（长中文文稿）→ webpage_transcript；短页面含
  YouTube embed → 转 YouTube transcript 路径；短页面无媒体 → no-existing-transcript。
- Firecrawl：标准 watch URL、抽 `## Transcript` 后正文、拒绝 403/登录页/过短正文，
  成功时标 `firecrawl_youtube` + `youtube_unknown_caption`。
- yt-dlp caption 排序：手动优先于自动；只有自动时接受并标 auto_caption。
- YouTube bot/auth stderr 或 Firecrawl/yt-dlp 均失败 →
  `youtube-transcript-unavailable`，不走浏览器补取。
- json3 解析：`events[].segs[].utf8` 拼接；VTT 兜底解析：时间戳剥离、
  自动字幕滚动重叠去重。
- 语言启发式：中文文稿 → zh；英文 → en；含假名 → en。
- 报告语义：claim 批量 processing；逐篇 done/failed；result.json 缺失 →
  pi-failed；done 后写入 reported state。

**真实链路**：按用户要求一次只跑一篇真实文章。先跑微信样本走 webpage 路径；
YouTube 只做一次低成本字幕验证（Firecrawl 或 yt-dlp，不下载音视频）。

## 已知限制与风险

- **归档记录不能加入 podcast 队列**：`setJobStatus` 命令只查活跃记录。归档
  判据是「全部 job 完成且 30 天未动」，近期记录都在活跃区，先接受；需要时
  再做「解档入队」。
- **Firecrawl 是云端服务，不是本地库**：CLI 未登录也可能可用，但不能假设永久免费
  或永久匿名可用；失败时继续走 yt-dlp，不把 Firecrawl 失败当终局。
- **YouTube 匿名提取大概率被 bot 拦截**（本机已实测）：不要把匿名 yt-dlp
  当成稳定能力。本地快路径可配 `YT_COOKIES_FILE` 或显式的
  `YT_COOKIES_BROWSER`；仍失败时明确报告 `youtube-transcript-unavailable`，不把
  用户 Chrome、浏览器 Cookie 或 OpenCLI 纳入后台兜底。
- **7000 字摘要逼近模型单次输出上限**：靠 skill 的分节/分段生成规避；摘要长度
  是区间目标不是硬约束。
- **failed 每小时自动重试**：对 pi-failed 意味着最多每小时烧一次 45 分钟运行；
  对 YouTube 也可能重复消耗 Firecrawl/风控预算。单次运行每个 YouTube provider
  只试一次，MAX_PER_RUN=2 兜底，用户可随时忽略该文章。

## 实施顺序

1. flow 脚本 + python 测试（resolve / caption / 语言 / 报告语义）。
2. `skills/podcast-digest/SKILL.md`。
3. setup 脚本 + launchd 模板。
4. 扩展：Dashboard 加入队列（P0）、popup 保存为播客（P1）+ node 测试。
5. Dashboard 注册 `podcast` 类型，跑一篇真实微信文章验证全链路；再跑一次
   YouTube transcript provider smoke（不生成完整分层产物）。
