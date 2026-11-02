#!/bin/sh
# The weekday morning fill, started by launchd (launchd/com.jawaid.swingtrading.morning.plist).
# Runs `swing fill-open` from the repo and appends its output to data/logs/daily_YYYY-MM-DD.log.
# Safe to run by hand at any time after the open: a pick already filled is not filled again.

set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

cd "$REPO" || exit 1
mkdir -p data/logs
LOG="data/logs/daily_$(date +%F).log"

echo "=== $(date '+%F %T %Z') swing fill-open ===" >> "$LOG"
uv run swing fill-open >> "$LOG" 2>&1
status=$?
echo "=== finished with exit code $status ===" >> "$LOG"
exit $status
