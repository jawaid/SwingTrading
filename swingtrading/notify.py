"""macOS notification when a run finishes. Never raises: a failed notification is logged."""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys

log = logging.getLogger(__name__)


def _quote(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def send(message: str, title: str = "SwingTrading") -> bool:
    """Show a notification via osascript. Returns False when it couldn't be shown."""
    if sys.platform != "darwin" or shutil.which("osascript") is None:
        log.info("Notification skipped (not macOS): %s", message)
        return False
    script = f"display notification {_quote(message)} with title {_quote(title)}"
    try:
        subprocess.run(["osascript", "-e", script], check=True, capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("Notification failed: %s", exc)
        return False
    return True
