"""Table styling shared by the screens."""

from __future__ import annotations

import pandas as pd
from pandas.io.formats.style import Styler

NEGATIVE_RED = "#c0392b"


def red_negatives(df: pd.DataFrame, columns: list[str]) -> Styler:
    """`df` with negative numbers in `columns` shown in red. Formatting stays with the
    table's column_config."""
    return df.style.map(
        lambda v: f"color: {NEGATIVE_RED}" if pd.notna(v) and v < 0 else "",
        subset=[c for c in columns if c in df.columns],
    )
