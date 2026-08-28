"""IPO data sources.

Two sources, each used for what it is authoritative about:

* BSE (`GetPublicIssue_par_updated`) - the definitive list of what is actually
  open for subscription on the exchange, with dates, price band and whether
  the issue is MainBoard or SME. It does NOT publish lot size, issue size or
  category-wise subscription.

* InvestorGain - lot size, issue size, subscription multiples (QIB/NII/RII),
  grey market premium, and the financial KPIs (EPS, post-issue P/E, RoNW,
  debt/equity, margins) needed to judge whether the pricing is sane.

A note on Chittorgarh: it used to be the standard GMP source, but its GMP
report now resolves to an unrelated page and its own site links out to
InvestorGain for GMP. It is deliberately not used here.

InvestorGain's detail pages are server-rendered Next.js, so the data arrives
inside `self.__next_f.push(...)` flight chunks rather than a JSON API. We
reassemble those chunks and brace-match the objects out. It is a scrape, and
it will break if they restructure the page - so every field is optional and
its absence is reported rather than guessed.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime

import requests

BSE_PUBLIC_ISSUE = "https://api.bseindia.com/BseIndiaAPI/api/GetPublicIssue_par_updated/w"
IG_LIST = "https://webnodejs.investorgain.com/cloud/v2/ipo/list-read"
IG_DETAIL = "https://www.investorgain.com/ipo/{slug}/{ipo_id}"

BSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.bseindia.com/",
    "Origin": "https://www.bseindia.com",
    "Accept": "application/json, text/plain, */*",
}
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}

_FLIGHT = re.compile(r'self\.__next_f\.push\(\[1,\s*"((?:[^"\\]|\\.)*)"\]\)')


@dataclass
class IPOData:
    """Raw, merged facts about one issue. No judgement applied here."""

    name: str
    ipo_type: str = "MainBoard"          # MainBoard | SME
    open_date: date | None = None
    close_date: date | None = None
    price_band: str | None = None
    price_low: float | None = None
    price_high: float | None = None
    face_value: float | None = None
    lot_size: int | None = None
    min_investment: float | None = None
    issue_size: str | None = None
    fresh_issue_amt: float | None = None
    ofs_amt: float | None = None
    # demand
    sub_qib: float | None = None
    sub_nii: float | None = None
    sub_rii: float | None = None
    sub_total: float | None = None
    sub_updated: str | None = None
    # sentiment
    gmp: float | None = None
    gmp_pct: float | None = None
    estimated_listing: float | None = None
    gmp_updated: str | None = None
    # Day-by-day history. GMP direction into the close matters far more than
    # today's absolute number: a premium fading through the window is a very
    # different signal from one holding firm.
    gmp_history: list[dict] = field(default_factory=list)
    sub_history: list[dict] = field(default_factory=list)
    # timetable
    allotment_date: str | None = None
    refund_date: str | None = None
    credit_date: str | None = None
    listing_date: str | None = None
    # application tiers
    min_qty_desc: str | None = None
    max_retail_qty_desc: str | None = None
    min_hni_qty_desc: str | None = None
    min_bhni_qty_desc: str | None = None
    retail_reservation: str | None = None
    qib_reservation: str | None = None
    nii_reservation: str | None = None
    registrar: str | None = None
    sector: str | None = None
    logo_url: str | None = None
    # Anchor book. Anchors are institutions who commit a day before bidding
    # opens, after seeing the prospectus and meeting management. A mainboard
    # issue that raised no anchor money at all is a meaningful absence.
    anchor_status: int | None = None       # 1 = anchors participated
    anchor_shares: int | None = None
    anchor_url: str | None = None
    # fundamentals
    eps: float | None = None
    eps_post: float | None = None
    pe_ratio: float | None = None
    post_pe_ratio: float | None = None
    roe: float | None = None
    ronw: float | None = None
    ronw_prev: float | None = None
    debt_equity: float | None = None
    pat_margin: float | None = None
    pat_margin_prev: float | None = None
    ebitda_margin: float | None = None
    promoter_pre: float | None = None
    promoter_post: float | None = None
    financial_date: str | None = None
    # provenance
    bse_listed: bool = False
    instrument: str = "EQUITY"   # EQUITY | INVIT | REIT
    detail_url: str | None = None
    missing: list[str] = field(default_factory=list)

    @property
    def is_open(self) -> bool:
        today = date.today()
        if self.open_date and self.close_date:
            return self.open_date <= today <= self.close_date
        return False

    @property
    def days_left(self) -> int | None:
        if self.close_date:
            return (self.close_date - date.today()).days
        return None

    @property
    def ofs_share(self) -> float | None:
        """Fraction of the issue that is promoters selling, not new capital."""
        if self.fresh_issue_amt is None or self.ofs_amt is None:
            return None
        total = self.fresh_issue_amt + self.ofs_amt
        return (self.ofs_amt / total) if total else None

    @property
    def sub_as_of(self):
        """When the subscription figures were last published."""
        from_history = self.sub_history[-1].get("bid_date") if self.sub_history else None
        return parse_bid_timestamp(self.sub_updated or from_history)

    @property
    def book_is_final(self) -> bool:
        """True only once bidding has genuinely ended.

        Being on the closing date is not enough: at 11am on the final day the
        institutional book is typically near empty and means nothing. Treat
        the book as final only after the close date has passed, or once that
        day's figures are timestamped after bidding shut (~17:00 IST).
        """
        if not self.close_date:
            return False
        if date.today() > self.close_date:
            return True
        stamp = self.sub_as_of
        if stamp is None:
            return False
        return stamp.date() >= self.close_date and stamp.hour >= 17

    @property
    def sub_data_age_hours(self) -> float | None:
        stamp = self.sub_as_of
        if stamp is None:
            return None
        return max(0.0, (datetime.now() - stamp).total_seconds() / 3600)

    @property
    def anchor_note(self) -> str | None:
        """Human-readable state of the anchor book, or None if unknown."""
        if self.anchor_status is None:
            return None
        if self.anchor_status == 1 and self.anchor_shares:
            return f"{self.anchor_shares:,} shares placed with anchor investors"
        if self.anchor_status == 1:
            return "anchor investors participated"
        return "no anchor investors"

    @property
    def is_equity(self) -> bool:
        return self.instrument == "EQUITY"

    @property
    def is_upcoming(self) -> bool:
        return bool(self.open_date and date.today() < self.open_date)

    @property
    def closes_today(self) -> bool:
        return self.close_date == date.today()

    @property
    def gmp_trend(self) -> dict | None:
        """Direction of the grey market premium across recent days.

        `gmp_history` arrives newest-first. Comparing the latest reading with
        the one three days back separates a premium that is building from one
        that is quietly draining away before listing.
        """
        usable = [h for h in self.gmp_history if h.get("pct") is not None]
        if len(usable) < 2:
            return None
        latest = usable[0]["pct"]
        earlier = usable[min(3, len(usable) - 1)]["pct"]
        change = latest - earlier
        if abs(change) < 1.0:
            direction = "flat"
        elif change > 0:
            direction = "rising"
        else:
            direction = "falling"
        return {
            "direction": direction,
            "change_pp": change,
            "latest": latest,
            "earlier": earlier,
            "days": min(3, len(usable) - 1),
            "points": len(usable),
        }

    @property
    def retail_allotment_odds(self) -> float | None:
        """Rough probability a single-lot retail application gets an allotment.

        When the retail book is oversubscribed, allotment is by lottery, so
        the chance of one lot is approximately 1 / (retail subscription).
        This assumes most retail applicants bid a single lot, which is the
        common case but not universal - so treat it as an estimate, not a
        guarantee.
        """
        if self.sub_rii is None:
            return None
        if self.sub_rii <= 1:
            return 1.0
        return 1.0 / self.sub_rii

    @property
    def sub_momentum(self) -> dict | None:
        """Change in total subscription between the last two snapshots."""
        usable = [h for h in self.sub_history if h.get("total") is not None]
        if len(usable) < 2:
            return None
        return {
            "from": usable[-2]["total"],
            "to": usable[-1]["total"],
            "delta": usable[-1]["total"] - usable[-2]["total"],
            "snapshots": len(usable),
        }


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

_ORDINAL = re.compile(r"(\d{1,2})(st|nd|rd|th)\b", re.I)


def parse_bid_timestamp(value) -> "datetime | None":
    """Parse InvestorGain's '27th Aug 2026 18:56' into a datetime.

    The clock time matters enormously. Bidding on the closing day runs until
    about 17:00 IST and the book fills in the final hours - Hy Tech Engineers
    went from 51x at midday to 247x by the close on 27 Aug 2026. A
    subscription figure is therefore only a *final* figure once that day's
    bidding has actually ended.
    """
    if not value:
        return None
    text = _ORDINAL.sub(r"\1", str(value).strip())
    for fmt in ("%d %b %Y %H:%M", "%d %B %Y %H:%M", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


# Instruments that are NOT equity IPOs. An InvIT or REIT is a yield vehicle
# holding infrastructure or property: it has no meaningful P/E, no promoter
# dilution and no earnings-growth story. Running the equity framework over one
# produces a confident, meaningless answer - in backtesting, four such trusts
# all scored APPLY and then averaged roughly flat, dragging the entire APPLY
# bucket below CONSIDER until they were separated out.
#
# Matching is on the tail of the name: 'Cube Highways Trust' is a trust, while
# 'Trust Fintech Limited' is an ordinary company that happens to start with the
# word. Anchoring on the end of the string keeps those apart.
_TRUST_TAIL = re.compile(
    r"\b(inv[ai]t|reit|trust)\s*(ltd|limited)?\s*$",
    re.I,
)
_TRUST_WORDS = re.compile(
    r"\b(invit|reit|real\s+estate\s+investment\s+trust|"
    r"investment\s+trust|infrastructure\s+trust)\b",
    re.I,
)


def classify_instrument(name: str, category: str = "") -> str:
    """EQUITY, INVIT or REIT, decided from the issue name and category."""
    blob = f"{name} {category}".strip()
    if re.search(r"\breit\b", blob, re.I) or re.search(
        r"real\s+estate\s+investment\s+trust", blob, re.I
    ):
        return "REIT"
    if _TRUST_WORDS.search(blob) or _TRUST_TAIL.search(name.strip()):
        return "INVIT"
    return "EQUITY"

def _f(value) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "").replace("₹", "")
    text = re.sub(r"&#8377;|Cr|%", "", text).strip()
    if text in ("", "-", "NA", "null", "None"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _parse_dt(value) -> date | None:
    if not value:
        return None
    text = str(value)[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def _object_around(blob: str, index: int) -> dict | None:
    """Extract the JSON object enclosing `index` by brace matching.

    Tries successively earlier opening braces, because the nearest one may
    belong to a nested object that does not parse on its own.
    """
    start = blob.rfind("{", 0, index)
    for _ in range(8):
        if start < 0:
            return None
        depth, in_string, escaped = 0, False, False
        for j in range(start, min(len(blob), start + 400_000)):
            char = blob[j]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(blob[start:j + 1])
                    except json.JSONDecodeError:
                        break
        start = blob.rfind("{", 0, start)
    return None


def _flight_blob(html: str) -> str:
    """Reassemble Next.js flight chunks into one string."""
    parts = []
    for chunk in _FLIGHT.findall(html):
        try:
            parts.append(json.loads(f'"{chunk}"'))
        except json.JSONDecodeError:
            continue
    return "".join(parts)


# ---------------------------------------------------------------------------
# BSE
# ---------------------------------------------------------------------------

def fetch_bse_issues(cache=None) -> list[dict]:
    """Public issues currently listed by BSE. Filtered to actual IPOs.

    IR_flag distinguishes IPO from OFS / rights / buyback. An OFS is an
    existing listed company placing shares, not a new issue you would 'apply'
    to in the same sense, so those are excluded.
    """
    key = f"bse_public_issues_{date.today()}"
    payload = cache.get(key, ttl_hours=3) if cache else None
    if payload is None:
        try:
            resp = requests.get(
                BSE_PUBLIC_ISSUE,
                params={"flag": "1", "status": "", "exchange": "", "ir_flag": ""},
                headers=BSE_HEADERS, timeout=25,
            )
            if resp.status_code != 200 or resp.text.lstrip()[:9].lower().startswith("<!doctype"):
                return []
            payload = resp.json()
        except (requests.RequestException, ValueError):
            return []
        if cache:
            cache.set(key, payload)

    out = []
    for row in payload.get("Table", []):
        if str(row.get("IR_flag", "")).upper() != "IPO":
            continue
        out.append({
            "name": str(row.get("Scrip_Name") or row.get("LONG_NAME") or "").strip(),
            "short_name": str(row.get("short_name") or "").strip(),
            "open_date": _parse_dt(row.get("Start_Dt")),
            "close_date": _parse_dt(row.get("End_Dt")),
            "price_band": row.get("Price_Band"),
            "face_value": _f(row.get("Face_Val")),
            "platform": str(row.get("eXCHANGE_PLATFORM") or "MainBoard"),
            "status": row.get("Status"),
            "scrip_code": row.get("Scrip_cd"),
        })
    return out


# ---------------------------------------------------------------------------
# InvestorGain
# ---------------------------------------------------------------------------

def fetch_ig_list(cache=None) -> list[dict]:
    key = f"ig_ipo_list_{date.today()}"
    payload = cache.get(key, ttl_hours=3) if cache else None
    if payload is None:
        try:
            resp = requests.get(IG_LIST, headers=UA, timeout=25)
            resp.raise_for_status()
            payload = resp.json()
        except (requests.RequestException, ValueError):
            return []
        if cache:
            cache.set(key, payload)
    return payload.get("ipoList", []) or []


def fetch_ig_detail(slug: str, ipo_id, cache=None) -> dict:
    """Scrape one IPO detail page into {ipo, subscription, gmp} dicts."""
    key = f"ig_detail_{ipo_id}_{date.today()}"
    html = cache.get(key, ttl_hours=2) if cache else None
    if html is None:
        try:
            resp = requests.get(
                IG_DETAIL.format(slug=slug, ipo_id=ipo_id), headers=UA, timeout=30
            )
            resp.raise_for_status()
            html = resp.text
        except requests.RequestException:
            return {}
        if cache:
            cache.set(key, html)

    blob = _flight_blob(html)
    if not blob:
        return {}

    out: dict = {}
    core = _object_around(blob, blob.find('"issue_price_lower"'))
    if core:
        out["ipo"] = core

    # Subscription rows are keyed by tsb_ipo_id, one per day of the window.
    # Keep the whole series, oldest first, so demand momentum is visible.
    subs = []
    seen_sub: set[str] = set()
    for match in re.finditer(r'"tsb_ipo_id"\s*:', blob):
        obj = _object_around(blob, match.start())
        if obj and any(k in obj for k in ("qib", "rii", "total")):
            marker = str(obj.get("bid_date") or obj.get("create_date") or obj.get("id"))
            if marker in seen_sub:
                continue
            seen_sub.add(marker)
            subs.append(obj)
    if subs:
        subs.sort(key=lambda r: str(r.get("create_date") or r.get("bid_date") or ""))
        out["subscription_history"] = subs
        out["subscription"] = subs[-1]

    # GMP rows form a daily history, newest first on the page.
    gmps = []
    seen_gmp: set[str] = set()
    for match in re.finditer(r'"gmp_percent_calc"\s*:', blob):
        obj = _object_around(blob, match.start())
        if not obj or _f(obj.get("gmp_percent_calc")) is None:
            continue
        marker = str(obj.get("gmp_date") or obj.get("last_updated_gmp"))
        if marker in seen_gmp:
            continue
        seen_gmp.add(marker)
        gmps.append(obj)
    if gmps:
        out["gmp_history"] = gmps
        out["gmp"] = gmps[0]

    # Registrar lives in its own object, not the issue core block, so pull it
    # straight out of the blob. Worth having: it is who you chase for
    # allotment status after the issue closes.
    registrar = re.search(r'"registrar_name"\s*:\s*"([^"]{2,80})"', blob)
    if registrar:
        out["registrar_name"] = registrar.group(1)

    return out


# ---------------------------------------------------------------------------
# merge
# ---------------------------------------------------------------------------

def _normalise(name: str) -> str:
    text = re.sub(r"\b(limited|ltd|private|pvt|ipo|the)\b", "", name.lower())
    return re.sub(r"[^a-z0-9]", "", text)


def collect_ipos(
    cache=None,
    open_only: bool = True,
    include_upcoming: bool = False,
    upcoming_within_days: int = 14,
) -> list[IPOData]:
    """Merge BSE's authoritative list with InvestorGain's detail.

    `include_upcoming` also returns issues that have not opened yet, so the
    dashboard can show what is coming and give you time to read a prospectus
    before the window opens rather than on the last afternoon of it.
    """
    bse_rows = fetch_bse_issues(cache)
    ig_rows = fetch_ig_list(cache)
    ig_by_name = {_normalise(str(r.get("company_short_name", ""))): r for r in ig_rows}

    results: list[IPOData] = []
    seen: set[str] = set()

    for row in bse_rows:
        ipo = IPOData(
            name=row["name"].title(),
            instrument=classify_instrument(row["name"], row.get("platform", "")),
            ipo_type="SME" if "sme" in row["platform"].lower() else "MainBoard",
            open_date=row["open_date"],
            close_date=row["close_date"],
            price_band=row["price_band"],
            face_value=row["face_value"],
            bse_listed=True,
        )
        if row["price_band"]:
            nums = re.findall(r"\d+\.?\d*", str(row["price_band"]))
            if len(nums) >= 2:
                ipo.price_low, ipo.price_high = float(nums[0]), float(nums[1])
            elif nums:
                ipo.price_low = ipo.price_high = float(nums[0])

        keep = ipo.is_open
        if include_upcoming and ipo.is_upcoming and ipo.open_date:
            keep = keep or (ipo.open_date - date.today()).days <= upcoming_within_days
        if open_only and not keep:
            continue

        key = _normalise(row["name"])
        seen.add(key)
        match = ig_by_name.get(key) or next(
            (v for k, v in ig_by_name.items() if k and (k in key or key in k)), None
        )
        if match:
            ipo.sector = match.get("company_sector") or None
            ipo.logo_url = match.get("logo_url") or None
            _enrich(ipo, match, cache)
        else:
            ipo.missing.append(
                "no matching InvestorGain record - lot size, subscription, "
                "GMP and financials unavailable"
            )
        results.append(ipo)

    # Open issues first (they need a decision now), then upcoming by start date.
    return sorted(
        results,
        key=lambda i: (i.is_upcoming, i.close_date or date.max, i.open_date or date.max),
    )


def _enrich(ipo: IPOData, ig_row: dict, cache) -> None:
    slug, ipo_id = ig_row.get("urlrewrite_folder_name"), ig_row.get("id")
    ipo.issue_size = ig_row.get("issue_size")
    if not slug or not ipo_id:
        ipo.missing.append("InvestorGain detail page not addressable")
        return
    ipo.detail_url = IG_DETAIL.format(slug=slug, ipo_id=ipo_id)

    detail = fetch_ig_detail(slug, ipo_id, cache)
    if not detail:
        ipo.missing.append("InvestorGain detail page could not be parsed")
        return

    core = detail.get("ipo") or {}
    if core:
        ipo.price_low = ipo.price_low or _f(core.get("issue_price_lower"))
        ipo.price_high = ipo.price_high or _f(core.get("issue_price_upper"))
        lot = core.get("market_lot_size") or core.get("minimum_order_quantity")
        ipo.lot_size = int(lot) if lot else None
        if ipo.lot_size and ipo.price_high:
            ipo.min_investment = ipo.lot_size * ipo.price_high
        ipo.fresh_issue_amt = _f(core.get("issue_size_fresh_in_amt"))
        ipo.ofs_amt = _f(core.get("issue_size_ofs_in_amt"))
        ipo.eps = _f(core.get("kpi_eps"))
        ipo.eps_post = _f(core.get("kpi_eps_post"))
        ipo.pe_ratio = _f(core.get("pe_ratio"))
        ipo.post_pe_ratio = _f(core.get("post_pe_ratio"))
        ipo.roe = _f(core.get("kpi_roe"))
        ipo.ronw = _f(core.get("kpi_ronw"))
        ipo.ronw_prev = _f(core.get("kpi_ronw_2"))
        ipo.debt_equity = _f(core.get("kpi_debt_equity"))
        ipo.pat_margin = _f(core.get("kpi_pat_margin"))
        ipo.pat_margin_prev = _f(core.get("kpi_pat_margin_2"))
        ipo.ebitda_margin = _f(core.get("kpi_ebitda"))
        ipo.promoter_pre = _f(core.get("promoter_shareholding_pre_issue"))
        ipo.promoter_post = _f(core.get("promoter_shareholding_post_issue"))
        ipo.financial_date = core.get("latest_financial_dt")
        # timetable
        ipo.allotment_date = core.get("basic_of_allotment_dt") or None
        ipo.refund_date = core.get("initiation_of_refund_dt") or None
        ipo.credit_date = core.get("demat_acct_credit_dt") or None
        ipo.listing_date = core.get("timetable_listing_dt") or None
        # application tiers
        ipo.min_qty_desc = core.get("min_order_qty") or None
        ipo.max_retail_qty_desc = core.get("max_retail_qty") or None
        ipo.min_hni_qty_desc = core.get("min_hni_qty") or None
        ipo.min_bhni_qty_desc = core.get("min_bhni_qty") or None
        ipo.retail_reservation = core.get("shares_offered_rii_percentage_temp") or None
        ipo.qib_reservation = core.get("shares_offered_qib_percentage_temp") or None
        ipo.nii_reservation = core.get("shares_offered_nii_percentage_temp") or None
        ipo.registrar = core.get("registrar_name") or detail.get("registrar_name")
        status = core.get("anchor_investor_status")
        ipo.anchor_status = int(status) if str(status).strip() in ("0", "1") else None
        shares = _f(core.get("shares_offered_anchor_investor"))
        ipo.anchor_shares = int(shares) if shares else None
        ipo.anchor_url = core.get("anchor_investor_url") or None
    else:
        ipo.missing.append("issue detail block not found on the page")
    ipo.registrar = ipo.registrar or detail.get("registrar_name")

    sub = detail.get("subscription") or {}
    if sub:
        ipo.sub_qib = _f(sub.get("qib"))
        ipo.sub_nii = _f(sub.get("nii"))
        ipo.sub_rii = _f(sub.get("rii"))
        ipo.sub_total = _f(sub.get("total"))
        ipo.sub_updated = sub.get("bid_date") or sub.get("create_date")
        ipo.sub_history = [
            {
                "bid_date": row.get("bid_date"),
                "qib": _f(row.get("qib")), "nii": _f(row.get("nii")),
                "rii": _f(row.get("rii")), "total": _f(row.get("total")),
                "total_bid_amt_cr": _f(row.get("total_bid_amt")),
            }
            for row in detail.get("subscription_history", [])
        ]
    else:
        ipo.missing.append("subscription figures not published yet")

    gmp = detail.get("gmp") or {}
    if gmp:
        ipo.gmp = _f(gmp.get("gmp"))
        ipo.gmp_pct = _f(gmp.get("gmp_percent_calc"))
        ipo.estimated_listing = _f(gmp.get("estimated_listing_price"))
        ipo.gmp_updated = gmp.get("last_updated_gmp")
        ipo.gmp_history = [
            {
                "date": row.get("gmp_date"),
                "gmp": _f(row.get("gmp")),
                "pct": _f(row.get("gmp_percent_calc")),
                "estimated_listing": _f(row.get("estimated_listing_price")),
            }
            for row in detail.get("gmp_history", [])
        ]
    else:
        ipo.missing.append("no grey market premium data")
