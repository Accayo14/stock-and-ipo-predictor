"""Orchestrator: fetch, analyse, and assemble the morning report.

Produces a MorningReport object plus a machine-readable "facts bundle". The
bundle is what Claude Code reads in hybrid mode: every number and every piece
of evidence is already computed deterministically here, so the reasoning layer
interprets real analysis rather than inventing it.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path

import yaml

from .analysis import indicators as ind
from .analysis import risk as risk_mod
from .analysis import signals as sig
from .cache import Cache
from .portfolio import load_portfolio
from .providers.base import PriceSeries
from .providers.bse import BSEProvider
from .providers.yahoo import YahooProvider


# Corporate actions that change the share count, and therefore silently
# invalidate a stored avg_buy_price. Dividends are excluded - they reduce the
# price but do not restate your cost basis or holding quantity.
_SHARE_CHANGING = ("bonus", "split", "consolidation", "sub-division", "sub division")


def share_changing_actions_since(actions: list[dict], since) -> list[dict]:
    """Splits/bonuses with an ex-date after `since`."""
    if since is None:
        return []
    out = []
    for action in actions:
        purpose = str(action.get("purpose", "")).lower()
        if action["ex_date"] > since and any(k in purpose for k in _SHARE_CHANGING):
            out.append(action)
    return out


@dataclass
class MarketContext:
    benchmark_name: str = "SENSEX"
    level: float | None = None
    change_pct: float | None = None
    trend_note: str = ""
    above_50dma: bool | None = None
    above_200dma: bool | None = None
    quality: str = ""


@dataclass
class MorningReport:
    run_at: datetime
    session_date: date | None
    market: MarketContext
    positions: list[risk_mod.PositionAnalysis] = field(default_factory=list)
    portfolio: risk_mod.PortfolioRisk | None = None
    ipos: list = field(default_factory=list)
    unresolved: list[dict] = field(default_factory=list)
    data_issues: list[str] = field(default_factory=list)
    load_errors: list[str] = field(default_factory=list)
    load_warnings: list[str] = field(default_factory=list)
    timings: dict = field(default_factory=dict)

    def actions(self) -> dict[str, list[risk_mod.PositionAnalysis]]:
        grouped: dict[str, list] = {}
        for analysis in self.positions:
            grouped.setdefault(analysis.final_action, []).append(analysis)
        return grouped


class Engine:
    def __init__(self, root: Path, config_path: Path | None = None) -> None:
        self.root = Path(root)
        cfg_path = config_path or self.root / "config" / "config.yaml"
        self.config = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
        self.cache = Cache(
            self.root / "data" / "cache", self.config["data"]["cache_hours"]
        )
        self.yahoo = YahooProvider(cache=self.cache, config=self.config)
        self.bse = BSEProvider(cache=self.cache, config=self.config)
        self._benchmark: PriceSeries | None = None

    # -- market context ----------------------------------------------------

    def benchmark_series(self) -> PriceSeries | None:
        if self._benchmark is None:
            self._benchmark = self.yahoo.get_history(
                self.config["market"]["benchmark"],
                self.config["data"]["history_days"],
            )
        return self._benchmark

    def market_context(self) -> MarketContext:
        series = self.benchmark_series()
        if series is None:
            return MarketContext(quality="Sensex history unavailable")

        icfg = self.config["signals"]["indicators"]
        indicators = ind.compute(series, icfg)
        price = series.last_close or 0.0
        sma50, sma200 = indicators.get("sma_short"), indicators.get("sma_long")

        above50 = bool(sma50) and price > sma50.value
        above200 = bool(sma200) and price > sma200.value
        if above50 and above200:
            note = ("Sensex is above both its 50- and 200-day averages: a "
                    "constructive backdrop, which historically favours holding "
                    "winners over aggressive bottom-fishing.")
        elif not above50 and not above200:
            note = ("Sensex is below both its 50- and 200-day averages: a weak "
                    "backdrop. Individual buy signals deserve extra scepticism "
                    "when the broad market is trending down.")
        else:
            note = ("Sensex is mixed relative to its moving averages - a "
                    "transitional or range-bound market.")

        change = None
        if len(series) >= 2:
            change = (series.close[-1] - series.close[-2]) / series.close[-2] * 100

        return MarketContext(
            benchmark_name="SENSEX",
            level=price,
            change_pct=change,
            trend_note=note,
            above_50dma=above50 if sma50 else None,
            above_200dma=above200 if sma200 else None,
            quality=series.quality.summary,
        )

    # -- per position ------------------------------------------------------

    def analyse_holding(self, position, portfolio_value: float):
        """Returns (PositionAnalysis | None, issue_note | None)."""
        symbol = position.symbol
        resolved = self.bse.resolve(symbol)
        if not resolved:
            suggestions = self.bse.suggest(symbol)
            return None, {
                "symbol": symbol,
                "reason": "not found among BSE listed equities",
                "suggestions": [
                    {"symbol": s["symbol"], "scrip_code": s["scrip_code"], "name": s["name"]}
                    for s in suggestions
                ],
            }

        scrip_code = resolved["scrip_code"]
        series = self.yahoo.get_history(symbol, self.config["data"]["history_days"])
        if series is None:
            return None, {
                "symbol": symbol,
                "reason": "no price history from any source",
                "suggestions": [],
            }

        data_note = ""
        if not series.quality.usable:
            data_note = (
                f"Analysis is limited: {series.quality.summary}. "
                f"Indicators needing more history are reported as unavailable "
                f"rather than estimated."
            )
        if series.exchange_used == "NSE":
            data_note += (
                (" " if data_note else "")
                + "Indicator history is from the NSE tape (BSE history for this "
                  "scrip is incomplete on the source). BSE and NSE prices track "
                  "within ~0.1%, so the technical picture is unaffected; "
                  "prices and fundamentals below are BSE's own."
            )

        indicators = ind.compute(series, self.config["signals"]["indicators"])
        rs = ind.relative_strength(
            series,
            self.benchmark_series(),
            self.config["signals"]["indicators"]["relative_strength_period"],
        )

        # BSE is the source of truth for the traded price.
        quote = self.bse.get_quote(symbol, scrip_code)
        price = (
            (quote.last_price if quote else None)
            or resolved.get("bse_close")
            or series.last_close
        )
        fundamentals = self.bse.get_fundamentals(symbol, scrip_code)

        signal = sig.analyse(
            symbol, indicators, price, rs, self.config["signals"], data_note
        )
        atr_value = indicators.get("atr").value if indicators.get("atr") else None

        analysis = risk_mod.analyse_position(
            position,
            signal,
            price,
            portfolio_value,
            self.config["risk"],
            atr_value=atr_value,
            company_name=(quote.company_name if quote else resolved.get("name")),
            sector=(fundamentals.sector if fundamentals else None),
            pe=(fundamentals.pe if fundamentals else None),
        )
        # A split or bonus after the buy date restates the share count, so a
        # stored avg_buy_price from before it is wrong - and every P&L number
        # derived from it is wrong too. Verified live: Reliance ran a 1:1 bonus
        # with ex-date 2024-10-28, which halves the effective cost basis of
        # anyone holding from before that date.
        events = share_changing_actions_since(
            self.bse.get_corporate_actions(scrip_code), position.buy_date
        )
        for event in events:
            analysis.notes.append(
                f"'{event['purpose'].strip()}' went ex on {event['ex_date']}, "
                f"after your buy date of {position.buy_date}. If your "
                f"avg_buy_price of {position.avg_buy_price:,.2f} is the "
                f"pre-event price, the P&L above is overstated - update "
                f"portfolio.csv to the adjusted cost."
            )

        analysis.indicators = indicators          # attached for the report layer
        analysis.fundamentals = fundamentals
        analysis.series = series
        analysis.corporate_actions = events
        return analysis, None

    # -- full run ----------------------------------------------------------

    def run(self, portfolio_path: Path | None = None, include_ipos: bool = True) -> MorningReport:
        started = time.time()
        portfolio_path = portfolio_path or self.root / "config" / "portfolio.csv"
        loaded = load_portfolio(portfolio_path)

        report = MorningReport(
            run_at=datetime.now(),
            session_date=None,
            market=self.market_context(),
            load_errors=loaded.errors,
            load_warnings=loaded.warnings,
        )
        if not loaded.positions:
            report.timings["total_s"] = round(time.time() - started, 1)
            return report

        # Two passes: the first prices everything so portfolio weights are
        # known before any position-level judgement depends on them.
        priced: list[tuple] = []
        for position in loaded.positions:
            resolved = self.bse.resolve(position.symbol)
            price = None
            if resolved:
                price = resolved.get("bse_close")
            if price is None:
                series = self.yahoo.get_history(position.symbol, 40)
                price = series.last_close if series else None
            priced.append((position, price))
        portfolio_value = sum(p.quantity * pr for p, pr in priced if pr) or 0.0

        for position, _ in priced:
            analysis, issue = self.analyse_holding(position, portfolio_value)
            if analysis:
                report.positions.append(analysis)
            elif issue:
                report.unresolved.append(issue)

        if report.positions:
            report.portfolio = risk_mod.assess_portfolio(
                report.positions, self.config["risk"]
            )
            master = self.bse.scrip_master()
            if master is not None and len(master):
                report.session_date = date.fromisoformat(str(master["_session"].iloc[0]))

        for analysis in report.positions:
            series = getattr(analysis, "series", None)
            if series is not None and not series.quality.usable:
                report.data_issues.append(
                    f"{analysis.position.symbol}: {series.quality.summary}"
                )

        if include_ipos:
            try:
                from .analysis.ipo import analyse_ipos
                report.ipos = analyse_ipos(self.config, self.cache)
            except ImportError:
                report.data_issues.append("IPO module not available.")
            except Exception as exc:                      # noqa: BLE001
                report.data_issues.append(f"IPO analysis failed: {exc}")

        # Attached for the HTML renderer's market sparkline.
        report._benchmark_series = self.benchmark_series()
        report.timings["total_s"] = round(time.time() - started, 1)
        return report


def facts_bundle(report: MorningReport) -> dict:
    """Flatten the report into JSON for the reasoning layer / archive."""

    def evidence_list(items) -> list[dict]:
        return [
            {"axis": e.axis, "statement": e.statement,
             "direction": e.direction, "score": round(e.score, 3)}
            for e in items
        ]

    positions = []
    for a in report.positions:
        indicators = getattr(a, "indicators", None)
        series = getattr(a, "series", None)
        fundamentals = getattr(a, "fundamentals", None)
        positions.append({
            "symbol": a.position.symbol,
            "company": a.company_name,
            "sector": a.sector,
            "quantity": a.position.quantity,
            "avg_buy_price": a.position.avg_buy_price,
            "current_price": round(a.current_price, 2),
            "invested": round(a.invested, 2),
            "market_value": round(a.market_value, 2),
            "pnl": round(a.pnl, 2),
            "pnl_pct": round(a.pnl_pct, 4),
            "weight": round(a.weight, 4),
            "days_held": a.days_held,
            "days_to_ltcg": a.days_to_ltcg,
            "is_long_term": a.is_long_term,
            "corporate_actions_since_buy": [
                {"ex_date": str(e["ex_date"]), "purpose": e["purpose"].strip()}
                for e in getattr(a, "corporate_actions", [])
            ],
            "suggested_stop": round(a.suggested_stop, 2) if a.suggested_stop else None,
            "stop_source": a.stop_source,
            "stop_breached": a.stop_breached,
            "technical_action": a.signal.action,
            "final_action": a.final_action,
            "action_changed": a.action_changed,
            "composite_score": round(a.signal.composite, 3),
            "confidence": round(a.signal.confidence, 3),
            "pe": a.pe,
            "axes": [
                {"name": ax.name, "score": round(ax.score, 3),
                 "weight": ax.weight, "available": ax.available,
                 "missing": ax.missing}
                for ax in a.signal.axes
            ],
            "evidence": evidence_list(a.signal.evidence),
            "risk_adjustments": evidence_list(a.adjustments),
            "notes": a.notes,
            "data_note": a.signal.data_note,
            "indicators": (
                {k: {"value": v.value, "available": v.available, "reason": v.reason}
                 for k, v in indicators.values.items()} if indicators else {}
            ),
            "history": {
                "bars": len(series) if series else 0,
                "source": series.source if series else None,
                "exchange_used": series.exchange_used if series else None,
                "quality": series.quality.summary if series else None,
            },
            "fundamentals": (
                {k: v for k, v in asdict(fundamentals).items() if v is not None}
                if fundamentals else {}
            ),
        })

    return {
        "run_at": report.run_at.isoformat(),
        "session_date": report.session_date.isoformat() if report.session_date else None,
        "market": asdict(report.market),
        "portfolio": (
            {
                "total_value": round(report.portfolio.total_value, 2),
                "total_invested": round(report.portfolio.total_invested, 2),
                "total_pnl": round(report.portfolio.total_pnl, 2),
                "total_pnl_pct": round(report.portfolio.total_pnl_pct, 4),
                "sector_weights": {
                    k: round(v, 4) for k, v in report.portfolio.sector_weights.items()
                },
                "warnings": report.portfolio.warnings,
            } if report.portfolio else None
        ),
        "positions": positions,
        "ipos": [i.to_dict() if hasattr(i, "to_dict") else i for i in report.ipos],
        "unresolved": report.unresolved,
        "data_issues": report.data_issues,
        "load_errors": report.load_errors,
        "load_warnings": report.load_warnings,
        "timings": report.timings,
    }
