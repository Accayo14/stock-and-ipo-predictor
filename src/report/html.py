"""HTML rendering of the morning report.

Charts are generated as inline SVG rather than pulling in a charting library,
so the report is a single self-contained file that opens anywhere - offline,
on your phone, or attached to an email - with no CDN and no build step.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from jinja2 import Environment, FileSystemLoader, select_autoescape

TEMPLATES = Path(__file__).resolve().parents[2] / "templates"


def rupees(value: float | None) -> str:
    """Indian digit grouping."""
    if value is None:
        return "—"
    negative = value < 0
    whole, _, frac = f"{abs(value):.2f}".partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join(parts + [tail])
    return f"{'−' if negative else ''}₹{whole}.{frac}"


def sparkline(
    values, width: int = 260, height: int = 56, overlay=None
) -> str:
    """Inline SVG sparkline. `overlay` draws a second (moving-average) line."""
    series = np.asarray([v for v in values if v is not None and np.isfinite(v)], dtype=float)
    if series.size < 2:
        return ""
    low, high = float(series.min()), float(series.max())
    span = (high - low) or 1.0
    pad = 3

    def to_points(data) -> str:
        data = np.asarray(data, dtype=float)
        n = len(data)
        step = (width - 2 * pad) / max(1, n - 1)
        pts = []
        for i, v in enumerate(data):
            if not np.isfinite(v):
                continue
            x = pad + i * step
            y = height - pad - ((v - low) / span) * (height - 2 * pad)
            pts.append(f"{x:.1f},{y:.1f}")
        return " ".join(pts)

    rising = series[-1] >= series[0]
    colour = "var(--up)" if rising else "var(--down)"
    parts = [
        f'<svg class="spark" viewBox="0 0 {width} {height}" width="{width}" '
        f'height="{height}" preserveAspectRatio="none" aria-hidden="true">'
    ]
    area = to_points(series)
    parts.append(
        f'<polyline points="{area}" fill="none" stroke="{colour}" '
        f'stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round"/>'
    )
    if overlay is not None:
        ov = np.asarray(overlay, dtype=float)
        ov = ov[-len(series):] if len(ov) >= len(series) else ov
        if np.isfinite(ov).sum() >= 2:
            parts.append(
                f'<polyline points="{to_points(ov)}" fill="none" '
                f'stroke="var(--muted)" stroke-width="1" stroke-dasharray="3 3"/>'
            )
    parts.append("</svg>")
    return "".join(parts)


def _series_payload(analysis, bars: int = 120) -> dict:
    series = getattr(analysis, "series", None)
    indicators = getattr(analysis, "indicators", None)
    if series is None or len(series) < 2:
        return {"svg": "", "bars": 0}
    closes = series.close[-bars:]
    overlay = None
    if indicators and "sma_short" in indicators.series:
        sma = indicators.series["sma_short"]
        overlay = sma[-bars:] if len(sma) >= bars else sma
    return {
        "svg": sparkline(closes, overlay=overlay),
        "bars": len(series),
        "window": len(closes),
        "source": series.source,
        "exchange": series.exchange_used,
    }


def render(report, out_path: Path) -> Path:
    env = Environment(
        loader=FileSystemLoader(TEMPLATES),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["rupees"] = rupees
    env.filters["pct"] = lambda v, d=1: ("—" if v is None else f"{v:+.{d}%}")
    env.filters["pct_plain"] = lambda v, d=0: ("—" if v is None else f"{v:.{d}%}")

    ordered = sorted(
        report.positions,
        key=lambda a: {"EXIT": 0, "TRIM": 1, "STRONG BUY": 2,
                       "ACCUMULATE": 3, "HOLD": 4}.get(a.final_action, 9),
    )
    charts = {a.position.symbol: _series_payload(a) for a in ordered}

    market_chart = ""
    bench = getattr(report, "_benchmark_series", None)
    if bench is not None and len(bench) > 2:
        market_chart = sparkline(bench.close[-120:], width=320, height=64)

    html = env.get_template("report.html.j2").render(
        report=report,
        positions=ordered,
        charts=charts,
        market_chart=market_chart,
        counts={
            action: len([a for a in report.positions if a.final_action == action])
            for action in ["STRONG BUY", "ACCUMULATE", "HOLD", "TRIM", "EXIT"]
        },
    )
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return out_path
