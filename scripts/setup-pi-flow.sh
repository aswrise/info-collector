#!/bin/bash
# 注册基于 pi CLI 的翻译流（仓库作者的个人流程，普通用户请用 setup.sh 的内置引擎）。
# 前提：已装 pi CLI + translate-article skill + com.pi.translate-bookmarks launchd 任务。
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
SPOOL="$HOME/.info-collector"
FLOW_TARGET="$HOME/.pi/scripts/translate-bookmarks.py"
OLD_DB="$HOME/.pi/scripts/translated-urls.json"
MARKER="$SPOOL/state/.migrated-translated-urls"

mkdir -p "$SPOOL/state" "$SPOOL/inbox"

echo "== 1/3 安装 pi 翻译流脚本（备份原版）"
chmod +x "$REPO/flows/translate-bookmarks.py"
if [[ -f "$FLOW_TARGET" ]]; then
  cp "$FLOW_TARGET" "$FLOW_TARGET.bak-$(date +%Y%m%d%H%M%S)"
fi
mkdir -p "$(dirname "$FLOW_TARGET")"
cp "$REPO/flows/translate-bookmarks.py" "$FLOW_TARGET"
chmod +x "$FLOW_TARGET"

echo "== 2/3 注册 flows.json"
SPOOL="$SPOOL" python3 <<'PY'
import json, os

spool = os.environ["SPOOL"]
path = os.path.join(spool, "flows.json")
flows = {}
if os.path.exists(path):
    try:
        flows = json.load(open(path))
    except Exception:
        flows = {}
home = os.path.expanduser("~")
sandbox = f'(version 1)(allow default)(deny file-read* file-write* (subpath "{home}/Library"))'
flows["translate"] = {
    "command": [
        "/usr/bin/sandbox-exec", "-p", sandbox,
        "/usr/bin/python3", f"{home}/.pi/scripts/translate-bookmarks.py", "--manual",
    ],
    "lockFile": f"{home}/.pi/logs/translate-bookmarks.lock",
    "intervalSeconds": 3600,
    "label": "com.pi.translate-bookmarks",
}
with open(path, "w") as f:
    json.dump(flows, f, ensure_ascii=False, indent=1)
print("   translate → pi 流已注册")
PY

echo "== 3/3 迁移历史已翻译记录（一次性）"
if [[ -f "$OLD_DB" && ! -f "$MARKER" ]]; then
  SPOOL="$SPOOL" OLD_DB="$OLD_DB" python3 <<'PY'
import json, os, time

spool = os.environ["SPOOL"]
with open(os.environ["OLD_DB"]) as f:
    urls = json.load(f)
now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
rid = "migrate-translated-" + time.strftime("%Y%m%d")
with open(os.path.join(spool, "inbox", rid + ".json"), "w") as f:
    json.dump({"reportId": rid, "processingType": "translate",
               "results": [{"url": u, "status": "done", "processedAt": now,
                            "meta": {"migrated": True}} for u in urls]}, f,
              ensure_ascii=False, indent=1)
state_path = os.path.join(spool, "state", "translate-reported.json")
state = {}
if os.path.exists(state_path):
    with open(state_path) as f:
        state = json.load(f)
for u in urls:
    state.setdefault(u, now)
with open(state_path, "w") as f:
    json.dump(state, f, ensure_ascii=False, indent=1)
print(f"   迁移 {len(urls)} 条已翻译 URL → 报告 {rid}")
PY
  touch "$MARKER"
else
  echo "   已迁移过或无历史库，跳过"
fi

echo "完成。launchd 任务 com.pi.translate-bookmarks 保持不变。"
