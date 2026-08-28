"""Backtest the IPO verdicts against what actually happened.

    python backtest.py                 # run on recently listed IPOs
    python backtest.py --limit 60      # widen the sample
    python backtest.py --csv out.csv   # save the per-issue detail

How it works
------------
For every IPO that has already listed:

  inputs   - subscription, grey market premium, financials and issue
             structure, scraped from the issue's own page
  outcome  - the actual close on its first trading day, taken from the BSE
             scrip master plus daily history, compared with the issue price

The engine is then run as if standing at the close of bidding, and its verdict
is compared with what the stock actually did.

Two honest caveats, both reported in the output
-----------------------------------------------
1. *Mild look-ahead.* The "at close" run uses the final subscription figure,
   which is only fully known once bidding has ended - slightly after the last
   moment you could apply. The `--strict` run instead uses the previous day's
   snapshot, which was genuinely on screen while you could still act. Where
   the two disagree, the strict number is the honest one.

2. *Listing-day close is not your return.* It measures the listing pop, which
   is what an apply/skip decision is usually about. It says nothing about
   holding the stock afterwards.

A backtest over a few dozen issues in one market period is weak evidence. It
can expose a rule that is clearly broken; it cannot prove one is good.
"""

from __future__ import annotations

import argparse
import csv
import difflib
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import requests  # noqa: E402
import yaml  # noqa: E402

from src.analysis.ipo import analyse_ipo  # noqa: E402
from src.cache import Cache  # noqa: E402
from src.providers.bse import BSEProvider  # noqa: E402
from src.providers.ipo import (  # noqa: E402
    BSE_HEADERS, IPOData, _f, _object_around, _flight_blob,
    classify_instrument, parse_bid_timestamp,
)
from src.providers.yahoo import YahooProvider  # noqa: E402

IG_DETAIL = "https://www.investorgain.com/ipo/{slug}/{ipo_id}"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
INDEX = ROOT / "data" / "ig_ipo_index.json"


def norm(text) -> str:
    text = re.sub(r"\b(limited|ltd|private|pvt|the|india|indian|ipo)\b",
                  "", str(text).lower())
    return re.sub(r"[^a-z0-9]", "", text)


# ---------------------------------------------------------------------------
# inputs
# ---------------------------------------------------------------------------

def build_from_detail(slug: str, ipo_id: int, cache) -> IPOData | None:
    """Reconstruct the engine's inputs for a past issue from its own page."""
    key = f"backtest_detail_{ipo_id}"
    html = cache.get(key, ttl_hours=24 * 30)
    if html is None:
        try:
            resp = requests.get(IG_DETAIL.format(slug=slug, ipo_id=ipo_id),
                                headers=UA, timeout=30)
            if resp.status_code != 200:
                return None
            html = resp.text
        except requests.RequestException:
            return None
        cache.set(key, html)

    blob = _flight_blob(html)
    if not blob:
        return None
    core = _object_around(blob, blob.find('"issue_price_lower"'))
    if not core:
        return None

    def d(key_):
        raw = core.get(key_)
        return raw if raw not in ("", None) else None

    ipo = IPOData(
        name=str(core.get("company_name") or core.get("current_company_name") or slug),
        ipo_type="SME" if "sme" in str(core.get("issue_category", "")).lower() else "MainBoard",
        open_date=_iso(d("issue_open_date")),
        close_date=_iso(d("issue_close_date")),
        price_low=_f(d("issue_price_lower")),
        price_high=_f(d("issue_price_upper")),
        lot_size=int(core["market_lot_size"]) if core.get("market_lot_size") else None,
        fresh_issue_amt=_f(d("issue_size_fresh_in_amt")),
        ofs_amt=_f(d("issue_size_ofs_in_amt")),
        eps=_f(d("kpi_eps")), eps_post=_f(d("kpi_eps_post")),
        pe_ratio=_f(d("pe_ratio")), post_pe_ratio=_f(d("post_pe_ratio")),
        ronw=_f(d("kpi_ronw")), ronw_prev=_f(d("kpi_ronw_2")),
        debt_equity=_f(d("kpi_debt_equity")),
        pat_margin=_f(d("kpi_pat_margin")), pat_margin_prev=_f(d("kpi_pat_margin_2")),
        ebitda_margin=_f(d("kpi_ebitda")),
        promoter_pre=_f(d("promoter_shareholding_pre_issue")),
        promoter_post=_f(d("promoter_shareholding_post_issue")),
        financial_date=d("latest_financial_dt"),
        bse_listed=True,
        detail_url=IG_DETAIL.format(slug=slug, ipo_id=ipo_id),
    )
    ipo.instrument = classify_instrument(
        ipo.name, str(core.get("issue_category", ""))
    )
    if ipo.price_high and ipo.price_low:
        ipo.price_band = f"{ipo.price_low:.2f} - {ipo.price_high:.2f}"
    status = core.get("anchor_investor_status")
    ipo.anchor_status = int(status) if str(status).strip() in ("0", "1") else None
    shares = _f(core.get("shares_offered_anchor_investor"))
    ipo.anchor_shares = int(shares) if shares else None

    # subscription series
    subs, seen = [], set()
    for m in re.finditer(r'"tsb_ipo_id"\s*:', blob):
        obj = _object_around(blob, m.start())
        if not obj or obj.get("total") in (None, ""):
            continue
        marker = str(obj.get("bid_date") or obj.get("id"))
        if marker in seen:
            continue
        seen.add(marker)
        subs.append(obj)
    subs.sort(key=lambda r: str(r.get("create_date") or r.get("bid_date") or ""))
    ipo.sub_history = [{
        "bid_date": s.get("bid_date"), "qib": _f(s.get("qib")),
        "nii": _f(s.get("nii")), "rii": _f(s.get("rii")), "total": _f(s.get("total")),
    } for s in subs]

    # grey market series
    gmps, gseen = [], set()
    for m in re.finditer(r'"gmp_percent_calc"\s*:', blob):
        obj = _object_around(blob, m.start())
        if not obj or _f(obj.get("gmp_percent_calc")) is None:
            continue
        marker = str(obj.get("gmp_date"))
        if marker in gseen:
            continue
        gseen.add(marker)
        gmps.append(obj)
    ipo.gmp_history = [{
        "date": g.get("gmp_date"), "gmp": _f(g.get("gmp")),
        "pct": _f(g.get("gmp_percent_calc")),
        "estimated_listing": _f(g.get("estimated_listing_price")),
    } for g in gmps]
    return ipo


def _iso(value):
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def apply_snapshot(ipo: IPOData, strict: bool) -> IPOData:
    """Point the issue at the data available at the chosen decision moment.

    strict=False -> the final book (known just after you could last apply)
    strict=True  -> the previous day's book (certainly on screen while open)
    """
    hist = ipo.sub_history
    if hist:
        row = hist[-2] if (strict and len(hist) >= 2) else hist[-1]
        ipo.sub_qib, ipo.sub_nii = row.get("qib"), row.get("nii")
        ipo.sub_rii, ipo.sub_total = row.get("rii"), row.get("total")
        ipo.sub_updated = row.get("bid_date")

    # Only grey market quotes dated on or before the close - never after.
    if ipo.gmp_history and ipo.close_date:
        usable = []
        for g in ipo.gmp_history:
            try:
                when = datetime.strptime(str(g["date"]), "%d-%m-%Y").date()
            except (ValueError, TypeError, KeyError):
                continue
            if when <= ipo.close_date:
                usable.append((when, g))
        if usable:
            usable.sort(key=lambda x: x[0], reverse=True)
            latest = usable[0][1]
            ipo.gmp, ipo.gmp_pct = latest.get("gmp"), latest.get("pct")
            ipo.estimated_listing = latest.get("estimated_listing")
            ipo.gmp_history = [g for _, g in usable]
    return ipo


# ---------------------------------------------------------------------------
# outcomes
# ---------------------------------------------------------------------------

def build_outcome_index(bse: BSEProvider) -> dict:
    master = bse.scrip_master()
    if master is None:
        return {}
    return {norm(r["FinInstrmNm"]): r for _, r in master.iterrows()}


def listing_result(name: str, issue_price: float, names: dict, yahoo) -> dict | None:
    """First-day close for a listed issue, matched by company name."""
    key = norm(name)
    row = names.get(key)
    if row is None:
        close = difflib.get_close_matches(key, list(names.keys()), n=1, cutoff=0.82)
        if not close:
            return None
        row = names[close[0]]
    symbol = str(row["TckrSymb"])
    series = yahoo.get_history(symbol, 400)
    if not series or not len(series):
        return None
    return {
        "symbol": symbol,
        "listing_date": series.dates[0],
        "listing_close": float(series.close[0]),
        "gain_pct": (float(series.close[0]) - issue_price) / issue_price * 100,
    }


# ---------------------------------------------------------------------------

def candidates(cache, limit: int) -> list[tuple[str, int]]:
    """Recent issues, newest id first - ids rise over time."""
    if not INDEX.exists():
        print(f"Missing {INDEX}. Run the sitemap indexer first.")
        return []
    index = json.loads(INDEX.read_text(encoding="utf-8"))
    ordered = sorted(index.items(), key=lambda kv: -kv[1])
    return ordered[: limit * 3]


def main() -> int:
    parser = argparse.ArgumentParser(description="Backtest IPO verdicts.")
    parser.add_argument("--limit", type=int, default=40,
                        help="max listed issues to evaluate")
    parser.add_argument("--min-age-days", type=int, default=3,
                        help="require the issue to have closed this long ago")
    parser.add_argument("--csv", type=Path, help="write per-issue detail here")
    parser.add_argument("--strict", action="store_true",
                        help="use the previous day's book (no look-ahead at all)")
    args = parser.parse_args()

    config = yaml.safe_load((ROOT / "config" / "config.yaml").read_text(encoding="utf-8"))
    cache = Cache(ROOT / "data" / "cache", 24)
    bse = BSEProvider(cache=cache, config=config)
    yahoo = YahooProvider(cache=cache, config=config)

    print("Loading BSE scrip master for outcome matching...")
    names = build_outcome_index(bse)
    print(f"  {len(names)} listed companies\n")

    cutoff = date.today() - timedelta(days=args.min_age_days)
    rows: list[dict] = []
    checked = 0

    for slug, ipo_id in candidates(cache, args.limit):
        if len(rows) >= args.limit:
            break
        checked += 1
        ipo = build_from_detail(slug, ipo_id, cache)
        if not ipo or not ipo.close_date or not ipo.price_high:
            continue
        if ipo.close_date > cutoff:
            continue                       # not listed yet
        if not ipo.sub_history:
            continue                       # no demand data to score on

        ipo = apply_snapshot(ipo, strict=args.strict)
        issue_price = ipo.price_high
        outcome = listing_result(ipo.name, issue_price, names, yahoo)
        if not outcome:
            continue

        verdict = analyse_ipo(ipo, config, today=ipo.close_date)
        rows.append({
            "name": ipo.name[:38],
            "type": ipo.ipo_type,
            "close_date": ipo.close_date,
            "verdict": verdict.verdict,
            "score": round(verdict.score, 3),
            "issue_price": issue_price,
            "listing_close": round(outcome["listing_close"], 2),
            "gain_pct": round(outcome["gain_pct"], 2),
            "sub_total": ipo.sub_total,
            "sub_qib": ipo.sub_qib,
            "gmp_pct": ipo.gmp_pct,
            "anchor": ipo.anchor_status,
            "symbol": outcome["symbol"],
            "listing_date": outcome["listing_date"],
        })
        print(f"  {len(rows):>3}. {ipo.name[:34]:<34} {verdict.verdict:<9} "
              f"score={verdict.score:+.2f}  actual={outcome['gain_pct']:+7.1f}%")

    if not rows:
        print("\nNo issues could be evaluated. Widen --limit or lower --min-age-days.")
        return 1

    report(rows, strict=args.strict, checked=checked)
    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        print(f"\nPer-issue detail -> {args.csv}")
    return 0


def report(rows: list[dict], strict: bool, checked: int) -> None:
    n = len(rows)
    gains = [r["gain_pct"] for r in rows]
    mode = ("previous day's book (no look-ahead)" if strict
            else "final book at the close (mild look-ahead)")

    print("\n" + "=" * 78)
    print(f"BACKTEST - {n} listed issues   ·   decision point: {mode}")
    print("=" * 78)
    print(f"Baseline: applying to everything averages {sum(gains)/n:+.1f}% "
          f"({sum(1 for g in gains if g > 0)}/{n} positive)")

    print(f"\n{'VERDICT':<12} {'N':>3} {'AVG GAIN':>10} {'MEDIAN':>9} "
          f"{'WIN RATE':>9} {'WORST':>9} {'BEST':>9}")
    print("-" * 78)
    order = ["APPLY", "CONSIDER", "NEUTRAL", "AVOID", "NO DATA"]
    for verdict in order:
        group = [r for r in rows if r["verdict"] == verdict]
        if not group:
            continue
        g = sorted(r["gain_pct"] for r in group)
        median = g[len(g) // 2] if len(g) % 2 else (g[len(g)//2 - 1] + g[len(g)//2]) / 2
        wins = sum(1 for x in g if x > 0)
        print(f"{verdict:<12} {len(group):>3} {sum(g)/len(g):>9.1f}% {median:>8.1f}% "
              f"{wins/len(g)*100:>8.0f}% {g[0]:>8.1f}% {g[-1]:>8.1f}%")

    positive = [r for r in rows if r["verdict"] in ("APPLY", "CONSIDER")]
    negative = [r for r in rows if r["verdict"] in ("AVOID", "NEUTRAL")]
    print("-" * 78)
    if positive and negative:
        pa = sum(r["gain_pct"] for r in positive) / len(positive)
        na = sum(r["gain_pct"] for r in negative) / len(negative)
        print(f"APPLY/CONSIDER ({len(positive)}) averaged {pa:+.1f}%   vs   "
              f"AVOID/NEUTRAL ({len(negative)}) {na:+.1f}%   spread {pa - na:+.1f}pp")
        if pa <= na:
            print("  The engine did NOT separate winners from losers in this sample.")

    # Does grey market premium actually predict the listing?
    with_gmp = [r for r in rows if r["gmp_pct"] is not None]
    if with_gmp:
        errs = [r["gain_pct"] - r["gmp_pct"] for r in with_gmp]
        over = sum(1 for e in errs if e < 0)
        print(f"\nGrey market premium vs reality ({len(with_gmp)} issues):")
        print(f"  overstated the listing in {over}/{len(with_gmp)} cases, "
              f"by {abs(sum(errs)/len(errs)):.1f}pp on average "
              f"({'over' if sum(errs) < 0 else 'under'}-optimistic)")
        try:
            import statistics
            corr = statistics.correlation([r["gmp_pct"] for r in with_gmp],
                                          [r["gain_pct"] for r in with_gmp])
            print(f"  correlation with actual listing gain: {corr:+.2f}")
        except Exception:
            pass

    # Anchor book
    no_anchor = [r for r in rows if r["anchor"] == 0]
    yes_anchor = [r for r in rows if r["anchor"] == 1]
    if no_anchor and yes_anchor:
        na_ = sum(r["gain_pct"] for r in no_anchor) / len(no_anchor)
        ya_ = sum(r["gain_pct"] for r in yes_anchor) / len(yes_anchor)
        print(f"\nAnchor book: with anchors ({len(yes_anchor)}) {ya_:+.1f}%  vs  "
              f"without ({len(no_anchor)}) {na_:+.1f}%")

    print(f"\n{n} issues evaluated from {checked} candidates examined.")
    print("A few dozen issues in one market period is weak evidence. It can show")
    print("a rule is broken; it cannot show one is good. Listing-day close only -")
    print("this says nothing about holding the stock afterwards.")


if __name__ == "__main__":
    raise SystemExit(main())
