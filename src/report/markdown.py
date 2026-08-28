"""Markdown archive of a morning report.

Kept deliberately plain so past reports diff cleanly against each other -
that is what makes it possible to look back in three months and ask whether
the calls were any good.
"""

from __future__ import annotations


def _rupees(value: float) -> str:
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
    return f"{'-' if negative else ''}Rs {whole}.{frac}"


def render(report) -> str:
    out: list[str] = []
    add = out.append

    add(f"# BSE Morning Analysis — {report.run_at:%A %d %B %Y}")
    add("")
    add(f"*Generated {report.run_at:%H:%M} IST"
        f"{f' · data as of session {report.session_date}' if report.session_date else ''}*")
    add("")

    if report.load_errors:
        add("## Portfolio could not be loaded")
        for e in report.load_errors:
            add(f"- {e}")
        return "\n".join(out)

    m = report.market
    if m.level:
        add("## Market backdrop")
        add("")
        add(f"**{m.benchmark_name} {m.level:,.2f} ({m.change_pct:+.2f}%)**")
        add("")
        add(m.trend_note)
        add("")

    p = report.portfolio
    if p:
        add("## Portfolio")
        add("")
        add(f"| | |")
        add("|---|---|")
        add(f"| Value | {_rupees(p.total_value)} |")
        add(f"| Invested | {_rupees(p.total_invested)} |")
        add(f"| Unrealised P&L | {_rupees(p.total_pnl)} ({p.total_pnl_pct:+.1%}) |")
        add("")
        add("Sector split: "
            + ", ".join(f"{k} {v:.0%}" for k, v in p.sector_weights.items()))
        add("")

    add("## Holdings")
    add("")
    add("| Symbol | Price | Avg | P&L | Weight | Score | Confidence | Action |")
    add("|---|---:|---:|---:|---:|---:|---:|:---:|")
    for a in report.positions:
        add(f"| {a.position.symbol} | {a.current_price:,.2f} | "
            f"{a.position.avg_buy_price:,.2f} | {a.pnl:+,.0f} ({a.pnl_pct:+.1%}) | "
            f"{a.weight:.0%} | {a.signal.composite:+.2f} | "
            f"{a.signal.confidence:.0%} | **{a.final_action}** |")
    add("")

    add("## Reasoning")
    add("")
    for a in report.positions:
        add(f"### {a.position.symbol} — {a.final_action}")
        add("")
        if a.company_name:
            add(f"*{a.company_name}"
                + (f" · {a.sector}*" if a.sector else "*"))
            add("")
        if a.action_changed:
            add(f"> The chart alone pointed to **{a.signal.action}**; "
                f"position context changed it to **{a.final_action}**.")
            add("")
        for e in a.signal.evidence:
            add(f"- {e.icon} {e.statement}")
        for adj in a.adjustments:
            add(f"- ⚠️ {adj.statement}")
        add("")
        details = []
        if a.suggested_stop:
            details.append(f"Stop: {a.suggested_stop:,.2f} ({a.stop_source})")
        if a.is_long_term:
            details.append("Long-term holding")
        elif a.days_to_ltcg is not None:
            details.append(f"{a.days_to_ltcg} days to long-term")
        if a.pe:
            details.append(f"P/E {a.pe:.1f}")
        if details:
            add(" · ".join(details))
            add("")
        for note in a.notes:
            add(f"**Note:** {note}")
            add("")
        if a.signal.data_note:
            add(f"<sub>{a.signal.data_note}</sub>")
            add("")

    if report.ipos:
        add("## IPOs")
        add("")
        for ipo in report.ipos:
            add(f"### {ipo.name} — {ipo.verdict}")
            add("")
            facts = []
            if ipo.price_band:
                facts.append(f"Band {ipo.price_band}")
            if ipo.lot_size:
                facts.append(f"Lot {ipo.lot_size}")
            if ipo.close_date:
                facts.append(f"Closes {ipo.close_date}")
            if facts:
                add(" · ".join(facts))
                add("")
            for e in ipo.evidence:
                add(f"- {e.icon} {e.statement}")
            add("")

    if p and p.warnings:
        add("## Risk warnings")
        add("")
        for w in p.warnings:
            add(f"- {w}")
        add("")

    if report.unresolved:
        add("## Holdings not analysed")
        add("")
        for u in report.unresolved:
            add(f"- **{u['symbol']}**: {u['reason']}")
            for s in u["suggestions"][:3]:
                add(f"  - did you mean `{s['symbol']}` ({s['scrip_code']}) — {s['name']}?")
        add("")

    if report.data_issues:
        add("## Data quality notes")
        add("")
        for d in report.data_issues:
            add(f"- {d}")
        add("")

    add("---")
    add("")
    add("*Decision support based on price history and published fundamentals. "
        "Not financial advice.*")
    return "\n".join(out)
