"""Smoke test the BSE provider against live endpoints.
Run: python tests/smoke_bse.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yaml  # noqa: E402

from src.cache import Cache  # noqa: E402
from src.providers.bse import BSEProvider  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
config = yaml.safe_load((ROOT / "config" / "config.yaml").read_text(encoding="utf-8"))
bse = BSEProvider(cache=Cache(ROOT / "data" / "cache", 12), config=config)

print("=" * 78)
print("BHAVCOPY / SCRIP MASTER")
print("=" * 78)
master = bse.scrip_master()
if master is None:
    print("  FAILED to load bhavcopy")
    sys.exit(1)
print(f"  rows={len(master)}  session={master['_session'].iloc[0]}")
print(f"  columns={list(master.columns)}")

print("\n" + "=" * 78)
print("SYMBOL RESOLUTION")
print("=" * 78)
for sym in ["RELIANCE", "TCS", "HDFCBANK", "TATAMOTORS", "ZZZNOTREAL"]:
    r = bse.resolve(sym)
    print(f"  {sym:<12} -> {r}" if r else f"  {sym:<12} -> NOT FOUND")

print("\n" + "=" * 78)
print("QUOTE + FUNDAMENTALS (RELIANCE)")
print("=" * 78)
q = bse.get_quote("RELIANCE")
if q:
    print(f"  {q.company_name}  LTP={q.last_price}  prev={q.previous_close} "
          f"chg={q.change_pct}%")
    print(f"  day {q.day_low}-{q.day_high}   52wk {q.week52_low}-{q.week52_high}")
    print(f"  mcap(Cr)={q.market_cap}  source={q.source}")
else:
    print("  FAILED")

f = bse.get_fundamentals("RELIANCE")
if f:
    print(f"  EPS={f.eps} PE={f.pe} FV={f.face_value} sector={f.sector}")
    print(f"  industry={f.industry} group={f.group} index={f.index_membership}")
    print(f"  mcap={f.market_cap_cr}Cr  freefloat={f.market_cap_ff_cr}Cr")
    print(f"  liquidity: TTQ={f.traded_qty_lakh}L  2wkAvg={f.two_week_avg_qty_lakh}L")
else:
    print("  FUNDAMENTALS FAILED")

print("\n" + "=" * 78)
print("CORPORATE ACTIONS (most recent 3)")
print("=" * 78)
for a in bse.get_corporate_actions("500325")[:3]:
    print(f"  {a['ex_date']}  {a['purpose']}")
