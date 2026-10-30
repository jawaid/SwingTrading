# SwingTrading — build plan for Claude Code

Work through the phases in order. Each phase ends with passing tests and a git commit.

## Phase 0: one-time setup on your Mac (you do this)

```bash
# 1. Tools
brew install git gh uv
gh auth login                      # log in as jawaid, choose HTTPS
npm install -g @anthropic-ai/claude-code   # or the native installer from claude.com

# 2. Create the private repo and clone it
cd ~/Projects                      # or wherever you keep code
gh repo create jawaid/SwingTrading --private --clone --description "End-of-day swing trading signals"
cd SwingTrading

# 3. Add the project brief
cp ~/Downloads/CLAUDE.md .
cp ~/Downloads/BUILD_PLAN.md .
git add . && git commit -m "Add project brief" && git push

# 4. Start Claude Code in the repo
claude
```

**Working tips for every phase:**
- Press Shift+Tab to enter plan mode, paste the prompt, review Claude's plan, then let it build.
- Run `/clear` between phases so each phase starts with fresh context (CLAUDE.md is reloaded automatically).
- If Claude drifts from a rule, say "Check CLAUDE.md, domain rules section."

## Phase 1: scaffolding, config, database

> Read CLAUDE.md. Set up the project skeleton exactly as in the repository layout: pyproject.toml managed by uv (Python 3.12) with a `swing` console script, ruff and pytest configured, .gitignore covering data/, .env, and caches. Create config/settings.yaml with every threshold from the domain rules (market cap, ETF assets, dollar volume, shortlist_pct, ATR period and multiplier, setup parameters, schedule). Implement config.py with pydantic validation and db.py with the full schema and simple migrations. Add a README with setup steps. Write tests for config loading and schema creation. Commit.

## Phase 2: market calendar and universe

> Read CLAUDE.md. Implement market_calendar.py using pandas_market_calendars (last completed session, sessions between two dates, is_trading_day). Then implement data/symbols.py to download and parse the NASDAQ Trader symbol files, and universe.py to apply every filter and exclusion in the domain rules, storing excluded_reason for each dropped symbol. Implement the yahoo.py metadata fetcher with throttling, retries, and a weekly cache. Run the cheap dollar-volume prefilter before per-ticker metadata calls. Add `swing refresh-universe`. Test the exclusion rules with fixture symbol rows (preferreds, ADRs, REITs, SPACs, leveraged ETFs, a normal stock, a normal ETF). Run it for real once and print counts by exclusion reason. Commit.

## Phase 3: price data

> Read CLAUDE.md. Implement the PriceProvider interface and the yfinance implementation: batched downloads of ~200 tickers, adjusted prices, retries, and never including a partial current-day bar. Add `swing backfill` (about 400 sessions) and an incremental update that fetches only missing sessions. Store bars in the prices table. Log tickers with gaps or failures without aborting. Test with mocked provider responses. Commit.

## Phase 4: indicators and SCTR ranking

> Read CLAUDE.md. Implement indicators.py (EMA, ROC, RSI-14 using Wilder smoothing, PPO 12/26/9 histogram, ATR-14, 3-point linear regression slope) as pure functions. Implement ranking.py: SCTR raw score exactly per the domain rules, then percentile rank across the full universe scaled to 0–99.99, stored in rankings with component columns. Before finalizing, look up the StockCharts ChartSchool SCTR article and confirm the weights and PPO-slope scoring; tell me about any discrepancy. Test indicators against known values and test that ranking is monotonic with raw score. Commit.

## Phase 5: setup detector

> Read CLAUDE.md. Implement setups/base.py (SetupDetector protocol, SetupResult dataclass) and setups/pullback_pivot.py with the placeholder rules, all parameters read from settings.yaml. Mark the file clearly as a placeholder to be replaced by my own formula. Build synthetic price fixtures for: a clean pullback-and-pivot (should trigger), a pullback too deep, a disorderly high-volume selloff, a pullback that broke the 50 EMA, and a stock with no pullback. Test each. Commit.

## Phase 6: stops and pick lifecycle

> Read CLAUDE.md. Implement stops.py (initial stop, chandelier trailing on highest close, never moves down) and positions.py (signal → open → exit_pending → closed, entries and exits at next session's open, return_pct, days_held, stop_history rows). Test: stop never decreases, close-below-stop triggers exit_pending, gap-down exit uses the next open, duplicate picks for the same ticker are blocked. Commit.

## Phase 7: daily pipeline and CLI

> Read CLAUDE.md. Implement pipeline.py to run one session end to end: update prices → rank → shortlist → detect setups → create signals → fill pending entries/exits at the open → trail stops → detect stop-outs → write the runs row. Make it idempotent and have it catch up on missed sessions in order. Add `swing run` with --date and --dry-run, and print a concise end-of-run summary (new picks, stops raised, exits). Add notify.py for a macOS notification via osascript. Run a dry run for the last completed session and show me the output. Commit.

## Phase 8: Streamlit app (4 screens)

> Read CLAUDE.md, UI guidance section. Build the Streamlit app with Current picks, Past picks, Performance, and About and settings, using Plotly. Charts: price line, entry marker, and a stepped dashed teal stop line like the mockup. About and settings: the 0–99.99 gradient scale graphic, plain-language methodology, last run status, editable settings with validation that writes settings.yaml, and a "Run now" button that runs the pipeline in the background and shows progress. Add useful empty states. Add `swing app`. Run it and fix any errors. Commit.

## Phase 9: performance metrics

> Read CLAUDE.md. Implement performance.py: win rate, average win, average loss, profit factor, expectancy, average days held, max drawdown, an equal-weighted equity curve of open picks compounded daily, and SPY over the same period. Wire it into the Performance screen with a monthly returns table. Test the math with a small hand-calculated set of trades. Commit.

## Phase 10: automation

> Read CLAUDE.md. Create scripts/run_daily.sh (cd to repo, `uv run swing run`, log to data/logs) and launchd/com.jawaid.swingtrading.plist scheduled Monday–Friday at 5:30 PM US Eastern, converted to my Mac's local time zone. Holidays are handled by the pipeline. Add scripts/install_launchd.sh to copy the plist to ~/Library/LaunchAgents and load it with launchctl, plus an uninstall option. Document in the README how to check status, view logs, and run manually. Explain how launchd handles a missed run while the Mac is asleep. Commit.

## Later phases (optional)

- **Backtest**: replay the pipeline over historical sessions with the same code paths, with no look-ahead (survivorship bias caveat: today's symbol list doesn't include delisted names).
- **Rankings screen**: browse the full day's SCTR table and the shortlist.
- **Your pivot formula**: replace `pullback_pivot.py`, or add a new detector and switch to it in settings.
- **Position sizing and alerts**: account size, risk per trade, email/Telegram alerts.

## Daily use

- **Manual:** after about 5:00 PM ET run `uv run swing run`, then `uv run swing app`.
- **Weekly:** `uv run swing refresh-universe` (or schedule it Sunday evenings in launchd).
- **Automated:** once Phase 10 is installed, just open the app whenever you want to check.
