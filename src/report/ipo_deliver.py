"""Push the IPO summary to Telegram / email.

Separate from the portfolio delivery because the urgency is different: an
IPO window closes at a fixed hour on a fixed day, so the message is ordered
by deadline rather than by conviction. What closes today comes first, whatever
the verdict.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from .deliver import TELEGRAM_LIMIT, load_secrets, send_email, send_telegram

ICON = {
    "APPLY": "✅",       # white heavy check
    "CONSIDER": "\U0001f7e1",  # yellow circle
    "NEUTRAL": "⚪",     # white circle
    "AVOID": "❌",       # cross mark
    "NO DATA": "❓",     # question mark
}
ARROW = {"rising": "↑", "falling": "↓", "flat": "→"}


def _note_key(name: str) -> str:
    text = re.sub(r"\b(limited|ltd|private|pvt|ipo|the)\b", "", name.lower())
    return re.sub(r"[^a-z0-9]", "", text)


def summarise_ipos(analyses, notes: dict | None = None,
                   max_chars: int = TELEGRAM_LIMIT) -> str:
    """Phone-sized IPO summary, ordered by what needs deciding first."""
    notes = notes or {}
    lines: list[str] = [f"\U0001f4cb IPOs — {date.today():%a %d %b %Y}"]

    buckets = [
        ("⏰ CLOSES TODAY — decide now:", "closes-today", True),
        ("Closes tomorrow:", "closes-tomorrow", True),
        ("Open:", "open", True),
        ("Opening soon:", "upcoming", False),
    ]

    for title, urgency, show_reason in buckets:
        items = [a for a in analyses if a.urgency == urgency]
        if not items:
            continue
        lines.append("")
        lines.append(title)
        for a in items:
            d = a.data
            lines.append(f"{ICON.get(a.verdict, '-')} {a.name[:38]} — {a.verdict}")
            bits = []
            if d.min_investment:
                bits.append(f"min ₹{d.min_investment:,.0f}")
            if d.sub_total is not None:
                bits.append(f"{d.sub_total:.1f}x")
            if d.gmp_pct is not None:
                arrow = ARROW.get((d.gmp_trend or {}).get("direction"), "")
                bits.append(f"GMP {d.gmp_pct:.0f}%{arrow}")
            if bits:
                lines.append("   " + " · ".join(bits))
            if show_reason:
                note = notes.get(_note_key(a.name)) or {}
                reason = note.get("summary")
                if not reason and a.evidence:
                    reason = a.evidence[0].statement
                if reason:
                    lines.append(f"   {reason[:160]}")

    if not analyses:
        lines.append("")
        lines.append("No IPOs open or upcoming.")

    if not notes and analyses:
        lines.append("")
        lines.append("⚠ Figures only - no news research attached today.")

    lines.append("")
    lines.append("Decision support, not financial advice.")

    text = "\n".join(lines)
    if len(text) > max_chars:
        return text[: max_chars - 3] + "..."
    return text


def deliver_ipos(analyses, config: dict, root: Path,
                 notes: dict | None = None,
                 html_path: Path | None = None) -> list[str]:
    """Send the IPO summary over every enabled channel.

    Never raises: a delivery failure must not cost you the dashboard that was
    already generated successfully.
    """
    results: list[str] = []
    delivery = config.get("delivery", {})
    secrets = load_secrets(root / "config" / "secrets.env")
    text = summarise_ipos(analyses, notes)

    if delivery.get("telegram", {}).get("enabled"):
        ok, message = send_telegram(text, secrets)
        results.append(("✓ " if ok else "✗ ") + message)

    email_cfg = delivery.get("email", {})
    if email_cfg.get("enabled"):
        prefix = email_cfg.get("subject_prefix", "[BSE Morning]")
        subject = f"{prefix} IPOs {date.today():%d %b %Y}"
        ok, message = send_email(subject, text, html_path, secrets)
        results.append(("✓ " if ok else "✗ ") + message)

    if not results:
        results.append(
            "No delivery channel enabled - set delivery.telegram.enabled or "
            "delivery.email.enabled in config/config.yaml"
        )
    return results
