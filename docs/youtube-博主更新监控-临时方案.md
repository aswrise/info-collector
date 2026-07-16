# YouTube 来源更新监控临时方案（频道 + Playlist）

> 状态：已被 `docs/source-sync-implementation-plan.html` 与 `source_sync/` 实现取代（2026-07-16）
>
> 用途：跨 session 保存已经确认可行的 YouTube 频道与 Playlist 更新监控方案。本文件不是最终设计、PRD 或实现承诺。

## 目标

定时检查指定 YouTube 博主最近发布的长视频，以及指定 Playlist 新加入的视频。发现本地尚未收集的新视频后，复用现有 podcast 流程获取字幕，并生成：

1. TLDR；
2. 深度总结；
3. 全文稿。

首个确认的频道：

```text
https://www.youtube.com/@LennysPodcast
```

频道 ID：

```text
UC6t1O76G0jYXOAoYCm153dA
```

## 已确认方案：扫描最近的长视频

使用现有的 `yt-dlp`，只读取频道 `/videos` 页面最近的若干条记录，不下载视频：

```bash
yt-dlp \
  --flat-playlist \
  --playlist-end 5 \
  --dump-json \
  'https://www.youtube.com/@LennysPodcast/videos'
```

该命令已经在本机实际验证成功，可以返回最近 5 个长视频的稳定 `video_id`、标题、URL、频道和时长等信息。

不把 YouTube RSS 作为这个场景的主扫描来源。实测该频道 RSS 的最新内容会混入 Shorts，而 `/videos` 更符合“监控长节目更新”的目标。

OpenCLI 暂不进入主路径。只有 `yt-dlp` 无法列出频道视频时，才考虑以下备用命令：

```bash
opencli youtube channel @LennysPodcast --limit 5 -f json
```

## 新内容判断

使用 `video_id` 作为稳定主键，不使用标题或发布时间去重：

```text
定时扫描频道最近 5 个长视频
    ↓
按 video_id 与本地 pending / done 记录比较
    ↓
不存在的 video_id 加入本地 podcast 队列
    ↓
现有 podcast 流处理成功后标记 done
```

建议记录：

```json
{
  "channel_id": "UC6t1O76G0jYXOAoYCm153dA",
  "video_id": "yQ_EWmtfWvQ",
  "url": "https://www.youtube.com/watch?v=yQ_EWmtfWvQ",
  "first_seen_at": "2026-07-12T15:00:00+08:00",
  "status": "pending"
}
```

规则：

- 只有成功写入本地队列后才能标记为 `pending`；
- 只有三篇产物全部生成且 `result.json` 校验成功后才能标记为 `done`；
- 处理失败的记录继续保留为 `pending`，供后续重试；
- 不能因为某个视频曾经被扫描到，就直接把它当作处理成功；
- 同一视频即使在多次扫描中出现，也只保留一个内容实体。

## 第一次运行

第一次运行时先从现有 podcast 状态和产物元数据中导入已经处理过的 YouTube `video_id`。

如果某个频道没有历史记录，默认把首次扫描到的最近 5 条作为基线，不自动处理旧内容；建立基线后新出现的 `video_id` 才进入队列。如果以后需要补历史内容，再单独开启回填，不和日常更新监控混在一起。

## 已确认方案：监控 YouTube Playlist

对指定 Playlist 定时读取完整条目快照：

```bash
yt-dlp \
  --flat-playlist \
  --dump-single-json \
  'https://www.youtube.com/playlist?list=PLOhHNjZItNnMm5tdW61JpnyxeYH5NDDx8'
```

测试来源：

```text
https://www.youtube.com/watch?v=6bGxm8gX41o&list=PLOhHNjZItNnMm5tdW61JpnyxeYH5NDDx8
```

规范化后只保留 Playlist ID 和 Playlist URL，不依赖链接中当时正在播放的 `v` 参数。

该命令已经在本机实际验证成功。当前可以匿名取得：

- Playlist ID：`PLOhHNjZItNnMm5tdW61JpnyxeYH5NDDx8`；
- 名称：`Training Data`；
- 所属频道：`Sequoia Capital`；
- 当前 90 个视频的 ID、标题、URL、时长和列表顺序；
- Playlist 标记的最后修改日期 `2026-07-07`。

Playlist 不只扫描前 5 条，而是扫描完整列表。Playlist 管理者可能把新视频插入中间、调整顺序、删除视频或重新加入视频；当前列表只有 90 条，完整平铺扫描成本很低，也不会因为只看列表头部而漏掉新增内容。

```text
定时读取完整 Playlist
    ↓
取得本次全部 video_id 和 position
    ↓
与上次快照及本地 pending / done 记录比较
    ↓
新增 video_id 加入 podcast 队列
    ↓
现有 podcast 流处理成功后标记 done
```

建议记录：

```json
{
  "playlist_id": "PLOhHNjZItNnMm5tdW61JpnyxeYH5NDDx8",
  "video_id": "6bGxm8gX41o",
  "url": "https://www.youtube.com/watch?v=6bGxm8gX41o",
  "first_seen_at": "2026-07-12T16:00:00+08:00",
  "position": 1,
  "status": "pending"
}
```

Playlist 规则：

- 新增 `video_id`：加入 podcast 队列；
- 只有顺序变化：更新 `position`，不重复处理；
- 视频被移除：记录为 `removed`，不删除已经生成的文章；
- 已完成的视频被移除后重新加入：恢复来源关系，但不重复生成；
- 私密或暂不可用视频：保留观察记录但不处理，后续扫描继续检查是否已经公开；
- 同一视频出现在多个频道或 Playlist 来源中时，内容实体仍只处理一次，但保留所有来源关系。

Playlist 第一次运行默认把当前全部条目建立为基线，不自动处理历史内容。需要补历史时单独指定回填范围，不和日常更新监控混在一起。

## 复用现有字幕和三篇文章流程

新视频进入队列后，复用现有 podcast 流，不重新实现字幕或文章生成逻辑。

YouTube 字幕来源顺序保持为：

1. Firecrawl 抓取标准 watch 页面中的 transcript；
2. Firecrawl 失败后，使用 `yt-dlp` 获取已有人工字幕或自动字幕；
3. 前两种都失败时，由 Pi 使用已登录 Chrome 的 Transcript 面板；
4. 不下载音视频，不做音频转写。

已使用 Lenny's Podcast 视频 `yQ_EWmtfWvQ` 实测现有解析器：Firecrawl 成功取得 71,192 个字符的字幕，同时取得标题、频道、频道 URL 和发布日期；没有触发 `yt-dlp` 或 Chrome 回退。

## 需要新增的最小实现

现有 podcast flow 已经具备“单个视频 URL → 字幕 → 三篇文章”的完整后半段。需要新增的只有：

1. 一个读取频道和 Playlist 配置并定时运行 `yt-dlp` 的轻量扫描器；
2. 一个由扫描器维护的本地 YouTube pending/done 队列和来源关系；
3. 让现有 podcast flow 合并消费该队列。

扫描器不要直接修改浏览器扩展拥有的 podcast outbox，避免两个写入方发生覆盖或竞争。

## 待决定事项

- 正式监控的频道列表；
- 正式监控的 Playlist 列表；
- 每个频道扫描最近 5 条还是其他数量；
- 定时同步频率和启动方式；
- 本地队列及频道配置的正式保存位置；
- 首次运行是否允许对指定频道补历史内容；
- 是否监控 Shorts、直播和即将开始的直播；
- 连续失败后的重试间隔和告警方式。
