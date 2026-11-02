import math

import streamlit as st

from swingtrading.ui import charts, data, disclaimer

settings, conn = data.open_db()
st.title("Performance")
perf = data.performance_data(conn)

if len(perf.equity) < 2:  # nothing entered, or entered today and no close since
    st.info(
        "No picks have been held through a close yet, so there is nothing to measure. A "
        "signal enters at the next session's open; results start after that session's "
        "evening run."
    )
    disclaimer.stop()


def pct(x: float | None) -> str:
    return "-" if x is None else f"{x * 100:+.1f}%"


s = perf.stats
st.subheader("Closed picks")
if s is None:
    st.caption("No picks have closed yet. Trade stats appear once the first pick stops out.")
else:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Closed picks", s.trades)
    c2.metric("Win rate", f"{s.win_rate * 100:.0f}%", help=f"{s.wins} wins, {s.losses} losses")
    c3.metric("Average win", pct(s.avg_win))
    c4.metric("Average loss", pct(s.avg_loss))
    c1, c2, c3, _ = st.columns(4)
    pf = s.profit_factor
    c1.metric(
        "Profit factor",
        "-" if pf is None else ("no losses" if math.isinf(pf) else f"{pf:.2f}"),
        help="Total gains of winners divided by total losses of losers.",
    )
    c2.metric("Expectancy", pct(s.expectancy), help="Average return per closed pick.")
    c3.metric("Average days held", f"{s.avg_days_held:.1f}")

st.subheader("Equity curve")
total = perf.equity.iloc[-1] - 1
spy_total = perf.benchmark.iloc[-1] - 1 if len(perf.benchmark) else None
c1, c2, c3, _ = st.columns(4)
c1.metric("Picks", pct(total))
c2.metric("SPY", pct(spy_total))
c3.metric("Max drawdown", f"{perf.max_drawdown * 100:.1f}%",
          help="Largest fall from a peak in the picks' equity curve.")  # fmt: skip
st.plotly_chart(charts.equity_chart(perf.equity, perf.benchmark), use_container_width=True)
st.caption(
    "Each session the portfolio holds every open pick in equal weight, including picks "
    "still open, and compounds the daily average. Days with nothing held earn nothing. "
    "SPY covers the same sessions."
)

st.subheader("Monthly returns")
st.dataframe(
    perf.monthly,
    hide_index=True,
    column_config={
        "month": st.column_config.TextColumn("Month"),
        "picks_pct": st.column_config.NumberColumn("Picks", format="%+.1f%%"),
        "spy_pct": st.column_config.NumberColumn("SPY", format="%+.1f%%"),
    },
)
