"""Tests for IPO scoring.

This is the code path an apply/skip decision actually runs through, so the
rules that are easy to get subtly wrong are pinned here - especially the
timing rules, which caused a real misread during development: a morning
snapshot on a closing day was treated as a finished book, and an issue whose
institutional demand went from 0.66x to 181x that same afternoon was briefly
reported as one institutions had "looked at and declined".

Run: python tests/test_ipo.py
"""

import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import yaml  # noqa: E402

from src.analysis.ipo import analyse_ipo  # noqa: E402
from src.providers.ipo import IPOData, parse_bid_timestamp  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CONFIG = yaml.safe_load((ROOT / "config" / "config.yaml").read_text(encoding="utf-8"))

TODAY = date.today()
failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}"
          f"{('  -> ' + str(detail)) if detail and not condition else ''}")
    if not condition:
        failures.append(label)


def stamp(day: date, hour: int, minute: int = 0) -> str:
    """Render a date the way InvestorGain does, e.g. '27th Aug 2026 18:56'."""
    n = day.day
    suffix = "th" if 11 <= n <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix} {day:%b %Y} {hour:02d}:{minute:02d}"


def make(**kw) -> IPOData:
    """An IPO with sane defaults; override what the test cares about."""
    base = dict(
        name="Test Issue Limited",
        ipo_type="MainBoard",
        open_date=TODAY - timedelta(days=2),
        close_date=TODAY,
        price_band="100.00 - 105.00",
        price_low=100.0, price_high=105.0,
        lot_size=100, min_investment=10500.0,
        fresh_issue_amt=1000.0, ofs_amt=0.0,
        sub_qib=2.0, sub_nii=2.0, sub_rii=2.0, sub_total=2.0,
        gmp_pct=10.0, gmp=10.0, estimated_listing=115.0,
        eps=10.0, eps_post=9.0, post_pe_ratio=20.0,
        ronw=18.0, ronw_prev=15.0, debt_equity=0.4,
        pat_margin=10.0, pat_margin_prev=9.0,
        promoter_pre=75.0, promoter_post=55.0,
    )
    base.update(kw)
    return IPOData(**base)


def score(d: IPOData):
    return analyse_ipo(d, CONFIG, today=TODAY)


# ---------------------------------------------------------------------------
print("Timestamp parsing")
cases = {
    "27th Aug 2026 18:56": datetime(2026, 8, 27, 18, 56),
    "1st Sep 2026 11:10": datetime(2026, 9, 1, 11, 10),
    "2nd Sep 2026 09:05": datetime(2026, 9, 2, 9, 5),
    "3rd Aug 2026": datetime(2026, 8, 3, 0, 0),
    "11th Aug 2026 10:00": datetime(2026, 8, 11, 10, 0),
    "": None, "not a date": None,
}
for raw, want in cases.items():
    check(f"parse {raw!r}", parse_bid_timestamp(raw) == want, parse_bid_timestamp(raw))
check("parse None", parse_bid_timestamp(None) is None)

# ---------------------------------------------------------------------------
print("\nbook_is_final — the rule that caused a real misread")
check("closed yesterday -> final",
      make(close_date=TODAY - timedelta(days=1)).book_is_final)
check("closing today, data from 11:00 -> NOT final",
      not make(close_date=TODAY, sub_updated=stamp(TODAY, 11)).book_is_final)
check("closing today, data from 18:56 -> final",
      make(close_date=TODAY, sub_updated=stamp(TODAY, 18, 56)).book_is_final)
check("closing today, data from 17:00 exactly -> final",
      make(close_date=TODAY, sub_updated=stamp(TODAY, 17)).book_is_final)
check("closing today, no timestamp -> NOT final (cannot assume)",
      not make(close_date=TODAY, sub_updated=None, sub_history=[]).book_is_final)
check("closes tomorrow -> NOT final",
      not make(close_date=TODAY + timedelta(days=1),
               sub_updated=stamp(TODAY, 18)).book_is_final)
check("no close date -> NOT final", not make(close_date=None).book_is_final)
check("timestamp falls back to sub_history",
      make(close_date=TODAY, sub_updated=None,
           sub_history=[{"bid_date": stamp(TODAY, 18, 30), "total": 5.0}]).book_is_final)

# ---------------------------------------------------------------------------
print("\nQIB is only damning once bidding has actually ended")
morning = score(make(close_date=TODAY, sub_updated=stamp(TODAY, 11),
                     sub_qib=0.66, sub_total=5.0))
qib_ev = [e for e in morning.evidence if "QIB" in e.statement][0]
check("morning of closing day: low QIB is not scored as a rejection",
      qib_ev.score > -0.2, qib_ev.score)
check("morning of closing day: says the number is not yet a verdict",
      "not yet a verdict" in qib_ev.statement, qib_ev.statement[:80])

closed = score(make(close_date=TODAY, sub_updated=stamp(TODAY, 18, 56),
                    sub_qib=0.66, sub_total=5.0))
qib_ev2 = [e for e in closed.evidence if "QIB" in e.statement][0]
check("after the close: low QIB IS scored as a rejection",
      qib_ev2.score <= -0.6, qib_ev2.score)
check("after the close: says institutions declined",
      "not filled their portion" in qib_ev2.statement, qib_ev2.statement[:80])

heavy = score(make(close_date=TODAY, sub_updated=stamp(TODAY, 18, 56),
                   sub_qib=181.2, sub_total=75.0))
qib_ev3 = [e for e in heavy.evidence if "QIB" in e.statement][0]
check("after the close: heavy QIB scores positively", qib_ev3.score > 0.5, qib_ev3.score)

# ---------------------------------------------------------------------------
print("\nUndersubscription is only meaningful late in the window")
day1 = score(make(open_date=TODAY, close_date=TODAY + timedelta(days=3),
                  sub_total=0.22, sub_qib=0.04, sub_rii=0.3))
sub_ev = [e for e in day1.evidence if "Overall subscription" in e.statement][0]
check("day 1: a thin book is not punished", sub_ev.score > -0.15, sub_ev.score)
check("day 1: explains bidding just opened",
      "only just opened" in sub_ev.statement, sub_ev.statement[:70])

closing_open = score(make(close_date=TODAY, sub_updated=stamp(TODAY, 11),
                          sub_total=0.5, sub_qib=0.3, sub_rii=0.5))
sub_ev2 = [e for e in closing_open.evidence if "Overall subscription" in e.statement][0]
check("closing day, bidding open: thin book still not fully punished",
      sub_ev2.score > -0.3, sub_ev2.score)
check("closing day, bidding open: tells you to re-check after the close",
      "re-check after the close" in sub_ev2.statement, sub_ev2.statement[:80])

final_thin = score(make(close_date=TODAY, sub_updated=stamp(TODAY, 18, 56),
                        sub_total=0.5, sub_qib=0.3, sub_rii=0.5))
sub_ev3 = [e for e in final_thin.evidence if "subscription finished" in e.statement][0]
check("after the close: an uncovered issue is punished", sub_ev3.score < -0.25, sub_ev3.score)

# ---------------------------------------------------------------------------
print("\nGrey market premium cannot buy an APPLY on its own")
gmp_only = score(make(
    close_date=TODAY, sub_updated=stamp(TODAY, 18, 56),
    gmp_pct=90.0, estimated_listing=200.0,
    post_pe_ratio=60.0, ronw=6.0, ronw_prev=8.0,
    pat_margin=2.0, pat_margin_prev=3.0, debt_equity=2.5,
    sub_total=1.2, sub_qib=1.1, sub_rii=1.2,
    ofs_amt=900.0, fresh_issue_amt=100.0, promoter_post=10.0,
))
check("huge GMP + weak fundamentals does not reach APPLY",
      gmp_only.verdict != "APPLY", f"{gmp_only.verdict} @ {gmp_only.score:+.3f}")

# The guard only has work to do when GMP is the thing carrying an issue over
# the APPLY line. Sweep the fundamentals to find such a case, then assert that
# when it occurs the verdict is stepped down AND the reason is stated. Also
# assert the guard never fires spuriously on an issue that earns APPLY on its
# own merits.
guard_fired = guard_verdicts = 0
false_positive = None
for ronw in (10.0, 13.0, 16.0, 19.0, 22.0):
    for pe in (16.0, 20.0, 24.0, 28.0):
        for margin in (5.0, 8.0, 12.0):
            a = score(make(
                close_date=TODAY, sub_updated=stamp(TODAY, 18, 56),
                gmp_pct=95.0, sub_total=20.0, sub_qib=15.0, sub_rii=25.0,
                post_pe_ratio=pe, ronw=ronw, ronw_prev=ronw - 1,
                pat_margin=margin, pat_margin_prev=margin,
                debt_equity=0.8, ofs_amt=500.0, fresh_issue_amt=500.0,
                promoter_post=50.0,
            ))
            stripped = any("Stripping out grey market premium" in e.statement
                           for e in a.evidence)
            if stripped:
                guard_fired += 1
                if a.verdict == "CONSIDER":
                    guard_verdicts += 1
                else:
                    false_positive = (a.verdict, ronw, pe, margin)
            elif a.verdict == "APPLY":
                # Reached APPLY without the guard: fundamentals carried it.
                pass

check("a GMP-carried APPLY exists in the sweep (guard has real work)",
      guard_fired > 0, f"{guard_fired} cases")
check("every GMP-carried case is stepped down to CONSIDER",
      guard_fired > 0 and guard_verdicts == guard_fired,
      f"{guard_verdicts}/{guard_fired}, first bad={false_positive}")

# And an issue strong enough without GMP keeps its APPLY untouched.
strong = score(make(
    close_date=TODAY, sub_updated=stamp(TODAY, 18, 56),
    gmp_pct=0.0, sub_total=60.0, sub_qib=50.0, sub_rii=70.0,
    post_pe_ratio=13.0, ronw=25.0, ronw_prev=21.0,
    pat_margin=15.0, pat_margin_prev=13.0, debt_equity=0.15,
    ofs_amt=0.0, fresh_issue_amt=1000.0, promoter_post=70.0,
))
check("a fundamentally strong issue reaches APPLY without GMP help",
      strong.verdict == "APPLY", f"{strong.verdict} @ {strong.score:+.3f}")
check("the guard does not fire when GMP is not carrying it",
      not any("Stripping out" in e.statement for e in strong.evidence))

print("\nGrey market trend direction")
falling = make(gmp_history=[
    {"date": "27-08-2026", "pct": 29.0}, {"date": "26-08-2026", "pct": 33.0},
    {"date": "25-08-2026", "pct": 38.0}, {"date": "24-08-2026", "pct": 41.4},
])
check("falling premium detected", falling.gmp_trend["direction"] == "falling",
      falling.gmp_trend)
rising = make(gmp_history=[
    {"date": "27-08-2026", "pct": 83.0}, {"date": "26-08-2026", "pct": 60.0},
    {"date": "25-08-2026", "pct": 51.0}, {"date": "24-08-2026", "pct": 47.2},
])
check("rising premium detected", rising.gmp_trend["direction"] == "rising")
flat = make(gmp_history=[{"date": "27-08-2026", "pct": 4.04},
                         {"date": "26-08-2026", "pct": 4.04},
                         {"date": "25-08-2026", "pct": 4.04},
                         {"date": "24-08-2026", "pct": 4.04}])
check("flat premium detected", flat.gmp_trend["direction"] == "flat")
check("single quote gives no trend", make(gmp_history=[{"pct": 5.0}]).gmp_trend is None)
check("no history gives no trend", make(gmp_history=[]).gmp_trend is None)

falling_scored = score(falling)
check("a falling premium is scored as bearish",
      any("draining out" in e.statement for e in falling_scored.evidence))

# ---------------------------------------------------------------------------
print("\nIssue structure: who actually gets the money")
ofs_heavy = score(make(fresh_issue_amt=100.0, ofs_amt=900.0))
check("heavy OFS is flagged",
      any("offer for sale" in e.statement and e.score < -0.5
          for e in ofs_heavy.evidence))
exit_all = score(make(promoter_pre=34.5, promoter_post=0.0))
check("total promoter exit is flagged hard",
      any("exiting entirely" in e.statement and e.score <= -0.8
          for e in exit_all.evidence))
clean = score(make(fresh_issue_amt=1000.0, ofs_amt=0.0, promoter_post=65.0))
check("all-fresh issue scores positively",
      any("fresh capital" in e.statement and e.score > 0
          for e in clean.evidence))
check("OFS share arithmetic", abs(make(fresh_issue_amt=100.0,
                                       ofs_amt=900.0).ofs_share - 0.9) < 1e-9)
check("no OFS data -> no share", make(fresh_issue_amt=None).ofs_share is None)

# ---------------------------------------------------------------------------
print("\nAnchor book")
no_anchor = score(make(anchor_status=0, ipo_type="MainBoard"))
check("mainboard with no anchor book is penalised",
      any("No anchor investors" in e.statement and e.score <= -0.5
          for e in no_anchor.evidence))
sme_no_anchor = score(make(anchor_status=0, ipo_type="SME"))
sme_ev = [e for e in sme_no_anchor.evidence if "No anchor investors" in e.statement]
main_ev = [e for e in no_anchor.evidence if "No anchor investors" in e.statement]
check("SME is penalised less harshly for it (anchors are less standard there)",
      bool(sme_ev) and bool(main_ev) and sme_ev[0].score > main_ev[0].score,
      f"sme={sme_ev[0].score if sme_ev else None} main={main_ev[0].score if main_ev else None}")
with_anchor = score(make(anchor_status=1, anchor_shares=12_648_000))
check("a placed anchor book scores positively",
      any("placed with anchor investors" in e.statement and e.score > 0
          for e in with_anchor.evidence))
check("anchor share count is reported",
      any("12,648,000" in e.statement for e in with_anchor.evidence))
unknown = score(make(anchor_status=None))
check("unknown anchor status is listed as unavailable, not assumed",
      any("anchor" in u for u in unknown.unavailable), unknown.unavailable)
check("unknown anchor status produces no anchor evidence",
      not any("anchor" in e.statement.lower() for e in unknown.evidence))
check("anchor_note reads cleanly",
      make(anchor_status=0).anchor_note == "no anchor investors")
check("anchor_note without a share count still reads",
      make(anchor_status=1, anchor_shares=None).anchor_note
      == "anchor investors participated")
check("anchor_note is None when unknown", make(anchor_status=None).anchor_note is None)

print("\nInvITs and REITs are not scored on the equity framework")
from src.providers.ipo import classify_instrument  # noqa: E402

for _name, _want in {
    "Cube Highways Trust": "INVIT",
    "Bagmane Prime Office REIT": "REIT",
    "Citius Transnet Investment Trust": "INVIT",
    "Anzen India Energy Yield Plus Trust": "INVIT",
    "Embassy Office Parks REIT": "REIT",
    "Hy Tech Engineers Limited": "EQUITY",
    "Trust Fintech Limited": "EQUITY",
    "Trustline Securities Limited": "EQUITY",
}.items():
    _got = classify_instrument(_name)
    check(f"classify {_name[:34]}", _got == _want, f"{_got} (want {_want})")

trust = score(make(name="Cube Highways Trust", instrument="INVIT",
                   sub_total=9.35, sub_qib=15.0, post_pe_ratio=None))
check("an InvIT is NOT RATED", trust.verdict == "NOT RATED", trust.verdict)
check("an InvIT scores zero, not a confident number", trust.score == 0.0)
check("an InvIT reports zero confidence", trust.confidence == 0.0)
check("the InvIT explanation names the instrument",
      any("yield vehicle" in e.statement for e in trust.evidence))
reit = score(make(name="Bagmane Prime Office REIT", instrument="REIT"))
check("a REIT is NOT RATED and named as such",
      reit.verdict == "NOT RATED"
      and any("REIT" in e.statement for e in reit.evidence))
equity = score(make(name="Hy Tech Engineers Limited", instrument="EQUITY"))
check("an equity issue is still scored normally",
      equity.verdict in ("APPLY", "CONSIDER", "NEUTRAL", "AVOID"), equity.verdict)
check("is_equity flag agrees", make(instrument="INVIT").is_equity is False
      and make(instrument="EQUITY").is_equity is True)

print("\nValuation bands")
for pe, expect_positive in [(12.0, True), (20.0, True), (32.0, False), (60.0, False)]:
    a = score(make(post_pe_ratio=pe))
    ev = [e for e in a.evidence if "earnings" in e.statement and "Priced at" in e.statement]
    ok = bool(ev) and ((ev[0].score > 0) == expect_positive)
    check(f"P/E {pe} scored {'positively' if expect_positive else 'negatively'}",
          ok, ev[0].score if ev else "no evidence")
loss = score(make(post_pe_ratio=-5.0, pe_ratio=None))
check("loss-making company flagged",
      any("loss-making" in e.statement for e in loss.evidence))

# ---------------------------------------------------------------------------
print("\nMissing data is never dressed up as a balanced verdict")
empty = score(IPOData(name="Nothing Known Ltd",
                      open_date=TODAY + timedelta(days=3),
                      close_date=TODAY + timedelta(days=6)))
check("no data -> NO DATA verdict", empty.verdict == "NO DATA", empty.verdict)
check("no data -> zero confidence", empty.confidence == 0.0, empty.confidence)
check("no data -> not reported as NEUTRAL", empty.verdict != "NEUTRAL")

partial = score(make(sub_total=None, sub_qib=None, sub_rii=None, gmp_pct=None,
                     gmp_history=[]))
check("partial data lowers confidence below 1",
      partial.confidence < 1.0, partial.confidence)
check("partial data still produces a verdict", partial.verdict in
      ("APPLY", "CONSIDER", "NEUTRAL", "AVOID"), partial.verdict)
check("missing axes are named", bool(partial.unavailable), partial.unavailable)

# Confidence must never read 100% while fields are listed as unavailable.
gapped = score(make(debt_equity=None))
check("a field gap stops confidence reading as 100%",
      not (gapped.confidence >= 0.999 and gapped.unavailable),
      f"conf={gapped.confidence} gaps={gapped.unavailable}")
full = score(make())
check("no gaps -> full confidence", full.confidence >= 0.999 or bool(full.unavailable),
      f"conf={full.confidence} gaps={full.unavailable}")
check("field gaps never dominate axis coverage", gapped.confidence >= 0.6,
      gapped.confidence)

# ---------------------------------------------------------------------------
print("\nSME issues carry an explicit penalty")
main = score(make(ipo_type="MainBoard"))
sme = score(make(ipo_type="SME"))
check("SME scores below the identical mainboard issue",
      sme.score < main.score, f"SME {sme.score:+.3f} vs main {main.score:+.3f}")
check("SME penalty is stated in the evidence",
      any("SME issue" in e.statement for e in sme.evidence))
check("penalty size matches config",
      abs((main.score - sme.score) - CONFIG["ipo"]["sme_score_penalty"]) < 1e-6,
      main.score - sme.score)

# ---------------------------------------------------------------------------
print("\nRetail allotment odds")
check("2x subscribed -> ~50%", abs(make(sub_rii=2.0).retail_allotment_odds - 0.5) < 1e-9)
check("100x subscribed -> ~1%",
      abs(make(sub_rii=100.0).retail_allotment_odds - 0.01) < 1e-9)
check("undersubscribed -> full allotment",
      make(sub_rii=0.5).retail_allotment_odds == 1.0)
check("no retail data -> unknown", make(sub_rii=None).retail_allotment_odds is None)

# ---------------------------------------------------------------------------
print("\nClosed issues are not presented as actionable")
past = score(make(open_date=TODAY - timedelta(days=6),
                  close_date=TODAY - timedelta(days=3)))
check("a finished issue reads CLOSED", past.verdict == "CLOSED", past.verdict)

print("\n" + "=" * 62)
if failures:
    print(f"{len(failures)} FAILURE(S):")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("All IPO tests passed.")
