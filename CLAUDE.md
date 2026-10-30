# SwingTrading — project context for Claude Code

## What this app does
An end-of-day swing trading signal and tracking app that runs locally on macOS.
After the US market close it:
1. Builds a universe of liquid large caps and large ETFs listed on NYSE / NASDAQ.
2. Ranks every name 0–99.99 using the StockCharts SCTR methodology (one pool: stocks + ETFs together).
3. Shortlists the top N% (default 15%) and scans them for a pullback-and-pivot setup.
4. Creates new picks with a volatility-based (ATR chandelier) stop.
5. Updates every open pick: trails the stop up, or closes it out if it stopped out.

It is a **signals and tracking tool only**. It never places orders. No position sizing yet; performance is tracked as % return per pick.

## Stack and conventions
- Python 3.12, managed with `uv` (`uv sync`, `uv run ...`). Never use system pip.
- Data: `yfinance` (pin the version in `pyproject.toml`), `pandas`, `numpy`, `pandas_market_calendars`.
- Storage: SQLite at `data/swing.db` (gitignored). Access only through `swingtrading/db.py`.
- UI: Streamlit multipage app with Plotly charts, in `app/`.
- Config: `config/settings.yaml` (committed defaults). The Settings screen edits this file. Load through `swingtrading/config.py`; never hardcode thresholds in logic.
- Tests: `pytest`. Tests must never hit the network; use synthetic OHLCV fixtures in `tests/fixtures/`.
- Style: `ruff` for lint + format, type hints on public functions, small pure functions for all calculations.
- Logging: standard `logging`, to console and `data/logs/run_YYYY-MM-DD.log`.
- Commit after each completed phase with a clear message. Never commit `data/`, `.env`, or the database.

## Repository layout
```
SwingTrading/
├── CLAUDE.md
├── README.md
├── pyproject.toml
├── config/settings.yaml
├── swingtrading/
│   ├── config.py           # load/validate settings.yaml (pydantic)
│   ├── db.py               # schema, migrations, query helpers
│   ├── market_calendar.py  # NYSE sessions, holidays, "last completed session"
│   ├── data/
│   │   ├── provider.py     # abstract PriceProvider / MetadataProvider interfaces
│   │   ├── yahoo.py        # yfinance implementation (batching, retries, throttling)
│   │   └── symbols.py      # NASDAQ Trader symbol directory download + parse
│   ├── universe.py         # filters and exclusions
│   ├── indicators.py       # EMA, ROC, RSI, PPO, ATR, linear-regression slope
│   ├── ranking.py          # SCTR raw score + percentile rank
│   ├── setups/
│   │   ├── base.py         # SetupDetector protocol + SetupResult dataclass
│   │   └── pullback_pivot.py  # placeholder formula (owner will replace)
│   ├── stops.py            # initial stop + trailing logic
│   ├── positions.py        # pick lifecycle state machine
│   ├── performance.py      # trade stats, equity curve, SPY comparison
│   ├── pipeline.py         # orchestrates one daily run, idempotent
│   ├── notify.py           # macOS notification on run completion
│   └── cli.py              # entry points
├── app/
│   ├── Home.py
│   └── pages/
│       ├── 1_Current_Picks.py
│       ├── 2_Past_Picks.py
│       ├── 3_Performance.py
│       └── 4_About_and_Settings.py
├── scripts/
│   ├── run_daily.sh
│   └── install_launchd.sh
├── launchd/com.jawaid.swingtrading.plist
├── data/                   # gitignored: swing.db, logs, caches
└── tests/
```

## CLI
```
uv run swing refresh-universe      # rebuild symbol list + metadata cache (weekly)
uv run swing backfill              # download ~400 trading days of history for the universe
uv run swing run [--date YYYY-MM-DD] [--dry-run]   # the daily pipeline
uv run swing app                   # launch Streamlit
```

## Domain rules (source of truth — do not change without being asked)

### Universe
- Source lists: `nasdaqlisted.txt` and `otherlisted.txt` from the NASDAQ Trader symbol directory.
- Keep exchanges: NASDAQ, NYSE (N), NYSE American (A), NYSE Arca (P). Drop test issues.
- Stocks: Yahoo `marketCap` >= $2B.
- ETFs: Yahoo `totalAssets` >= $2B (ETFs have no market cap).
- Liquidity: 50-day average dollar volume (close × volume) >= $20M.
- History: at least 250 trading days of prices (SCTR requirement).
- Exclude:
  - **Preferreds, warrants, units, rights**: symbols containing `$`, `.`, `-`, `+`, `=`, `^` (except share classes written as a single letter after a dot, such as `BRK.B` or `BF.A`; `.W`, `.U` and `.R` stay excluded), or names matching preferred/warrant/unit/right/depositary-share-of-preferred.
  - **ADRs**: security name contains "American Depositary" / "ADR" / "ADS".
  - **REITs**: Yahoo industry starts with "REIT".
  - **SPACs**: Yahoo industry "Shell Companies", or names like "Acquisition Corp" with no revenue.
  - **Leveraged and inverse ETFs**: Yahoo fund category contains "Leveraged" or "Inverse"/"Trading", or name matches 2x/3x/Ultra/UltraPro/Bull/Bear/Inverse/Short/-1x patterns.
- Store the reason for every exclusion in `symbols.excluded_reason` so it can be audited.
- Performance: apply the cheap dollar-volume filter from batch price data **before** calling per-ticker `Ticker.info`. Cache metadata; refresh weekly.

### SCTR ranking (StockCharts methodology)
Raw score components:
- Long-term (60%): % above/below 200-day EMA (30%), 125-day rate of change (30%).
- Mid-term (30%): % above/below 50-day EMA (15%), 20-day rate of change (15%).
- Short-term (10%): 3-day slope of PPO(12,26,9) histogram (5%), 14-day RSI (5%).
- PPO histogram slope: 3-day linear regression slope; slope > 1 → 5 points, slope < −1 → 0 points, otherwise (slope + 1) × 2.5.
- RSI contributes RSI × 0.05.
- Final SCTR = percentile rank of raw score across the entire universe that day, scaled 0–99.99.
- Verify the formula against StockCharts ChartSchool "StockCharts Technical Rank (SCTR)" before finalizing, and add a test.

### Setup detection
- Only names with SCTR in the top `shortlist_pct` (default 15%) are scanned.
- Detector is pluggable: anything implementing `SetupDetector.detect(df) -> SetupResult | None`.
- `pullback_pivot.py` is a **placeholder**, clearly marked. Default rules:
  - Recent swing high within the last 20 sessions.
  - Pullback depth 3–12% from that high, and 1–4 ATRs.
  - Orderly: pullback lasted <= 10 sessions, average volume on down days < 50-day average volume, no close below the 50-day EMA.
  - Pivot: today's close > prior day's high, close in the upper half of today's range, volume >= 20-day average.
  - `SetupResult` includes a 0–100 setup score and a details dict (depth, bars, volume ratio) for display.
- Skip tickers that already have a pending, open, or exit-pending pick.

### Stops (ATR chandelier, close-based)
- ATR period 14, multiplier `atr_mult` (default 2.5).
- Initial stop = entry price − atr_mult × ATR(14) at signal.
- Daily: candidate = highest close since entry − atr_mult × current ATR. New stop = max(old stop, candidate). **The stop never moves down.**
- Stopped out when a daily **close** is below the current stop.
- Record every day's stop in `stop_history`.

### Pick lifecycle
`signal` → (next session open) `open` → (close < stop) `exit_pending` → (next session open) `closed`.
- Entry price = next session's open after the signal.
- Exit price = next session's open after the stop-out close.
- return_pct = exit_price / entry_price − 1. days_held = trading sessions between entry and exit.

### Daily run behavior
- Only use bars for sessions that are fully complete (per NYSE calendar, after 4:00 PM ET plus a buffer). Never use a partial current-day bar.
- Idempotent: the `runs` table records the last processed session. If sessions were missed (Mac asleep), process each missed session in order.
- Skip non-trading days cleanly with a log line.
- `--dry-run` computes everything and prints a summary without writing to the DB.
- Each run writes a row to `runs` with counts: universe size, shortlist size, new picks, stops raised, exits, errors.

## Database tables (summary)
- `symbols`: ticker, name, exchange, is_etf, quote_type, sector, industry, fund_category, market_cap, total_assets, excluded_reason, updated_at
- `prices`: ticker, date, open, high, low, close, volume (PK ticker+date), adjusted
- `rankings`: date, ticker, sctr, raw_score, component columns (PK date+ticker)
- `picks`: id, ticker, status, signal_date, signal_close, sctr_at_signal, setup_score, setup_details (JSON), entry_date, entry_price, initial_stop, current_stop, highest_close, exit_signal_date, exit_date, exit_price, return_pct, days_held
- `stop_history`: pick_id, date, close, stop
- `runs`: session_date, started_at, finished_at, status, counts, error

## UI guidance
Four screens minimum, in the sidebar in this order:
1. **Current picks**: table of pending/open picks (ticker, entry, current price, stop, distance to stop %, open P/L %, SCTR, days held). Selecting a row shows a Plotly chart: price line, entry marker, stepped dashed stop line, matching the style in the original mockup.
2. **Past picks**: closed trades with filters (date range, winners/losers), each with the same chart.
3. **Performance**: win rate, average win vs. average loss, profit factor, expectancy, average days held, equity curve of an equal-weighted portfolio of open picks vs. SPY, monthly returns table.
4. **About and settings**: plain-language methodology (the 0–99.99 scale gradient bar, how the stop trails), last run status, editable settings with validation, and a "Run now" button.

Visual direction: light background, slate (#2D3748) price lines, teal (#4FB99F) dashed stop lines, a red → amber → teal gradient for the SCTR scale, as in the mockup. Sentence-case labels, plain wording. Empty states should tell the user what to do (for example, "No picks yet. Run the pipeline after today's close.").

## Things to be careful about
- yfinance is unofficial: batch downloads (~200 tickers), retry with backoff, throttle `Ticker.info` calls, and never let one bad ticker abort the run.
- Use split/dividend-adjusted prices consistently for indicators and stops.
- Log, don't crash, on missing data; mark the ticker as skipped for that day.
- This is not financial advice software; the About screen should say so.
