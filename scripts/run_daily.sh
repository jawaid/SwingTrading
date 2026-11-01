#!/bin/sh
# The weekday evening run, started by launchd (launchd/com.jawaid.swingtrading.plist).
# Runs `swing run` from the repo and appends its output to data/logs/daily_YYYY-MM-DD.log.
# Safe to run by hand at any time: a session already processed is not processed again.

set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

cd "$REPO" || exit 1
mkdir -p data/logs
LOG="data/logs/daily_$(date +%F).log"

echo "=== $(date '+%F %T %Z') swing run ===" >> "$LOG"
uv run swing run "$@" >> "$LOG" 2>&1
status=$?
echo "=== finished with exit code $status ===" >> "$LOG"
exit $status
