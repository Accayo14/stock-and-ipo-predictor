"""BSE official data (api.bseindia.com + bhavcopy).

This is the authoritative source for anything BSE-specific: the traded price
on the BSE tape, scrip codes, BSE-published fundamentals, and the daily
bhavcopy. Yahoo supplies deep OHLCV history; BSE supplies truth about BSE.

All endpoints here were verified working on 2026-08-26. api.bseindia.com
answers unknown routes with a ~1.8KB Angular shell and HTTP 200, so every
response is checked for that shell before being treated as data - otherwise a
dead route looks like a successful fetch.
"""

from __future__ import annotations

import io
import re
from datetime import date, datetime, timedelta

import pandas as pd
import requests

from .base import DataProvider, Fundamentals, Quote

API = "https://api.bseindia.com/BseIndiaAPI/api"
MSOURCE = "https://api.bseindia.com/Msource/1D"
BHAVCOPY = (
    "https://www.bseindia.com/download/BhavCopy/Equity/"
    "BhavCopy_BSE_CM_0_0_0_{yyyymmdd}_F_0000.CSV"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bseindia.com/",
    "Origin": "https://www.bseindia.com",
    "Accept": "application/json, text/plain, */*",
}


def _num(value) -> float | None:
    """Parse BSE's numbers: Indian grouping, '-' for missing, '(Cr.)' units.

    Examples: '17,57,876.78' -> 1757876.78 ; '-' -> None ; '' -> None
    """
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if text in ("", "-", "NA", "N.A.", "null"):
        return None
    match = re.search(r"-?\d+\.?\d*", text)
    if not match:
        return None
    try:
        return float(match.group())
    except ValueError:
        return None


class BSEProvider(DataProvider):
    name = "bse"

    def __init__(self, cache=None, config: dict | None = None) -> None:
        self.cache = cache
        self.cache_hours = (config or {}).get("data", {}).get("cache_hours", 12)
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self._master: pd.DataFrame | None = None

    # -- low level ---------------------------------------------------------

    def _api(self, route: str, params: dict, ttl: float | None = None) -> dict | None:
        """GET a BSE API route, rejecting the SPA-shell fallback."""
        key = f"bse_{route}_{sorted(params.items())}_{date.today()}"
        if self.cache:
            hit = self.cache.get(key, ttl_hours=ttl if ttl is not None else self.cache_hours)
            if hit is not None:
                return hit
        try:
            resp = self.session.get(f"{API}/{route}/w", params=params, timeout=20)
            if resp.status_code != 200:
                return None
            body = resp.text
            # Unknown routes return the Angular shell with a 200.
            if body.lstrip()[:9].lower().startswith("<!doctype"):
                return None
            data = resp.json()
        except (requests.RequestException, ValueError):
            return None
        if self.cache:
            self.cache.set(key, data)
        return data

    # -- scrip master via bhavcopy ----------------------------------------

    def get_bhavcopy(self, on: date | None = None, lookback: int = 6) -> pd.DataFrame | None:
        """Whole-market BSE EOD for the most recent available session.

        The bhavcopy for a session is published after the close, so on a
        morning run "today" will usually 404 - we walk back to the last
        published file. That file is also the cleanest symbol -> scrip_code
        map available, covering every listed scrip in a single request.
        """
        start = on or date.today()
        for offset in range(lookback):
            day = start - timedelta(days=offset)
            if day.weekday() >= 5:            # skip Sat/Sun
                continue
            stamp = day.strftime("%Y%m%d")
            key = f"bse_bhavcopy_{stamp}"
            raw = self.cache.get(key, ttl_hours=24 * 7) if self.cache else None
            if raw is None:
                try:
                    resp = self.session.get(BHAVCOPY.format(yyyymmdd=stamp), timeout=45)
                except requests.RequestException:
                    continue
                if resp.status_code != 200 or "," not in resp.text[:200]:
                    continue
                raw = resp.text
                if self.cache:
                    self.cache.set(key, raw)
            try:
                frame = pd.read_csv(io.StringIO(raw))
            except (pd.errors.ParserError, ValueError):
                continue
            frame["_session"] = day.isoformat()
            return frame
        return None

    def scrip_master(self) -> pd.DataFrame | None:
        """Cached symbol/ISIN/scrip-code table for equities."""
        if self._master is not None:
            return self._master
        frame = self.get_bhavcopy()
        if frame is None:
            return None
        needed = {"TckrSymb", "FinInstrmId", "ISIN", "FinInstrmNm", "FinInstrmTp"}
        if not needed.issubset(frame.columns):
            return None
        equities = frame[frame["FinInstrmTp"] == "STK"].copy()
        equities["TckrSymb"] = equities["TckrSymb"].astype(str).str.upper().str.strip()
        self._master = equities
        return self._master

    @staticmethod
    def _row_to_resolution(row, source: str) -> dict:
        return {
            "scrip_code": str(row["FinInstrmId"]),
            "symbol": str(row["TckrSymb"]).upper(),
            "isin": str(row.get("ISIN", "")),
            "name": str(row.get("FinInstrmNm", "")),
            "bse_close": _num(row.get("ClsPric")),
            "bse_prev_close": _num(row.get("PrvsClsgPric")),
            "session": row.get("_session"),
            "source": source,
        }

    def resolve(self, symbol: str, isin: str | None = None) -> dict | None:
        """symbol -> {scrip_code, isin, name, session close...}.

        Order: exact symbol in the bhavcopy master, then ISIN (survives
        renames), then BSE's autocomplete endpoint.

        Returns None only when nothing matched - call suggest() to offer the
        user alternatives. Symbols really do disappear: Tata Motors demerged,
        so "TATAMOTORS" resolves to nothing while TMPV (500570) and TMCV
        (544569) are the live scrips. Silently dropping such a holding from a
        portfolio report would be worse than failing loudly.
        """
        target = symbol.upper().strip()
        master = self.scrip_master()
        if master is not None:
            hit = master[master["TckrSymb"] == target]
            if not hit.empty:
                return self._row_to_resolution(hit.iloc[0], "bhavcopy")
            if isin:
                hit = master[master["ISIN"].astype(str).str.upper() == isin.upper().strip()]
                if not hit.empty:
                    return self._row_to_resolution(hit.iloc[0], "bhavcopy:isin")
        return self._resolve_via_search(target)

    def suggest(self, symbol: str, limit: int = 5) -> list[dict]:
        """Closest matching live scrips, for when resolve() finds nothing."""
        master = self.scrip_master()
        if master is None:
            return []
        target = symbol.upper().strip()
        scored: list[tuple[float, dict]] = []
        symbols = master["TckrSymb"].astype(str)
        names = master["FinInstrmNm"].astype(str).str.upper()

        # Substring hits on symbol or company name rank first.
        mask = symbols.str.contains(target, na=False, regex=False) | names.str.contains(
            target, na=False, regex=False
        )
        for _, row in master[mask].head(limit * 3).iterrows():
            scored.append((1.0, self._row_to_resolution(row, "suggest:substring")))

        if len(scored) < limit:
            import difflib

            close = difflib.get_close_matches(target, symbols.tolist(), n=limit, cutoff=0.6)
            for candidate in close:
                row = master[symbols == candidate].iloc[0]
                scored.append((0.8, self._row_to_resolution(row, "suggest:fuzzy")))

        seen: set[str] = set()
        out: list[dict] = []
        for _, item in sorted(scored, key=lambda s: -s[0]):
            if item["scrip_code"] in seen:
                continue
            seen.add(item["scrip_code"])
            out.append(item)
            if len(out) >= limit:
                break
        return out

    def _resolve_via_search(self, symbol: str) -> dict | None:
        """Fallback: BSE's quote autocomplete returns <li> HTML with the code."""
        try:
            resp = self.session.get(
                f"{MSOURCE}/getQouteSearch.aspx",
                params={"Type": "EQ", "text": symbol, "flag": "site"},
                timeout=20,
            )
            if resp.status_code != 200:
                return None
            html = resp.text
        except requests.RequestException:
            return None
        # href pattern: /stock-share-price/<name>/<symbol>/<scripcode>/
        match = re.search(r"/stock-share-price/([^/]+)/([^/]+)/(\d{6})/", html)
        if not match:
            return None
        isin = re.search(r"(INE[0-9A-Z]{9})", html)
        return {
            "scrip_code": match.group(3),
            "symbol": match.group(2).upper(),
            "isin": isin.group(1) if isin else None,
            "name": match.group(1).replace("-", " ").title(),
            "source": "bse_search",
        }

    # -- quote & fundamentals ---------------------------------------------

    def get_quote(self, symbol: str, scrip_code: str | None = None) -> Quote | None:
        """Live/last BSE quote. Short TTL - this is the one thing we want fresh."""
        if not scrip_code:
            resolved = self.resolve(symbol)
            if not resolved:
                return None
            scrip_code = resolved["scrip_code"]

        header = self._api(
            "getScripHeaderData",
            {"Debtflag": "", "scripcode": scrip_code, "seriesid": ""},
            ttl=0.25,
        )
        if not header:
            return None
        head = header.get("Header", {}) or {}
        rate = header.get("CurrRate", {}) or {}
        name = (header.get("Cmpname", {}) or {}).get("FullN")

        hl = self._api("HighLow", {"Type": "EQ", "flag": "C", "scripcode": scrip_code}) or {}
        trading = self._api(
            "StockTrading",
            {"flag": "", "quotetype": "EQ", "scripcode": scrip_code, "seriesid": ""},
        ) or {}

        return Quote(
            symbol=symbol.upper(),
            scrip_code=scrip_code,
            company_name=name,
            last_price=_num(head.get("LTP") or rate.get("LTP")),
            change=_num(rate.get("Chg")),
            change_pct=_num(rate.get("PcChg")),
            previous_close=_num(head.get("PrevClose")),
            day_high=_num(head.get("High")),
            day_low=_num(head.get("Low")),
            volume=_num(trading.get("TTQ")),
            week52_high=_num(hl.get("Fifty2WkHigh_adj")),
            week52_low=_num(hl.get("Fifty2WkLow_adj")),
            market_cap=_num(trading.get("MktCapFull")),
            source=f"bse:{scrip_code} as-on {head.get('Ason', '?')}",
        )

    def get_fundamentals(self, symbol: str, scrip_code: str | None = None) -> Fundamentals | None:
        """BSE-published fundamentals: EPS, P/E, sector, market cap, liquidity."""
        if not scrip_code:
            resolved = self.resolve(symbol)
            if not resolved:
                return None
            scrip_code = resolved["scrip_code"]

        com = self._api(
            "ComHeader", {"quotetype": "EQ", "scripcode": scrip_code, "seriesid": ""}
        )
        if not com:
            return None
        trading = self._api(
            "StockTrading",
            {"flag": "", "quotetype": "EQ", "scripcode": scrip_code, "seriesid": ""},
        ) or {}

        return Fundamentals(
            scrip_code=str(scrip_code),
            symbol=com.get("SecurityId"),
            isin=com.get("ISIN"),
            eps=_num(com.get("EPS")),
            pe=_num(com.get("PE")),
            pb=_num(com.get("PB")),
            roe=_num(com.get("ROE")),
            ceps=_num(com.get("CEPS")),
            face_value=_num(com.get("FaceVal")),
            industry=com.get("IndustryNew") or com.get("Industry"),
            sector=com.get("Sector"),
            group=com.get("Group"),
            index_membership=com.get("Index"),
            settlement_type=com.get("SetlType"),
            market_cap_cr=_num(trading.get("MktCapFull")),
            market_cap_ff_cr=_num(trading.get("MktCapFF")),
            turnover_cr=_num(trading.get("Turnover")),
            traded_qty_lakh=_num(trading.get("TTQ")),
            two_week_avg_qty_lakh=_num(trading.get("TwoWkAvgQty")),
            wap=_num(trading.get("WAP")),
        )

    def get_corporate_actions(self, scrip_code: str) -> list[dict]:
        """Dividends, splits, bonuses. Upcoming ex-dates matter for sell timing."""
        data = self._api("DefaultData", {"Grpcode": "", "IsPf": "", "scripcode": scrip_code})
        if not isinstance(data, list):
            return []
        actions = []
        for row in data:
            raw = str(row.get("exdate", ""))
            try:
                ex = datetime.strptime(raw, "%Y%m%d").date()
            except ValueError:
                continue
            actions.append({
                "ex_date": ex,
                "purpose": str(row.get("Purpose", "")).strip(),
                "record_date": row.get("RD_Date"),
            })
        return sorted(actions, key=lambda a: a["ex_date"], reverse=True)

    def get_history(self, symbol: str, days: int = 400):
        """Not supported - BSE has no clean public daily-history API.

        Deep history comes from YahooProvider; this provider is deliberately
        explicit rather than returning None and looking like a fetch failure.
        """
        raise NotImplementedError(
            "BSEProvider has no history endpoint; use YahooProvider for OHLCV."
        )
