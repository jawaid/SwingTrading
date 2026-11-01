"""The disclaimer shown in the app, read from DISCLAIMER.md so there is one copy."""

from __future__ import annotations

from swingtrading.config import PROJECT_ROOT

DISCLAIMER_PATH = PROJECT_ROOT / "DISCLAIMER.md"
SHORT = "For educational purposes only. Not financial advice. Trading involves substantial risk."
RISK_WARNING = (
    "**Stock trading carries a high level of risk and is not suitable for everyone.**\n\n"
    "You can lose some or all of your investment, and, if you trade on margin or sell "
    "short, more than your initial investment. This app provides signals and tracking for "
    "educational and decision-support purposes only; it does not give financial, "
    "investment, or tax advice, and nothing on this page or anywhere in this app is a "
    "recommendation to buy or sell any security. Consult a licensed financial advisor "
    "before trading, and understand that SwingTrading never places orders: any trade you "
    "place is placed manually, at your own broker, entirely at your own risk and "
    "discretion."
)
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
