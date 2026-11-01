"""SwingTrading app entry point: `uv run swing app` (or `streamlit run app/Home.py`)."""

import streamlit as st

from swingtrading.ui import disclaimer

st.set_page_config(page_title="SwingTrading", page_icon=":chart_with_upwards_trend:",
                   layout="wide")  # fmt: skip

pages = [
    st.Page("screens/1_Current_Picks.py", title="Current picks", icon=":material/trending_up:",
            default=True),
    st.Page("screens/2_Past_Picks.py", title="Past picks", icon=":material/history:"),
    st.Page("screens/3_Performance.py", title="Performance", icon=":material/monitoring:"),
    st.Page("screens/4_About_and_Settings.py", title="About and settings",
            icon=":material/settings:"),
    st.Page("screens/5_FAQ.py", title="FAQ", icon=":material/help:"),
]  # fmt: skip
st.sidebar.caption(disclaimer.SHORT)
st.navigation(pages).run()
