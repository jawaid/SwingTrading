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


def support_text(styler: Styler, broken: pd.Series) -> Styler:
    """The Support column at 2 decimals, "Broken" in the `broken` rows and blank when
    empty. Display only: the values stay numbers, so the column sorts by them.

    Known limitation: Streamlit draws an empty (NaN) number cell as "None" whatever the
    Styler says, so a pick with no setup data shows "None", not blank."""
    styler.format("{:.2f}", subset=["support"], na_rep="")
    rows = pd.IndexSlice[broken[broken].index, ["support"]]
    return styler.format(lambda _: "Broken", subset=rows)
