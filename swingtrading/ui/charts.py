"""Plotly figures for the app. Colours follow CLAUDE.md's UI guidance."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

SLATE = "#2D3748"  # price lines
TEAL = "#4FB99F"  # stop lines
RED = "#E05252"
AMBER = "#F2B33D"
GRID = "#E2E8F0"
SCTR_GRADIENT = f"linear-gradient(90deg, {RED} 0%, {AMBER} 50%, {TEAL} 100%)"


def pick_chart(bars: pd.DataFrame, pick: dict, history: pd.DataFrame) -> go.Figure:
    """Close price line, entry and exit markers, and the stepped dashed stop line."""
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=bars["date"], y=bars["close"], name="Close", mode="lines",
            line={"color": SLATE, "width": 2},
            hovertemplate="%{x|%b %d, %Y}<br>Close %{y:.2f}<extra></extra>",
        )
    )  # fmt: skip
    if not history.empty:
        fig.add_trace(
            go.Scatter(
                x=history["date"], y=history["stop"], name="Stop", mode="lines",
                line={"color": TEAL, "width": 2, "dash": "dash", "shape": "hv"},
                hovertemplate="%{x|%b %d, %Y}<br>Stop %{y:.2f}<extra></extra>",
            )
        )  # fmt: skip
    markers = [
        ("Signal", pick.get("signal_date"), pick.get("signal_close"), "circle-open", SLATE),
        ("Entry", pick.get("entry_date"), pick.get("entry_price"), "triangle-up", TEAL),
        ("Exit", pick.get("exit_date"), pick.get("exit_price"), "triangle-down", RED),
    ]
    for name, day, price, symbol, color in markers:
        if day and price is not None:
            fig.add_trace(
                go.Scatter(
                    x=[pd.Timestamp(day)], y=[price], name=name, mode="markers",
                    marker={"symbol": symbol, "size": 12, "color": color,
                            "line": {"color": color, "width": 2}},
                    hovertemplate=f"{name} %{{x|%b %d, %Y}}<br>%{{y:.2f}}<extra></extra>",
                )
            )  # fmt: skip
    fig.update_layout(
        template="plotly_white",
        height=380,
        margin={"l": 10, "r": 10, "t": 30, "b": 10},
        legend={"orientation": "h", "y": 1.08, "x": 0},
        hovermode="x unified",
        title={"text": pick["ticker"], "x": 0, "font": {"color": SLATE}},
    )
    fig.update_xaxes(gridcolor=GRID, rangebreaks=[{"bounds": ["sat", "mon"]}])
    fig.update_yaxes(gridcolor=GRID, tickformat=".2f")
    return fig


def sctr_scale_html(marker: float | None = None, label: str = "") -> str:
    """The 0-99.99 SCTR scale as a red-amber-teal gradient bar, optionally with a marker."""
    pin = ""
    if marker is not None:
        left = max(0.0, min(100.0, marker / 99.99 * 100))
        pin = (
            f'<div style="position:absolute;left:{left}%;top:-6px;transform:translateX(-50%);'
            f'width:3px;height:30px;background:{SLATE};border-radius:2px"></div>'
            f'<div style="position:absolute;left:{left}%;top:-24px;transform:translateX(-50%);'
            f'font-size:12px;color:{SLATE};white-space:nowrap">{label}</div>'
        )
    ticks = "".join(
        f'<span style="position:absolute;left:{v}%;transform:translateX(-50%)">{t}</span>'
        for v, t in ((0, "0"), (50, "50"), (100, "99.99"))
    )
    return (
        '<div style="position:relative;margin:36px 24px 24px 4px">'
        f'<div style="height:18px;border-radius:9px;background:{SCTR_GRADIENT}"></div>'
        f"{pin}"
        f'<div style="position:relative;height:16px;margin-top:6px;font-size:12px;'
        f'color:#4A5568">{ticks}</div></div>'
    )
