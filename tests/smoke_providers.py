"""Smoke test: prove the data layer returns real, graded data before we build
analysis on top of it. Run: python tests/smoke_providers.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yaml  # noqa: E402

from src.cache import Cache  # noqa: E402
from src.providers.yahoo import YahooProvider  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
config = yaml.safe_load((ROOT / "config" / "config.yaml").read_text(encoding="utf-8"))

cache = Cache(ROOT / "data" / "cache", config["data"]["cache_hours"])
yahoo = YahooProvider(cache=cache, config=config)

# RELIANCE is the acid test: its .BO series is broken, so a correct
# implementation must transparently fall back to .NS and say so.
SYMBOLS = ["RELIANCE", "TCS", "HDFCBANK", "^BSESN", "NOTAREALTICKERXYZ"]

print(f"{'SYMBOL':<20} {'SOURCE':<22} {'EXCH':<5} {'BARS':>5} {'LAST':>10}  QUALITY")
print("-" * 100)
for sym in SYMBOLS:
    series = yahoo.get_history(sym, days=config["data"]["history_days"])
    if series is None:
        print(f"{sym:<20} {'-':<22} {'-':<5} {'-':>5} {'-':>10}  no data from any candidate")
        continue
    print(
        f"{sym:<20} {series.source:<22} {series.exchange_used:<5} "
        f"{len(series):>5} {series.last_close:>10.2f}  {series.quality.summary}"
    )

print("\nBenchmark quote check:")
q = yahoo.get_quote("^BSESN")
if q:
    print(f"  Sensex {q.last_price:,.2f}  prev {q.previous_close:,.2f}  "
          f"chg {q.change_pct:+.2f}%  ({q.source})")
else:
    print("  FAILED to fetch benchmark quote")
