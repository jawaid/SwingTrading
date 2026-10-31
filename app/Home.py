"""SwingTrading app entry point: `uv run swing app` (or `streamlit run app/Home.py`)."""

import streamlit as st

st.set_page_config(page_title="SwingTrading", page_icon=":chart_with_upwards_trend:",
                   layout="wide")  # fmt: skip

pages = [
    st.Page("pages/1_Current_Picks.py", title="Current picks", icon=":material/trending_up:",
            default=True),
    st.Page("pages/2_Past_Picks.py", title="Past picks", icon=":material/history:"),
    st.Page("pages/3_Performance.py", title="Performance", icon=":material/monitoring:"),
    st.Page("pages/4_About_and_Settings.py", title="About and settings",
            icon=":material/settings:"),
]  # fmt: skip
st.sidebar.caption("End-of-day signals and tracking. Not financial advice.")
st.navigation(pages).run()
