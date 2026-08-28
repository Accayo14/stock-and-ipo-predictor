"""Tests for the portfolio/risk overlay.

This is the layer most likely to cause real financial harm if it is subtly
wrong, so each rule is tested against a hand-constructed scenario with an
obvious right answer.

Run: python tests/test_risk.py
"""

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.analysis.risk import (  # noqa: E402
    Position, analyse_position, assess_portfolio, position_size,
)
from src.analysis.signals import AxisScore, StockSignal  # noqa: E402
from src.engine import share_changing_actions_since  # noqa: E402

TODAY = date(2026, 8, 26)
RISK = {
    "atr_stop_multiplier": 2.5,
    "max_position_weight": 0.20,
    "max_sector_weight": 0.35,
    "risk_per_trade": 0.02,
    "review_drawdown": -0.15,
    "ltcg_days": 365,
    "ltcg_warning_window_days": 45,
}

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}"
          f"{('  -> ' + str(detail)) if detail and not condition else ''}")
    if not condition:
        failures.append(label)


def signal(action: str, composite: float) -> StockSignal:
    return StockSignal(
        symbol="TEST", composite=composite, action=action, confidence=1.0,
        axes=[AxisScore("trend", composite, 1.0, True, [], [])],
    )


def analyse(position, action="HOLD", composite=0.0, price=100.0,
            pv=100_000.0, atr=4.0):
    return analyse_position(
        position, signal(action, composite), price, pv, RISK,
        atr_value=atr, today=TODAY,
    )


print("Capital-gains timing")
p_long = Position("A", 10, 100.0, buy_date=TODAY - timedelta(days=500))
r = analyse(p_long)
check("held 500d -> is_long_term", r.is_long_term)
check("held 500d -> no misleading countdown", r.days_to_ltcg is None, r.days_to_ltcg)

p_near = Position("B", 10, 100.0, buy_date=TODAY - timedelta(days=340))
r = analyse(p_near, action="TRIM", composite=-0.4)
check("held 340d -> 25 days to LTCG", r.days_to_ltcg == 25, r.days_to_ltcg)
check("TRIM near LTCG raises a tax caveat",
      any(a.axis == "tax" for a in r.adjustments))
check("tax caveat lands in notes", any("LTCG" in n for n in r.notes))

r_hold = analyse(p_near, action="HOLD", composite=0.0)
check("HOLD near LTCG raises no tax caveat",
      not any(a.axis == "tax" for a in r_hold.adjustments))

p_none = Position("C", 10, 100.0, buy_date=None)
r = analyse(p_none)
check("no buy_date -> no days_held", r.days_held is None)
check("no buy_date -> not marked long-term", r.is_long_term is False)

print("\nStop losses")
p_stop = Position("D", 10, 100.0, stop_loss=95.0)
r = analyse(p_stop, action="ACCUMULATE", composite=0.3, price=90.0)
check("price below configured stop -> breached", r.stop_breached)
check("stop breach downgrades ACCUMULATE two steps",
      r.final_action == "EXIT", r.final_action)
check("stop breach is explained", any("stop loss" in a.statement for a in r.adjustments))
check("configured stop is preferred over ATR", r.suggested_stop == 95.0)

r_ok = analyse(Position("E", 10, 100.0), price=100.0, atr=4.0)
check("no configured stop -> ATR stop used", r_ok.stop_source.startswith("2.5x ATR"))
check("ATR stop = price - 2.5*ATR", abs(r_ok.suggested_stop - 90.0) < 1e-9,
      r_ok.suggested_stop)
check("healthy price -> not breached", not r_ok.stop_breached)

print("\nConcentration")
# 500 shares at 100 = 50,000 of a 100,000 portfolio = 50%.
p_big = Position("F", 500, 80.0)
r = analyse(p_big, action="STRONG BUY", composite=0.6, price=100.0, pv=100_000.0)
check("weight computed correctly", abs(r.weight - 0.5) < 1e-9, r.weight)
check("overweight caps a buy signal to HOLD", r.final_action == "HOLD", r.final_action)
check("cap is explained in notes", any("position-size limit" in n for n in r.notes))

r_small = analyse(Position("G", 10, 80.0), action="STRONG BUY",
                  composite=0.6, price=100.0, pv=100_000.0)
check("normal weight leaves buy signal intact",
      r_small.final_action == "STRONG BUY", r_small.final_action)

print("\nDrawdown")
p_loss = Position("H", 100, 200.0)
r = analyse(p_loss, action="HOLD", composite=-0.2, price=100.0)
check("deep loss + bearish signal downgrades", r.final_action == "TRIM", r.final_action)
r2 = analyse(p_loss, action="HOLD", composite=+0.3, price=100.0)
check("deep loss + bullish signal does NOT downgrade",
      r2.final_action == "HOLD", r2.final_action)

print("\nTarget reached")
p_target = Position("I", 10, 80.0, target_price=100.0)
r = analyse(p_target, action="ACCUMULATE", composite=0.3, price=105.0)
check("target hit caps further buying", r.final_action == "HOLD", r.final_action)
check("target hit is explained", any("target" in a.statement for a in r.adjustments))

print("\nP&L arithmetic")
r = analyse(Position("J", 10, 100.0), price=150.0)
check("P&L value", abs(r.pnl - 500.0) < 1e-9, r.pnl)
check("P&L percent", abs(r.pnl_pct - 0.5) < 1e-9, r.pnl_pct)
check("market value", abs(r.market_value - 1500.0) < 1e-9, r.market_value)

print("\nCorporate actions vs buy date")
actions = [
    {"ex_date": date(2024, 10, 28), "purpose": "Bonus issue 1:1"},
    {"ex_date": date(2026, 6, 5), "purpose": "Final Dividend - Rs. - 6.0000"},
    {"ex_date": date(2026, 3, 1), "purpose": "Stock  Split From Rs.10 to Rs.2"},
]
after_2024 = share_changing_actions_since(actions, date(2024, 1, 1))
check("bonus after buy date is flagged",
      any("Bonus" in a["purpose"] for a in after_2024))
check("split after buy date is flagged",
      any("Split" in a["purpose"] for a in after_2024))
check("dividend is NOT flagged as share-changing",
      not any("Dividend" in a["purpose"] for a in after_2024))
after_2025 = share_changing_actions_since(actions, date(2025, 3, 14))
check("bonus before buy date is ignored",
      not any("Bonus" in a["purpose"] for a in after_2025))
check("split after that buy date still flagged", len(after_2025) == 1, after_2025)
check("no buy date -> nothing flagged", share_changing_actions_since(actions, None) == [])

print("\nPosition sizing")
size = position_size(100_000.0, entry=100.0, stop=90.0, risk_cfg=RISK)
check("risks 2% of portfolio", abs(size["risk_amount"] - 2000.0) < 100.0, size)
check("share count = risk / per-share risk", size["shares"] == 200, size["shares"])
check("stop above entry is rejected",
      position_size(100_000.0, 100.0, 110.0, RISK) is None)
check("zero portfolio is rejected", position_size(0.0, 100.0, 90.0, RISK) is None)

print("\nPortfolio aggregation")
positions = [
    analyse(Position("K", 100, 90.0), price=100.0, pv=30_000.0),
    analyse(Position("L", 100, 100.0), price=100.0, pv=30_000.0),
    analyse(Position("M", 100, 110.0), price=100.0, pv=30_000.0),
]
for a, sector in zip(positions, ["IT", "IT", "Energy"]):
    a.sector = sector
pr = assess_portfolio(positions, RISK)
check("total value", abs(pr.total_value - 30_000.0) < 1e-9, pr.total_value)
check("total invested", abs(pr.total_invested - 30_000.0) < 1e-9, pr.total_invested)
check("IT weight is 2/3", abs(pr.sector_weights["IT"] - 2 / 3) < 1e-9, pr.sector_weights)
check("sector concentration warned", any("IT is" in w for w in pr.warnings))
check("few-holdings warning present", any("Only 3 holdings" in w for w in pr.warnings))

print("\n" + "=" * 60)
if failures:
    print(f"{len(failures)} FAILURE(S): {failures}")
    sys.exit(1)
print("All risk tests passed.")
