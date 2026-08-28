"""Track record for IPO calls.

    python decisions.py record            # snapshot today's calls
    python decisions.py show              # the track record so far
    python decisions.py outcome "<name>" --listing-price 61.5

Why this exists
---------------
An IPO recommendation that is never scored teaches nothing. Recording the
call on the day it was made - before the outcome is known - is the only way
to find out later whether the process is any good, and in particular whether
grey market premium deserves the weight it is given. Editing a call after the
outcome is known would defeat the entire point, so a call is frozen the
moment its outcome is recorded. Before that it can be completed or corrected
freely - notably to attach the news research layer.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

LOG = ROOT / "data" / "decisions.jsonl"


def _key(name: str) -> str:
    text = re.sub(r"\b(limited|ltd|private|pvt|ipo|the)\b", "", name.lower())
    return re.sub(r"[^a-z0-9]", "", text)


def load() -> list[dict]:
    if not LOG.exists():
        return []
    rows = []
    for line in LOG.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def save_all(rows: list[dict]) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(json.dumps(r, default=str) for r in rows)
    LOG.write_text(body + "\n", encoding="utf-8")


def cmd_record(args) -> int:
    today = args.date or date.today().isoformat()
    bundle_path = ROOT / "data" / "reports" / today / "ipos.json"
    if not bundle_path.exists():
        print(f"No IPO bundle for {today}. Run 'python ipo.py' first.")
        return 1
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))

    notes_path = ROOT / "data" / "ipo_notes" / f"{today}.json"
    notes: dict = {}
    if notes_path.exists():
        try:
            raw = json.loads(notes_path.read_text(encoding="utf-8")).get("notes", {})
            notes = {_key(k): v for k, v in raw.items()}
        except (json.JSONDecodeError, OSError):
            pass

    rows = load()
    # A call may be completed or corrected freely until its outcome is known;
    # once an outcome has been recorded the call is frozen, because revising a
    # prediction after seeing the result is the one thing that would make this
    # log worthless.
    frozen = {(r["date"], _key(r["name"])) for r in rows if r.get("outcome")}
    open_rows = {
        (r["date"], _key(r["name"])): i
        for i, r in enumerate(rows) if not r.get("outcome")
    }
    added = updated = 0

    for ipo in bundle.get("ipos", []):
        # Only record issues that could actually be acted on today.
        if ipo.get("is_upcoming") or ipo["verdict"] in ("NO DATA", "CLOSED"):
            continue
        name_key = _key(ipo["name"])
        if (today, name_key) in frozen:
            continue

        note = notes.get(name_key) or {}
        gmp = ipo.get("gmp") or {}
        sub = ipo.get("subscription") or {}
        band = ipo.get("price_band") or ""
        issue_price = None
        if band:
            nums = re.findall(r"\d+\.?\d*", band)
            if nums:
                # Upper band is the cut-off price nearly all retail bids use.
                issue_price = float(nums[-1])

        first_reason = ""
        evidence = ipo.get("evidence") or []
        if evidence:
            first_reason = evidence[0].get("statement", "")

        record = {
            "date": today,
            "name": ipo["name"],
            "type": ipo.get("type"),
            "computed_verdict": ipo["verdict"],
            "final_call": note.get("final_call") or ipo["verdict"],
            "researched": bool(note),
            "score": ipo.get("score"),
            "confidence": ipo.get("confidence"),
            "issue_price": issue_price,
            "price_band": band,
            "sub_total": sub.get("total"),
            "sub_qib": sub.get("qib"),
            "gmp_pct": gmp.get("pct"),
            "gmp_implied_listing": gmp.get("estimated_listing"),
            "close_date": ipo.get("close_date"),
            "listing_date": (ipo.get("timetable") or {}).get("listing"),
            "key_reason": note.get("adjustment_reason") or first_reason,
            "outcome": None,
        }
        slot = open_rows.get((today, name_key))
        if slot is None:
            rows.append(record)
            added += 1
        elif rows[slot] != record:
            rows[slot] = record
            updated += 1

    save_all(rows)
    researched = sum(1 for r in rows if r["date"] == today and r.get("researched"))
    print(f"Recorded {added} new call(s), refreshed {updated}, for {today}. "
          f"Track record: {len(rows)} total.")
    print(f"{researched} of today's calls carry the news research layer.")
    if not notes:
        print("No research notes found for today - run /ipo in Claude Code "
              "before recording, so the calls are logged with their reasoning.")
    return 0


def cmd_outcome(args) -> int:
    rows = load()
    target = _key(args.name)
    matches = [r for r in rows
               if _key(r["name"]) == target and r.get("outcome") is None]
    if not matches:
        print(f"No open record found for '{args.name}'.")
        pending = sorted({r["name"] for r in rows if r.get("outcome") is None})
        if pending:
            print("Awaiting an outcome: " + ", ".join(pending))
        return 1

    row = matches[-1]
    issue = row.get("issue_price")
    listing = args.listing_price
    gain = ((listing - issue) / issue * 100) if issue else None
    predicted = row.get("gmp_pct")
    error = (round(gain - predicted, 2)
             if gain is not None and predicted is not None else None)

    row["outcome"] = {
        "listing_price": listing,
        "listing_gain_pct": round(gain, 2) if gain is not None else None,
        "gmp_predicted_pct": predicted,
        "gmp_error_pp": error,
        "recorded": date.today().isoformat(),
    }
    save_all(rows)

    if gain is not None:
        print(f"{row['name']}: issue {issue:,.2f} -> listed {listing:,.2f} "
              f"({gain:+.1f}%)")
    else:
        print(f"{row['name']}: outcome recorded")
    if error is not None:
        direction = "overstated" if error < 0 else "understated"
        print(f"  Grey market premium {direction} the move: predicted "
              f"{predicted:+.1f}%, actual {gain:+.1f}% "
              f"({error:+.1f} percentage points).")
    return 0


def cmd_show(args) -> int:
    rows = load()
    if not rows:
        print("No calls recorded yet. Run 'python decisions.py record' after /ipo.")
        return 0

    print(f"\nIPO track record - {len(rows)} call(s)")
    print("-" * 94)
    print(f"{'DATE':<11} {'COMPANY':<32} {'CALL':<10} {'SUB':>7} "
          f"{'GMP':>7} {'RESULT':>12}")
    print("-" * 94)
    for r in sorted(rows, key=lambda x: x["date"]):
        out = r.get("outcome") or {}
        gain = out.get("listing_gain_pct")
        result = f"{gain:+.1f}%" if gain is not None else "pending"
        sub = f"{r['sub_total']:.1f}x" if r.get("sub_total") is not None else "-"
        gmp = f"{r['gmp_pct']:+.0f}%" if r.get("gmp_pct") is not None else "-"
        flag = "" if r.get("researched") else " *"
        print(f"{r['date']:<11} {r['name'][:32]:<32} {r['final_call']:<10} "
              f"{sub:>7} {gmp:>7} {result:>12}{flag}")
    print("-" * 94)

    scored = [r for r in rows
              if (r.get("outcome") or {}).get("listing_gain_pct") is not None]
    if not scored:
        print("No outcomes recorded yet. Once an issue lists, run:")
        print('  python decisions.py outcome "<company>" --listing-price <price>')
        if any(not r.get("researched") for r in rows):
            print("* call made without the news research layer")
        return 0

    gains = [r["outcome"]["listing_gain_pct"] for r in scored]
    positive = sum(1 for g in gains if g > 0)
    print(f"{len(scored)} listed - {positive} gained, {len(scored) - positive} "
          f"fell - average listing move {sum(gains) / len(gains):+.1f}%")

    errors = [r["outcome"]["gmp_error_pp"] for r in scored
              if r["outcome"].get("gmp_error_pp") is not None]
    if errors:
        avg = sum(errors) / len(errors)
        over = sum(1 for e in errors if e < 0)
        mood = "over" if avg < 0 else "under"
        print(f"Grey market premium overstated the outcome in {over} of "
              f"{len(errors)} cases, by {abs(avg):.1f} percentage points on "
              f"average ({mood}-optimistic).")

    applied = [r for r in scored if r["final_call"] in ("APPLY", "CONSIDER")]
    avoided = [r for r in scored if r["final_call"] in ("AVOID", "NEUTRAL")]
    if applied:
        avg_applied = sum(r["outcome"]["listing_gain_pct"] for r in applied) / len(applied)
        print(f"Issues called APPLY/CONSIDER averaged {avg_applied:+.1f}% "
              f"({len(applied)} of them).")
    if avoided:
        avg_avoided = sum(r["outcome"]["listing_gain_pct"] for r in avoided) / len(avoided)
        print(f"Issues called AVOID/NEUTRAL averaged {avg_avoided:+.1f}% "
              f"({len(avoided)} of them).")
    if any(not r.get("researched") for r in rows):
        print("* call made without the news research layer")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="IPO decision track record.")
    subs = parser.add_subparsers(dest="cmd", required=True)

    rec = subs.add_parser("record", help="snapshot today's calls")
    rec.add_argument("--date", help="YYYY-MM-DD (defaults to today)")
    rec.set_defaults(func=cmd_record)

    show = subs.add_parser("show", help="display the track record")
    show.set_defaults(func=cmd_show)

    out = subs.add_parser("outcome", help="record an actual listing price")
    out.add_argument("name", help="company name as recorded")
    out.add_argument("--listing-price", type=float, required=True)
    out.set_defaults(func=cmd_outcome)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
