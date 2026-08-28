"""Push the morning report to Telegram and/or email.

Credentials are read from config/secrets.env, which is gitignored. Nothing
here ever raises into the caller: a delivery failure must not lose you the
report you already generated, so failures are returned as messages.
"""

from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path

import requests

TELEGRAM_LIMIT = 4096


def load_secrets(path: Path) -> dict[str, str]:
    """Minimal KEY=value parser - avoids a dependency for eight lines of work."""
    secrets: dict[str, str] = {}
    if not Path(path).exists():
        return secrets
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        secrets[key.strip()] = value.strip().strip('"').strip("'")
    return secrets


def _get(secrets: dict, key: str) -> str | None:
    return secrets.get(key) or os.environ.get(key)


def summarise(report, max_chars: int = TELEGRAM_LIMIT) -> str:
    """Compact text summary suitable for a phone notification."""
    lines: list[str] = [f"📊 BSE Morning — {report.run_at:%a %d %b %Y}", ""]

    if report.load_errors:
        lines.append("⚠️ Portfolio could not be loaded:")
        lines.extend(f"• {e}" for e in report.load_errors)
        return "\n".join(lines)[:max_chars]

    m = report.market
    if m.level:
        arrow = "🟢" if (m.change_pct or 0) >= 0 else "🔴"
        lines.append(f"{arrow} Sensex {m.level:,.0f} ({m.change_pct:+.2f}%)")

    p = report.portfolio
    if p:
        sign = "🟢" if p.total_pnl >= 0 else "🔴"
        lines.append(
            f"{sign} Portfolio ₹{p.total_value:,.0f} "
            f"({p.total_pnl:+,.0f}, {p.total_pnl_pct:+.1%})"
        )
    lines.append("")

    # Only surface things that need a decision.
    urgent = [a for a in report.positions if a.final_action in ("EXIT", "TRIM")]
    buys = [a for a in report.positions
            if a.final_action in ("STRONG BUY", "ACCUMULATE")]

    if urgent:
        lines.append("🔻 Needs attention:")
        for a in urgent:
            top = a.signal.evidence[0].statement if a.signal.evidence else ""
            lines.append(f"• {a.position.symbol} — {a.final_action} ({a.pnl_pct:+.1%})")
            if top:
                lines.append(f"  {top[:110]}")
        lines.append("")

    if buys:
        lines.append("🔼 Adding candidates:")
        for a in buys:
            lines.append(f"• {a.position.symbol} — {a.final_action} "
                         f"(score {a.signal.composite:+.2f})")
        lines.append("")

    if not urgent and not buys and report.positions:
        lines.append("✅ No action needed on any holding today.")
        lines.append("")

    actionable = [i for i in report.ipos if i.verdict in ("APPLY", "CONSIDER", "AVOID")]
    if actionable:
        lines.append("📋 IPOs open:")
        for i in actionable:
            icon = {"APPLY": "✅", "CONSIDER": "🟡", "AVOID": "❌"}.get(i.verdict, "•")
            days = f", {i.data.days_left}d left" if i.data.days_left is not None else ""
            lines.append(f"{icon} {i.name} — {i.verdict}{days}")
        lines.append("")

    if p and p.warnings:
        lines.append(f"⚠️ {len(p.warnings)} risk warning(s) — see full report.")

    text = "\n".join(lines)
    return text[: max_chars - 3] + "..." if len(text) > max_chars else text


# ---------------------------------------------------------------------------

def send_telegram(text: str, secrets: dict) -> tuple[bool, str]:
    token = _get(secrets, "TELEGRAM_BOT_TOKEN")
    chat_id = _get(secrets, "TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False, "TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID missing from secrets.env"
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text[:TELEGRAM_LIMIT],
                  "disable_web_page_preview": True},
            timeout=25,
        )
        if resp.status_code == 200 and resp.json().get("ok"):
            return True, "sent to Telegram"
        return False, f"Telegram API returned {resp.status_code}: {resp.text[:180]}"
    except requests.RequestException as exc:
        return False, f"Telegram request failed: {exc}"


def send_email(subject: str, text: str, html_path: Path | None,
               secrets: dict) -> tuple[bool, str]:
    host = _get(secrets, "SMTP_HOST")
    port = int(_get(secrets, "SMTP_PORT") or 587)
    user = _get(secrets, "SMTP_USER")
    password = _get(secrets, "SMTP_PASSWORD")
    to_addr = _get(secrets, "EMAIL_TO") or user
    if not all([host, user, password, to_addr]):
        return False, "SMTP_HOST/SMTP_USER/SMTP_PASSWORD/EMAIL_TO missing from secrets.env"

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = user
    message["To"] = to_addr
    message.set_content(text)

    if html_path and Path(html_path).exists():
        # Inline the full report so it renders in the mail client itself.
        message.add_alternative(
            Path(html_path).read_text(encoding="utf-8"), subtype="html"
        )
    try:
        context = ssl.create_default_context()
        if port == 465:
            with smtplib.SMTP_SSL(host, port, context=context, timeout=30) as server:
                server.login(user, password)
                server.send_message(message)
        else:
            with smtplib.SMTP(host, port, timeout=30) as server:
                server.starttls(context=context)
                server.login(user, password)
                server.send_message(message)
        return True, f"emailed to {to_addr}"
    except (smtplib.SMTPException, OSError) as exc:
        return False, f"Email failed: {exc}"


def deliver(report, config: dict, root: Path,
            html_path: Path | None = None) -> list[str]:
    """Send via every enabled channel. Returns human-readable status lines."""
    results: list[str] = []
    delivery = config.get("delivery", {})
    secrets = load_secrets(root / "config" / "secrets.env")
    text = summarise(report)

    telegram = delivery.get("telegram", {})
    if telegram.get("enabled"):
        body = text
        if telegram.get("send_full_report") and html_path:
            body = text + f"\n\nFull report: {html_path}"
        ok, message = send_telegram(body, secrets)
        results.append(("✓ " if ok else "✗ ") + message)

    email_cfg = delivery.get("email", {})
    if email_cfg.get("enabled"):
        prefix = email_cfg.get("subject_prefix", "[BSE Morning]")
        subject = f"{prefix} {report.run_at:%d %b %Y}"
        ok, message = send_email(subject, text, html_path, secrets)
        results.append(("✓ " if ok else "✗ ") + message)

    return results
