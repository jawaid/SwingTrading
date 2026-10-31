"""Flatten settings into editable fields and validate edits back into Settings."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel

from swingtrading.config import Settings

EDITABLE_SECTIONS = ["universe", "ranking", "setup", "stops", "data", "schedule"]


def label(name: str) -> str:
    """Sentence-case label from a field name: min_market_cap -> Min market cap."""
    text = name.replace("_", " ")
    for short, full in (("pct", "%"), ("atr", "ATR"), ("ema", "EMA"), ("roc", "ROC"),
                        ("rsi", "RSI"), ("ppo", "PPO"), ("etf", "ETF")):  # fmt: skip
        text = " ".join(full if w == short else w for w in text.split())
    return text[0].upper() + text[1:]


def fields(model: BaseModel, prefix: tuple[str, ...] = ()) -> list[tuple[tuple[str, ...], Any]]:
    """(path, value) for every leaf field, nested models flattened."""
    out = []
    for name in type(model).model_fields:
        value = getattr(model, name)
        if isinstance(value, BaseModel):
            out += fields(value, (*prefix, name))
        else:
            out.append(((*prefix, name), value))
    return out


def apply(settings: Settings, edits: dict[tuple[str, ...], Any]) -> Settings:
    """A new Settings with `edits` applied. Raises pydantic.ValidationError if invalid."""
    data = settings.model_dump(mode="json")
    for path, value in edits.items():
        node = data
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = str(value) if isinstance(value, Path) else value
    return Settings.model_validate(data)
