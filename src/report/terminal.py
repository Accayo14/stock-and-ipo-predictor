"""Terminal rendering of the morning report."""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

ACTION_STYLE = {
    "STRONG BUY": "bold white on dark_green",
    "ACCUMULATE": "bold green",
    "HOLD": "yellow",
    "TRIM": "bold dark_orange",
    "EXIT": "bold white on red3",
}
ACTION_ORDER = ["EXIT", "TRIM", "STRONG BUY", "ACCUMULATE", "HOLD"]


def _rupees(value: float) -> str:
    """Indian digit grouping: 12,34,567.89"""
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
    return f"{'-' if negative else ''}₹{whole}.{frac}"


def _pnl_text(value: float, pct: float) -> Text:
    style = "green" if value >= 0 else "red"
    return Text(f"{_rupees(value)} ({pct:+.1%})", style=style)


def render(report, console: Console | None = None) -> None:
    console = console or Console()
    console.print()
    session = report.session_date or "unknown session"
    console.rule(f"[bold]BSE Morning Analysis[/]  ·  {report.run_at:%a %d %b %Y, %H:%M}")

    # -- blocking problems first -----------------------------------------
    if report.load_errors:
        console.print(Panel(
            "\n".join(f"• {e}" for e in report.load_errors),
            title="[bold red]Portfolio could not be loaded[/]",
            border_style="red",
        ))
        return

    # -- market ------------------------------------------------------------
    m = report.market
    if m.level:
        colour = "green" if (m.change_pct or 0) >= 0 else "red"
        head = Text.assemble(
            (f"{m.benchmark_name} ", "bold"),
            (f"{m.level:,.2f} ", "bold"),
            (f"({m.change_pct:+.2f}%)", colour),
        )
        console.print(Panel(
            Text.assemble(head, "\n\n", (m.trend_note, "dim")),
            title="Market backdrop", border_style="blue",
        ))

    # -- portfolio summary -------------------------------------------------
    p = report.portfolio
    if p:
        table = Table(box=None, pad_edge=False, show_header=False)
        table.add_column(style="dim")
        table.add_column(justify="right")
        table.add_row("Portfolio value", _rupees(p.total_value))
        table.add_row("Invested", _rupees(p.total_invested))
        table.add_row("Unrealised P&L", _pnl_text(p.total_pnl, p.total_pnl_pct))
        table.add_row(
            "Sectors",
            "  ".join(f"{k} {v:.0%}" for k, v in list(p.sector_weights.items())[:4]),
        )
        console.print(Panel(table, title=f"Portfolio · as of {session}",
                            border_style="cyan"))

    # -- holdings ----------------------------------------------------------
    table = Table(title="Holdings", header_style="bold", expand=True)
    table.add_column("Symbol", style="bold")
    table.add_column("Price", justify="right")
    table.add_column("Avg", justify="right")
    table.add_column("P&L", justify="right")
    table.add_column("Wt", justify="right")
    table.add_column("Score", justify="right")
    table.add_column("Conf", justify="right")
    table.add_column("Action", justify="center")

    ordered = sorted(
        report.positions,
        key=lambda a: ACTION_ORDER.index(a.final_action)
        if a.final_action in ACTION_ORDER else 99,
    )
    for a in ordered:
        table.add_row(
            a.position.symbol,
            f"{a.current_price:,.2f}",
            f"{a.position.avg_buy_price:,.2f}",
            _pnl_text(a.pnl, a.pnl_pct),
            f"{a.weight:.0%}",
            f"{a.signal.composite:+.2f}",
            f"{a.signal.confidence:.0%}",
            Text(a.final_action, style=ACTION_STYLE.get(a.final_action, "")),
        )
    console.print(table)

    # -- reasoning ---------------------------------------------------------
    console.print()
    console.rule("[bold]Reasoning[/]")
    for a in ordered:
        header = Text.assemble(
            (f"{a.position.symbol} ", "bold"),
            (f"{a.company_name or ''}  ", "dim"),
            (f" {a.final_action} ", ACTION_STYLE.get(a.final_action, "")),
        )
        if a.action_changed:
            header.append(f"  (chart alone said {a.signal.action})", style="dim italic")
        console.print(header)

        for e in a.signal.evidence[:5]:
            style = {"bullish": "green", "bearish": "red"}.get(e.direction, "dim")
            console.print(Text.assemble(
                ("   ", ""), (e.icon, style), (f" {e.statement}", ""),
            ))
        for adj in a.adjustments:
            console.print(Text.assemble(
                ("   ! ", "bold yellow"), (adj.statement, "yellow"),
            ))

        bits = []
        if a.suggested_stop:
            bits.append(f"stop {a.suggested_stop:,.2f} ({a.stop_source})")
        if a.is_long_term:
            bits.append("long-term holding")
        elif a.days_to_ltcg is not None:
            bits.append(f"{a.days_to_ltcg}d to long-term")
        if a.pe:
            bits.append(f"P/E {a.pe:.1f}")
        if bits:
            console.print(Text("   " + "  ·  ".join(bits), style="dim"))

        for note in a.notes:
            console.print(Text(f"   → {note}", style="cyan"))
        if a.signal.missing_axes:
            console.print(Text(
                f"   Data gaps: {', '.join(a.signal.missing_axes)} "
                f"(excluded from the score)", style="dim italic",
            ))
        console.print()

    # -- IPOs --------------------------------------------------------------
    if report.ipos:
        console.rule("[bold]IPOs open today[/]")
        for ipo in report.ipos:
            render_ipo(ipo, console)
    else:
        console.print(Text("No IPOs open for subscription today.", style="dim"))

    # -- warnings ----------------------------------------------------------
    if p and p.warnings:
        console.print(Panel(
            "\n".join(f"• {w}" for w in p.warnings),
            title="[bold]Risk warnings[/]", border_style="yellow",
        ))

    if report.unresolved:
        lines = []
        for u in report.unresolved:
            lines.append(f"• {u['symbol']}: {u['reason']}")
            for s in u["suggestions"][:3]:
                lines.append(f"    did you mean [bold]{s['symbol']}[/] "
                             f"({s['scrip_code']}) — {s['name']}?")
        console.print(Panel("\n".join(lines),
                            title="[bold red]Holdings not analysed[/]",
                            border_style="red"))

    if report.data_issues:
        console.print(Panel(
            "\n".join(f"• {d}" for d in report.data_issues),
            title="Data quality notes", border_style="dim",
        ))

    console.print(Text(
        "\nThis is decision support based on price history and published "
        "fundamentals — not financial advice, and it cannot see news, "
        "management quality, or anything that has not happened yet.",
        style="dim italic",
    ))


def render_ipo(ipo, console: Console) -> None:
    style = {
        "APPLY": "bold white on dark_green",
        "CONSIDER": "yellow",
        "AVOID": "bold white on red3",
        "NEUTRAL": "dim",
    }.get(ipo.verdict, "")
    console.print(Text.assemble(
        (f"{ipo.name} ", "bold"),
        (f"[{ipo.ipo_type}] ", "dim"),
        (f" {ipo.verdict} ", style),
    ))
    facts = []
    if ipo.price_band:
        facts.append(f"band {ipo.price_band}")
    if ipo.lot_size:
        facts.append(f"lot {ipo.lot_size}")
    if ipo.min_investment:
        facts.append(f"min {_rupees(ipo.min_investment)}")
    if ipo.close_date:
        facts.append(f"closes {ipo.close_date}")
    if facts:
        console.print(Text("   " + "  ·  ".join(facts), style="dim"))
    for e in ipo.evidence[:5]:
        colour = {"bullish": "green", "bearish": "red"}.get(e.direction, "dim")
        console.print(Text.assemble(("   ", ""), (e.icon, colour),
                                    (f" {e.statement}", "")))
    console.print()
