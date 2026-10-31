import streamlit as st

from swingtrading.ui import data

settings, conn = data.open_db()
st.title("Performance")
df = data.past_picks(conn)

if df.empty:
    st.info(
        "No closed picks yet, so there is nothing to measure. Performance fills in as picks "
        "stop out and close."
    )
    st.stop()

wins, losses = df[df["return_pct"] > 0], df[df["return_pct"] <= 0]
c1, c2, c3, c4 = st.columns(4)
c1.metric("Closed picks", len(df))
c2.metric("Win rate", f"{len(wins) / len(df) * 100:.0f}%")
c3.metric("Average return", f"{df['return_pct'].mean():+.1f}%")
c4.metric("Average days held", f"{df['days_held'].mean():.1f}")
c1, c2 = st.columns(2)
c1.metric("Average win", f"{wins['return_pct'].mean():+.1f}%" if len(wins) else "-")
c2.metric("Average loss", f"{losses['return_pct'].mean():+.1f}%" if len(losses) else "-")
st.caption("Profit factor, drawdown, the equity curve against SPY and monthly returns are "
           "being added next.")  # fmt: skip
