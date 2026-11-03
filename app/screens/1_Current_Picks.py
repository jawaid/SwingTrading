import streamlit as st

from swingtrading.ui import data, disclaimer, pick_dialog, tables

settings, conn = data.open_db()
st.title("Current picks")
df = data.current_picks(conn)
status = data.data_status(conn)

if df.empty:
    st.info(
        "No picks yet. Run the pipeline after today's close: "
        "`uv run swing run`, or **Run now** on the About and settings screen."
    )
    if status["last_price_date"]:
        st.caption(f"Prices are stored through {status['last_price_date']}.")
    disclaimer.stop()

counts = df["status"].value_counts()
c1, c2, c3 = st.columns(3)
c1.metric("Open", int(counts.get("open", 0)))
c2.metric("Pending entry", int(counts.get("signal", 0)), help="Enters at the next open")
c3.metric(
    "Exit pending",
    int(counts.get("exit_pending", 0)),
    help="Closed below the stop; exits at the next open",
)

STATUS_TEXT = {"signal": "Pending entry", "open": "Open", "exit_pending": "Exit pending"}
view = df.assign(status=df["status"].map(STATUS_TEXT)).drop(columns=["id"])
event = st.dataframe(
    tables.red_negatives(view, ["distance_to_stop_pct", "open_pl_pct"]),
    hide_index=True,
    use_container_width=True,
    on_select="rerun",
    selection_mode="single-row",
    column_config={
        "ticker": "Ticker",
        "status": "Status",
        "signal_date": "Signal",
        "entry_date": "Entered",
        "entry_price": st.column_config.NumberColumn("Entry", format="%.2f"),
        "current_price": st.column_config.NumberColumn("Price", format="%.2f"),
        "current_stop": st.column_config.NumberColumn("Stop", format="%.2f"),
        "support": st.column_config.NumberColumn(
            "Support", format="%.2f", help="The pullback low the stock bounced from"
        ),
        "distance_to_stop_pct": st.column_config.NumberColumn("To stop", format="%.1f%%"),
        "open_pl_pct": st.column_config.NumberColumn("Open P/L", format="%+.1f%%"),
        "sctr": st.column_config.NumberColumn("SCTR", format="%.1f"),
        "days_held": st.column_config.NumberColumn("Days held", format="%d"),
        "setup_score": st.column_config.NumberColumn("Setup score", format="%.0f"),
    },
)
rows = event.selection.rows if event and event.selection else []
if rows:
    pick = next(p for p in data.picks(conn) if p["id"] == int(df.iloc[rows[0]]["id"]))
    if st.button(f"📈 Show chart: {pick['ticker']}", type="primary"):
        pick_dialog.show(settings, pick)
else:
    st.button("📈 Show chart", disabled=True)
    st.caption("Tick a row, then click **Show chart** to open its chart and why it was picked.")
