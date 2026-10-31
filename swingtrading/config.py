"""Load, validate and save config/settings.yaml."""

from __future__ import annotations

import io
import os
import re
from pathlib import Path
from typing import Any, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, PositiveFloat, PositiveInt, model_validator

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.yaml"
SETTINGS_ENV_VAR = "SWING_SETTINGS"

VALID_EXCHANGES = {"Q", "N", "A", "P"}


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PathSettings(_Section):
    db_path: Path = Path("data/swing.db")
    log_dir: Path = Path("data/logs")
    cache_dir: Path = Path("data/cache")


class UniverseSettings(_Section):
    exchanges: list[str] = Field(default_factory=lambda: ["Q", "N", "A", "P"], min_length=1)
    min_market_cap: PositiveFloat = 2e9
    min_etf_total_assets: PositiveFloat = 2e9
    min_avg_dollar_volume: PositiveFloat = 20e6
    dollar_volume_period: PositiveInt = 50
    min_history_sessions: PositiveInt = 250
    metadata_refresh_days: PositiveInt = 7

    @model_validator(mode="after")
    def _check_exchanges(self) -> Self:
        unknown = set(self.exchanges) - VALID_EXCHANGES
        if unknown:
            raise ValueError(
                f"unknown exchange codes {sorted(unknown)}; use {sorted(VALID_EXCHANGES)}"
            )
        return self


class RankingWeights(_Section):
    long_ema: float = Field(30, ge=0)
    long_roc: float = Field(30, ge=0)
    mid_ema: float = Field(15, ge=0)
    mid_roc: float = Field(15, ge=0)
    ppo_slope: float = Field(5, ge=0)
    rsi: float = Field(5, ge=0)

    @model_validator(mode="after")
    def _check_total(self) -> Self:
        total = sum(self.model_dump().values())
        if abs(total - 100) > 1e-9:
            raise ValueError(f"ranking weights must sum to 100, got {total}")
        return self


class RankingSettings(_Section):
    shortlist_pct: float = Field(15, gt=0, le=100)
    long_ema_period: PositiveInt = 200
    long_roc_period: PositiveInt = 125
    mid_ema_period: PositiveInt = 50
    mid_roc_period: PositiveInt = 20
    ppo_fast: PositiveInt = 12
    ppo_slow: PositiveInt = 26
    ppo_signal: PositiveInt = 9
    ppo_slope_period: PositiveInt = 3
    rsi_period: PositiveInt = 14
    weights: RankingWeights = Field(default_factory=RankingWeights)

    @model_validator(mode="after")
    def _check_ppo(self) -> Self:
        if self.ppo_fast >= self.ppo_slow:
            raise ValueError("ppo_fast must be smaller than ppo_slow")
        return self


class SetupSettings(_Section):
    detector: str = "pullback_pivot"
    swing_high_lookback: PositiveInt = 20
    pullback_min_pct: float = Field(3.0, ge=0)
    pullback_max_pct: float = Field(12.0, gt=0, lt=100)
    pullback_min_atr: float = Field(1.0, ge=0)
    pullback_max_atr: PositiveFloat = 4.0
    max_pullback_sessions: PositiveInt = 10
    down_volume_max_ratio: PositiveFloat = 1.0
    volume_avg_period: PositiveInt = 50
    trend_ema_period: PositiveInt = 50
    pivot_close_range_min: float = Field(0.5, ge=0, le=1)
    pivot_volume_period: PositiveInt = 20
    pivot_volume_min_ratio: float = Field(1.0, ge=0)

    @model_validator(mode="after")
    def _check_ranges(self) -> Self:
        if self.pullback_min_pct >= self.pullback_max_pct:
            raise ValueError("pullback_min_pct must be smaller than pullback_max_pct")
        if self.pullback_min_atr >= self.pullback_max_atr:
            raise ValueError("pullback_min_atr must be smaller than pullback_max_atr")
        return self


class StopSettings(_Section):
    atr_period: PositiveInt = 14
    atr_mult: PositiveFloat = 2.5


class DataSettings(_Section):
    backfill_sessions: PositiveInt = 400
    adjustment_check_sessions: PositiveInt = 5
    adjustment_tolerance: float = Field(0.0005, gt=0)
    batch_size: int = Field(200, ge=1, le=500)
    max_retries: int = Field(3, ge=0)
    retry_backoff_seconds: float = Field(2.0, ge=0)
    info_throttle_seconds: float = Field(0.5, ge=0)
    request_timeout_seconds: float = Field(60.0, gt=0)


class ScheduleSettings(_Section):
    timezone: str = "America/New_York"
    run_time: str = "17:30"
    close_buffer_minutes: int = Field(30, ge=0)

    @model_validator(mode="after")
    def _check_run_time(self) -> Self:
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", self.run_time):
            raise ValueError("run_time must be HH:MM in 24-hour time")
        return self


class Settings(_Section):
    paths: PathSettings = Field(default_factory=PathSettings)
    universe: UniverseSettings = Field(default_factory=UniverseSettings)
    ranking: RankingSettings = Field(default_factory=RankingSettings)
    setup: SetupSettings = Field(default_factory=SetupSettings)
    stops: StopSettings = Field(default_factory=StopSettings)
    data: DataSettings = Field(default_factory=DataSettings)
    schedule: ScheduleSettings = Field(default_factory=ScheduleSettings)

    def resolve(self, path: Path) -> Path:
        """Resolve a configured path relative to the project root."""
        return path if path.is_absolute() else PROJECT_ROOT / path


def settings_path(path: Path | str | None = None) -> Path:
    """The settings file to use: explicit path, then $SWING_SETTINGS, then the default."""
    if path is not None:
        return Path(path)
    env = os.environ.get(SETTINGS_ENV_VAR)
    return Path(env) if env else DEFAULT_SETTINGS_PATH


def load_settings(path: Path | str | None = None) -> Settings:
    """Read and validate settings. Raises pydantic.ValidationError on bad values."""
    raw = yaml.safe_load(settings_path(path).read_text()) or {}
    return Settings.model_validate(raw)


def _safe_scalar(value: Any) -> Any:
    """Quote strings PyYAML would read back as something else (e.g. 17:30 is an int)."""
    from ruamel.yaml.scalarstring import DoubleQuotedScalarString

    if isinstance(value, str) and yaml.safe_load(value) != value:
        return DoubleQuotedScalarString(value)
    return value


def _merge(node: Any, values: dict[str, Any]) -> None:
    """Update a ruamel mapping in place so its comments and layout survive."""
    for key, value in values.items():
        if isinstance(value, dict) and isinstance(node.get(key), dict):
            _merge(node[key], value)
        elif node.get(key) != value:
            node[key] = _safe_scalar(value)


def save_settings(settings: Settings, path: Path | str | None = None) -> Path:
    """Validate and write settings back to YAML, keeping the file's comments.
    Returns the path written."""
    from ruamel.yaml import YAML

    target = settings_path(path)
    Settings.model_validate(settings.model_dump())
    data = settings.model_dump(mode="json")
    rt = YAML()
    rt.preserve_quotes = True
    rt.width = 100
    doc = rt.load(target.read_text()) if target.exists() else None
    target.parent.mkdir(parents=True, exist_ok=True)
    if doc is None:
        target.write_text(yaml.safe_dump(data, sort_keys=False))
        return target
    _merge(doc, data)
    buf = io.StringIO()
    rt.dump(doc, buf)
    target.write_text(buf.getvalue())
    return target
