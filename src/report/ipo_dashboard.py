"""IPO dashboard renderer.

Shows, for every issue: the verdict, the full reasoning behind it, and every
data point we actually hold - so nothing sits behind a summary you cannot
audit. Where a figure is missing it is shown as missing rather than omitted,
because "we don't know" is itself decision-relevant.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

TEMPLATES = Path(__file__).resolve().parents[2] / "templates"

VERDICT_ORDER = {
    "APPLY": 0, "CONSIDER": 1, "NEUTRAL": 2, "AVOID": 3,
    "NO DATA": 4, "CLOSED": 5,
}
URGENCY_ORDER = {
    "closes-today": 0, "closes-tomorrow": 1, "open": 2, "upcoming": 3,
}


def rupees(value) -> str:
    """Indian digit grouping."""
    if value is None:
        return "—"
    try:
        value = float(value)
    except (TypeError, ValueError):
        return str(value)
    negative = value < 0
    whole, _, frac = f"{abs(value):.2f}".partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join(parts + [tail])
    out = f"₹{whole}" if frac == "00" else f"₹{whole}.{frac}"
    return ("−" + out) if negative else out


def crore(value) -> str:
    """Rupee amounts arrive in absolute rupees; crore is how India reads them."""
    if value is None:
        return "—"
    try:
        return f"₹{float(value) / 1e7:,.2f} Cr"
    except (TypeError, ValueError):
        return "—"


def gmp_spark(history: list[dict], width: int = 200, height: int = 40) -> str:
    """Inline SVG of the grey-market-premium history (oldest → newest)."""
    points = [h["pct"] for h in reversed(history or []) if h.get("pct") is not None]
    if len(points) < 2:
        return ""
    low, high = min(points), max(points)
    span = (high - low) or 1.0
    pad = 3
    step = (width - 2 * pad) / (len(points) - 1)
    coords = " ".join(
        f"{pad + i * step:.1f},"
        f"{height - pad - ((v - low) / span) * (height - 2 * pad):.1f}"
        for i, v in enumerate(points)
    )
    colour = "var(--up)" if points[-1] >= points[0] else "var(--down)"
    last_x = pad + (len(points) - 1) * step
    last_y = height - pad - ((points[-1] - low) / span) * (height - 2 * pad)
    return (
        f'<svg class="gmpspark" viewBox="0 0 {width} {height}" width="{width}" '
        f'height="{height}" preserveAspectRatio="none" aria-hidden="true">'
        f'<polyline points="{coords}" fill="none" stroke="{colour}" '
        f'stroke-width="1.8" stroke-linejoin="round" stroke-linecap="round"/>'
        f'<circle cx="{last_x:.1f}" cy="{last_y:.1f}" r="2.6" fill="{colour}"/>'
        f"</svg>"
    )


def sub_bar(multiple: float | None, cap: float = 10.0) -> float:
    """Subscription multiple → 0-100 bar width, log-ish so 50x doesn't
    flatten everything below 5x into nothing."""
    if multiple is None:
        return 0.0
    if multiple <= 1:
        return max(3.0, multiple * 30)
    import math
    return min(100.0, 30 + (math.log10(multiple) / math.log10(cap)) * 70)


def build_data_rows(a) -> list[dict]:
    """Every field we hold, grouped for display. Missing values stay visible."""
    d = a.data

    def pct(value, digits=1):
        return f"{value:.{digits}f}%" if value is not None else "—"

    def num(value, digits=2):
        return f"{value:,.{digits}f}" if value is not None else "—"

    def ratio(value):
        return f"{value:.2f}x" if value is not None else "—"

    groups = [
        ("The issue", [
            ("Price band", d.price_band or "—"),
            ("Face value", num(d.face_value)),
            ("Lot size", f"{d.lot_size} shares" if d.lot_size else "—"),
            ("Minimum investment", rupees(d.min_investment)),
            ("Total issue size", a.issue_size or "—"),
            ("Fresh issue", crore(d.fresh_issue_amt)),
            ("Offer for sale", crore(d.ofs_amt)),
            ("OFS share of issue",
             pct(d.ofs_share * 100, 0) if d.ofs_share is not None else "—"),
            ("Listing on", "BSE" if d.bse_listed else "—"),
            ("Registrar", d.registrar or "—"),
            ("Anchor investors", d.anchor_note or "—"),
            ("Sector", d.sector or "—"),
        ]),
        ("How much you can apply for", [
            ("Retail minimum", d.min_qty_desc or "—"),
            ("Retail maximum", d.max_retail_qty_desc or "—"),
            ("Small HNI from", d.min_hni_qty_desc or "—"),
            ("Big HNI from", d.min_bhni_qty_desc or "—"),
            ("Retail reservation", d.retail_reservation or "—"),
            ("QIB reservation", d.qib_reservation or "—"),
            ("NII reservation", d.nii_reservation or "—"),
        ]),
        ("Demand so far", [
            ("QIB (institutions)", ratio(d.sub_qib)),
            ("NII (wealthy individuals)", ratio(d.sub_nii)),
            ("Retail", ratio(d.sub_rii)),
            ("Total", ratio(d.sub_total)),
            ("Last updated", d.sub_updated or "—"),
            ("Retail allotment odds",
             (f"~{d.retail_allotment_odds * 100:.0f}% chance of 1 lot"
              if d.retail_allotment_odds is not None else "—")),
        ]),
        ("Grey market", [
            ("Premium", rupees(d.gmp) if d.gmp is not None else "—"),
            ("Premium %", pct(d.gmp_pct)),
            ("Implied listing price", rupees(d.estimated_listing)),
            ("Direction",
             (f"{d.gmp_trend['direction']} "
              f"({d.gmp_trend['change_pp']:+.1f}pp over "
              f"{d.gmp_trend['days']}d)") if d.gmp_trend else "—"),
            ("Quotes on record",
             f"{len(d.gmp_history)} days" if d.gmp_history else "—"),
            ("Last updated", d.gmp_updated or "—"),
        ]),
        ("Financials", [
            ("As reported for", d.financial_date or "—"),
            ("EPS (pre-issue)", num(d.eps)),
            ("EPS (post-issue)", num(d.eps_post)),
            ("P/E (pre-issue)", ratio(d.pe_ratio)),
            ("P/E (post-issue)", ratio(d.post_pe_ratio)),
            ("Return on net worth", pct(d.ronw)),
            ("RoNW, prior year", pct(d.ronw_prev)),
            ("Net profit margin", pct(d.pat_margin)),
            ("Margin, prior year", pct(d.pat_margin_prev)),
            ("EBITDA margin", pct(d.ebitda_margin)),
            ("Debt to equity", num(d.debt_equity)),
            ("Promoter holding, pre", pct(d.promoter_pre)),
            ("Promoter holding, post", pct(d.promoter_post)),
        ]),
        ("Dates", [
            ("Opens", str(d.open_date) if d.open_date else "—"),
            ("Closes", str(d.close_date) if d.close_date else "—"),
            ("Allotment", d.allotment_date or "—"),
            ("Refunds start", d.refund_date or "—"),
            ("Shares in demat", d.credit_date or "—"),
            ("Listing", d.listing_date or "—"),
        ]),
    ]
    return [
        {"title": title,
         "rows": rows,
         "known": sum(1 for _, v in rows if v not in ("—", None)),
         "total": len(rows)}
        for title, rows in groups
    ]


def normalise_key(name: str) -> str:
    """Stable key for matching research notes to an issue."""
    import re
    text = re.sub(r"\b(limited|ltd|private|pvt|ipo|the)\b", "", name.lower())
    return re.sub(r"[^a-z0-9]", "", text)


def load_notes(path: Path) -> dict:
    """Research notes written by the reasoning layer, keyed by issue name.

    The Python pipeline cannot read news, regulatory action, anchor-book
    quality or sector context. Those are researched separately and merged in
    here, kept in their own file so the computed figures and the judgement
    layer never get confused for one another.
    """
    import json
    path = Path(path)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return {normalise_key(k): v for k, v in (payload.get("notes") or {}).items()}


def load_notes_with_fallback(notes_dir: Path, today: date) -> tuple[dict, str | None, int]:
    """Today's research, or the most recent earlier set marked as stale.

    Subscription figures, grey market premium and the days remaining all move
    daily, so research written yesterday can be actively misleading on an
    issue closing today. Rather than silently dropping it - which loses real
    work - or silently showing it - which is worse - stale notes are surfaced
    with their age attached so the staleness is impossible to miss.
    """
    notes_dir = Path(notes_dir)
    fresh = notes_dir / f"{today.isoformat()}.json"
    if fresh.exists():
        return load_notes(fresh), today.isoformat(), 0

    if not notes_dir.exists():
        return {}, None, 0
    earlier = sorted(
        (f for f in notes_dir.glob("*.json") if f.stem < today.isoformat()),
        reverse=True,
    )
    if not earlier:
        return {}, None, 0
    previous = earlier[0]
    try:
        age = (today - date.fromisoformat(previous.stem)).days
    except ValueError:
        return {}, None, 0
    return load_notes(previous), previous.stem, age


def render(
    analyses: list,
    out_path: Path,
    config: dict | None = None,
    notes: dict | None = None,
    notes_generated: str | None = None,
    notes_age_days: int = 0,
) -> Path:
    env = Environment(
        loader=FileSystemLoader(TEMPLATES),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["rupees"] = rupees
    env.filters["crore"] = crore

    ordered = sorted(
        analyses,
        key=lambda a: (
            URGENCY_ORDER.get(a.urgency, 9),
            VERDICT_ORDER.get(a.verdict, 9),
            -a.score,
        ),
    )
    notes = notes or {}
    cards = [
        {
            "a": a,
            "data_groups": build_data_rows(a),
            "gmp_svg": gmp_spark(a.data.gmp_history),
            "bars": {
                "qib": sub_bar(a.data.sub_qib),
                "nii": sub_bar(a.data.sub_nii),
                "rii": sub_bar(a.data.sub_rii),
                "total": sub_bar(a.data.sub_total),
            },
            "note": notes.get(normalise_key(a.name)),
            "book_final": a.data.book_is_final,
            "sub_as_of": a.data.sub_as_of,
            "sub_age_h": a.data.sub_data_age_hours,
            # The single most misread moment: bidding still open on the last
            # day, when the book is about to fill.
            "closing_day_provisional": (
                a.data.closes_today and not a.data.book_is_final
            ),
        }
        for a in ordered
    ]

    counts: dict[str, int] = {}
    for a in ordered:
        counts[a.verdict] = counts.get(a.verdict, 0) + 1

    html = env.get_template("ipo_dashboard.html.j2").render(
        cards=cards,
        generated=datetime.now(),
        today=date.today(),
        counts=counts,
        open_count=sum(1 for a in ordered if not a.data.is_upcoming),
        upcoming_count=sum(1 for a in ordered if a.data.is_upcoming),
        thresholds=(config or {}).get("ipo", {}).get("thresholds", {}),
        notes_generated=notes_generated,
        notes_age_days=notes_age_days,
        notes_count=sum(1 for c in cards if c["note"]),
    )
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return out_path
