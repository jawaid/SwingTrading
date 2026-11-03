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


EMA_COLORS = ["#7F9CF5", "#B794F4"]  # shorter, longer moving average
SUPPORT = "#A0AEC0"


def pick_chart(
    bars: pd.DataFrame,
    pick: dict,
    history: pd.DataFrame,
    subtitle: str = "",
    support: float | None = None,
) -> go.Figure:
    """Daily candles with moving averages (any `ema_<period>` columns in `bars`), entry and
    exit markers, the stepped dashed stop line and a dotted support line."""
    fig = go.Figure()
    fig.add_trace(
        go.Candlestick(
            x=bars["date"], open=bars["open"], high=bars["high"], low=bars["low"],
            close=bars["close"], name="Price",
            increasing={"line": {"color": TEAL}, "fillcolor": TEAL},
            decreasing={"line": {"color": RED}, "fillcolor": RED},
        )
    )  # fmt: skip
    ema_columns = sorted(
        (c for c in bars.columns if c.startswith("ema_")), key=lambda c: int(c[4:])
    )
    for i, column in enumerate(ema_columns):
        fig.add_trace(
            go.Scatter(
                x=bars["date"], y=bars[column], name=f"{column[4:]} EMA", mode="lines",
                line={"color": EMA_COLORS[i % len(EMA_COLORS)], "width": 1.5},
                hovertemplate=f"{column[4:]} EMA %{{y:.2f}}<extra></extra>",
            )
        )  # fmt: skip
    if support is not None and not bars.empty:
        fig.add_trace(
            go.Scatter(
                x=[bars["date"].iloc[0], bars["date"].iloc[-1]], y=[support, support],
                name=f"Support {support:.2f}", mode="lines",
                line={"color": SUPPORT, "width": 1.5, "dash": "dot"},
                hovertemplate=f"Support {support:.2f}<extra></extra>",
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
    title = f"<b>{pick['ticker']}</b>"
    if subtitle:
        title += f"  <span style='font-size:14px;color:#4A5568'>{subtitle}</span>"
    fig.update_layout(
        template="plotly_white",
        height=460,
        margin={"l": 10, "r": 10, "t": 70, "b": 10},
        legend={"orientation": "h", "y": 1.02, "yanchor": "bottom", "x": 0},
        hovermode="x unified",
        title={"text": title, "x": 0, "y": 0.98, "font": {"color": SLATE}},
        xaxis_rangeslider_visible=False,
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


def equity_chart(equity: pd.Series, benchmark: pd.Series) -> go.Figure:
    """Equal-weighted picks against SPY, both as % change from the start."""
    fig = go.Figure()
    lines = [("Picks", equity, {"color": SLATE, "width": 2}),
             ("SPY", benchmark, {"color": TEAL, "width": 2, "dash": "dot"})]  # fmt: skip
    for name, curve, line in lines:
        if curve.empty:
            continue
        fig.add_trace(
            go.Scatter(
                x=list(curve.index), y=(curve - 1) * 100, name=name, mode="lines", line=line,
                hovertemplate=f"{name} %{{y:+.1f}}%<extra></extra>",
            )
        )  # fmt: skip
    fig.add_hline(y=0, line={"color": GRID, "width": 1})
    fig.update_layout(
        template="plotly_white",
        height=360,
        margin={"l": 10, "r": 10, "t": 30, "b": 10},
        legend={"orientation": "h", "y": 1.08, "x": 0},
        hovermode="x unified",
    )
    fig.update_xaxes(gridcolor=GRID, rangebreaks=[{"bounds": ["sat", "mon"]}])
    fig.update_yaxes(gridcolor=GRID, ticksuffix="%", tickformat=".0f")
    return fig
