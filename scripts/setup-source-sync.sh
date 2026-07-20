#!/bin/bash
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
ROOT="$HOME/.info-collector/source-sync"
APP="$ROOT/app"
LABELS=(com.info-collector.source-sync-cycle)
LEGACY_LABELS=(com.info-collector.source-sync-youtube com.info-collector.source-sync-x com.info-collector.source-sync-worker)
ALL_LABELS=("${LABELS[@]}" "${LEGACY_LABELS[@]}")

if [[ "${1:-}" == "--uninstall" ]]; then
  for label in "${ALL_LABELS[@]}"; do
    plist="$HOME/Library/LaunchAgents/$label.plist"
    launchctl unload "$plist" 2>/dev/null || true
    rm -f "$plist"
  done
  rm -rf "$APP"
  rm -f "$HOME/.opencli/clis/twitter/likes-page.js" "$HOME/.opencli/clis/twitter/list-page.js" "$HOME/.opencli/clis/twitter/page-shared.js"
  rm -rf "$HOME/.claude/skills/tweet-organizer" "$HOME/.claude/skills/tweet-value-evaluator"
  echo "Source Sync 已卸载；数据库、日志和产物保留在 $ROOT"
  exit 0
fi

mkdir -p "$ROOT/logs" "$ROOT/locks" "$APP" "$HOME/Library/LaunchAgents" "$HOME/.opencli/clis/twitter" "$HOME/.claude/skills"
PYTHON="$(command -v python3)"
"$PYTHON" -c 'import sys; assert sys.version_info >= (3, 10), "Source Sync requires Python 3.10+"'
rm -rf "$APP/source_sync" "$APP/processors" "$APP/templates"
cp -R "$REPO/source_sync" "$REPO/processors" "$APP/"
cp -R "$REPO/templates" "$APP/templates"

for skill in tweet-organizer tweet-value-evaluator; do
  rm -rf "$HOME/.claude/skills/$skill"
  cp -R "$REPO/skills/$skill" "$HOME/.claude/skills/$skill"
done
cp "$REPO/opencli/twitter/page-shared.js" "$REPO/opencli/twitter/likes-page.js" "$REPO/opencli/twitter/list-page.js" "$HOME/.opencli/clis/twitter/"

LAUNCHD_PATH="/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin:/opt/homebrew/bin:$HOME/.local/bin"
for exe in pi opencli yt-dlp defuddle; do
  if command -v "$exe" >/dev/null 2>&1; then
    dir="$(dirname "$(command -v "$exe")")"
    case ":$LAUNCHD_PATH:" in *":$dir:"*) ;; *) LAUNCHD_PATH="$dir:$LAUNCHD_PATH" ;; esac
  fi
done

for label in "${LEGACY_LABELS[@]}"; do
  plist="$HOME/Library/LaunchAgents/$label.plist"
  launchctl unload "$plist" 2>/dev/null || true
  rm -f "$plist"
done

(
  cd "$APP"
  "$PYTHON" -m source_sync maintenance
)

for label in "${LABELS[@]}"; do
  source="$REPO/templates/$label.plist"
  target="$HOME/Library/LaunchAgents/$label.plist"
  SOURCE="$source" TARGET="$target" APP="$APP" PYTHON="$PYTHON" LAUNCHD_PATH="$LAUNCHD_PATH" "$PYTHON" - <<'PY'
import os
from pathlib import Path

text = Path(os.environ["SOURCE"]).read_text(encoding="utf-8")
text = text.replace("__HOME__", str(Path.home()))
text = text.replace("__APP__", os.environ["APP"])
text = text.replace("__PYTHON__", os.environ["PYTHON"])
text = text.replace("__PATH__", os.environ["LAUNCHD_PATH"])
Path(os.environ["TARGET"]).write_text(text, encoding="utf-8")
PY
  plutil -lint "$target" >/dev/null
  launchctl unload "$target" 2>/dev/null || true
  launchctl load "$target"
done

cat <<EOF
Source Sync 已安装。

打开 Dashboard：
  cd "$APP" && "$PYTHON" -m source_sync dashboard
  然后访问 http://127.0.0.1:8787

卸载（保留数据库与产物）：
  bash "$REPO/scripts/setup-source-sync.sh" --uninstall
EOF
