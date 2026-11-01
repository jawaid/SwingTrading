# SwingTrading

An end-of-day swing trading signal and tracking app that runs locally on macOS. After the US market close it ranks liquid large-cap stocks and large ETFs with the StockCharts SCTR method, scans the top names for a pullback-and-pivot setup, creates picks with an ATR chandelier stop, and trails or closes open picks.

It is a signals and tracking tool only. It never places orders, and it is not financial advice.

See [CLAUDE.md](CLAUDE.md) for the domain rules and [BUILD_PLAN.md](BUILD_PLAN.md) for the build phases.

## Setup

You need macOS, [uv](https://docs.astral.sh/uv/) and git.

```bash
brew install uv
git clone https://github.com/jawaid/SwingTrading.git
cd SwingTrading
uv sync
```

`uv sync` installs Python 3.12 if needed, creates `.venv`, and installs the `swing` command along with the dev tools (pytest, ruff).

## Configuration

All thresholds live in [config/settings.yaml](config/settings.yaml): universe filters, SCTR periods and weights, setup parameters, stop settings, download settings, and the schedule. Values are validated on load by `swingtrading/config.py`, and the app refuses to start with an invalid file. To use a different file, set `SWING_SETTINGS=/path/to/settings.yaml` or pass `swing --settings PATH`.

## Data

Everything local lives in `data/`, which git ignores:

- `data/swing.db`: SQLite database, created and migrated automatically on first use
- `data/logs/`: run logs
- `data/cache/`: downloaded symbol files and metadata

## Commands

```bash
uv run swing refresh-universe      # rebuild symbol list + metadata cache (weekly)
uv run swing backfill              # download ~400 trading days of history
uv run swing update-prices         # fetch only sessions missing since the last update
uv run swing rank [--date YYYY-MM-DD] [--top 25]   # SCTR rankings from stored prices
uv run swing scan [--date YYYY-MM-DD]              # setups in the stored shortlist (read-only)
uv run swing run [--date YYYY-MM-DD] [--dry-run]   # the daily pipeline
uv run swing app [--port 8501]     # the app at http://localhost:8501
```


### Universe refresh

`swing refresh-universe` downloads the NASDAQ Trader symbol files (cached per day), drops symbols by the rules in CLAUDE.md, and records the reason for every dropped symbol in `symbols.excluded_reason`. It runs in three passes so the slow part touches as few tickers as possible:

1. Checks that need only the symbol files: test issues, exchange, preferred/warrant/unit/right symbols and names, ADRs, leveraged and inverse ETF names.
2. One batched price download for the survivors: at least 250 sessions of history and $20M average daily dollar volume.
3. Yahoo metadata, one ticker at a time, only for names that passed: market cap, ETF assets, REITs, SPACs, leveraged fund categories. Metadata is cached in the database for 7 days, so a weekly refresh mostly reuses it.

A first run takes roughly an hour (about half on prices, half on metadata). It prints a count per exclusion reason at the end. To see why a ticker was dropped:

```bash
sqlite3 data/swing.db "SELECT ticker, excluded_reason FROM symbols WHERE ticker = 'BRK.B'"
```

### Prices

`swing backfill` downloads the last `backfill_sessions` (400) sessions of split- and dividend-adjusted daily bars for every universe ticker, replacing whatever was stored. `swing update-prices` fetches only the sessions after each ticker's last stored bar; the daily run uses the same code. Both accept `--tickers AAPL MSFT` to limit the run.

- Only fully completed sessions are stored: a session counts as complete `close_buffer_minutes` after its close, so there is never a partial current-day bar.
- A new split or dividend changes every earlier adjusted price. Each update re-fetches the last few stored sessions; if a close moved by more than `adjustment_tolerance`, that ticker's whole history is reloaded so all of its bars share one adjustment basis.
- Tickers with no data, a stale last bar, or missing sessions are logged and counted; they never stop the run.

### Setups

`swing scan` takes the stored SCTR shortlist for a session (run `swing rank` first) and prints every name where the configured detector fires, with its 0-100 setup score and details. It writes nothing; the daily run (Phase 7) is what turns setups into picks.

The default detector, [swingtrading/setups/pullback_pivot.py](swingtrading/setups/pullback_pivot.py), is a **placeholder** for your own formula: a 3-12% (1-4 ATR) orderly pullback from a swing high in the last 20 sessions, holding the 50-day EMA, then a pivot day that closes above the prior day's high, in the upper half of its range, on above-average volume. Every threshold is in the `setup` section of settings.yaml. To use a different formula, write a class with a `name` and a `detect(df) -> SetupResult | None` method, register it in `DETECTORS` in [swingtrading/setups/base.py](swingtrading/setups/base.py), and set `setup.detector` to its name.

### Stops and picks

[swingtrading/stops.py](swingtrading/stops.py) and [swingtrading/positions.py](swingtrading/positions.py) track each pick through `signal` → `open` → `exit_pending` → `closed`:

- A pick is entered at the open of the session after its signal, with an initial stop of entry − `atr_mult` × ATR(14) as of the signal session.
- After each close the stop trails to the highest close since entry − `atr_mult` × the current ATR, but it never moves down. Every session's stop is saved in `stop_history`.
- A close below the stop in effect that morning marks the pick `exit_pending`; it is closed at the next session's open, even if that open gaps well below the stop.
- `return_pct` is a fraction (0.05 = +5%), and `days_held` counts sessions from entry to exit.
- A ticker can have only one pick that is not closed. Re-processing a session changes nothing.

The daily run drives this; there is no separate command.

### Daily run

`swing run` does one end-of-day run after the close:

1. Updates prices for the universe, every ticker with an active pick, and SPY (the Performance benchmark), up to the last completed session.
2. For each session not yet processed, in order: ranks the universe, takes the shortlist, fills exits and entries at that session's open, trails stops and flags stop-outs on its close, then scans the shortlist (skipping tickers with an active pick) and creates a signal for each setup.
3. Records a row per session in `runs` with counts (universe, shortlist, new picks, stops raised, exits, errors), prints a summary, and shows a macOS notification.

If the Mac was asleep and sessions were missed, the next run processes each one from the session after the last successful run. Running it again for a session already processed does nothing. A failed session stops the run there and is retried next time. `--date YYYY-MM-DD` processes up to that session instead (a weekend or holiday is logged and skipped). `--dry-run` runs everything on an in-memory copy of the database, prints the summary, and writes nothing; it still downloads the latest prices, so it takes a few minutes.

### App

`swing app` starts the Streamlit app on your Mac only (localhost) and prints its address; stop it with Ctrl+C. It has four screens:

- **Current picks**: pending, open and exit-pending picks with price, stop, distance to stop, open P/L, SCTR and days held. Select a row for its chart: the close price, entry and exit markers, and the stop as a stepped dashed line.
- **Past picks**: closed picks filtered by exit date and winners or losers, with the same chart.
- **Performance**: for closed picks, win rate, average win and loss, profit factor (gross gains over gross losses), expectancy (average return per pick) and average days held. Then an equity curve against SPY with its max drawdown, and a monthly returns table for both.

The equity curve holds every entered pick in equal weight each session, including picks still open, and compounds the day's average return. A pick earns from its entry price (the open) to the close on its entry day, and from the prior close to its exit price (the open) on its exit day. A day with nothing held earns nothing. SPY is measured over the same sessions. The math is in `swingtrading/performance.py`.
- **About and settings**: how the method works, the last run's status, a **Run now** button (and a dry run) that runs `swing run` in the background and shows its log, and an editor for settings.yaml. Edits are validated before saving, and the file's comments are kept.

## Automation

Two launchd jobs run SwingTrading on its own:

- `com.jawaid.swingtrading`: the evening run, `scripts/run_daily.sh`, Monday to Friday at 5:30 PM US Eastern (4:30 PM on a Mac set to Central time). The installer converts the time to the Mac's time zone. On a market holiday the run sees the market was closed, records a skipped session and does nothing else.
- `com.jawaid.swingtrading.app`: keeps the app running at http://localhost:8502. It starts at login and restarts if it stops.

```bash
scripts/install_launchd.sh            # install or reinstall both (run again after moving time zones)
scripts/install_launchd.sh status     # loaded? running? last exit code
scripts/install_launchd.sh uninstall  # remove both
```

**Logs:** each evening run appends to `data/logs/daily_YYYY-MM-DD.log` (the `swing run` summary) and `data/logs/run_YYYY-MM-DD.log` (detailed). launchd's own output goes to `data/logs/launchd_daily.log`, and the app's to `data/logs/app_server.log`. The About and settings screen also shows the last run.

**Run it by hand:** `scripts/run_daily.sh` (the same as the scheduled job), or ask launchd to start it now with `launchctl kickstart gui/$(id -u)/com.jawaid.swingtrading`. Running it again for a session already processed does nothing.

**Missed runs:** if the Mac is asleep at 5:30 PM, launchd runs the job once as soon as it wakes. Several missed evenings still give one run, and that run processes every missed session in order. If the Mac was shut down at that time, launchd skips the run, and the next scheduled run catches up the same way. Either way no session is lost, though the picks update late.

## Development

```bash
uv run pytest          # tests never touch the network
uv run ruff check .
uv run ruff format .
```
