#!/bin/sh
# Install, remove or check SwingTrading's two launchd jobs:
#   com.jawaid.swingtrading      the weekday evening run (scripts/run_daily.sh)
#   com.jawaid.swingtrading.app  the app at http://localhost:8502, kept running
#
# Usage: scripts/install_launchd.sh [install|uninstall|status]   (default: install)

set -eu
REPO="$(cd "$(dirname "$0")/.." && pwd)"
AGENTS="$HOME/Library/LaunchAgents"
DOMAIN="gui/$(id -u)"
JOBS="com.jawaid.swingtrading com.jawaid.swingtrading.app"

# 5:30 PM US Eastern in this Mac's time zone (the same all year: both shift for DST).
local_run_time() {
    epoch=$(TZ=America/New_York date -j -f "%Y-%m-%d %H:%M" "$(date +%F) 17:30" +%s)
    date -r "$epoch" "+%H %M"
}

unload() {
    launchctl bootout "$DOMAIN/$1" 2>/dev/null || true
}

install() {
    mkdir -p "$AGENTS" "$REPO/data/logs"
    set -- $(local_run_time)
    hour=$(expr "$1" + 0) minute=$(expr "$2" + 0)
    for job in $JOBS; do
        unload "$job"
        cp "$REPO/launchd/$job.plist" "$AGENTS/$job.plist"
    done
    for day in 0 1 2 3 4; do
        plutil -replace "StartCalendarInterval.$day.Hour" -integer "$hour" \
            "$AGENTS/com.jawaid.swingtrading.plist"
        plutil -replace "StartCalendarInterval.$day.Minute" -integer "$minute" \
            "$AGENTS/com.jawaid.swingtrading.plist"
    done
    for job in $JOBS; do
        launchctl bootstrap "$DOMAIN" "$AGENTS/$job.plist"
    done
    printf "Installed. Daily run: Monday-Friday at %02d:%02d local time (5:30 PM Eastern).\n" \
        "$hour" "$minute"
    echo "App: http://localhost:8502"
}

uninstall() {
    for job in $JOBS; do
        unload "$job"
        rm -f "$AGENTS/$job.plist"
    done
    echo "Removed both jobs. The app is stopped and the daily run will not happen."
}

status() {
    for job in $JOBS; do
        if launchctl print "$DOMAIN/$job" >/dev/null 2>&1; then
            info=$(launchctl print "$DOMAIN/$job" | grep -E "^\s*(state|last exit code) =" \
                | sed 's/^[[:space:]]*//' | paste -sd ',' - | sed 's/,/, /g')
            echo "$job: loaded ($info)"
        else
            echo "$job: not installed"
        fi
    done
}

case "${1:-install}" in
    install) install ;;
    uninstall) uninstall ;;
    status) status ;;
    *) echo "usage: $0 [install|uninstall|status]" >&2; exit 2 ;;
esac
