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
uv run swing run [--date YYYY-MM-DD] [--dry-run]   # the daily pipeline
uv run swing app                   # launch the Streamlit app
```

These are being built phase by phase; a command that isn't built yet says which phase adds it.

## Development

```bash
uv run pytest          # tests never touch the network
uv run ruff check .
uv run ruff format .
```
