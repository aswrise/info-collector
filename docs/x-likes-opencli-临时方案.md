# X 收集临时方案（Likes + Lists）

> 状态：已被 `docs/source-sync-implementation-plan.html` 与 `source_sync/` 实现取代（2026-07-16）
>
> 用途：跨 session 保存已经确认可行的想法，后续可以继续追加。本文件不是最终设计、PRD 或实现承诺。

## 已确认的总体方向

info-collector 后续收集 Twitter/X 内容时，主要使用两类来源：

1. 当前账号的 Likes；
2. 指定 Twitter Lists 的时间线。

两类来源都通过 OpenCLI 只读命令定时拉取，再由 info-collector 在本地按 `tweet_id` 去重、记录首次观察时间并交给后续处理流程。

## 已确认方案：使用 OpenCLI 读取 Likes

使用 OpenCLI 已登录的 X 浏览器会话，按只读方式获取当前账号的 Likes：

```bash
opencli twitter likes Tenitsugunsmith --limit 20 -f json
```

该命令已经在本机实际验证成功，可以返回最近的 Likes。每条记录当前包含：

- 推文 ID；
- 作者用户名和显示名；
- 推文正文；
- 原帖点赞数和转发数；
- 原帖发布时间；
- 推文 URL；
- 图片、视频及其封面 URL。

注意：返回的 `created_at` 是原帖发布时间，不是用户点击 Like 的时间。OpenCLI 当前也没有返回精确点赞时间。

## 近似记录点赞时间

定时读取最新 Likes，并在本地记录第一次观察到某条推文的时间：

```text
定时运行 opencli twitter likes
    ↓
按 tweet_id 与本地记录比较
    ↓
第一次出现时写入 first_seen_at
    ↓
后续按 first_seen_at 筛选新增 Likes
```

示例：

```json
{
  "tweet_id": "2076079588686344447",
  "first_seen_at": "2026-07-12T14:30:00+08:00",
  "tweet_created_at": "2026-07-12T07:02:00+08:00"
}
```

字段含义：

- `tweet_created_at`：X 返回的原帖发布时间；
- `first_seen_at`：info-collector 第一次在 Likes 中观察到该推文的时间；
- `first_seen_at` 只是点赞时间的近似值，不是 X 提供的精确事件时间；
- 一旦写入，后续同步不得修改已有记录的 `first_seen_at`。

如果每小时同步一次，并且同步正常完成、抓取范围覆盖到了上次的水位线，那么点赞时间的通常误差约为一小时。

## 第一次导入

第一次导入的历史 Likes 无法推断准确点赞时间，只能记录导入时间：

```json
{
  "tweet_id": "2076079588686344447",
  "liked_at": null,
  "imported_at": "2026-07-12T14:30:00+08:00",
  "tweet_created_at": "2026-07-12T07:02:00+08:00"
}
```

规则：

- 历史 Likes 的 `liked_at` 保持为空；
- 不得用 `tweet_created_at` 冒充点赞时间；
- 建立初始水位线后，新增 Likes 才使用 `first_seen_at`；
- 抓取中断、分页不足、登录失效或遇到限流时，不能继续声称时间误差受轮询周期约束；
- 收集阶段保留 X 原生 Likes 顺序，互动量排序属于后续展示逻辑。

## 已确认方案：使用 OpenCLI 读取 Twitter List

对指定 List 定时拉取最新推文：

```bash
opencli twitter list-tweets 2058340249626128801 --limit 20 -f json
```

对应的 X 页面：

```text
https://x.com/i/lists/2058340249626128801
```

该命令已在本机实际验证成功，可以返回 List 时间线中的最新推文。每条记录当前包含：

- 推文 ID；
- 作者用户名、显示名和个人简介；
- 推文正文；
- 点赞数、转发数和回复数；
- 原帖发布时间和 URL；
- 图片、视频及封面 URL；
- 外链卡片；
- 被引用推文。

List URL 本身不直接传给命令，需要取出其中的数字 List ID。

List 来源同样按 `tweet_id` 去重。新推文第一次被收集时写入 `first_seen_at`；`created_at` 仍然保留为 X 返回的原帖发布时间。

## 定时拉取的共用流程

```text
定时运行 Likes 命令和各个 List 命令
    ↓
为每条结果标记来源（like 或 list + list_id）
    ↓
按 tweet_id 与本地记录比较
    ↓
新推文写入 first_seen_at，已有推文保留原值
    ↓
记录本次源水位和同步结果
    ↓
将新内容交给 info-collector 后续流程
```

同一条推文可能同时出现在 Likes 和一个或多个 Lists 中。内容实体只保留一份，但需要保留所有来源关系，例如：

```json
{
  "tweet_id": "2076227936202662357",
  "sources": [
    { "type": "like" },
    { "type": "list", "list_id": "2058340249626128801" }
  ]
}
```

## 待决定事项

- 定时同步频率和启动方式；
- 本地记录的文件格式和保存位置；
- Likes 和每个 List 正式运行时各自的 `--limit` 数值；
- 如何为 Likes 和每个 List 分别确认已经到达上次保存的 `tweet_id` 水位线；
- 后续是否会增加更多 List ID；
- 是否记录 `last_seen_at` 和每次同步的观察时间窗口；
- 私人 Likes 数据的保留、日志和隐私规则。

## 后续已确认想法

后续 session 中确认可行的新想法继续追加到这里。尚未确认的方案先放入“待决定事项”。
