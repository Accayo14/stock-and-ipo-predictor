"""Technical indicators, computed on numpy arrays.

Two rules govern this module:

1. Every indicator declares how many bars it needs. If the series is too
   short the result is marked unavailable with a reason, rather than
   returning a number computed from insufficient data. An RSI derived from
   20 bars is not a weak signal, it is a meaningless one, and the difference
   must survive all the way into the report.

2. RSI and ATR use Wilder's smoothing (alpha = 1/n), not a simple mean.
   That is the definition every charting platform uses, so our numbers agree
   with what you see on your broker's terminal.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class IndicatorValue:
    name: str
    value: float | None
    available: bool
    min_bars: int
    reason: str = ""

    def __bool__(self) -> bool:
        return self.available and self.value is not None

    def fmt(self, spec: str = ".2f", suffix: str = "") -> str:
        if not self:
            return "n/a"
        return f"{self.value:{spec}}{suffix}"


def _unavailable(name: str, min_bars: int, have: int) -> IndicatorValue:
    return IndicatorValue(
        name, None, False, min_bars,
        f"needs {min_bars} bars, have {have}",
    )


# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------

def sma(values: np.ndarray, period: int) -> np.ndarray:
    """Simple moving average; leading positions are NaN."""
    out = np.full(values.shape, np.nan)
    if len(values) < period:
        return out
    cumsum = np.cumsum(np.insert(values, 0, 0.0))
    out[period - 1:] = (cumsum[period:] - cumsum[:-period]) / period
    return out


def ema(values: np.ndarray, period: int) -> np.ndarray:
    """Exponential moving average seeded with the first SMA."""
    out = np.full(values.shape, np.nan)
    if len(values) < period:
        return out
    alpha = 2.0 / (period + 1.0)
    out[period - 1] = values[:period].mean()
    for i in range(period, len(values)):
        out[i] = alpha * values[i] + (1 - alpha) * out[i - 1]
    return out


def wilder_smooth(values: np.ndarray, period: int) -> np.ndarray:
    """Wilder's smoothing (alpha = 1/period), used by RSI and ATR."""
    out = np.full(values.shape, np.nan)
    if len(values) < period:
        return out
    out[period - 1] = values[:period].mean()
    for i in range(period, len(values)):
        out[i] = (out[i - 1] * (period - 1) + values[i]) / period
    return out


# ---------------------------------------------------------------------------
# Indicators
# ---------------------------------------------------------------------------

def rsi(close: np.ndarray, period: int = 14) -> np.ndarray:
    """Wilder's RSI."""
    out = np.full(close.shape, np.nan)
    if len(close) < period + 1:
        return out
    delta = np.diff(close)
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_gain = wilder_smooth(gain, period)
    avg_loss = wilder_smooth(loss, period)
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = np.divide(avg_gain, avg_loss)
        values = 100.0 - (100.0 / (1.0 + rs))
    # All-gain windows give avg_loss == 0 -> RSI is 100 by definition.
    values = np.where((avg_loss == 0) & (avg_gain > 0), 100.0, values)
    values = np.where((avg_loss == 0) & (avg_gain == 0), 50.0, values)
    out[1:] = values
    return out


def macd(
    close: np.ndarray, fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Returns (macd_line, signal_line, histogram)."""
    macd_line = ema(close, fast) - ema(close, slow)
    valid = macd_line[~np.isnan(macd_line)]
    signal_line = np.full(close.shape, np.nan)
    if len(valid) >= signal:
        smoothed = ema(valid, signal)
        signal_line[-len(smoothed):] = smoothed
    return macd_line, signal_line, macd_line - signal_line


def bollinger(
    close: np.ndarray, period: int = 20, num_std: float = 2.0
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Returns (upper, middle, lower)."""
    middle = sma(close, period)
    std = np.full(close.shape, np.nan)
    for i in range(period - 1, len(close)):
        std[i] = close[i - period + 1: i + 1].std(ddof=0)
    return middle + num_std * std, middle, middle - num_std * std


def atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    """Average True Range, Wilder-smoothed."""
    out = np.full(close.shape, np.nan)
    if len(close) < period + 1:
        return out
    prev_close = close[:-1]
    tr = np.maximum.reduce([
        high[1:] - low[1:],
        np.abs(high[1:] - prev_close),
        np.abs(low[1:] - prev_close),
    ])
    out[1:] = wilder_smooth(tr, period)
    return out


def obv(close: np.ndarray, volume: np.ndarray) -> np.ndarray:
    """On-balance volume: cumulative volume signed by daily direction."""
    out = np.zeros(close.shape)
    direction = np.sign(np.diff(close))
    out[1:] = np.cumsum(direction * volume[1:])
    return out


def slope_pct(values: np.ndarray, period: int) -> float | None:
    """Least-squares slope over the last `period` points, normalised by the
    mean level so it is comparable across instruments."""
    series = values[~np.isnan(values)]
    if len(series) < period or period < 2:
        return None
    window = series[-period:]
    x = np.arange(period, dtype=float)
    slope = np.polyfit(x, window, 1)[0]
    scale = np.abs(window.mean())
    if scale == 0:
        return None
    return float(slope / scale)


def roc(close: np.ndarray, period: int) -> np.ndarray:
    """Rate of change over `period` bars, as a fraction."""
    out = np.full(close.shape, np.nan)
    if len(close) <= period:
        return out
    with np.errstate(invalid="ignore", divide="ignore"):
        out[period:] = (close[period:] - close[:-period]) / close[:-period]
    return out


# ---------------------------------------------------------------------------
# Bundle
# ---------------------------------------------------------------------------

@dataclass
class IndicatorSet:
    """Everything computed for one instrument, with availability preserved."""

    symbol: str
    bars: int
    last_close: float
    values: dict[str, IndicatorValue] = field(default_factory=dict)
    series: dict[str, np.ndarray] = field(default_factory=dict)

    def get(self, name: str) -> IndicatorValue:
        return self.values.get(name, IndicatorValue(name, None, False, 0, "not computed"))

    def available(self, *names: str) -> bool:
        return all(bool(self.get(n)) for n in names)


def _last_finite(values: np.ndarray) -> float | None:
    finite = values[np.isfinite(values)]
    return float(finite[-1]) if finite.size else None


def compute(series, cfg: dict) -> IndicatorSet:
    """Compute the full indicator set for a PriceSeries."""
    close, high, low, volume = series.close, series.high, series.low, series.volume
    bars = len(close)
    out = IndicatorSet(symbol=series.symbol, bars=bars, last_close=series.last_close or 0.0)

    def record(name: str, value: float | None, min_bars: int) -> None:
        if bars < min_bars:
            out.values[name] = _unavailable(name, min_bars, bars)
        elif value is None or not np.isfinite(value):
            out.values[name] = IndicatorValue(
                name, None, False, min_bars, "computed to a non-finite value"
            )
        else:
            out.values[name] = IndicatorValue(name, float(value), True, min_bars)

    # -- trend
    s_short, s_long = cfg["sma_short"], cfg["sma_long"]
    sma_s, sma_l = sma(close, s_short), sma(close, s_long)
    ema_s = ema(close, cfg["ema_short"])
    out.series["sma_short"], out.series["sma_long"] = sma_s, sma_l
    record("sma_short", _last_finite(sma_s), s_short)
    record("sma_long", _last_finite(sma_l), s_long)
    record("ema_short", _last_finite(ema_s), cfg["ema_short"])

    # -- momentum
    period = cfg["rsi_period"]
    rsi_series = rsi(close, period)
    out.series["rsi"] = rsi_series
    record("rsi", _last_finite(rsi_series), period + 1)

    macd_line, signal_line, hist = macd(
        close, cfg["macd_fast"], cfg["macd_slow"], cfg["macd_signal"]
    )
    out.series["macd"], out.series["macd_signal"], out.series["macd_hist"] = (
        macd_line, signal_line, hist
    )
    macd_min = cfg["macd_slow"] + cfg["macd_signal"]
    record("macd", _last_finite(macd_line), macd_min)
    record("macd_hist", _last_finite(hist), macd_min)
    record("roc", _last_finite(roc(close, cfg["roc_period"])), cfg["roc_period"] + 1)

    # -- volatility / mean reversion
    up, mid, lo = bollinger(close, cfg["bollinger_period"], cfg["bollinger_std"])
    out.series["bb_upper"], out.series["bb_mid"], out.series["bb_lower"] = up, mid, lo
    last_up, last_lo = _last_finite(up), _last_finite(lo)
    bb_pos = None
    if last_up is not None and last_lo is not None and last_up > last_lo:
        bb_pos = (close[-1] - last_lo) / (last_up - last_lo)
    record("bb_position", bb_pos, cfg["bollinger_period"])

    atr_series = atr(high, low, close, cfg["atr_period"])
    out.series["atr"] = atr_series
    last_atr = _last_finite(atr_series)
    record("atr", last_atr, cfg["atr_period"] + 1)
    record(
        "atr_pct",
        (last_atr / close[-1]) if last_atr and close[-1] else None,
        cfg["atr_period"] + 1,
    )

    # -- volume
    obv_series = obv(close, volume)
    out.series["obv"] = obv_series
    record("obv_slope", slope_pct(obv_series, cfg["obv_slope_period"]), cfg["obv_slope_period"])

    vol_finite = volume[np.isfinite(volume) & (volume > 0)]
    if vol_finite.size >= 20:
        record("volume_ratio", float(vol_finite[-1] / vol_finite[-20:].mean()), 20)
    else:
        record("volume_ratio", None, 20)

    # -- 52-week position (0 = at the low, 1 = at the high)
    window = close[-252:] if bars >= 252 else close
    hi, lw = float(np.nanmax(window)), float(np.nanmin(window))
    record("week52_position", ((close[-1] - lw) / (hi - lw)) if hi > lw else None, 60)
    record("week52_high", hi, 60)
    record("week52_low", lw, 60)

    return out


def relative_strength(stock, benchmark, period: int) -> IndicatorValue:
    """Stock return minus benchmark return over `period` trading days.

    Positive means the stock outperformed the Sensex - the thing that
    actually distinguishes a good holding from one merely carried by a
    rising market.
    """
    name = "relative_strength"
    stock_move = stock.pct_change_over(period)
    bench_move = benchmark.pct_change_over(period) if benchmark else None
    if stock_move is None:
        return IndicatorValue(name, None, False, period + 1,
                              f"needs {period + 1} bars, have {len(stock)}")
    if bench_move is None:
        return IndicatorValue(name, None, False, period + 1,
                              "benchmark history unavailable")
    return IndicatorValue(name, float(stock_move - bench_move), True, period + 1)
