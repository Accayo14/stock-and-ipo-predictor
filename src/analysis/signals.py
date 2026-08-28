"""Signal engine: indicators in, a scored recommendation with evidence out.

Design intent
-------------
Every recommendation must be defensible line by line. So the engine never
produces a bare score; it produces a list of Evidence, each carrying the
actual number, the threshold it was compared against, and how much it moved
the result. The report is then just a rendering of that evidence - the
reasoning is not narrated after the fact, it *is* the computation.

Scores are all in [-1, +1]. Axes with insufficient data are excluded from the
weighted mean and drag down `confidence`, so a stock analysed on two of five
axes is never presented with the same authority as one analysed on all five.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .indicators import IndicatorSet, IndicatorValue


def clip(value: float, low: float = -1.0, high: float = 1.0) -> float:
    return float(max(low, min(high, value)))


@dataclass
class Evidence:
    """One observation that moved the score, with the number behind it."""

    axis: str
    statement: str
    direction: str          # "bullish" | "bearish" | "neutral"
    score: float            # contribution within its axis, [-1, 1]

    @property
    def icon(self) -> str:
        return {"bullish": "▲", "bearish": "▼"}.get(self.direction, "•")


@dataclass
class AxisScore:
    name: str
    score: float
    weight: float
    available: bool
    evidence: list[Evidence] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


@dataclass
class StockSignal:
    symbol: str
    composite: float
    action: str
    confidence: float
    axes: list[AxisScore]
    data_note: str = ""

    @property
    def evidence(self) -> list[Evidence]:
        """All evidence, strongest influence first."""
        items = [e for axis in self.axes for e in axis.evidence]
        return sorted(items, key=lambda e: -abs(e.score))

    def bullish(self) -> list[Evidence]:
        return [e for e in self.evidence if e.direction == "bullish"]

    def bearish(self) -> list[Evidence]:
        return [e for e in self.evidence if e.direction == "bearish"]

    @property
    def missing_axes(self) -> list[str]:
        return [a.name for a in self.axes if not a.available]


def _direction(score: float, deadzone: float = 0.05) -> str:
    if score > deadzone:
        return "bullish"
    if score < -deadzone:
        return "bearish"
    return "neutral"


def _combine(parts: list[Evidence]) -> float:
    return float(np.mean([p.score for p in parts])) if parts else 0.0


# ---------------------------------------------------------------------------
# Axes
# ---------------------------------------------------------------------------

def score_trend(ind: IndicatorSet, price: float, cfg: dict) -> AxisScore:
    """Where price sits relative to its own moving averages."""
    parts: list[Evidence] = []
    missing: list[str] = []

    sma_s, sma_l = ind.get("sma_short"), ind.get("sma_long")
    n_s, n_l = cfg["sma_short"], cfg["sma_long"]

    if sma_s:
        gap = (price / sma_s.value) - 1
        score = clip(gap / 0.10)
        parts.append(Evidence(
            "trend",
            f"Price {price:,.2f} is {gap:+.1%} vs its {n_s}-day average "
            f"({sma_s.value:,.2f})",
            _direction(score), score,
        ))
    else:
        missing.append(f"{n_s}-day SMA ({sma_s.reason})")

    if sma_l:
        gap = (price / sma_l.value) - 1
        score = clip(gap / 0.15)
        parts.append(Evidence(
            "trend",
            f"Price is {gap:+.1%} vs its {n_l}-day average ({sma_l.value:,.2f}), "
            f"the dividing line between a long-term uptrend and downtrend",
            _direction(score), score,
        ))
    else:
        missing.append(f"{n_l}-day SMA ({sma_l.reason})")

    if sma_s and sma_l:
        spread = (sma_s.value / sma_l.value) - 1
        score = clip(spread / 0.08)
        if spread > 0:
            text = (f"{n_s}-day average sits {spread:+.1%} above the {n_l}-day "
                    f"(golden-cross configuration, a bullish structure)")
        else:
            text = (f"{n_s}-day average sits {spread:+.1%} below the {n_l}-day "
                    f"(death-cross configuration, a bearish structure)")
        parts.append(Evidence("trend", text, _direction(score), score))

    return AxisScore("trend", _combine(parts), cfg["weights"]["trend"],
                     bool(parts), parts, missing)


def score_momentum(ind: IndicatorSet, price: float, cfg: dict) -> AxisScore:
    """Speed and direction of the move, not its level."""
    parts: list[Evidence] = []
    missing: list[str] = []
    rsi_v = ind.get("rsi")
    overbought, oversold = cfg["rsi_overbought"], cfg["rsi_oversold"]

    if rsi_v:
        value = rsi_v.value
        score = clip((value - 50) / 25)
        if value >= overbought:
            # Strong, but stretched: haircut the reward for chasing.
            score *= 0.55
            text = (f"RSI {value:.1f} is above the {overbought} overbought line - "
                    f"momentum is strong but the move is stretched, so this is "
                    f"discounted rather than treated as a fresh buy signal")
        elif value <= oversold:
            text = (f"RSI {value:.1f} is below the {oversold} oversold line - "
                    f"selling pressure has been heavy")
        else:
            # Describe it relative to the 50 midpoint, because the score is
            # signed off 50. Calling a sub-50 reading simply "neutral" while
            # the score marks it bearish reads as a contradiction.
            side = "above" if value >= 50 else "below"
            lean = "mild upward" if value >= 50 else "mild downward"
            text = (f"RSI {value:.1f} is {side} the 50 midpoint, inside the "
                    f"neutral {oversold}-{overbought} band - {lean} momentum "
                    f"with no extreme to trade against")
        parts.append(Evidence("momentum", text, _direction(score), score))
    else:
        missing.append(f"RSI ({rsi_v.reason})")

    hist = ind.get("macd_hist")
    if hist and price:
        normalised = hist.value / price
        score = clip(normalised / 0.010)
        state = "above" if hist.value > 0 else "below"
        parts.append(Evidence(
            "momentum",
            f"MACD histogram is {hist.value:+.2f} ({state} the signal line), "
            f"meaning short-term momentum is "
            f"{'building' if hist.value > 0 else 'fading'}",
            _direction(score), score,
        ))
    else:
        missing.append(f"MACD ({hist.reason})")

    roc_v = ind.get("roc")
    if roc_v:
        score = clip(roc_v.value / 0.15)
        parts.append(Evidence(
            "momentum",
            f"Price has moved {roc_v.value:+.1%} over the last "
            f"{cfg['roc_period']} sessions",
            _direction(score), score,
        ))

    return AxisScore("momentum", _combine(parts), cfg["weights"]["momentum"],
                     bool(parts), parts, missing)


def score_mean_reversion(ind: IndicatorSet, cfg: dict) -> AxisScore:
    """Contrarian axis: stretched away from the mean invites a snap back.

    Deliberately the lightest-weighted axis, because 'it has fallen a lot'
    is the single most expensive reason retail investors buy falling knives.
    """
    parts: list[Evidence] = []
    missing: list[str] = []

    bb = ind.get("bb_position")
    if bb:
        pos = bb.value
        score = clip((0.5 - pos) * 2.0)
        if pos > 1.0:
            text = (f"Trading above the upper Bollinger band "
                    f"(band position {pos:.2f}) - statistically extended")
        elif pos < 0.0:
            text = (f"Trading below the lower Bollinger band "
                    f"(band position {pos:.2f}) - statistically depressed")
        else:
            text = (f"Sits at {pos:.0%} of its Bollinger band range "
                    f"(0% = lower band, 100% = upper band)")
        parts.append(Evidence("mean_reversion", text, _direction(score), score))
    else:
        missing.append(f"Bollinger position ({bb.reason})")

    w52 = ind.get("week52_position")
    if w52:
        pos = w52.value
        # Half-weighted: distance from the 52w high is weak evidence alone.
        score = clip((0.5 - pos) * 1.2) * 0.5
        hi, lo = ind.get("week52_high"), ind.get("week52_low")
        where = (
            "closer to the low, which this contrarian axis reads as value "
            "rather than as weakness"
            if pos < 0.5 else
            "closer to the high, leaving less room before it looks stretched"
        )
        text = (f"At {pos:.0%} of its 52-week range "
                f"({lo.value:,.2f} low to {hi.value:,.2f} high) - {where}")
        parts.append(Evidence("mean_reversion", text, _direction(score), score))

    return AxisScore("mean_reversion", _combine(parts),
                     cfg["weights"]["mean_reversion"], bool(parts), parts, missing)


def score_volume(ind: IndicatorSet, cfg: dict) -> AxisScore:
    """Is the price move backed by participation, or is it thin drift?"""
    parts: list[Evidence] = []
    missing: list[str] = []

    obv = ind.get("obv_slope")
    if obv:
        score = clip(obv.value * 60)
        flow = "accumulation" if obv.value > 0 else "distribution"
        parts.append(Evidence(
            "volume",
            f"On-balance volume is trending {'up' if obv.value > 0 else 'down'} "
            f"over {cfg['obv_slope_period']} sessions, indicating net {flow}",
            _direction(score), score,
        ))
    else:
        missing.append(f"OBV ({obv.reason})")

    ratio = ind.get("volume_ratio")
    if ratio:
        value = ratio.value
        # Volume is confirmation, not direction: it never sets the sign alone.
        score = clip((value - 1.0) / 2.0) * 0.4
        if value >= 1.5:
            text = (f"Latest volume is {value:.1f}x its 20-day average - "
                    f"unusually high participation confirms the current move")
        elif value <= 0.5:
            text = (f"Latest volume is only {value:.1f}x its 20-day average - "
                    f"the move lacks conviction")
            score = -abs(score)
        else:
            text = f"Volume is {value:.1f}x its 20-day average (normal)"
        parts.append(Evidence("volume", text, _direction(score), score))

    return AxisScore("volume", _combine(parts), cfg["weights"]["volume"],
                     bool(parts), parts, missing)


def score_relative_strength(rs: IndicatorValue, cfg: dict) -> AxisScore:
    """Performance against the Sensex - separates real strength from a
    stock merely floating up on a rising market."""
    period = cfg["relative_strength_period"]
    if not rs:
        return AxisScore("relative_strength", 0.0,
                         cfg["weights"]["relative_strength"], False, [],
                         [f"relative strength ({rs.reason})"])
    score = clip(rs.value / 0.15)
    verb = "outperformed" if rs.value > 0 else "underperformed"
    evidence = Evidence(
        "relative_strength",
        f"Has {verb} the Sensex by {abs(rs.value):.1%} over the last "
        f"{period} sessions (~3 months)",
        _direction(score), score,
    )
    return AxisScore("relative_strength", score,
                     cfg["weights"]["relative_strength"], True, [evidence], [])


# ---------------------------------------------------------------------------
# Composite
# ---------------------------------------------------------------------------

def decide_action(composite: float, thresholds: dict) -> str:
    if composite >= thresholds["strong_buy"]:
        return "STRONG BUY"
    if composite >= thresholds["accumulate"]:
        return "ACCUMULATE"
    if composite <= thresholds["exit"]:
        return "EXIT"
    if composite <= thresholds["trim"]:
        return "TRIM"
    return "HOLD"


def analyse(
    symbol: str,
    ind: IndicatorSet,
    price: float,
    rs: IndicatorValue,
    signals_cfg: dict,
    data_note: str = "",
) -> StockSignal:
    """Run every axis and combine into one recommendation."""
    cfg = {**signals_cfg["indicators"], "weights": signals_cfg["weights"]}
    axes = [
        score_trend(ind, price, cfg),
        score_momentum(ind, price, cfg),
        score_mean_reversion(ind, cfg),
        score_volume(ind, cfg),
        score_relative_strength(rs, cfg),
    ]

    live = [a for a in axes if a.available]
    total_weight = sum(a.weight for a in live)
    composite = (
        sum(a.score * a.weight for a in live) / total_weight if total_weight else 0.0
    )
    # Confidence = share of total intended weight that actually had data.
    all_weight = sum(a.weight for a in axes)
    confidence = (total_weight / all_weight) if all_weight else 0.0

    return StockSignal(
        symbol=symbol,
        composite=float(composite),
        action=decide_action(composite, signals_cfg["thresholds"]),
        confidence=float(confidence),
        axes=axes,
        data_note=data_note,
    )
