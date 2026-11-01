from datetime import date

import streamlit as st

from swingtrading.ui import charts, data, disclaimer

settings, conn = data.open_db()
st.title("Past picks")
df = data.past_picks(conn)

if df.empty:
    st.info("No closed picks yet. A pick closes at the open after a close below its stop.")
    disclaimer.stop()

first, last = date.fromisoformat(df["exit_date"].min()), date.fromisoformat(df["exit_date"].max())
c1, c2 = st.columns([2, 1])
picked = c1.date_input("Exit date range", (first, last), min_value=first, max_value=last)
outcome = c2.radio("Show", ["All", "Winners", "Losers"], horizontal=True)
lo, hi = (picked[0], picked[-1]) if isinstance(picked, tuple) and picked else (first, last)
mask = (df["exit_date"] >= lo.isoformat()) & (df["exit_date"] <= hi.isoformat())
if outcome == "Winners":
    mask &= df["return_pct"] > 0
elif outcome == "Losers":
    mask &= df["return_pct"] <= 0
shown = df[mask].reset_index(drop=True)

if shown.empty:
    st.info("No closed picks match these filters.")
    disclaimer.stop()

wins = (shown["return_pct"] > 0).mean() * 100
c1, c2, c3 = st.columns(3)
c1.metric("Trades", len(shown))
c2.metric("Winners", f"{wins:.0f}%")
c3.metric("Average return", f"{shown['return_pct'].mean():+.1f}%")

event = st.dataframe(
    shown.drop(columns=["id"]),
    hide_index=True,
    use_container_width=True,
    on_select="rerun",
    selection_mode="single-row",
    column_config={
        "ticker": "Ticker",
        "signal_date": "Signal",
        "entry_date": "Entered",
        "entry_price": st.column_config.NumberColumn("Entry", format="%.2f"),
        "exit_date": "Exited",
        "exit_price": st.column_config.NumberColumn("Exit", format="%.2f"),
        "return_pct": st.column_config.NumberColumn("Return", format="%+.1f%%"),
        "days_held": st.column_config.NumberColumn("Days held", format="%d"),
        "sctr_at_signal": st.column_config.NumberColumn("SCTR at signal", format="%.1f"),
        "setup_score": st.column_config.NumberColumn("Setup score", format="%.0f"),
    },
)
rows = event.selection.rows if event and event.selection else []
st.caption("Select a row to see its chart.")
pick = next(p for p in data.picks(conn) if p["id"] == int(shown.iloc[rows[0] if rows else 0]["id"]))
bars, history = data.pick_chart_data(conn, pick)
st.plotly_chart(charts.pick_chart(bars, pick, history), use_container_width=True)
