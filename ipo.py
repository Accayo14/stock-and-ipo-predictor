"""IPO dashboard - command line entry point.

    python ipo.py                 # open + upcoming issues, writes the dashboard
    python ipo.py --open-only     # only what you can apply to right now
    python ipo.py --show          # open the dashboard in your browser
    python ipo.py --json          # print the facts bundle path and exit

The dashboard merges two layers that are kept deliberately separate:

  1. Computed facts and scores - deterministic, from BSE and InvestorGain.
  2. News and context research - written into data/ipo_notes/<date>.json by
     the /ipo command in Claude Code, because a Python script cannot read a
     news story, a regulatory notice, or the quality of an anchor book.

Running this file produces layer 1. If layer 2 exists for today, it is merged
in; if not, the dashboard says so plainly rather than implying the verdicts
already account for the news.
"""

from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Windows consoles frequently default to cp1252, which cannot encode the box
# drawing characters and rupee signs this script prints - that raised
# UnicodeEncodeError and killed an otherwise successful run. Force UTF-8 so a
# daily run never dies on its own output.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import yaml  # noqa: E402

from src.analysis.ipo import analyse_ipos  # noqa: E402
from src.cache import Cache  # noqa: E402
from src.report import ipo_dashboard  # noqa: E402
from src.report import ipo_deliver  # noqa: E402


def notes_path(day: date) -> Path:
    return ROOT / "data" / "ipo_notes" / f"{day.isoformat()}.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyse today's BSE IPOs.")
    parser.add_argument("--open-only", action="store_true",
                        help="exclude upcoming issues")
    parser.add_argument("--upcoming-days", type=int, default=14,
                        help="how far ahead to include upcoming issues")
    parser.add_argument("--show", action="store_true",
                        help="open the dashboard in your browser")
    parser.add_argument("--fresh", action="store_true",
                        help="ignore cached data and refetch")
    parser.add_argument("--json", action="store_true",
                        help="print the facts bundle path and exit")
    parser.add_argument("--quiet", action="store_true",
                        help="suppress the terminal summary")
    parser.add_argument("--send", action="store_true",
                        help="push the summary to Telegram/email")
    args = parser.parse_args()

    config = yaml.safe_load(
        (ROOT / "config" / "config.yaml").read_text(encoding="utf-8")
    )
    cache = Cache(ROOT / "data" / "cache", config["data"]["cache_hours"])
    if args.fresh:
        cache.clear()

    analyses = analyse_ipos(
        config, cache,
        include_upcoming=not args.open_only,
        upcoming_within_days=args.upcoming_days,
    )

    today = date.today()
    out_dir = ROOT / "data" / "reports" / today.isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)

    bundle_path = out_dir / "ipos.json"
    bundle_path.write_text(
        json.dumps(
            {
                "generated": datetime.now().isoformat(),
                "date": today.isoformat(),
                "count": len(analyses),
                "ipos": [a.to_dict() for a in analyses],
            },
            indent=2, default=str,
        ),
        encoding="utf-8",
    )

    if args.json:
        print(bundle_path)
        return 0

    notes, notes_date, notes_age = ipo_dashboard.load_notes_with_fallback(
        ROOT / "data" / "ipo_notes", today
    )

    dash_path = out_dir / "ipo_dashboard.html"
    ipo_dashboard.render(
        analyses, dash_path, config,
        notes=notes, notes_generated=notes_date, notes_age_days=notes_age,
    )

    if not args.quiet:
        print(f"\nIPOs — {today:%A %d %B %Y}\n" + "─" * 68)
        if not analyses:
            print("  Nothing open or upcoming.")
        for a in analyses:
            d = a.data
            flag = "!" if a.urgency == "closes-today" else " "
            print(f"{flag} {a.verdict:<9} {a.name[:40]:<40} {a.ipo_type:<9} "
                  f"{a.urgency_label}")
            bits = []
            if d.price_band:
                bits.append(f"band {d.price_band}")
            if d.min_investment:
                bits.append(f"min ₹{d.min_investment:,.0f}")
            if d.sub_total is not None:
                bits.append(f"sub {d.sub_total:.2f}x")
            if d.gmp_pct is not None:
                arrow = ""
                if d.gmp_trend:
                    arrow = {"rising": "↑", "falling": "↓", "flat": "→"}[
                        d.gmp_trend["direction"]]
                bits.append(f"GMP {d.gmp_pct:.1f}%{arrow}")
            if bits:
                print(f"            {'  ·  '.join(bits)}")
        print("─" * 68)
        print(f"Dashboard  {dash_path}")
        print(f"Facts      {bundle_path}")
        if notes and notes_age == 0:
            print(f"News layer {len(notes)} issue(s) researched today")
        elif notes:
            print(f"News layer STALE — {len(notes)} issue(s) researched "
                  f"{notes_date} ({notes_age}d old). Subscription and GMP move "
                  f"daily; re-run /ipo in Claude Code to refresh.")
        else:
            print("News layer none — run /ipo in Claude Code to add it")

    if args.send:
        for line in ipo_deliver.deliver_ipos(
            analyses, config, ROOT, notes=notes, html_path=dash_path
        ):
            print(line)

    if args.show:
        webbrowser.open(dash_path.resolve().as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
