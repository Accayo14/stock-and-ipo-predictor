"""Convert the published dashboard into a hosted-artifact page.

The artifact host wraps the file in its own document skeleton, so this strips
the <!doctype>/<html>/<head>/<body> wrapper and emits the title, styles and
body content on their own.

Two substantive changes are made in the process:

1. **Three theme states, not two.** The site CSS only handles
   `prefers-color-scheme`. An artifact viewer can also stamp an explicit
   `data-theme` on the root, and a palette defined only inside a media query
   never applies in that case - which renders one theme's text on the other
   theme's ground. The dark tokens are therefore emitted twice: once guarded
   against an explicit light choice, once for an explicit dark choice.

2. **Links that resolve.** The site's nav points at sibling pages that do not
   exist beside a hosted artifact, so those become links to the repository
   and the disclaimer stays inline where it cannot be missed.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "docs" / "index.html"
TARGET = ROOT / "data" / "artifact_dashboard.html"
REPO = "https://github.com/Accayo14/stock-and-ipo-predictor"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def make_theme_aware(css: str) -> str:
    """Emit the dark palette for both the system default and an explicit stamp."""
    match = re.search(
        r"@media\s*\(prefers-color-scheme:\s*dark\)\s*\{\s*(:root\s*\{.*?\})\s*\}",
        css, re.S,
    )
    if not match:
        print("  ! no dark-mode block found; leaving theme CSS untouched")
        return css

    dark_root = match.group(1)                       # ":root{ --bg:...; }"
    tokens = re.search(r":root\s*\{(.*?)\}", dark_root, re.S).group(1)

    replacement = (
        "@media (prefers-color-scheme:dark){\n"
        "    /* System dark, unless the viewer explicitly chose light. */\n"
        f"    :root:not([data-theme=\"light\"]){{{tokens}}}\n"
        "  }\n"
        "  /* An explicit dark choice must win regardless of the OS setting. */\n"
        f"  :root[data-theme=\"dark\"]{{{tokens}}}"
    )
    return css[: match.start()] + replacement + css[match.end():]


def main() -> int:
    if not SOURCE.exists():
        print(f"No dashboard at {SOURCE}. Run `python ipo.py && python publish.py`.")
        return 1
    html = SOURCE.read_text(encoding="utf-8")

    styles = re.findall(r"<style>(.*?)</style>", html, re.S)
    body = re.search(r"<body[^>]*>(.*)</body>", html, re.S)
    if not styles or not body:
        print("Could not parse the dashboard structure.")
        return 1

    css = make_theme_aware("\n".join(styles))
    content = body.group(1)

    # Sibling pages do not exist next to a hosted artifact.
    content = content.replace('href="./index.html"', f'href="{REPO}"')
    content = content.replace('href="./archive/index.html"',
                              f'href="{REPO}/tree/main/docs/archive"')
    content = content.replace('href="./disclaimer.html"',
                              f'href="{REPO}/blob/main/DISCLAIMER.md"')
    content = content.replace('<a href="./disclaimer.html">Read the full disclaimer</a>',
                              f'<a href="{REPO}/blob/main/DISCLAIMER.md">Read the full disclaimer</a>')
    # The in-page back link must stay a hash link, not jump to the repo.
    content = content.replace(f'<a class="back" href="{REPO}">', '<a class="back" href="#">')

    out = (
        "<title>BSE IPO Dashboard</title>\n"
        f"<style>\n{css}\n</style>\n"
        f"{content}\n"
    )
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(out, encoding="utf-8")

    print(f"Artifact page -> {TARGET}  ({len(out):,} chars)")
    for label, ok in {
        "no <!doctype>": "<!doctype" not in out.lower(),
        "no <html>/<body>": "<html" not in out.lower() and "<body" not in out.lower(),
        "has <title>": "<title>" in out,
        "explicit dark stamp handled": '[data-theme="dark"]' in out,
        "explicit light respected": '[data-theme="light"]' in out,
        "body background painted": re.search(r"body\{[^}]*background:", out) is not None,
        "disclaimer banner present": "site-warning" in out,
    }.items():
        print(f"  {'ok  ' if ok else 'FAIL'} {label}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
