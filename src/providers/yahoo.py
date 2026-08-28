"""Yahoo Finance chart API - the source of daily OHLCV history.

Why this file is more careful than it looks
-------------------------------------------
Yahoo's Indian coverage is inconsistent *per symbol*, not per exchange.
Measured on 2026-08-26 with range=5y&interval=1d:

    RELIANCE.BO   ->    29 bars starting 2026-07-17   (broken)
    RELIANCE.NS   ->  1240 bars starting 2021-08-26   (good)
    TCS.BO        ->  1240 bars                        (good)
    500325.BO     ->  1254 bars but every close null   (garbage)
    ^BSESN        ->  1240 bars                        (good)

So we cannot simply trust ".BO" for a BSE tool, and we cannot simply trust
".NS" either. Instead we fetch each candidate, grade it, and keep the best
usable series - recording which exchange actually supplied it so the report
can tell you. BSE and NSE prices for the same scrip track within ~0.1%
(RELIANCE closed 1299.00 on BSE vs 1298.00 on NSE), which is far below the
noise floor of any indicator here, so NSE history is a sound basis for
technical analysis of a BSE holding.

Note also that `range=max` silently switches to a coarser granularity and
returns FEWER daily bars than `range=5y`. Always request an explicit range.
"""

from __future__ import annotations

from datetime import date, datetime

import numpy as np
import requests

from .base import (
    DataProvider,
    PriceSeries,
    Quote,
    grade_series,
)

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
}

# Explicit ranges only - "max" degrades granularity.
_RANGE_LADDER = [(400, "2y"), (800, "5y"), (2000, "10y")]


def _range_for(days: int) -> str:
    for limit, rng in _RANGE_LADDER:
        if days <= limit:
            return rng
    return "10y"


def _exchange_label(ticker: str) -> str:
    if ticker.startswith("^"):
        return "INDEX"
    return "BSE" if ticker.endswith(".BO") else "NSE"


class YahooProvider(DataProvider):
    name = "yahoo"

    def __init__(self, cache=None, config: dict | None = None) -> None:
        self.cache = cache
        cfg = (config or {}).get("data", {})
        self.min_bars = cfg.get("min_bars_required", 60)
        self.max_staleness = cfg.get("max_staleness_days", 7)
        self.max_nan_ratio = cfg.get("max_nan_ratio", 0.10)
        self.preference = cfg.get("history_ticker_preference", ["NS", "BO"])
        self.cache_hours = cfg.get("cache_hours", 12)
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    # -- raw fetch ---------------------------------------------------------

    def _fetch_chart(self, ticker: str, days: int) -> dict | None:
        cache_key = f"yahoo_chart_{ticker}_{days}_{date.today()}"
        if self.cache:
            cached = self.cache.get(cache_key, ttl_hours=self.cache_hours)
            if cached is not None:
                return cached
        try:
            resp = self.session.get(
                CHART_URL.format(ticker=ticker),
                params={"range": _range_for(days), "interval": "1d"},
                timeout=25,
            )
            resp.raise_for_status()
            payload = resp.json()
        except (requests.RequestException, ValueError):
            return None

        chart = payload.get("chart") or {}
        if chart.get("error") or not chart.get("result"):
            return None
        if self.cache:
            self.cache.set(cache_key, payload)
        return payload

    def _parse(self, payload: dict, ticker: str, days: int) -> PriceSeries | None:
        try:
            result = payload["chart"]["result"][0]
            timestamps = result.get("timestamp") or []
            quote = result["indicators"]["quote"][0]
        except (KeyError, IndexError, TypeError):
            return None
        if not timestamps:
            return None

        def arr(field: str) -> np.ndarray:
            raw = quote.get(field) or []
            raw = list(raw) + [None] * (len(timestamps) - len(raw))
            return np.array(
                [np.nan if v is None else float(v) for v in raw], dtype=float
            )

        dates = [datetime.fromtimestamp(ts).date() for ts in timestamps]
        close = arr("close")

        series = PriceSeries(
            symbol=ticker,
            dates=dates,
            open=arr("open"),
            high=arr("high"),
            low=arr("low"),
            close=close,
            volume=arr("volume"),
            source=f"yahoo:{ticker}",
            exchange_used=_exchange_label(ticker),
            quality=grade_series(
                dates,
                close,
                min_bars=self.min_bars,
                max_staleness_days=self.max_staleness,
                max_nan_ratio=self.max_nan_ratio,
            ),
        ).clean()

        # Trim to the requested depth.
        if len(series) > days:
            keep = slice(len(series) - days, None)
            series = PriceSeries(
                symbol=series.symbol,
                dates=series.dates[keep],
                open=series.open[keep],
                high=series.high[keep],
                low=series.low[keep],
                close=series.close[keep],
                volume=series.volume[keep],
                source=series.source,
                exchange_used=series.exchange_used,
                quality=series.quality,
            )
        return series

    # -- public API --------------------------------------------------------

    def candidates_for(self, symbol: str) -> list[str]:
        """Ticker candidates for a plain BSE symbol, in preference order."""
        base = symbol.upper().replace(".BO", "").replace(".NS", "").strip()
        if base.startswith("^"):          # index, e.g. ^BSESN
            return [base]
        return [f"{base}.{suffix}" for suffix in self.preference]

    def get_history(self, symbol: str, days: int = 400) -> PriceSeries | None:
        """Return the healthiest available series for `symbol`.

        Tries each candidate ticker, grades it, and returns the first usable
        one. If none are usable, returns the deepest series anyway with its
        failing quality attached, so callers can explain *why* a stock could
        not be analysed instead of dropping it silently.
        """
        attempts: list[PriceSeries] = []
        for ticker in self.candidates_for(symbol):
            payload = self._fetch_chart(ticker, days)
            if not payload:
                continue
            series = self._parse(payload, ticker, days)
            if series is None:
                continue
            # Re-grade post-clean: nulls are dropped by clean(), so bar count
            # here reflects genuinely usable data.
            series.quality = grade_series(
                series.dates,
                series.close,
                min_bars=self.min_bars,
                max_staleness_days=self.max_staleness,
                max_nan_ratio=self.max_nan_ratio,
            )
            if series.quality.usable:
                return series
            attempts.append(series)

        return max(attempts, key=len) if attempts else None

    def get_quote(self, symbol: str) -> Quote | None:
        """Quote from chart metadata. BSE's own API is preferred for BSE
        holdings; this exists as a fallback and for the Sensex benchmark.

        Previous close is taken from the second-to-last *bar*, never from
        meta["chartPreviousClose"]. That field means "the close immediately
        before the requested range began", so on a 2y range it reports the
        price from two years ago - which made the Sensex look like it fell
        5% in a day during testing.
        """
        for ticker in self.candidates_for(symbol):
            payload = self._fetch_chart(ticker, 40)
            if not payload:
                continue
            result = payload["chart"]["result"][0]
            meta = result.get("meta", {})
            price = meta.get("regularMarketPrice")
            if price is None:
                continue

            prev = None
            series = self._parse(payload, ticker, 40)
            if series is not None and len(series) >= 2:
                closes = series.close[np.isfinite(series.close)]
                if closes.size >= 2:
                    # If the last bar is today's in-progress session, the prior
                    # bar is yesterday's close; otherwise the last bar itself is.
                    last_is_today = series.dates[-1] == date.today()
                    prev = float(closes[-2] if last_is_today else closes[-1])

            change = (price - prev) if prev else None
            return Quote(
                symbol=symbol,
                scrip_code=None,
                company_name=meta.get("longName") or meta.get("shortName"),
                last_price=float(price),
                change=change,
                change_pct=(change / prev * 100) if change and prev else None,
                previous_close=prev,
                day_high=meta.get("regularMarketDayHigh"),
                day_low=meta.get("regularMarketDayLow"),
                volume=meta.get("regularMarketVolume"),
                week52_high=meta.get("fiftyTwoWeekHigh"),
                week52_low=meta.get("fiftyTwoWeekLow"),
                source=f"yahoo:{ticker}",
            )
        return None
