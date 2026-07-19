# Article Queue Extension — v1 设计

本文是 v1 的总体设计。领域语言见 `../CONTEXT.md`，关键决策见 `adr/`。
第二个处理类型（播客流）的设计见 `podcast-design.md`。

## 总体架构

```
┌─────────────── Chrome ────────────────┐        ┌────────── macOS ──────────┐
│                                        │        │                            │
│  书签文件夹「收藏文章」 (Bookmark Source) │        │  launchd 定时任务           │
│        │ chrome.bookmarks (只读)        │        │  (translate 流，未来更多流)  │
│        ▼                               │        │      ▲            │        │
│  Service Worker ──── chrome.storage ───│        │  读 outbox/    写 inbox/    │
│   导入 / 桥接 / 状态机     sync + local   │        │      │            │        │
│        │                               │        │      ▼            ▼        │
│        │ Native Messaging              │        │   ~/.info-collector/        │
│        └────────► host (python) ◄──────┼────────┤   outbox/  inbox/  spool   │
│                                        │        │                            │
│  Dashboard 页 + Toolbar Popup           │        └────────────────────────────┘
└────────────────────────────────────────┘
```

三个角色：

1. **扩展**（MV3）：队列的唯一事实来源（ADR 0001）。从书签文件夹只读导入，
   维护每篇文章按 Processing Type 分桶的状态，提供 Dashboard 管理界面。
2. **Native host**（`host/info_collector_host.py`）：哑文件搬运工。扩展通过
   Native Messaging 调它，它负责把待处理清单写到 outbox、把 inbox 里的
   Completion Report 读回来。不含业务逻辑（ADR 0002）。
3. **外部处理流**：普通本机脚本（如 launchd 的 translate 流）。只跟
   `~/.info-collector/` 文件打交道，完全不需要知道 Chrome 的存在。

## 已定决策一览

| 决策 | 结论 | 出处 |
|---|---|---|
| 事实来源 | 扩展自有存储，书签只是导入源 | ADR 0001 |
| 书签写权限 | v1 完全不动书签，manifest 只读 | 访谈确认 |
| 首个 Processing Type | `translate`（类型是数据注册表，不是硬编码） | 见下文说明 |
| 外部流集成方式 | 文件 spool 契约 + Native Messaging 桥 | ADR 0002 |
| sync 存储布局 | 分桶存储 + local 归档 | ADR 0003 |
| URL 归一化 | 保守：小写协议/主机、去 hash、暂不去 utm_* | 见下文 |
| Toolbar 直接保存 | v1 包含（popup 一键入队当前标签页） | 访谈推荐 |
| Dashboard | 扩展内页面（chrome-extension://…/dashboard.html） | 访谈推荐 |
| 立即触发流程 | host + flows.json 命令注册表，Dashboard「▶ 立即处理」 | ADR 0004 |

**关于 `translate` 取代先前的 `default`**：访谈早期曾定 v1 内部用 `default`。
之后用户补充了关键上下文——现有 launchd 翻译流之外，将来还会有「视频/播客文稿」
「书籍下载」等彼此独立的处理流。既然每条流就是一个独立的 Processing Type，
第一个类型如实命名为 `translate` 比抽象的 `default` 更准确；类型注册表本身
就是预留的扩展口（新流 = 注册表加一条数据，不改代码）。

## 数据模型

### Queue Record（内存中的完整形态）

```js
{
  articleKey: "https://example.com/post",   // 归一化 URL，即身份
  url: "https://example.com/post",          // 原始（或首见）URL
  title: "Post title",
  sources: [
    { kind: "bookmark", folderName: "收藏文章", bookmarkId: "123", importedAt: "…" },
    { kind: "manual",   importedAt: "…" },          // toolbar 保存
    { kind: "report",   importedAt: "…" }           // Completion Report 先于导入到达
  ],
  jobs: {
    translate: { status: "pending", processedAt: null, updatedAt: "…",
                 attempts: 0, lastError: null, meta: {} }
  },
  createdAt: "…",
  updatedAt: "…"
}
```

- `jobs.<type>.status` ∈ `pending | processing | done | failed | ignored`。
- `processing` 是外部流的**认领**：流程启动时先写一份 claim 报告（status
  `processing`），扩展应用后显示「处理中」，且不再进 outbox（防重复认领）。
  桥接时若发现流程未在运行（锁探测）却仍有 processing 文章，自动回退
  pending——流程崩溃自愈，不计失败次数。
- `failed` 表示外部流报告失败，仍会出现在 outbox 里等待重试（attempts 递增）。
- `ignored` 是用户在 Dashboard 主动排除，不进 outbox。
- Completion Report 提到的未知 articleKey 会**创建** done 记录（source kind
  `report`），保证「书签先删了、处理历史也不丢」。

### Processing Type 注册表（meta）

```js
{
  version: 1,
  settings: { folders: ["收藏文章"] },
  types: {
    translate: { label: "翻译", autoEnroll: true, createdAt: "…" }
  }
}
```

- `autoEnroll: true` 的类型：新文章入队时自动建 pending job。
- 未来的 `transcript`、`book` 等流：在 Dashboard 设置里加一条注册即可，
  outbox 会自动多出对应文件。是否 autoEnroll 由该流的语义决定。

### URL 归一化（articleKey 推导）

保守策略，先不做激进清洗：

1. 小写 protocol 与 hostname；
2. 去掉 hash fragment；
3. 去掉默认端口（:80/:443）；
4. 空路径归一为 `/`（`https://a.com` ≡ `https://a.com/`），其余路径的尾斜杠**保留原样**；
5. 保留全部 query 参数（含 `utm_*`，除非用户以后明确要去）。

## 存储布局（ADR 0003 摘要）

- `chrome.storage.sync`：活跃记录，32 个桶键 `aq:b:00`…`aq:b:1f`
  （articleKey 的 FNV-1a hash 取模），桶内是 articleKey → 压缩记录的 map；
  `aq:meta` 存注册表和设置。压缩编码（短字段名）由 `codec.js` 负责，
  读写两侧只见完整形态。
- `chrome.storage.local`：`aq:archive` 归档记录、`aq:acks` 待发送的报告 ack、
  `aq:runs` 导入/桥接运行状态、`aq:flows` 各流程运行状态缓存。
- 归档触发：所有 job 均为 done/ignored 且 30 天未更新，或 sync 用量超 80% 时
  从最旧的已完成记录开始迁移。导入去重同时查活跃桶和归档。

## Spool 文件契约（ADR 0002 摘要）

根目录 `~/.info-collector/`：

```
outbox/<type>.json         扩展写（经 host），外部流读。该类型全部 pending 文章。
inbox/<uuid>.json          外部流写，扩展读（经 host）。Completion Report。
inbox/processed/           扩展确认（ack）后 host 把报告移到这里留痕。
flows.json                 流程注册表（setup 写入）：type → {command, lockFile,
                           intervalSeconds, label}。host 据此触发流程与探测运行状态。
state/<type>-status.json   流程自己维护的运行状态：lastRun{startedAt, trigger,
                           outcome…} 与 lastScheduledStartAt（估算下次定时运行用）。
state/<type>-trigger.log   手动触发的流程输出。
```

**outbox/translate.json**

```json
{ "processingType": "translate", "generatedAt": "…",
  "articles": [ { "articleKey": "…", "url": "…", "title": "…", "enqueuedAt": "…" } ] }
```

**inbox 报告**（文件名即 reportId，写入必须先写临时文件再 rename）

```json
{ "reportId": "translate-20260705T120000-ab12", "processingType": "translate",
  "results": [ { "url": "…", "status": "done", "processedAt": "…", "meta": {} } ] }
```

- 扩展按归一化 URL 匹配记录；`status` ∈ `done | failed`。
- 双向都用「临时文件 + rename」原子写，避免读到半个文件。
- 应用报告 → 下次桥接会话发送 ack → host 把报告文件移入 `processed/`。
  在 ack 之前重复读到同一报告是幂等的（done 覆写 done 无副作用）。

## 桥接节奏

- `chrome.alarms`：`import`（30 分钟）扫描书签文件夹；`bridge`（10 分钟）
  与 host 做一次同步会话。Dashboard 上有手动「立即导入 / 立即同步」按钮。
- 桥接会话：connectNative → 发送 `sync`（携带各类型 outbox 内容 + 上次
  已应用报告的 ack）→ host 写 outbox、移走已 ack 报告、返回 inbox 新报告
  与各流程运行状态 → 扩展应用 → 断开。
- 立即触发（ADR 0004）：Dashboard 待处理行的「▶ 立即处理」= 先桥接刷新
  outbox，再发 `trigger` 让 host 按 flows.json 启动该类型流程（队列级，
  会处理该类型全部待处理文章），随后安排 3/8 分钟后的跟进桥接尽快收报告。
  状态区显示各流程：运行中 / 上次结果（手动或定时）/ 预计下次定时运行时间
  （由 `lastScheduledStartAt + intervalSeconds` 推算，手动触发不污染锚点）。

## 翻译流的两个实现

`flows/` 提供两个满足同一 spool 契约的 translate 流，`scripts/setup.sh`
默认为普通用户安装通用版：

- **`translate-claude-api.py`（通用，随仓库分发）**：纯 python 标准库支持
  DeepSeek API 和 Claude API 两种 provider。DeepSeek 路径优先调用本机
  `defuddle parse --json` 提取正文与 metadata，再调 OpenAI-compatible
  `/chat/completions`；未安装 defuddle 时退回内置简易抓取器。Claude 路径直接
  调 Anthropic Messages API，用服务端 `web_fetch` 工具抓取原文。逐篇翻译并按篇
  报告 done/failed。配置在 `~/.info-collector/config.json`（provider/apiKey/
  baseUrl/model/outputDir），`--check` 自检可验证 Key 与目录。launchd 模板见
  `templates/`，由 setup.sh 渲染安装。
- **`translate-bookmarks.py`（作者个人流）**：调 `pi` CLI +
  translate-article skill 存入 Obsidian，`scripts/setup-pi-flow.sh` 安装。

## 外部流改造：translate

`flows/translate-bookmarks.py` 取代 `~/.pi/scripts/translate-bookmarks.py`：

- **删除**全部 Chrome Bookmarks 文件读写（原脚本直改文件的行为正是事故根源）。
- 读 `outbox/translate.json`；跳过本地 `state/translate-reported.json` 里
  已报告过的 URL（防 outbox 未刷新时重复翻译）。
- 照旧调 `pi -p` + translate-article skill。
- 成功后原子写 inbox 报告，更新本地已报告清单，追加 action-history。
- outbox 不存在时打日志静默退出（扩展尚未装好时的过渡行为）。

历史迁移：`scripts/setup-pi-flow.sh` 把现有 `translated-urls.json` 转成一份
inbox 报告，扩展首次桥接时即把它们记为 done。

## Dashboard / Popup

- **dashboard.html**：状态计数、sync 配额条、按状态/类型过滤、搜索；
  行内操作：标记完成、忽略、重新排队、删除；全局操作：立即导入、立即同步、
  批量粘贴已完成 URL（自动化桥失效时的手动兜底）、导出 JSON；
  设置区：书签文件夹名、新增 Processing Type（v1 只增不删）。
- **popup**：显示当前标签页入队状态，一键「保存到队列」，入口到 Dashboard。

## v1 明确不做

- 写书签（归档/删除导入过的书签）。
- Processing Type 的删除/改名。
- 文章正文抓取或存储（只存元数据，ADR 0001 的量级约束）。
- 除 translate 外其他流的脚本实现（只保证契约就绪）。
