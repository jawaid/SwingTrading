"""The pick pop-up: chart with Copy and Close buttons, and why the pick was made."""

from __future__ import annotations

import sqlite3

import streamlit as st
import streamlit.components.v1 as components

from swingtrading.config import Settings
from swingtrading.ui import charts, data

CHART_HEIGHT = 510  # the figure's 460 plus the button bar


@st.dialog("Pick chart", width="large")
def show(settings: Settings, pick: dict) -> None:
    # Buttons in the pop-up rerun only the pop-up, on another thread, so it opens its own
    # connection rather than borrowing the page's.
    _, conn = data.open_db(settings)
    try:
        _body(conn, settings, pick)
    finally:
        conn.close()


def _body(conn: sqlite3.Connection, settings: Settings, pick: dict) -> None:
    bars, history = data.pick_chart_data(
        conn, pick, ema_periods=(settings.setup.trend_ema_period, settings.ranking.long_ema_period)
    )
    fig = charts.pick_chart(
        bars, pick, history, data.company(conn, pick["ticker"]),
        data.support_level(pick, bars), data.support_now(pick, bars),
    )  # fmt: skip
    name = f"{pick['ticker']}_{pick['signal_date']}"
    components.html(charts.chart_html(fig, name), height=CHART_HEIGHT)
    reasons = data.why_selected(pick, settings)
    if reasons:
        st.markdown("#### Why it was picked")
        st.markdown("\n".join(f"- {r}" for r in reasons))
    details = data.setup_details(pick)
    if details:
        with st.expander("Raw setup numbers"):
            st.json(details)
    if st.button("Close", use_container_width=True):
        st.rerun()
