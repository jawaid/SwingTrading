"""The disclaimer shown in the app, read from DISCLAIMER.md so there is one copy."""

from __future__ import annotations

from swingtrading.config import PROJECT_ROOT

DISCLAIMER_PATH = PROJECT_ROOT / "DISCLAIMER.md"
SHORT = "For educational purposes only. Not financial advice. Trading involves substantial risk."
FOOTER = (
    "For educational purposes only. Not financial advice. May contain errors. Trading "
    "involves substantial risk of loss. Use at your own risk and consult a licensed "
    "financial advisor before investing."
)


def text() -> str:
    """DISCLAIMER.md without its heading, as markdown."""
    lines = DISCLAIMER_PATH.read_text().splitlines()
    return "\n".join(line for line in lines if not line.startswith("# ")).strip()


def footer() -> None:
    """The one-line disclaimer under every screen."""
    import streamlit as st

    st.divider()
    st.caption(FOOTER)


def stop() -> None:
    """End a screen early (an empty state) with the footer still shown: Streamlit drops
    anything drawn after st.stop(), so Home.py's footer can't be drawn for it."""
    import streamlit as st

    footer()
    st.stop()
