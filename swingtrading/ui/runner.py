"""Run the pipeline in a background process for the app's "Run now" button.

The process writes its output to data/logs/app_run.log and its pid to
data/logs/app_run.json, so any browser tab (or a reload) can see that a run is going.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from swingtrading.config import Settings, settings_path

STATE_FILE = "app_run.json"
LOG_FILE = "app_run.log"


def _paths(settings: Settings) -> tuple[Path, Path]:
    log_dir = settings.resolve(settings.paths.log_dir)
    return log_dir / STATE_FILE, log_dir / LOG_FILE


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # A finished child stays a zombie until reaped; reap it so it reads as done.
    try:
        done, _ = os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        return True  # not our child (started by another app process): trust kill(0)
    return done == 0


def status(settings: Settings) -> dict | None:
    """The current or last app-started run: pid, started, running, dry_run."""
    state_file, _ = _paths(settings)
    if not state_file.exists():
        return None
    state = json.loads(state_file.read_text())
    state["running"] = _alive(state["pid"])
    return state


def start(settings: Settings, dry_run: bool = False) -> dict:
    """Start `swing run` in the background unless one is already running."""
    current = status(settings)
    if current and current["running"]:
        return current
    state_file, log_file = _paths(settings)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "swingtrading.cli", "--settings", str(settings_path()), "run"]
    if dry_run:
        cmd.append("--dry-run")
    with log_file.open("w") as out:
        proc = subprocess.Popen(
            cmd, stdout=out, stderr=subprocess.STDOUT, start_new_session=True,
            cwd=settings.resolve(Path(".")),
        )  # fmt: skip
    state = {"pid": proc.pid, "started": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
             "dry_run": dry_run}  # fmt: skip
    state_file.write_text(json.dumps(state))
    return {**state, "running": True}


def log_tail(settings: Settings, lines: int = 40) -> str:
    _, log_file = _paths(settings)
    if not log_file.exists():
        return ""
    text = log_file.read_text(errors="replace").splitlines()
    noise = ("Warning", "warnings.warn", "dt.weekday")
    return "\n".join([t for t in text if not any(n in t for n in noise)][-lines:])
