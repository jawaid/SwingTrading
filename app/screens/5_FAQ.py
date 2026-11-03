import streamlit as st

from swingtrading.ui import data, disclaimer

settings, conn = data.open_db()
m = f"{settings.stops.atr_mult:g}"
top = f"{settings.ranking.shortlist_pct:g}"
period = settings.stops.atr_period

st.title("FAQ")
st.warning(disclaimer.RISK_WARNING, icon=":material/warning:")
st.caption("What every screen and column means, in plain words. Open any question to read it.")


def section(items: list[tuple[str, str]]) -> None:
    for title, body in items:
        with st.expander(title):
            st.markdown(body)


tab_basics, tab_logic, tab_current, tab_past, tab_perf, tab_about = st.tabs(
    ["The basics", "Logic", "Current picks", "Past picks", "Performance", "About and settings"]
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
        ("Is this financial advice?", disclaimer.text()),
        ("When does it update?",
         "Twice a day, Monday to Friday:\n\n"
         "- **9:00 AM Central** (10:00 AM Eastern), 30 minutes after the open: picks due to "
         "buy or sell today are recorded at today's **opening price**. Nothing else changes.\n"
         "- **4:30 PM Central** (5:30 PM Eastern), after the close: the main run. It stores "
         "the day's prices, moves stops, sells picks that closed below their stop, and finds "
         "new picks.\n\n"
         "Prices are **end of day**, not live: between runs the numbers don't change. Refresh "
         "the browser after a run to see the new numbers. On weekends and market holidays "
         "nothing changes."),
        ("When is a pick bought?",
         "At the **opening price of the next trading day** after the signal. A signal on "
         "Friday buys at Monday's open, whatever that open is, higher or lower than Friday's "
         "close. The 9:00 AM Central morning run records it, so it shows as **Open** from "
         "then on (if the morning run didn't happen, the evening run records it). If a stock "
         "has no price that day (for example a trading halt), it buys on the next day it has "
         "one."),
        ("When is a pick sold?",
         "Only when the stock **closes below its stop**. It then sells at the next day's open. "
         "There is no profit target, no \"too extended\" rule and no time limit: a stock that "
         "keeps rising is held, and the rising stop protects the gain. Only the close counts; "
         "dipping below the stop during the day does nothing."),
        ("What if my Mac is asleep or off at run time?",
         "If it's asleep, the run happens as soon as it wakes. If it was off, the next run "
         "catches up every missed day in order. Nothing is lost; the picks just update late."),
    ])  # fmt: skip

# ---- logic -------------------------------------------------------------------------------
u, r, su = settings.universe, settings.ranking, settings.setup
w = r.weights
universe = data.data_status(conn)["universe"]
shortlist = round(universe * r.shortlist_pct / 100)
long_q = f"Well above its {r.long_ema_period}-day average? Up over the last ~6 months?"
mid_q = f"Above its {r.mid_ema_period}-day average? Up over the last month?"


def money(x: float) -> str:
    return f"\\${x / 1e9:g} billion" if x >= 1e9 else f"\\${x / 1e6:g} million"


with tab_logic:
    st.markdown(
        "The whole journey from every US stock down to a single pick, in plain English. It "
        "runs every weekday evening after the market closes. The numbers below are your "
        "current settings."
    )
    st.markdown(f"""
### Step 1: Decide which stocks to watch (the universe)

Start with every stock and ETF listed on the major US exchanges (NYSE, NASDAQ, NYSE
American, NYSE Arca), and keep only the solid, easy-to-trade ones:

- **Big companies:** stocks worth at least **{money(u.min_market_cap)}**, ETFs holding at
  least **{money(u.min_etf_total_assets)}**.
- **Heavily traded:** at least **{money(u.min_avg_dollar_volume)}** changing hands per day on
  average, so you could get in and out easily.
- **Enough history:** at least **{u.min_history_sessions} trading days** of prices (about a
  year), which the strength score needs.
- **No unusual types:** preferred shares, warrants, foreign ADRs, REITs, SPACs and 2x/3x
  leveraged or inverse ETFs are left out.

That leaves about **{universe:,}** names. The list is refreshed weekly.

### Step 2: Rank them by strength (SCTR)

Every stock gets a strength score from six measures:

| Time frame | Weight | Question it asks |
|---|---|---|
| Long term | {w.long_ema + w.long_roc:g}% | {long_q} |
| Medium term | {w.mid_ema + w.mid_roc:g}% | {mid_q} |
| Short term | {w.ppo_slope + w.rsi:g}% | Is momentum improving over the last few days? |

Then all of them are ranked against each other, from **0 (weakest) to 99.99 (strongest)**.

### Step 3: Keep only the leaders (the shortlist)

Only the **top {top}%** (about **{shortlist:,}** stocks) move on. Strong stocks tend to
keep being strong, so the app only looks for buying chances among the leaders.

### Step 4: Look for a dip and a turn-up (the setup)

Among the shortlist, look for a stock that **paused, pulled back calmly, and has just
started rising again**. All of these must be true:

**A. A recent high:** it made a high in the last **{su.swing_high_lookback} trading days**.

**B. A healthy dip from that high:**
- down **{su.pullback_min_pct:g}% to {su.pullback_max_pct:g}%** from the high,
- which is also **{su.pullback_min_atr:g} to {su.pullback_max_atr:g} times its normal daily
  move (ATR)**: meaningful for that stock, but not a collapse,
- and the dip lasted **{su.max_pullback_sessions} days or fewer**.

**C. A calm dip, not panic selling:**
- the down days had **lighter volume than normal** (sellers weren't rushing out),
- and it **never closed below its {su.trend_ema_period}-day average** during the dip, so the
  uptrend held.

**D. Today it turned back up (the pivot day):**
- it **closed above yesterday's high** (the turn-up),
- in the **upper half of today's range** (buyers held control into the close),
- on **at least normal volume** (real buying interest).

If A to D are all true, **it's a setup**, with a **setup score from 0 to 100** for how
cleanly it passed. *This setup rule is a starting placeholder and can be swapped for your
own formula.*

### Step 5: Make the pick

Each setup becomes a pick with status **Pending entry**. A stock that already has an active
pick isn't picked again.

### Step 6: Buy at the next open

The next trading day the pick is **bought at the opening price**, whatever it is. The
morning run (9:00 AM Central) records it, and the status becomes **Open**.

### Step 7: Set the safety stop

On the buy day the stop is set **{m} times the stock's normal daily move** (ATR over
{period} days) below the entry. Calm stocks get a tight stop, jumpy stocks more room.

### Step 8: Trail the stop up, every evening

- When the stock makes a **new highest close**, the stop moves up to that close minus
  {m} daily moves.
- **The stop never moves down.**
- Only **closing prices** count; a dip below the stop during the day does nothing.

### Step 9: Sell

When the stock **closes below its stop**, it becomes **Exit pending** and is **sold at the
next day's open**. There is no profit target and no time limit: winners are held as long as
they keep rising.

### Step 10: Keep score

The sold pick moves to **Past picks** with its final return, and **Performance** adds it to
the win rate, average win and loss, and the comparison with simply holding SPY.

---

**In one sentence:** pick **strong stocks** (top {top}%) that **dipped calmly** and have
**just turned back up on good volume**, buy at the **next open**, and hold as long as a
**rising stop** isn't broken.
""")

# ---- current picks -----------------------------------------------------------------------
with tab_current:
    st.markdown(
        "Every pick you are still in or about to be in. The three numbers at the top count "
        "picks in each status: **Open**, **Pending entry** and **Exit pending**."
    )
    section([
        ("☐ Checkbox",
         "Tick a row, then click **Show chart** under the table. The chart opens in a pop-up. "
         "The title shows the ticker, company name and industry.\n\n"
         "- **Candles**: each day's open, high, low and close. Green: closed higher than it "
         "opened. Red: closed lower.\n"
         "- **Blue line (50 EMA)** and **purple line (200 EMA)**: the 50- and 200-day moving "
         "averages, the medium- and long-term trend. The setup needs the price to stay above "
         "the 50 EMA during the pullback; the 200 EMA is part of the SCTR rank.\n"
         "- **Dotted dark line (support)**: the nearest floor under the price. It starts at "
         "the lowest price of the pullback before the signal, on the day that low was made. "
         "When the stock breaks above a recent high, support moves up to that high and the "
         "dark line starts on the breakout day; the old pullback low stays as a **lighter "
         "dotted line**. A fall back through support means the bounce is failing.\n"
         "- **Small circle**: the signal day.\n"
         "- **Green triangle up**: where it was bought.\n"
         "- **Dashed green line**: the stop. It starts on the buy day at the first stop (buy "
         "price minus 2.5 times the daily move) and steps up as the price rises.\n"
         "- **Red triangle down**: where it was sold.\n\n"
         "The current **Stop** and **Support** levels are written at the right edge of the "
         "chart.\n\n"
         "**Copy chart** (top right of the chart) copies a picture of it, ready to paste into "
         "a message or document. If your browser doesn't allow copying, it downloads the "
         "picture instead. **Close** (or the × at the top) closes the pop-up.\n\n"
         "**Why it was picked**, under the chart, lists each rule the stock passed in plain "
         "English: its SCTR rank, the recent high, how deep and how long the dip was, how "
         "quiet the selling was, that the trend held, and how it turned up on the signal "
         "day, each with the stock's number next to the limit. The limits are today's "
         "settings. **Raw setup numbers** shows the same numbers as stored."),
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
         "Buys and sells at the open are recorded by the 9:00 AM Central morning run.\n\n"
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
        ("Support",
         "The nearest floor under the price, where buyers are likely to step in.\n\n"
         "- **It starts at the pullback low**: the lowest price of the dip before the signal, "
         "where the stock turned up.\n"
         "- **It moves up when the stock breaks out.** When a close clears a ceiling (the "
         "recent high before the dip, or a later peak), that ceiling becomes the new floor. "
         "Example: HRB dipped to 46.90, then closed above its 50.54 high, so its support is "
         "now 50.54.\n"
         "- **If the price falls back under a broken ceiling**, support drops back to the "
         "next level down.\n\n"
         "- **Price well above support**: the bounce is holding.\n"
         "- **Price back near or below support**: the bounce failed; the stop usually sells "
         "it soon after.\n\n"
         "It is only information; the sell rule is the **Stop**. On the chart it is the "
         "dotted dark line."),
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
         "Tick a row, then click **Show chart** to open that pick's chart in a pop-up: daily "
         "candles, the 50 and 200 EMA, the support line, the signal circle, buy triangle, the "
         "stop as it stepped up, and the red sell triangle, with **Why it was picked**, "
         "**Copy chart** and **Close**. It reads the same as the Current picks pop-up."),
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
