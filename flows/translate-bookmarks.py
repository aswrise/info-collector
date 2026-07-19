#!/usr/bin/env python3
"""
翻译流（translate）：每小时消费 Info Collector 扩展导出的待处理清单。
1. 读 ~/.info-collector/outbox/translate.json（扩展经 native host 写出）
2. 未报告过的文章 → 调 pi 用 translate-article skill 翻译
3. 结果写成 Completion Report 放进 ~/.info-collector/inbox/，扩展下次桥接时应用

每次运行把状态写进 state/translate-status.json（Dashboard 由此显示
上次/预计下次运行）。带 --manual 参数表示手动触发（Dashboard「立即处理」
或命令行），不计入定时锚点。

不再读写 Chrome 书签文件——书签与队列状态归扩展管，
见 info-collector 仓库 docs/adr/0001、0002、0004。
"""

import fcntl
import json
import os
import random
import string
import subprocess
import sys
import tempfile
import time

SPOOL = os.path.expanduser("~/.info-collector")
OUTBOX_FILE = os.path.join(SPOOL, "outbox", "translate.json")
INBOX_DIR = os.path.join(SPOOL, "inbox")
STATE_FILE = os.path.join(SPOOL, "state", "translate-reported.json")
STATUS_FILE = os.path.join(SPOOL, "state", "translate-status.json")
HISTORY_FILE = os.path.expanduser("~/.pi/scripts/action-history.json")
LOG_FILE = os.path.expanduser("~/.pi/logs/translate-bookmarks.log")
LOCK_FILE = os.path.expanduser("~/.pi/logs/translate-bookmarks.lock")
PI_TIMEOUT = 600

TRIGGER = "manual" if "--manual" in sys.argv else "scheduled"


def log(msg):
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{ts}] {msg}\n")
    print(f"[{ts}] {msg}", flush=True)


def append_history(entry):
    os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
    history = []
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, encoding="utf-8") as f:
                history = json.load(f)
        except (json.JSONDecodeError, OSError):
            history = []
    if not isinstance(history, list):
        history = []
    history.append(entry)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)


def acquire_lock():
    fd = os.open(LOCK_FILE, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except BlockingIOError:
        os.close(fd)
        return None


def atomic_write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def local_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def update_status(patch):
    status = {}
    if os.path.exists(STATUS_FILE):
        try:
            with open(STATUS_FILE, encoding="utf-8") as f:
                status = json.load(f)
        except (json.JSONDecodeError, OSError):
            status = {}
    status.update(patch)
    atomic_write_json(STATUS_FILE, status)


def load_state():
    """已报告 done 的文章：articleKey/url → 报告时间。"""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def write_report(results):
    rid = "translate-%s-%s" % (
        time.strftime("%Y%m%dT%H%M%S"),
        "".join(random.choices(string.ascii_lowercase + string.digits, k=4)),
    )
    atomic_write_json(os.path.join(INBOX_DIR, rid + ".json"), {
        "reportId": rid,
        "processingType": "translate",
        "results": results,
    })
    return rid


def pi_env():
    home = os.path.expanduser("~")
    path = os.environ.get("PATH", "")
    local_bin = os.path.join(home, ".local", "bin")
    if local_bin not in path.split(":"):
        path = f"{local_bin}:{path}" if path else local_bin
    return {**os.environ, "HOME": home, "PATH": path}


def run_pi(url):
    prompt = (
        "Translate this article using the translate-article skill. "
        "Save the translated Markdown file to "
        "/Users/aqua/D/Documents/ob/obsidian-sync-win-v1/大信息收集/dan koe/翻译/ "
        "and do not sync it to Notion.\n\n"
        f"URL: {url}\n\n"
        'When done, output "ALL_DONE" as the very last line.'
    )
    t_start = time.time()
    try:
        result = subprocess.run(
            ["pi", "-p", prompt],
            capture_output=True,
            text=True,
            timeout=PI_TIMEOUT,
            env=pi_env(),
        )
        elapsed = time.time() - t_start
        return result.returncode == 0 and "ALL_DONE" in (result.stdout or ""), result, elapsed, None
    except subprocess.TimeoutExpired as e:
        elapsed = time.time() - t_start
        return False, e, elapsed, f"pi timeout after {PI_TIMEOUT}s"


def main():
    log("=" * 50)
    log(f"开始巡检翻译队列（{TRIGGER}）")

    lock_fd = acquire_lock()
    if lock_fd is None:
        log("另有进程在运行，跳过")
        return

    last_run = {"startedAt": local_iso(), "trigger": TRIGGER, "outcome": "running"}
    patch = {"lastRun": last_run}
    if TRIGGER == "scheduled":
        patch["lastScheduledStartAt"] = last_run["startedAt"]
    update_status(patch)

    def finish(outcome, **extra):
        last_run.update({"outcome": outcome, "finishedAt": local_iso(), **extra})
        update_status({"lastRun": last_run})

    try:
        if not os.path.exists(OUTBOX_FILE):
            log("outbox 不存在——扩展尚未安装或未完成首次桥接，跳过")
            finish("no-outbox")
            return
        try:
            with open(OUTBOX_FILE, encoding="utf-8") as f:
                outbox = json.load(f)
        except json.JSONDecodeError as e:
            log(f"❌ outbox 解析失败: {e}")
            finish("error", error=str(e))
            return

        articles = outbox.get("articles") or []
        state = load_state()
        pending = [
            a for a in articles
            if a.get("url") and a.get("articleKey") not in state and a.get("url") not in state
        ]
        log(f"outbox 共 {len(articles)} 条，其中 {len(pending)} 条未报告")

        if not pending:
            log("✅ 无需翻译")
            finish("empty")
            return

        for a in pending:
            log(f"  🆕 待翻译: {a.get('title', '?')}")

        urls = [a["url"] for a in pending]

        # 认领报告：先把这批标为 processing，扩展桥接后显示「处理中」；
        # 若本进程崩溃（锁释放），扩展会在下次桥接时自动回退为 pending。
        claim_rid = write_report([{"url": u, "status": "processing"} for u in urls])
        log(f"📌 已认领 {len(urls)} 篇（{claim_rid}）")

        done = 0
        last_report = claim_rid
        attempts = []
        # ponytail: serial Pi calls keep per-article reports simple; parallelize only if queue volume needs it.
        for a in pending:
            url = a["url"]
            log(f"🚀 启动 pi -p: {a.get('title', '?')}")
            success, result, elapsed, error = run_pi(url)
            code = getattr(result, "returncode", None)
            log(f"⏱ pi 运行 {elapsed:.0f}s，退出码: {code if code is not None else 'timeout'}")

            stdout = getattr(result, "stdout", None)
            stderr = getattr(result, "stderr", None)
            if stdout:
                for line in stdout.strip().split("\n")[-10:]:
                    log(f"  | {line[:300]}")
            if stderr:
                log(f"pi 错误: {str(stderr)[:500]}")

            now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            if success:
                rid = write_report([{"url": url, "status": "done", "processedAt": now}])
                state[a.get("articleKey") or url] = now
                atomic_write_json(STATE_FILE, state)
                done += 1
                log(f"✅ 翻译成功，报告已写入 inbox: {rid}")
            else:
                rid = write_report([{
                    "url": url, "status": "failed", "processedAt": now,
                    "meta": {"error": error or f"pi exit {code}"},
                }])
                log(f"❌ 翻译失败，失败报告已写入 inbox: {rid}，下次重试")
            last_report = rid
            attempts.append({"title": a.get("title", "?"), "url": url, "success": success})

        finish("success" if done == len(pending) else "failed",
               count=done, reportId=last_report)

        append_history({
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "action": "translate",
            "trigger": TRIGGER,
            "status": "success" if done == len(pending) else "failed",
            "report_id": last_report,
            "attempted": attempts,
        })

    except Exception as e:
        finish("error", error=str(e))
        raise
    finally:
        os.close(lock_fd)


if __name__ == "__main__":
    main()
