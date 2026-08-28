"""Load and validate the holdings CSV.

Validation is loud on purpose. A portfolio tool that silently skips a row it
could not parse will happily tell you your portfolio is healthy while ignoring
the position that is down 40%.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from .analysis.risk import Position

REQUIRED = ["symbol", "quantity", "avg_buy_price"]


@dataclass
class LoadResult:
    positions: list[Position] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.positions) and not self.errors


def _parse_date(text: str) -> date | None:
    text = (text or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _parse_float(text: str) -> float | None:
    text = (text or "").strip().replace(",", "").replace("₹", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def load_portfolio(path: Path) -> LoadResult:
    """Read holdings CSV, skipping '#' comment lines."""
    result = LoadResult()
    path = Path(path)
    if not path.exists():
        result.errors.append(
            f"No portfolio file at {path}. Copy config/portfolio.example.csv "
            f"to config/portfolio.csv and fill in your holdings."
        )
        return result

    lines = [
        line for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not lines:
        result.errors.append(f"{path.name} has no data rows.")
        return result

    reader = csv.DictReader(lines)
    headers = [h.strip().lower() for h in (reader.fieldnames or [])]
    missing = [c for c in REQUIRED if c not in headers]
    if missing:
        result.errors.append(
            f"{path.name} is missing required column(s): {', '.join(missing)}. "
            f"Found: {', '.join(headers) or '(none)'}"
        )
        return result

    seen: dict[str, int] = {}
    for row_number, raw in enumerate(reader, start=2):
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
        symbol = row.get("symbol", "").upper()
        if not symbol:
            result.warnings.append(f"Row {row_number}: blank symbol, skipped.")
            continue

        quantity = _parse_float(row.get("quantity", ""))
        avg_price = _parse_float(row.get("avg_buy_price", ""))
        if quantity is None or quantity <= 0:
            result.errors.append(
                f"Row {row_number} ({symbol}): quantity "
                f"'{row.get('quantity')}' is not a positive number."
            )
            continue
        if avg_price is None or avg_price <= 0:
            result.errors.append(
                f"Row {row_number} ({symbol}): avg_buy_price "
                f"'{row.get('avg_buy_price')}' is not a positive number."
            )
            continue

        if symbol in seen:
            result.warnings.append(
                f"Row {row_number}: {symbol} also appears on row {seen[symbol]}. "
                f"Both are kept - merge them if that was unintended."
            )
        seen[symbol] = row_number

        buy_date = _parse_date(row.get("buy_date", ""))
        if row.get("buy_date") and buy_date is None:
            result.warnings.append(
                f"Row {row_number} ({symbol}): could not read buy_date "
                f"'{row['buy_date']}' - capital-gains timing will be skipped "
                f"for this holding. Use YYYY-MM-DD."
            )
        if buy_date and buy_date > date.today():
            result.warnings.append(
                f"Row {row_number} ({symbol}): buy_date {buy_date} is in the future."
            )

        result.positions.append(Position(
            symbol=symbol,
            quantity=quantity,
            avg_buy_price=avg_price,
            scrip_code=(row.get("scrip_code") or "").strip() or None,
            buy_date=buy_date,
            target_price=_parse_float(row.get("target_price", "")),
            stop_loss=_parse_float(row.get("stop_loss", "")),
        ))

    if not result.positions and not result.errors:
        result.errors.append(f"{path.name} produced no usable holdings.")
    return result
