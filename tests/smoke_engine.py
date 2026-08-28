"""End-to-end engine run against live data.
Run: python tests/smoke_engine.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.engine import Engine, facts_bundle  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
engine = Engine(ROOT)
report = engine.run(include_ipos=False)

print("=" * 78)
print("MARKET")
print("=" * 78)
m = report.market
print(f"  {m.benchmark_name} {m.level:,.2f} ({m.change_pct:+.2f}%)" if m.level else "  unavailable")
print(f"  above 50dma={m.above_50dma}  above 200dma={m.above_200dma}")
print(f"  {m.trend_note}")

print("\n" + "=" * 78)
print("POSITIONS")
print("=" * 78)
for a in report.positions:
    print(f"\n  {a.position.symbol}  ({a.company_name})")
    print(f"    price {a.current_price:,.2f}   avg {a.position.avg_buy_price:,.2f}   "
          f"qty {a.position.quantity:g}")
    print(f"    P&L {a.pnl:+,.0f} ({a.pnl_pct:+.1%})   weight {a.weight:.1%}   "
          f"sector {a.sector}")
    print(f"    technical={a.signal.action}  final={a.final_action}  "
          f"score={a.signal.composite:+.3f}  confidence={a.signal.confidence:.0%}")
    print(f"    stop {a.suggested_stop:,.2f} ({a.stop_source}) breached={a.stop_breached}"
          if a.suggested_stop else f"    stop: {a.stop_source}")
    if a.days_to_ltcg is not None:
        print(f"    held {a.days_held}d, {a.days_to_ltcg}d to LTCG")
    print("    evidence:")
    for e in a.signal.evidence[:4]:
        print(f"      {e.icon} [{e.score:+.2f}] {e.statement}")
    for adj in a.adjustments:
        print(f"      ! [{adj.score:+.2f}] {adj.statement[:110]}")
    if a.notes:
        print(f"    notes: {a.notes}")

print("\n" + "=" * 78)
print("PORTFOLIO")
print("=" * 78)
p = report.portfolio
if p:
    print(f"  value {p.total_value:,.0f}   invested {p.total_invested:,.0f}   "
          f"P&L {p.total_pnl:+,.0f} ({p.total_pnl_pct:+.1%})")
    print(f"  sectors: { {k: f'{v:.0%}' for k, v in p.sector_weights.items()} }")
    for w in p.warnings:
        print(f"  WARN: {w}")

if report.unresolved:
    print("\nUNRESOLVED:")
    for u in report.unresolved:
        print(f"  {u['symbol']}: {u['reason']}")
        for s in u["suggestions"][:3]:
            print(f"     did you mean {s['symbol']} ({s['scrip_code']}) {s['name']}?")

if report.data_issues:
    print("\nDATA ISSUES:")
    for d in report.data_issues:
        print(f"  {d}")

print(f"\nElapsed: {report.timings.get('total_s')}s")

out = ROOT / "data" / "reports" / "smoke_facts.json"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(facts_bundle(report), indent=2, default=str), encoding="utf-8")
print(f"Facts bundle -> {out}  ({out.stat().st_size:,} bytes)")
