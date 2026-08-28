"""Portfolio and risk overlay.

A chart signal alone is not a recommendation for *your* holding. Whether you
should sell TCS depends on what you paid, how long you have held it, how much
of your portfolio it already is, and whether a sale eleven months in triggers
short-term capital gains that a three-week wait would avoid.

This module takes the technical signal and applies that position context,
producing a final action plus an explicit list of the adjustments made - so a
downgrade from ACCUMULATE to HOLD always comes with the reason attached.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .signals import Evidence, StockSignal

ACTION_ORDER = ["EXIT", "TRIM", "HOLD", "ACCUMULATE", "STRONG BUY"]


def _shift(action: str, steps: int) -> str:
    """Move an action along the bullish/bearish ladder, clamped."""
    idx = ACTION_ORDER.index(action) if action in ACTION_ORDER else 2
    return ACTION_ORDER[max(0, min(len(ACTION_ORDER) - 1, idx + steps))]


@dataclass
class Position:
    symbol: str
    quantity: float
    avg_buy_price: float
    scrip_code: str | None = None
    buy_date: date | None = None
    target_price: float | None = None
    stop_loss: float | None = None

    @property
    def invested(self) -> float:
        return self.quantity * self.avg_buy_price


@dataclass
class PositionAnalysis:
    position: Position
    company_name: str | None
    current_price: float
    signal: StockSignal
    market_value: float
    invested: float
    pnl: float
    pnl_pct: float
    weight: float                       # share of total portfolio value
    days_held: int | None
    days_to_ltcg: int | None        # None once the holding is already long-term
    is_long_term: bool
    suggested_stop: float | None
    stop_source: str
    stop_breached: bool
    final_action: str
    adjustments: list[Evidence] = field(default_factory=list)
    sector: str | None = None
    pe: float | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def action_changed(self) -> bool:
        return self.final_action != self.signal.action


def analyse_position(
    position: Position,
    signal: StockSignal,
    current_price: float,
    portfolio_value: float,
    risk_cfg: dict,
    *,
    atr_value: float | None = None,
    company_name: str | None = None,
    sector: str | None = None,
    pe: float | None = None,
    today: date | None = None,
) -> PositionAnalysis:
    """Apply position context on top of the raw technical signal."""
    today = today or date.today()
    market_value = position.quantity * current_price
    invested = position.invested
    pnl = market_value - invested
    pnl_pct = (pnl / invested) if invested else 0.0
    weight = (market_value / portfolio_value) if portfolio_value else 0.0

    days_held = (today - position.buy_date).days if position.buy_date else None
    ltcg_days = risk_cfg["ltcg_days"]
    is_long_term = days_held is not None and days_held >= ltcg_days
    # Once long-term, there is no countdown left to report - carrying a 0 here
    # reads as "LTCG is imminent", which is the opposite of the truth.
    days_to_ltcg = (
        None if (days_held is None or is_long_term) else ltcg_days - days_held
    )

    # -- stop loss: yours if set, otherwise a volatility-scaled one ---------
    if position.stop_loss:
        suggested_stop, stop_source = position.stop_loss, "your configured stop"
    elif atr_value:
        multiplier = risk_cfg["atr_stop_multiplier"]
        suggested_stop = current_price - multiplier * atr_value
        stop_source = f"{multiplier}x ATR below current price"
    else:
        suggested_stop, stop_source = None, "not available (no ATR)"
    stop_breached = bool(suggested_stop and current_price <= suggested_stop)

    action = signal.action
    adjustments: list[Evidence] = []
    notes: list[str] = []

    # -- 1. hard stop breach ----------------------------------------------
    # A stop you set yourself is a commitment made while thinking clearly,
    # away from the pressure of a falling price. If it breaks, the answer is
    # EXIT outright - not a partial trim, and regardless of how bullish the
    # chart still looks. Overriding your own stop because the indicators
    # disagree is precisely the behaviour a stop exists to prevent.
    if stop_breached and position.stop_loss:
        adjustments.append(Evidence(
            "risk",
            f"Price {current_price:,.2f} has broken your stop loss of "
            f"{position.stop_loss:,.2f}. A stop is only useful if it is "
            f"honoured, so this overrides the chart signal"
            + (f", which was {signal.action}." if signal.action != "EXIT" else "."),
            "bearish", -0.8,
        ))
        action = "EXIT"
        notes.append(
            f"Stop loss breached - action forced to EXIT from {signal.action}."
        )

    # -- 2. deep drawdown with a bearish signal ---------------------------
    if pnl_pct <= risk_cfg["review_drawdown"] and signal.composite < 0:
        adjustments.append(Evidence(
            "risk",
            f"Down {pnl_pct:.1%} on this position and the technical picture is "
            f"still negative (score {signal.composite:+.2f}). Averaging down "
            f"into continued weakness is how small losses become large ones.",
            "bearish", -0.4,
        ))
        action = _shift(action, -1)

    # -- 3. concentration --------------------------------------------------
    max_weight = risk_cfg["max_position_weight"]
    if weight > max_weight:
        adjustments.append(Evidence(
            "risk",
            f"This single holding is {weight:.1%} of your portfolio, above your "
            f"{max_weight:.0%} limit. Even on a bullish signal, adding more "
            f"concentrates rather than diversifies risk.",
            "bearish", -0.3,
        ))
        if action in ("STRONG BUY", "ACCUMULATE"):
            action = "HOLD"
            notes.append("Buy signal capped to HOLD by position-size limit.")

    # -- 4. capital gains timing (India) ----------------------------------
    window = risk_cfg["ltcg_warning_window_days"]
    if (
        days_to_ltcg is not None
        and 0 < days_to_ltcg <= window
        and action in ("TRIM", "EXIT")
    ):
        adjustments.append(Evidence(
            "tax",
            f"Held {days_held} days - only {days_to_ltcg} more days until this "
            f"qualifies as a long-term holding. Selling now realises short-term "
            f"capital gains, which are taxed at a materially higher rate. "
            f"Unless the thesis has broken, waiting {days_to_ltcg} days is "
            f"usually the cheaper choice.",
            "neutral", 0.0,
        ))
        notes.append(f"Consider deferring the sale {days_to_ltcg} days for LTCG treatment.")

    # -- 5. target reached -------------------------------------------------
    if position.target_price and current_price >= position.target_price:
        adjustments.append(Evidence(
            "risk",
            f"Price {current_price:,.2f} has reached your target of "
            f"{position.target_price:,.2f}. Booking at least part of the gain "
            f"realises the plan you set when you were thinking clearly.",
            "bearish", -0.25,
        ))
        if action in ("STRONG BUY", "ACCUMULATE"):
            action = "HOLD"

    return PositionAnalysis(
        position=position,
        company_name=company_name,
        current_price=current_price,
        signal=signal,
        market_value=market_value,
        invested=invested,
        pnl=pnl,
        pnl_pct=pnl_pct,
        weight=weight,
        days_held=days_held,
        days_to_ltcg=days_to_ltcg,
        is_long_term=is_long_term,
        suggested_stop=suggested_stop,
        stop_source=stop_source,
        stop_breached=stop_breached,
        final_action=action,
        adjustments=adjustments,
        sector=sector,
        pe=pe,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Portfolio level
# ---------------------------------------------------------------------------

@dataclass
class PortfolioRisk:
    total_value: float
    total_invested: float
    total_pnl: float
    total_pnl_pct: float
    sector_weights: dict[str, float]
    warnings: list[str] = field(default_factory=list)
    concentration_top: float = 0.0


def assess_portfolio(analyses: list[PositionAnalysis], risk_cfg: dict) -> PortfolioRisk:
    """Portfolio-wide concentration and exposure checks."""
    total_value = sum(a.market_value for a in analyses)
    total_invested = sum(a.invested for a in analyses)
    total_pnl = total_value - total_invested

    sector_values: dict[str, float] = {}
    for a in analyses:
        key = a.sector or "Unclassified"
        sector_values[key] = sector_values.get(key, 0.0) + a.market_value
    sector_weights = {
        k: (v / total_value if total_value else 0.0)
        for k, v in sorted(sector_values.items(), key=lambda kv: -kv[1])
    }

    warnings: list[str] = []
    max_sector = risk_cfg["max_sector_weight"]
    for sector, weight in sector_weights.items():
        if weight > max_sector and sector != "Unclassified":
            warnings.append(
                f"{sector} is {weight:.0%} of the portfolio, above the "
                f"{max_sector:.0%} guideline - a sector-wide shock would hit "
                f"an outsized share of your capital at once."
            )

    over = [a for a in analyses if a.weight > risk_cfg["max_position_weight"]]
    for a in over:
        warnings.append(
            f"{a.position.symbol} alone is {a.weight:.0%} of the portfolio "
            f"(limit {risk_cfg['max_position_weight']:.0%})."
        )

    if len(analyses) < 5 and analyses:
        warnings.append(
            f"Only {len(analyses)} holdings - with so few positions, "
            f"single-stock risk dominates portfolio outcomes."
        )

    return PortfolioRisk(
        total_value=total_value,
        total_invested=total_invested,
        total_pnl=total_pnl,
        total_pnl_pct=(total_pnl / total_invested) if total_invested else 0.0,
        sector_weights=sector_weights,
        warnings=warnings,
        concentration_top=max((a.weight for a in analyses), default=0.0),
    )


def position_size(
    portfolio_value: float, entry: float, stop: float, risk_cfg: dict
) -> dict | None:
    """How many shares to buy so that hitting the stop costs only
    `risk_per_trade` of the portfolio."""
    if not (portfolio_value and entry and stop) or entry <= stop:
        return None
    risk_amount = portfolio_value * risk_cfg["risk_per_trade"]
    per_share_risk = entry - stop
    shares = int(risk_amount / per_share_risk)
    if shares <= 0:
        return None
    return {
        "shares": shares,
        "capital_required": shares * entry,
        "risk_amount": shares * per_share_risk,
        "risk_pct_of_portfolio": (shares * per_share_risk) / portfolio_value,
        "per_share_risk": per_share_risk,
    }
