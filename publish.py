"""Publish the IPO dashboard to docs/ for GitHub Pages.

    python publish.py            # copy the latest dashboard into the site
    python publish.py --push     # ...and commit + push it

The site is PUBLIC, so this script is deliberately narrow about what it moves:
only the IPO dashboard, which contains public market data and analysis of it.
The portfolio report renders real holdings and is never published - it is
excluded here and in .gitignore, belt and braces.

Every published page gets a site header and a disclaimer banner injected, so
no page can be read in isolation without the warning attached.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DOCS = ROOT / "docs"
ARCHIVE = DOCS / "archive"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO_URL = "https://github.com/Accayo14/stock-and-ipo-predictor"

# Anything matching these never reaches docs/, regardless of how it got there.
FORBIDDEN = ("portfolio", "holdings", "secrets", "facts.json")

BANNER = """
<div id="site-nav">
  <a class="brand" href="./index.html">IPO Dashboard</a>
  <nav>
    <a href="./archive/index.html">Archive</a>
    <a href="./disclaimer.html">Disclaimer</a>
    <a href="{repo}" rel="noopener">Source</a>
  </nav>
</div>
<div id="site-warning">
  <strong>Not financial advice.</strong> This is the output of a scoring
  formula applied to public data, published so the logic can be inspected —
  not so the result can be followed. IPOs regularly list below their issue
  price, including heavily subscribed ones. You can lose money.
  <a href="./disclaimer.html">Read the full disclaimer</a>.
</div>
"""

BANNER_CSS = """
<style>
  #site-nav{display:flex;justify-content:space-between;align-items:center;
    gap:16px;flex-wrap:wrap;max-width:1080px;margin:0 auto;
    padding:14px 18px 0}
  #site-nav .brand{font-weight:700;font-size:15px;text-decoration:none;
    color:var(--ink,#12161d)}
  #site-nav nav{display:flex;gap:16px}
  #site-nav nav a{font-size:13px;text-decoration:none;
    color:var(--accent,#1f5fbf)}
  #site-nav nav a:hover{text-decoration:underline}
  #site-warning{max-width:1080px;margin:12px auto 0;padding:12px 16px;
    border:1px solid var(--warn,#96610a);background:var(--warn-bg,#fdf2e0);
    color:var(--warn,#96610a);border-radius:10px;font-size:13px;
    line-height:1.5}
  #site-warning a{color:inherit;font-weight:600}
  @media (prefers-color-scheme:dark){
    #site-nav .brand{color:#e9eef5}
  }
</style>
"""


def inject(html: str, generated: str) -> str:
    """Put the site header and disclaimer into a rendered dashboard."""
    banner = BANNER.format(repo=REPO_URL)
    if "</head>" in html:
        html = html.replace("</head>", BANNER_CSS + "</head>", 1)
    match = re.search(r"<body[^>]*>", html)
    if match:
        html = html[: match.end()] + banner + html[match.end():]
    else:
        html = banner + html
    return html.replace(
        "</body>",
        f'<p style="max-width:1080px;margin:0 auto 40px;padding:0 18px;'
        f'font-size:12px;color:#8a94a2">Generated {generated}. '
        f'Not financial advice — see the disclaimer.</p></body>',
        1,
    )


def latest_dashboard() -> tuple[Path, str] | tuple[None, None]:
    reports = ROOT / "data" / "reports"
    if not reports.exists():
        return None, None
    days = sorted(
        (d for d in reports.iterdir()
         if d.is_dir() and re.fullmatch(r"\d{4}-\d{2}-\d{2}", d.name)),
        reverse=True,
    )
    for day in days:
        candidate = day / "ipo_dashboard.html"
        if candidate.exists():
            return candidate, day.name
    return None, None


def build_archive_index(days: list[str]) -> str:
    rows = "\n".join(
        f'      <li><a href="./{d}.html">{d}</a></li>' for d in sorted(days, reverse=True)
    ) or "      <li>No archived reports yet.</li>"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>IPO Dashboard — Archive</title>
<style>
 body{{margin:0;background:#f5f6f8;color:#12161d;
   font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
   font-size:15px;line-height:1.55}}
 @media (prefers-color-scheme:dark){{body{{background:#0c0f14;color:#e9eef5}}}}
 .wrap{{max-width:1080px;margin:0 auto;padding:28px 18px 60px}}
 h1{{font-size:24px;margin:0 0 6px}} p{{color:#48525f}}
 ul{{list-style:none;padding:0}} li{{padding:8px 0;border-bottom:1px solid #e0e5ec}}
 a{{color:#1f5fbf;text-decoration:none}} a:hover{{text-decoration:underline}}
 .warn{{border:1px solid #96610a;background:#fdf2e0;color:#96610a;
   border-radius:10px;padding:12px 16px;font-size:13px;margin:14px 0 22px}}
</style></head><body><div class="wrap">
  <p><a href="./../index.html">&larr; Latest</a></p>
  <h1>Archive</h1>
  <div class="warn"><strong>Not financial advice.</strong> Past reports are kept
    so the reasoning can be checked against what actually happened. They were
    accurate to the data available on their date and are not updated.</div>
  <ul>
{rows}
  </ul>
</div></body></html>
"""


def build_disclaimer_page() -> str:
    md = (ROOT / "DISCLAIMER.md").read_text(encoding="utf-8")
    # Minimal markdown rendering - headings, bold, list items, paragraphs.
    out, in_list = [], False
    for line in md.splitlines():
        s = line.strip()
        if not s:
            if in_list:
                out.append("</ul>")
                in_list = False
            continue
        s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
        s = re.sub(r"`(.+?)`", r"<code>\1</code>", s)
        if s.startswith("# "):
            out.append(f"<h1>{s[2:]}</h1>")
        elif s.startswith("## "):
            out.append(f"<h2>{s[3:]}</h2>")
        elif s.startswith("- "):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{s[2:]}</li>")
        else:
            if in_list:
                out.append("</ul>")
                in_list = False
            out.append(f"<p>{s}</p>")
    if in_list:
        out.append("</ul>")
    body = "\n".join(out)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Disclaimer — IPO Dashboard</title>
<style>
 body{{margin:0;background:#f5f6f8;color:#12161d;
   font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
   font-size:15px;line-height:1.6}}
 @media (prefers-color-scheme:dark){{body{{background:#0c0f14;color:#e9eef5}}}}
 .wrap{{max-width:760px;margin:0 auto;padding:28px 18px 70px}}
 h1{{font-size:26px;margin:0 0 14px}}
 h2{{font-size:15px;text-transform:uppercase;letter-spacing:.07em;
   color:#8a94a2;margin:30px 0 8px}}
 a{{color:#1f5fbf}} li{{margin:5px 0}}
 strong{{font-weight:680}}
</style></head><body><div class="wrap">
  <p><a href="./index.html">&larr; Back to the dashboard</a></p>
  {body}
</div></body></html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish the IPO dashboard.")
    parser.add_argument("--push", action="store_true",
                        help="commit and push the site to GitHub")
    parser.add_argument("--message", help="commit message override")
    args = parser.parse_args()

    source, day = latest_dashboard()
    if not source:
        print("No IPO dashboard found. Run `python ipo.py` first.")
        return 1

    DOCS.mkdir(exist_ok=True)
    ARCHIVE.mkdir(parents=True, exist_ok=True)

    generated = datetime.now().strftime("%d %b %Y, %H:%M IST")
    html = inject(source.read_text(encoding="utf-8"), generated)

    (DOCS / "index.html").write_text(html, encoding="utf-8")
    archived = html.replace('href="./', 'href="./../').replace(
        'href="./../index.html"', 'href="./../index.html"'
    )
    (ARCHIVE / f"{day}.html").write_text(archived, encoding="utf-8")

    days = [p.stem for p in ARCHIVE.glob("*.html") if p.stem != "index"]
    (ARCHIVE / "index.html").write_text(build_archive_index(days), encoding="utf-8")
    (DOCS / "disclaimer.html").write_text(build_disclaimer_page(), encoding="utf-8")
    (DOCS / ".nojekyll").write_text("", encoding="utf-8")

    # Safety net: nothing personal may sit in the published folder.
    leaked = [
        p for p in DOCS.rglob("*")
        if p.is_file() and any(word in p.name.lower() for word in FORBIDDEN)
    ]
    if leaked:
        print("REFUSING TO PUBLISH - personal files found in docs/:")
        for p in leaked:
            print("   ", p)
        return 1

    print(f"Published {day} dashboard -> docs/index.html")
    print(f"  archive entries: {len(days)}")
    print(f"  disclaimer     : docs/disclaimer.html")

    if args.push:
        return git_push(args.message or f"Update IPO dashboard for {day}")
    return 0


def git_push(message: str) -> int:
    def run(*cmd) -> tuple[int, str]:
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        return proc.returncode, (proc.stdout + proc.stderr).strip()

    code, out = run("git", "add", "-A")
    if code:
        print(out)
        return code
    code, out = run("git", "status", "--porcelain")
    if not out.strip():
        print("Nothing to commit - the site is already up to date.")
        return 0
    code, out = run("git", "commit", "-m", message)
    if code:
        print(out)
        return code
    print(out.splitlines()[0] if out else "committed")
    code, out = run("git", "push")
    print(out)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
