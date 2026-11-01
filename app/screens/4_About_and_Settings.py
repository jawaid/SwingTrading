from pathlib import Path

import streamlit as st
from pydantic import ValidationError

from swingtrading.config import load_settings, save_settings, settings_path
from swingtrading.ui import charts, data, runner
from swingtrading.ui.settings_form import EDITABLE_SECTIONS, apply, fields, label

settings, conn = data.open_db()
st.title("About and settings")
st.warning("SwingTrading is a signals and tracking tool. It never places orders, and nothing "
           "here is financial advice.", icon=":material/info:")  # fmt: skip

# ---- run status and Run now --------------------------------------------------------------
st.header("Daily run")
status = data.data_status(conn)
recent = data.runs(conn, limit=10)
c1, c2, c3, c4 = st.columns(4)
c1.metric("Universe", f"{status['universe']:,}")
c2.metric("Prices through", status["last_price_date"] or "-")
c3.metric("Last ranking", status["last_ranking_date"] or "-")
c4.metric("Last run", recent.iloc[0]["session_date"] if len(recent) else "never")
if len(recent):
    last = recent.iloc[0]
    text = (f"Last run for {last['session_date']}: {last['status']}, "
            f"{last['new_picks'] or 0} new picks, {last['stops_raised'] or 0} stops raised, "
            f"{last['exits'] or 0} exits, {last['errors'] or 0} errors.")  # fmt: skip
    (st.error if last["status"] == "failed" else st.caption)(
        text + (f" Error: {last['error']}" if last["error"] else "")
    )
else:
    st.caption("No runs yet.")


def _run_panel() -> None:
    state = runner.status(settings)
    running = bool(state and state["running"])
    c1, c2, _ = st.columns([1, 1, 3])
    if c1.button("Run now", type="primary", disabled=running):
        runner.start(settings)
        st.rerun()
    if c2.button("Dry run", disabled=running, help="Compute everything, write nothing"):
        runner.start(settings, dry_run=True)
        st.rerun()
    if state:
        kind = "Dry run" if state.get("dry_run") else "Run"
        if running:
            st.info(f"{kind} started {state['started']} is in progress. It downloads the "
                    "latest prices first, which takes a few minutes.")  # fmt: skip
        else:
            st.caption(f"{kind} started {state['started']} has finished.")
        tail = runner.log_tail(settings)
        if tail:
            st.code(tail, language=None)
    if running:
        st.session_state["was_running"] = True
    elif st.session_state.pop("was_running", False):
        st.rerun()  # refresh the counts above now that the run is done


state = runner.status(settings)
st.fragment(run_every=2 if state and state["running"] else None)(_run_panel)()
if len(recent) > 1:
    with st.expander("Recent runs"):
        st.dataframe(recent.drop(columns=["started_at"]), hide_index=True)

# ---- methodology -------------------------------------------------------------------------
st.header("How it works")
st.subheader("1. Universe")
st.markdown(
    "Stocks and ETFs listed on NYSE, NASDAQ, NYSE American and NYSE Arca: stocks with a "
    "market cap of at least \\$2B, ETFs with at least \\$2B in assets, at least \\$20M a day in "
    "average dollar volume over 50 days, and at least 250 sessions of history. Preferreds, "
    "warrants, units, rights, ADRs, REITs, SPACs and leveraged or inverse ETFs are left out."
)
st.subheader("2. Rank: StockCharts Technical Rank (SCTR)")
st.markdown(
    "Every name gets a raw score from six measures: distance from the 200-day EMA (30%) and "
    "125-day rate of change (30%) for the long term, distance from the 50-day EMA (15%) and "
    "20-day rate of change (15%) for the mid term, and the 3-day slope of the PPO histogram "
    "(5%) and 14-day RSI (5%) for the short term. The SCTR is that score's percentile rank "
    "across the whole universe, from 0 (weakest) to 99.99 (strongest)."
)
cutoff = 100 - settings.ranking.shortlist_pct
st.html(
    charts.sctr_scale_html(cutoff * 0.9999, f"Shortlist: top {settings.ranking.shortlist_pct:g}%")
)
st.subheader("3. Setup")
st.markdown(
    "Only the shortlist is scanned. The current detector looks for an orderly pullback: a "
    "3-12% drop (1-4 ATRs) from a swing high in the last 20 sessions, lasting at most 10 "
    "sessions, on lighter volume, without closing below the 50-day EMA. It fires on a pivot "
    "day that closes above the prior day's high, in the upper half of its range, on at "
    "least average volume. This detector is a placeholder for your own formula."
)
st.subheader("4. Entry and the trailing stop")
m = settings.stops.atr_mult
st.markdown(
    f"A pick enters at the next session's open. Its first stop is the entry price minus "
    f"{m:g} x ATR(14). After every close the stop moves up to the highest close since entry "
    f"minus {m:g} x the current ATR, and it never moves down. When a session closes below "
    "the stop, the pick exits at the next session's open. Returns are percent per pick."
)

# ---- settings ----------------------------------------------------------------------------
st.header("Settings")
path = settings_path()
st.caption(f"Saved to `{Path(path).name}` in the config folder. Changes apply to the next run.")
current = load_settings()
with st.form("settings"):
    edits = {}
    tabs = st.tabs([label(s) for s in EDITABLE_SECTIONS])
    for tab, section in zip(tabs, EDITABLE_SECTIONS, strict=True):
        with tab:
            cols = st.columns(3)
            for i, (key, value) in enumerate(fields(getattr(current, section), (section,))):
                name = label(key[-1]) if len(key) == 2 else f"{label(key[-2])}: {label(key[-1])}"
                col = cols[i % 3]
                wid = ".".join(key)
                if isinstance(value, bool):
                    edits[key] = col.checkbox(name, value, key=wid)
                elif isinstance(value, int):
                    edits[key] = int(col.number_input(name, value=value, step=1, key=wid))
                elif isinstance(value, float):
                    edits[key] = float(col.number_input(name, value=value, key=wid,
                                                        format="%g"))  # fmt: skip
                elif isinstance(value, list):
                    text = col.text_input(name, ", ".join(map(str, value)), key=wid)
                    edits[key] = [v.strip() for v in text.split(",") if v.strip()]
                else:
                    edits[key] = col.text_input(name, str(value), key=wid)
    saved = st.form_submit_button("Save settings", type="primary")
if saved:
    try:
        new = apply(current, edits)
    except ValidationError as exc:
        for err in exc.errors():
            where = " > ".join(label(str(p)) for p in err["loc"]) or "Settings"
            st.error(f"{where}: {err['msg']}")
    else:
        save_settings(new, path)
        st.success("Settings saved.")
