"""The disclaimer shown in the app, read from DISCLAIMER.md so there is one copy."""

from __future__ import annotations

from swingtrading.config import PROJECT_ROOT

DISCLAIMER_PATH = PROJECT_ROOT / "DISCLAIMER.md"
SHORT = "For educational purposes only. Not financial advice. Trading involves substantial risk."


def text() -> str:
    """DISCLAIMER.md without its heading, as markdown."""
    lines = DISCLAIMER_PATH.read_text().splitlines()
    return "\n".join(line for line in lines if not line.startswith("# ")).strip()
