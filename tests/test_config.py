from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from swingtrading.config import (
    DEFAULT_SETTINGS_PATH,
    SETTINGS_ENV_VAR,
    Settings,
    load_settings,
    save_settings,
)


def _write(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


def _defaults() -> dict:
    return yaml.safe_load(DEFAULT_SETTINGS_PATH.read_text())


def test_committed_settings_load_with_domain_defaults():
    s = load_settings(DEFAULT_SETTINGS_PATH)
    assert s.universe.exchanges == ["Q", "N", "A", "P"]
    assert s.universe.min_market_cap == 2e9
    assert s.universe.min_etf_total_assets == 2e9
    assert s.universe.min_avg_dollar_volume == 20e6
    assert s.universe.dollar_volume_period == 50
    assert s.universe.min_history_sessions == 250
    assert s.ranking.shortlist_pct == 15
    assert s.ranking.weights.model_dump() == {
        "long_ema": 30,
        "long_roc": 30,
        "mid_ema": 15,
        "mid_roc": 15,
        "ppo_slope": 5,
        "rsi": 5,
    }
    assert s.setup.swing_high_lookback == 20
    assert (s.setup.pullback_min_pct, s.setup.pullback_max_pct) == (3.0, 12.0)
    assert (s.setup.pullback_min_atr, s.setup.pullback_max_atr) == (1.0, 4.0)
    assert s.setup.max_pullback_sessions == 10
    assert s.stops.atr_period == 14
    assert s.stops.atr_mult == 2.5
    assert s.data.batch_size == 200
    assert s.schedule.run_time == "17:30"


def test_committed_file_matches_model_defaults():
    assert load_settings(DEFAULT_SETTINGS_PATH) == Settings()


def test_empty_file_uses_defaults(tmp_path):
    path = tmp_path / "settings.yaml"
    path.write_text("")
    assert load_settings(path) == Settings()


def test_env_var_selects_file(tmp_path, monkeypatch):
    data = _defaults()
    data["stops"]["atr_mult"] = 3.0
    monkeypatch.setenv(SETTINGS_ENV_VAR, str(_write(tmp_path, data)))
    assert load_settings().stops.atr_mult == 3.0


@pytest.mark.parametrize(
    ("section", "key", "value"),
    [
        ("ranking", "shortlist_pct", 0),
        ("ranking", "shortlist_pct", 150),
        ("stops", "atr_mult", -1),
        ("setup", "pullback_min_pct", 15.0),  # above pullback_max_pct
        ("setup", "pivot_close_range_min", 1.5),
        ("universe", "exchanges", ["Q", "X"]),
        ("schedule", "run_time", "5:30 PM"),
        ("stops", "unknown_key", 1),
    ],
)
def test_invalid_values_rejected(tmp_path, section, key, value):
    data = _defaults()
    data[section][key] = value
    with pytest.raises(ValidationError):
        load_settings(_write(tmp_path, data))


def test_weights_must_sum_to_100(tmp_path):
    data = _defaults()
    data["ranking"]["weights"]["rsi"] = 10
    with pytest.raises(ValidationError, match="sum to 100"):
        load_settings(_write(tmp_path, data))


def test_save_round_trip(tmp_path):
    s = Settings()
    s.stops.atr_mult = 3.0
    path = save_settings(s, tmp_path / "out" / "settings.yaml")
    assert load_settings(path) == s


def test_save_rejects_invalid(tmp_path):
    s = Settings()
    s.setup.pullback_min_pct = 50.0
    path = tmp_path / "settings.yaml"
    with pytest.raises(ValidationError):
        save_settings(s, path)
    assert not path.exists()


def test_relative_paths_resolve_to_project_root():
    s = Settings()
    resolved = s.resolve(s.paths.db_path)
    assert resolved.is_absolute()
    assert resolved.parts[-2:] == ("data", "swing.db")


def test_save_keeps_comments_and_quotes_times(tmp_path):
    path = tmp_path / "settings.yaml"
    path.write_text(DEFAULT_SETTINGS_PATH.read_text())
    s = load_settings(path)
    s.stops.atr_mult = 3.0
    s.schedule.run_time = "18:05"
    save_settings(s, path)
    text = path.read_text()
    assert "# SwingTrading settings. Every threshold" in text
    assert "atr_mult: 3.0" in text and '"18:05"' in text
    again = load_settings(path)
    assert again.schedule.run_time == "18:05" and again.stops.atr_mult == 3.0
