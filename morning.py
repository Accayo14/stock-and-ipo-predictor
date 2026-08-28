"""BSE morning analyser - command line entry point.

Typical use, before the market opens:

    python morning.py

Options let you skip IPOs, point at a different portfolio file, force fresh
data, or emit only the JSON facts bundle (which is what the /morning Claude
Code command reads).
"""

from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from datetime import date
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

from src.engine import Engine, facts_bundle  # noqa: E402
from src.report import deliver as deliver_mod  # noqa: E402
from src.report import html as html_report  # noqa: E402
from src.report import markdown as md_report  # noqa: E402
from src.report import terminal as term_report  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Analyse your BSE portfolio and today's IPOs before the open.",
    )
    parser.add_argument("--portfolio", type=Path, default=None,
                        help="path to holdings CSV (default: config/portfolio.csv)")
    parser.add_argument("--no-ipo", action="store_true",
                        help="skip IPO analysis")
    parser.add_argument("--no-deliver", action="store_true",
                        help="skip Telegram/email delivery even if enabled")
    parser.add_argument("--fresh", action="store_true",
                        help="ignore cached data and refetch everything")
    parser.add_argument("--json-only", action="store_true",
                        help="write the facts bundle and print its path, nothing else")
    parser.add_argument("--open", action="store_true",
                        help="open the HTML report in your browser when done")
    parser.add_argument("--quiet", action="store_true",
                        help="suppress the terminal report")
    args = parser.parse_args()

    engine = Engine(ROOT)
    if args.fresh:
        removed = engine.cache.clear()
        if not args.quiet:
            print(f"Cleared {removed} cached file(s).")

    report = engine.run(portfolio_path=args.portfolio, include_ipos=not args.no_ipo)

    # One directory per day keeps the archive browsable.
    day = (report.session_date or date.today()).isoformat()
    out_dir = ROOT / "data" / "reports" / day
    out_dir.mkdir(parents=True, exist_ok=True)

    bundle_path = out_dir / "facts.json"
    bundle_path.write_text(
        json.dumps(facts_bundle(report), indent=2, default=str), encoding="utf-8"
    )

    if args.json_only:
        print(bundle_path)
        return 0

    if not args.quiet:
        term_report.render(report)

    html_path = out_dir / "report.html"
    md_path = out_dir / "report.md"
    try:
        html_report.render(report, html_path)
    except Exception as exc:                                   # noqa: BLE001
        print(f"HTML report failed: {exc}", file=sys.stderr)
        html_path = None
    md_path.write_text(md_report.render(report), encoding="utf-8")

    if not args.quiet:
        print(f"\nHTML     {html_path}")
        print(f"Markdown {md_path}")
        print(f"Facts    {bundle_path}")

    if not args.no_deliver:
        for line in deliver_mod.deliver(report, engine.config, ROOT, html_path):
            print(line)

    if args.open and html_path:
        webbrowser.open(html_path.resolve().as_uri())

    # Non-zero exit if the portfolio could not be read, so a scheduled task
    # surfaces the failure instead of appearing to succeed silently.
    return 1 if report.load_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
