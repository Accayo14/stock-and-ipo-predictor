"""Core data structures shared by every provider.

The single most important idea in this module is DataQuality. During
development we found that Yahoo returns only 29 usable daily bars for
RELIANCE.BO while returning 1240 for RELIANCE.NS. A 14-period RSI computed on
a broken 29-bar series still produces a number - it just isn't a meaningful
one. Silently reporting that number as a "signal" is the worst thing this
tool could do, so every series is graded before analysis and anything that
fails the grade is reported as unavailable rather than guessed at.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Sequence

import numpy as np


# ---------------------------------------------------------------------------
# Quality grading
# ---------------------------------------------------------------------------

@dataclass
class DataQuality:
    """Verdict on whether a price series can be trusted for analysis."""

    bars: int
    last_bar_date: date | None
    staleness_days: int | None
    nan_ratio: float
    usable: bool
    problems: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        if self.usable:
            return f"{self.bars} bars, last {self.last_bar_date}"
        return f"UNUSABLE: {'; '.join(self.problems)}"

    def supports(self, min_bars: int) -> bool:
        """Whether this series is deep enough for an indicator needing min_bars."""
        return self.usable and self.bars >= min_bars


def grade_series(
    dates: Sequence[date],
    close: np.ndarray,
    *,
    min_bars: int,
    max_staleness_days: int,
    max_nan_ratio: float,
    asof: date | None = None,
) -> DataQuality:
    """Grade a price series against the configured quality floor."""
    problems: list[str] = []
    bars = len(dates)
    asof = asof or datetime.now().date()

    if bars == 0:
        return DataQuality(0, None, None, 1.0, False, ["no bars returned"])

    nan_count = int(np.count_nonzero(~np.isfinite(close)))
    nan_ratio = nan_count / bars if bars else 1.0
    last_bar = dates[-1]
    staleness = (asof - last_bar).days

    if bars < min_bars:
        problems.append(f"only {bars} bars (need >= {min_bars})")
    if nan_ratio > max_nan_ratio:
        problems.append(f"{nan_ratio:.0%} of closes missing")
    if staleness > max_staleness_days:
        problems.append(f"last bar {last_bar} is {staleness}d stale")
    # A series that never moves is a dead/suspended listing, not a flat market.
    finite = close[np.isfinite(close)]
    if finite.size and float(np.nanstd(finite)) == 0.0:
        problems.append("price never changes across the series")

    return DataQuality(
        bars=bars,
        last_bar_date=last_bar,
        staleness_days=staleness,
        nan_ratio=nan_ratio,
        usable=not problems,
        problems=problems,
    )


# ---------------------------------------------------------------------------
# Price series
# ---------------------------------------------------------------------------

@dataclass
class PriceSeries:
    """Daily OHLCV history for one instrument."""

    symbol: str
    dates: list[date]
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray
    source: str            # e.g. "yahoo:RELIANCE.NS"
    exchange_used: str     # "NSE" or "BSE" - which tape the history came from
    quality: DataQuality

    def __len__(self) -> int:
        return len(self.dates)

    @property
    def last_close(self) -> float | None:
        finite = self.close[np.isfinite(self.close)]
        return float(finite[-1]) if finite.size else None

    @property
    def last_date(self) -> date | None:
        return self.dates[-1] if self.dates else None

    def returns(self) -> np.ndarray:
        """Simple daily returns, NaN-safe."""
        c = self.close
        out = np.full(len(c), np.nan)
        with np.errstate(invalid="ignore", divide="ignore"):
            out[1:] = (c[1:] - c[:-1]) / c[:-1]
        return out

    def pct_change_over(self, days: int) -> float | None:
        """Percentage change across the last `days` bars."""
        c = self.close[np.isfinite(self.close)]
        if c.size <= days:
            return None
        return float((c[-1] - c[-1 - days]) / c[-1 - days])

    def clean(self) -> PriceSeries:
        """Drop bars with a non-finite close - they break every indicator."""
        mask = np.isfinite(self.close)
        if mask.all():
            return self
        return PriceSeries(
            symbol=self.symbol,
            dates=[d for d, keep in zip(self.dates, mask) if keep],
            open=self.open[mask],
            high=self.high[mask],
            low=self.low[mask],
            close=self.close[mask],
            volume=self.volume[mask],
            source=self.source,
            exchange_used=self.exchange_used,
            quality=self.quality,
        )


# ---------------------------------------------------------------------------
# Point-in-time quote
# ---------------------------------------------------------------------------

@dataclass
class Quote:
    """A point-in-time quote, ideally straight from the BSE tape."""

    symbol: str
    scrip_code: str | None
    company_name: str | None
    last_price: float | None
    change: float | None
    change_pct: float | None
    previous_close: float | None = None
    day_high: float | None = None
    day_low: float | None = None
    volume: float | None = None
    week52_high: float | None = None
    week52_low: float | None = None
    market_cap: float | None = None
    face_value: float | None = None
    group: str | None = None
    index_membership: str | None = None
    source: str = ""
    fetched_at: datetime = field(default_factory=datetime.now)


@dataclass
class Fundamentals:
    """Company fundamentals as published by BSE itself.

    BSE returns "-" for ratios it has not computed; those become None here so
    downstream scoring can distinguish "not available" from "zero".
    """

    scrip_code: str
    symbol: str | None = None
    company_name: str | None = None
    isin: str | None = None
    eps: float | None = None
    pe: float | None = None
    pb: float | None = None
    roe: float | None = None
    ceps: float | None = None
    face_value: float | None = None
    industry: str | None = None
    sector: str | None = None
    group: str | None = None          # BSE group A/B/T/M/MT/X etc.
    index_membership: str | None = None
    settlement_type: str | None = None
    market_cap_cr: float | None = None       # full market cap, INR crore
    market_cap_ff_cr: float | None = None    # free-float market cap, INR crore
    turnover_cr: float | None = None
    traded_qty_lakh: float | None = None
    two_week_avg_qty_lakh: float | None = None
    wap: float | None = None
    source: str = "bse"


# ---------------------------------------------------------------------------
# Provider contract
# ---------------------------------------------------------------------------

class DataProvider:
    """Interface every price/quote source implements.

    Keeping this deliberately small is what lets a broker adapter (Zerodha,
    Upstox, Dhan) be dropped in later without touching the analysis engine.
    """

    name: str = "base"

    def get_history(self, symbol: str, days: int) -> PriceSeries | None:
        raise NotImplementedError

    def get_quote(self, symbol: str) -> Quote | None:
        raise NotImplementedError


class ProviderError(RuntimeError):
    """Raised when a provider fails in a way the caller should surface."""


def trading_days_ago(days: int) -> date:
    """Rough calendar span covering `days` trading sessions (~252/year)."""
    return (datetime.now() - timedelta(days=int(days * 1.45) + 10)).date()
