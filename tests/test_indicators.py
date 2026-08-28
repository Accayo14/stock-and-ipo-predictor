"""Correctness tests for the indicator maths.

Each indicator is checked against an independent reference: pandas for the
moving averages, and a deliberately naive textbook loop for the Wilder-
smoothed ones. If these two disagree, the fast implementation is wrong.

Run: python tests/test_indicators.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from src.analysis import indicators as ind  # noqa: E402

rng = np.random.default_rng(42)
N = 300
# A realistic-ish price path: geometric random walk with drift.
close = 1000 * np.exp(np.cumsum(rng.normal(0.0004, 0.014, N)))
high = close * (1 + np.abs(rng.normal(0, 0.006, N)))
low = close * (1 - np.abs(rng.normal(0, 0.006, N)))
volume = rng.integers(50_000, 500_000, N).astype(float)

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}{('  -> ' + detail) if detail and not condition else ''}")
    if not condition:
        failures.append(label)


def close_enough(a, b, tol=1e-9) -> bool:
    if a is None or b is None:
        return False
    return abs(a - b) < tol


print("SMA vs pandas.rolling")
for period in (5, 20, 50, 200):
    mine = ind.sma(close, period)
    ref = pd.Series(close).rolling(period).mean().to_numpy()
    both = ~np.isnan(mine) & ~np.isnan(ref)
    check(f"sma({period})", bool(both.any()) and np.allclose(mine[both], ref[both]),
          f"max diff {np.nanmax(np.abs(mine[both] - ref[both])) if both.any() else 'n/a'}")

print("\nEMA seeded with SMA vs pandas ewm(adjust=False) seeded identically")
for period in (12, 26):
    mine = ind.ema(close, period)
    seed = close[:period].mean()
    ref = np.full(N, np.nan)
    ref[period - 1] = seed
    alpha = 2.0 / (period + 1.0)
    for i in range(period, N):
        ref[i] = alpha * close[i] + (1 - alpha) * ref[i - 1]
    both = ~np.isnan(mine) & ~np.isnan(ref)
    check(f"ema({period})", np.allclose(mine[both], ref[both]))

print("\nRSI vs naive textbook Wilder loop")
period = 14
mine = ind.rsi(close, period)
delta = np.diff(close)
gains = np.where(delta > 0, delta, 0.0)
losses = np.where(delta < 0, -delta, 0.0)
ref = np.full(N, np.nan)
avg_g = gains[:period].mean()
avg_l = losses[:period].mean()
ref[period] = 100.0 if avg_l == 0 else 100 - 100 / (1 + avg_g / avg_l)
for i in range(period + 1, N):
    avg_g = (avg_g * (period - 1) + gains[i - 1]) / period
    avg_l = (avg_l * (period - 1) + losses[i - 1]) / period
    ref[i] = 100.0 if avg_l == 0 else 100 - 100 / (1 + avg_g / avg_l)
both = ~np.isnan(mine) & ~np.isnan(ref)
check("rsi(14) matches reference", np.allclose(mine[both], ref[both], atol=1e-9),
      f"max diff {np.nanmax(np.abs(mine[both] - ref[both])):.2e}" if both.any() else "no overlap")
check("rsi within [0,100]", bool(np.all((mine[both] >= 0) & (mine[both] <= 100))))

print("\nRSI boundary behaviour")
rising = np.arange(1.0, 60.0)
check("monotonic rise -> RSI 100", close_enough(ind.rsi(rising, 14)[-1], 100.0, 1e-6),
      f"got {ind.rsi(rising, 14)[-1]}")
falling = np.arange(60.0, 1.0, -1.0)
check("monotonic fall -> RSI 0", close_enough(ind.rsi(falling, 14)[-1], 0.0, 1e-6),
      f"got {ind.rsi(falling, 14)[-1]}")
flat = np.full(60, 100.0)
check("flat series -> RSI 50 (not NaN/0)", close_enough(ind.rsi(flat, 14)[-1], 50.0, 1e-6),
      f"got {ind.rsi(flat, 14)[-1]}")

print("\nATR vs naive Wilder loop")
period = 14
mine = ind.atr(high, low, close, period)
tr = np.full(N, np.nan)
for i in range(1, N):
    tr[i] = max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
ref = np.full(N, np.nan)
ref[period] = np.nanmean(tr[1:period + 1])
for i in range(period + 1, N):
    ref[i] = (ref[i - 1] * (period - 1) + tr[i]) / period
both = ~np.isnan(mine) & ~np.isnan(ref)
check("atr(14) matches reference", np.allclose(mine[both], ref[both], atol=1e-9))
check("atr strictly positive", bool(np.all(mine[both] > 0)))

print("\nBollinger")
up, mid, lo = ind.bollinger(close, 20, 2.0)
both = ~np.isnan(up)
check("upper > mid > lower", bool(np.all(up[both] > mid[both]) and np.all(mid[both] > lo[both])))
ref_mid = pd.Series(close).rolling(20).mean().to_numpy()
check("bollinger mid == sma(20)", np.allclose(mid[both], ref_mid[both]))

print("\nInsufficient-data handling (the RELIANCE.BO failure mode)")
cfg = {
    "rsi_period": 14, "macd_fast": 12, "macd_slow": 26, "macd_signal": 9,
    "sma_short": 50, "sma_long": 200, "ema_short": 20, "bollinger_period": 20,
    "bollinger_std": 2.0, "atr_period": 14, "roc_period": 21, "obv_slope_period": 20,
}


class FakeSeries:
    """Minimal stand-in for PriceSeries."""

    def __init__(self, n):
        self.symbol = f"SHORT{n}"
        self.close = close[:n]
        self.high = high[:n]
        self.low = low[:n]
        self.volume = volume[:n]
        self.last_close = float(close[n - 1])

    def __len__(self):
        return len(self.close)


short = ind.compute(FakeSeries(29), cfg)   # exactly the broken RELIANCE.BO depth
check("29 bars -> sma_long unavailable", not short.get("sma_long").available)
check("29 bars -> sma_long value is None", short.get("sma_long").value is None)
check("29 bars -> macd unavailable", not short.get("macd").available)
check("29 bars -> rsi still available (needs 15)", short.get("rsi").available)
check("29 bars -> reason explains the gap",
      "needs 200" in short.get("sma_long").reason, short.get("sma_long").reason)

full = ind.compute(FakeSeries(300), cfg)
check("300 bars -> sma_long available", full.get("sma_long").available)
check("300 bars -> macd available", full.get("macd").available)
check("300 bars -> bb_position in [0,1]-ish",
      full.get("bb_position").available and -0.5 <= full.get("bb_position").value <= 1.5)
check("IndicatorValue is falsy when unavailable", not bool(short.get("sma_long")))
check("fmt() renders n/a when unavailable", short.get("sma_long").fmt() == "n/a")

print("\n" + "=" * 60)
if failures:
    print(f"{len(failures)} FAILURE(S): {failures}")
    sys.exit(1)
print("All indicator tests passed.")
