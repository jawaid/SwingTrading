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
uv run swing app                   # launch the Streamlit app
```

These are being built phase by phase; a command that isn't built yet says which phase adds it.

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

## Development

```bash
uv run pytest          # tests never touch the network
uv run ruff check .
uv run ruff format .
```
