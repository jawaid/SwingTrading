"""`swing` command-line entry points."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from swingtrading import db
from swingtrading.config import load_settings

# Commands that later build phases implement. Until then they say so and exit non-zero.
_PENDING = {
    "refresh-universe": "Phase 2",
    "backfill": "Phase 3",
    "run": "Phase 7",
    "app": "Phase 8",
}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="swing", description="End-of-day swing trading signals")
    parser.add_argument("--settings", help="path to settings.yaml (default: config/settings.yaml)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("refresh-universe", help="rebuild the symbol list and metadata cache")
    sub.add_parser("backfill", help="download price history for the universe")
    run = sub.add_parser("run", help="run the daily pipeline")
    run.add_argument("--date", help="session date YYYY-MM-DD (default: last completed session)")
    run.add_argument("--dry-run", action="store_true", help="compute and print, write nothing")
    sub.add_parser("app", help="launch the Streamlit app")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    settings = load_settings(args.settings)
    db.connect(settings.resolve(settings.paths.db_path)).close()

    phase = _PENDING.get(args.command)
    if phase:
        print(f"`swing {args.command}` is not built yet ({phase} of BUILD_PLAN.md).")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
