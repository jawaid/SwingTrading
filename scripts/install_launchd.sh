#!/bin/sh
# Install, remove or check SwingTrading's three launchd jobs:
#   com.jawaid.swingtrading          the weekday evening run (scripts/run_daily.sh)
#   com.jawaid.swingtrading.morning  the weekday morning fill (scripts/run_morning.sh)
#   com.jawaid.swingtrading.app      the app at http://localhost:8502, kept running
#
# Usage: scripts/install_launchd.sh [install|uninstall|status]   (default: install)

set -eu
REPO="$(cd "$(dirname "$0")/.." && pwd)"
AGENTS="$HOME/Library/LaunchAgents"
DOMAIN="gui/$(id -u)"
JOBS="com.jawaid.swingtrading com.jawaid.swingtrading.morning com.jawaid.swingtrading.app"

# An Eastern time (HH:MM) in this Mac's time zone (the same all year: both shift for DST).
local_time() {
    epoch=$(TZ=America/New_York date -j -f "%Y-%m-%d %H:%M" "$(date +%F) $1" +%s)
    date -r "$epoch" "+%H %M"
}

# Point a job's weekday times at an Eastern time.
set_time() {
    set -- "$1" $(local_time "$2")
    for day in 0 1 2 3 4; do
        plutil -replace "StartCalendarInterval.$day.Hour" -integer "$(expr "$2" + 0)" "$1"
        plutil -replace "StartCalendarInterval.$day.Minute" -integer "$(expr "$3" + 0)" "$1"
    done
}

unload() {
    launchctl bootout "$DOMAIN/$1" 2>/dev/null || true
}

install() {
    mkdir -p "$AGENTS" "$REPO/data/logs"
    for job in $JOBS; do
        unload "$job"
        cp "$REPO/launchd/$job.plist" "$AGENTS/$job.plist"
    done
    set_time "$AGENTS/com.jawaid.swingtrading.plist" 17:30
    set_time "$AGENTS/com.jawaid.swingtrading.morning.plist" 10:00
    for job in $JOBS; do
        launchctl bootstrap "$DOMAIN" "$AGENTS/$job.plist"
    done
    set -- $(local_time 10:00) $(local_time 17:30)
    echo "Installed. Monday-Friday, local time:"
    echo "  Morning fill: $1:$2 (10:00 AM Eastern)"
    echo "  Evening run:  $3:$4 (5:30 PM Eastern)"
    echo "App: http://localhost:8502"
}

uninstall() {
    for job in $JOBS; do
        unload "$job"
        rm -f "$AGENTS/$job.plist"
    done
    echo "Removed all jobs. The app is stopped and the scheduled runs will not happen."
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
