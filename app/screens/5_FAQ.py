import streamlit as st

from swingtrading.ui import data

settings, conn = data.open_db()
m = f"{settings.stops.atr_mult:g}"
top = f"{settings.ranking.shortlist_pct:g}"
period = settings.stops.atr_period

st.title("FAQ")
st.caption("What every screen and column means, in plain words. Open any question to read it.")


def section(items: list[tuple[str, str]]) -> None:
    for title, body in items:
        with st.expander(title):
            st.markdown(body)


tab_basics, tab_current, tab_past, tab_perf, tab_about = st.tabs(
    ["The basics", "Current picks", "Past picks", "Performance", "About and settings"]
)

# ---- basics ------------------------------------------------------------------------------
with tab_basics:
    section([
        ("What does this app do?",
         "Every weekday evening, after the market closes, it:\n\n"
         "1. **Gets prices** for about 2,000 US stocks and ETFs.\n"
         f"2. **Ranks them** by strength from 0 to 99.99 (the SCTR) and keeps the top {top}%.\n"
         "3. **Looks for a setup**: a strong stock that dipped a little and has just turned "
         "back up.\n"
         "4. **Makes a pick** of each one, which \"buys\" at the next day's open.\n"
         "5. **Trails a stop** under each pick, and \"sells\" when the price closes below it.\n\n"
         "It never places real orders. It suggests picks and keeps score. Nothing here is "
         "financial advice."),
        ("When does it update?",
         "Once a day, Monday to Friday, at 5:30 PM US Eastern (4:30 PM Central). Prices are "
         "**end of day**, not live. Refresh the browser after the run to see the new numbers. "
         "On weekends and market holidays nothing changes."),
        ("When is a pick bought?",
         "At the **opening price of the next trading day** after the signal. A signal on "
         "Friday buys at Monday's open, whatever that open is, higher or lower than Friday's "
         "close. The evening run that day records it. If a stock has no price that day (for "
         "example a trading halt), it buys on the next day it has one."),
        ("When is a pick sold?",
         "Only when the stock **closes below its stop**. It then sells at the next day's open. "
         "There is no profit target, no \"too extended\" rule and no time limit: a stock that "
         "keeps rising is held, and the rising stop protects the gain. Only the close counts; "
         "dipping below the stop during the day does nothing."),
        ("What if my Mac is asleep or off at run time?",
         "If it's asleep, the run happens as soon as it wakes. If it was off, the next run "
         "catches up every missed day in order. Nothing is lost; the picks just update late."),
    ])  # fmt: skip

# ---- current picks -----------------------------------------------------------------------
with tab_current:
    st.markdown(
        "Every pick you are still in or about to be in. The three numbers at the top count "
        "picks in each status: **Open**, **Pending entry** and **Exit pending**."
    )
    section([
        ("☐ Checkbox",
         "Tick a row to see that stock's chart below the table. Without a tick, the chart "
         "shows the first row.\n\n"
         "- **Dark line**: the closing price.\n"
         "- **Small circle**: the signal day.\n"
         "- **Green triangle up**: where it was bought.\n"
         "- **Dashed green line**: the stop, stepping up as the price rises.\n"
         "- **Red triangle down**: where it was sold.\n\n"
         "**Setup details** under the chart shows the raw numbers behind the setup."),
        ("Ticker",
         "The stock's trading symbol, for example PAYC (Paycom), COP (ConocoPhillips) or HRB "
         "(H&R Block). A ticker can have only one active pick at a time; it can be picked "
         "again after it is sold."),
        ("Status",
         "Where the pick is in its life:\n\n"
         "1. **Pending entry**: found, not bought yet. Buys at the next day's open.\n"
         "2. **Open**: bought. Each evening the stop moves up if the stock made a new high "
         "close.\n"
         "3. **Exit pending**: closed below the stop. Sells at the next day's open.\n\n"
         "After it sells it moves to **Past picks**."),
        ("Signal",
         "The date the setup was found, from that day's closing prices. The stock was in "
         f"the top {top}% by SCTR, had dipped from a recent high, and that day closed above "
         "the prior day's high, near the top of its range, on good volume. It isn't bought "
         "that day, because the signal is only known after the close. On the chart it is the "
         "small circle."),
        ("Entered",
         "The date the pick was bought: always the trading day after the signal. Blank while "
         "pending. It is day 0 for **Days held**. On the chart it is the green triangle."),
        ("Entry",
         "The buy price: the **opening price** on the Entered day. It can be above or below "
         "the signal day's close; the app buys at whatever the open is. Open P/L and the "
         "final return are measured from it. Blank (None) until bought."),
        ("Price",
         "The latest **closing price** the app has. It updates once each trading evening, "
         "not during the day. To stop and Open P/L are calculated from it."),
        ("Stop",
         "The \"sell if it closes below this\" price.\n\n"
         f"- **First stop**, on the buy day: entry price minus {m} times the stock's ATR "
         f"(its average daily move over {period} days). Example: a \\$7.83 ATR gives a "
         f"cushion of {m} × 7.83 = \\$19.58, so a 264.20 entry gets a 244.62 stop. Calm "
         "stocks get tight stops, jumpy stocks get more room.\n"
         f"- **Each evening after**: if the stock made a new highest close, the stop moves up "
         f"to that close minus {m} ATRs. **It never moves down.**\n"
         "- **Only the close counts.** A dip below the stop during the day does nothing.\n\n"
         "On the chart it is the dashed green line."),
        ("To stop",
         "How far the price is above the stop, in percent: (Price ÷ Stop − 1) × 100. "
         "Example: price 264.20, stop 244.62 → **8.0%**, so the stock can fall about 8% "
         "before it closes below its stop.\n\n"
         "- **10% or more**: lots of room.\n"
         "- **1–2%**: close to the stop; one bad day could sell it.\n"
         "- **Negative**: closed below the stop; it is about to be sold (Exit pending)."),
        ("Open P/L",
         "Profit or loss so far, in percent: (Price ÷ Entry − 1) × 100. Bought at 264.20 and "
         "now at 277.41 → **+5.0%**. It changes every evening and isn't locked in. The final "
         "result is set when the pick sells at the next open, which can differ from the last "
         "close. It is per stock, not dollars."),
        ("SCTR",
         "StockCharts Technical Rank: strength from **0 to 99.99** compared with every other "
         "stock the app watches. 98.3 means stronger than about 98% of them.\n\n"
         "| Part | Weight | What it checks |\n|---|---|---|\n"
         "| Long term | 60% | distance above the 200-day average, gain over about 6 months |\n"
         "| Medium term | 30% | distance above the 50-day average, gain over about 1 month |\n"
         "| Short term | 10% | momentum over the last few days |\n\n"
         f"Only the top {top}% are scanned for setups. The number here updates every evening "
         "so you can see if a pick is staying strong. It doesn't affect the stop or the sale."),
        ("Days held",
         "Trading days since the pick was bought. The buy day is 0, and weekends and "
         "holidays don't count: bought Monday, the next Monday shows 5. Blank until bought."),
        ("Setup score",
         "How clean the dip-and-turn pattern looked on the signal day, **0 to 100**. Every "
         "pick already passed all the required checks; the score grades how well. Four parts, "
         "up to 25 points each:\n\n"
         "| Part | Full points when |\n|---|---|\n"
         "| Dip size | the dip was about 2.5 daily moves (ATRs): not too shallow, not too deep |\n"
         "| Quiet selling | the down days during the dip had light volume |\n"
         "| Turn-up volume | the turn-up day traded about twice its normal volume |\n"
         "| Strong close | the turn-up day closed at the top of its range |\n\n"
         "- **Fixed**: calculated once on the signal day and never changes.\n"
         "- **Maximum**: 100 (full marks on all four), which is rare.\n"
         "- **Higher** means a cleaner pattern on paper. Whether it means better returns "
         "isn't tested yet.\n"
         "- **Rough guide** (judgment, not tested): 60+ very clean, 40–60 solid, below 40 "
         "passed but only just on several parts.\n\n"
         "It is information only: every setup becomes a pick whatever its score. Once 30–50 "
         "picks have closed, the score can be checked against results."),
    ])  # fmt: skip

# ---- past picks --------------------------------------------------------------------------
with tab_past:
    st.markdown(
        "Every pick that has been sold. Use **Exit date range** to pick a period and **Show** "
        "to see all, only winners or only losers. The three numbers at the top cover just the "
        "picks shown: **Trades** (how many), **Winners** (percent with a gain) and **Average "
        "return**."
    )
    section([
        ("☐ Checkbox",
         "Tick a row to see that pick's chart: the price, signal circle, buy triangle, the "
         "stop as it stepped up, and the red sell triangle."),
        ("Ticker", "The stock's trading symbol."),
        ("Signal", "The date the setup was found."),
        ("Entered", "The date it was bought: the trading day after the signal."),
        ("Entry", "The buy price: the opening price on the Entered day."),
        ("Exited",
         "The date it was sold: the trading day after it closed below its stop."),
        ("Exit",
         "The sell price: the **opening price** on the Exited day. If the stock opened far "
         "below the stop (a gap down), the sale is at that lower open, so a loss can be bigger "
         "than the stop suggested."),
        ("Return",
         "The final result, in percent: (Exit ÷ Entry − 1) × 100. A 0% return counts as a "
         "loss."),
        ("Days held", "Trading days from the buy to the sale."),
        ("SCTR at signal",
         "The strength rank on the signal day (it doesn't change, unlike the live SCTR on "
         "Current picks). Useful later to see whether higher-ranked picks did better."),
        ("Setup score", "The pattern grade from the signal day, as on Current picks."),
    ])  # fmt: skip

# ---- performance -------------------------------------------------------------------------
with tab_perf:
    st.markdown(
        "How the picks have done overall. It stays empty until the first pick is bought; the "
        "**Closed picks** numbers appear once the first pick is sold."
    )
    section([
        ("Closed picks", "How many picks have been sold."),
        ("Win rate",
         "The percent of sold picks with a gain. Hover the ⓘ to see wins and losses."),
        ("Average win / Average loss",
         "The average return of the winners, and of the losers. A good swing method usually "
         "has an average win clearly bigger than its average loss, even with a win rate under "
         "50%."),
        ("Profit factor",
         "Total gains of all winners ÷ total losses of all losers. Above 1 means the method "
         "made money overall; 2 means it made \\$2 for every \\$1 lost. Shows \"no losses\" "
         "until the first loser."),
        ("Expectancy",
         "The average return per sold pick, winners and losers together. It is what one "
         "pick has been worth on average."),
        ("Average days held", "How long a pick typically lasts, in trading days."),
        ("Equity curve: Picks and SPY",
         "**Picks** is the total return of a pretend portfolio that holds every bought pick "
         "in equal amounts, including picks still open, adding up each day's average move. "
         "Days with nothing held earn nothing. **SPY** is what simply holding the S&P 500 "
         "would have made over the same days. The chart draws both: dark line for the picks, "
         "dotted green for SPY."),
        ("Max drawdown",
         "The biggest fall of the Picks line from a high point to a later low, in percent. "
         "It shows the worst stretch you'd have sat through."),
        ("Monthly returns",
         "Each calendar month's return for the picks and for SPY, newest month first. The "
         "months multiplied together equal the total return."),
    ])  # fmt: skip

# ---- about and settings ------------------------------------------------------------------
with tab_about:
    section([
        ("Daily run: the four numbers",
         "- **Universe**: how many stocks and ETFs the app watches.\n"
         "- **Prices through**: the latest trading day with prices saved.\n"
         "- **Last ranking**: the latest day the SCTR was calculated.\n"
         "- **Last run**: the latest trading day the evening job finished.\n\n"
         "The line under them summarises that run: new picks, stops raised, exits and errors. "
         "A few errors are normal (stocks Yahoo had no price for that day). If a run failed, "
         "it shows in red, and the next run tries that day again."),
        ("Run now and Dry run",
         "**Run now** runs the evening job immediately, the same as the scheduled one; useful "
         "if your Mac was off. If the latest day is already done it does nothing. **Dry run** "
         "does all the work and shows what would happen, but saves nothing. Both download "
         "prices first, which takes a few minutes; the log shows progress."),
        ("Recent runs", "A table of the last runs, one row per trading day, with counts."),
        ("How it works",
         "A plain description of the four steps: which stocks are watched, how the SCTR is "
         "built (with the red-to-green 0–99.99 scale showing the shortlist cut-off), what "
         "the setup looks for, and how the buy and trailing stop work."),
        ("Settings",
         "Every number the method uses, grouped in tabs: universe, ranking, setup, stops, "
         "data and schedule. Change a value and press **Save settings**. Values are checked "
         "first, and a bad value shows a red message and isn't saved. Changes apply from the "
         "next run, and a new stop multiple also changes how open picks' stops trail from "
         "then on (a stop still never moves down). Leave these alone unless you mean to "
         "change the method."),
    ])  # fmt: skip
