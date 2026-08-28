"""IPO scoring.

The output is a verdict (APPLY / CONSIDER / NEUTRAL / AVOID) built from five
axes, each contributing evidence with the actual figure attached.

Three judgement calls are baked in deliberately:

1. **QIB timing.** Institutional bids land almost entirely on the final day of
   the window. A QIB book at 0.5x on day one says nothing; the same figure at
   the close says institutions looked and declined. The score treats those two
   situations completely differently, because conflating them is the most
   common way retail investors misread a subscription table.

2. **GMP is capped.** Grey market premium is an unofficial, thinly traded,
   easily manipulated quote. It is real information about sentiment, so it is
   not ignored - but `gmp_max_influence` stops it from ever being the reason
   an issue gets an APPLY.

3. **Who gets the money.** An offer-for-sale transfers cash to selling
   shareholders; a fresh issue puts it into the business. An issue that is
   mostly OFS, or where the promoter's stake falls to near zero, is scored
   down regardless of how strong the demand looks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from ..providers.ipo import IPOData, collect_ipos
from .signals import Evidence, clip


@dataclass
class IPOAnalysis:
    data: IPOData
    score: float
    verdict: str
    evidence: list[Evidence] = field(default_factory=list)
    axis_scores: dict = field(default_factory=dict)
    confidence: float = 1.0
    unavailable: list[str] = field(default_factory=list)

    # convenience passthroughs for the report templates
    @property
    def name(self) -> str:
        return self.data.name

    @property
    def ipo_type(self) -> str:
        return self.data.ipo_type

    @property
    def price_band(self) -> str | None:
        return self.data.price_band

    @property
    def lot_size(self) -> int | None:
        return self.data.lot_size

    @property
    def min_investment(self) -> float | None:
        return self.data.min_investment

    @property
    def close_date(self):
        return self.data.close_date

    @property
    def issue_size(self) -> str | None:
        if not self.data.issue_size:
            return None
        return self.data.issue_size.replace("&#8377;", "₹")

    # -- dashboard conveniences -------------------------------------------

    @property
    def urgency(self) -> str:
        """How soon this needs a decision - drives dashboard ordering."""
        d = self.data
        if d.is_upcoming:
            return "upcoming"
        if d.closes_today:
            return "closes-today"
        if d.days_left is not None and d.days_left <= 1:
            return "closes-tomorrow"
        return "open"

    @property
    def urgency_label(self) -> str:
        d = self.data
        if d.is_upcoming and d.open_date:
            days = (d.open_date - date.today()).days
            return "Opens tomorrow" if days == 1 else f"Opens in {days} days"
        if d.closes_today:
            return "Closes TODAY"
        if d.days_left == 1:
            return "Closes tomorrow"
        if d.days_left is not None:
            return f"{d.days_left} days left"
        return "Open"

    @property
    def axis_rows(self) -> list[dict]:
        """Per-axis contribution, for the score breakdown bar."""
        labels = {
            "valuation": "Valuation", "financials": "Financials",
            "subscription": "Demand", "gmp": "Grey market",
            "qualitative": "Structure",
        }
        return [
            {"name": labels.get(k, k), "score": v}
            for k, v in sorted(self.axis_scores.items(), key=lambda kv: -abs(kv[1]))
        ]

    def to_dict(self) -> dict:
        d = self.data
        return {
            "name": d.name,
            "type": d.ipo_type,
            "verdict": self.verdict,
            "score": round(self.score, 3),
            "confidence": round(self.confidence, 2),
            "open_date": str(d.open_date) if d.open_date else None,
            "close_date": str(d.close_date) if d.close_date else None,
            "days_left": d.days_left,
            "price_band": d.price_band,
            "lot_size": d.lot_size,
            "min_investment": d.min_investment,
            "issue_size": self.issue_size,
            "ofs_share": round(d.ofs_share, 3) if d.ofs_share is not None else None,
            "subscription": {"qib": d.sub_qib, "nii": d.sub_nii,
                             "rii": d.sub_rii, "total": d.sub_total},
            "subscription_history": d.sub_history,
            "subscription_momentum": d.sub_momentum,
            "retail_allotment_odds": (
                round(d.retail_allotment_odds, 4)
                if d.retail_allotment_odds is not None else None
            ),
            "gmp": {"value": d.gmp, "pct": d.gmp_pct,
                    "estimated_listing": d.estimated_listing,
                    "updated": d.gmp_updated},
            "gmp_trend": d.gmp_trend,
            "gmp_history": d.gmp_history[:10],
            "timetable": {
                "allotment": d.allotment_date, "refund": d.refund_date,
                "credit": d.credit_date, "listing": d.listing_date,
            },
            "application_tiers": {
                "min": d.min_qty_desc, "retail_max": d.max_retail_qty_desc,
                "shni_min": d.min_hni_qty_desc, "bhni_min": d.min_bhni_qty_desc,
            },
            "reservation": {
                "retail": d.retail_reservation, "qib": d.qib_reservation,
                "nii": d.nii_reservation,
            },
            "registrar": d.registrar,
            "anchor": {"participated": d.anchor_status,
                       "shares": d.anchor_shares,
                       "note": d.anchor_note, "url": d.anchor_url},
            "is_upcoming": d.is_upcoming,
            "closes_today": d.closes_today,
            "financials": {
                "eps": d.eps, "eps_post_issue": d.eps_post,
                "pe": d.pe_ratio, "post_issue_pe": d.post_pe_ratio,
                "ronw": d.ronw, "ronw_prev": d.ronw_prev,
                "debt_equity": d.debt_equity,
                "pat_margin": d.pat_margin, "pat_margin_prev": d.pat_margin_prev,
                "promoter_pre": d.promoter_pre, "promoter_post": d.promoter_post,
                "as_of": d.financial_date,
            },
            "axis_scores": {k: round(v, 3) for k, v in self.axis_scores.items()},
            "evidence": [
                {"axis": e.axis, "statement": e.statement,
                 "direction": e.direction, "score": round(e.score, 3)}
                for e in self.evidence
            ],
            "unavailable": self.unavailable,
            "detail_url": d.detail_url,
        }


def _mean(parts: list[Evidence]) -> float:
    return sum(p.score for p in parts) / len(parts) if parts else 0.0


def _dir(score: float, deadzone: float = 0.05) -> str:
    """Direction label, with a genuine neutral band.

    Without the neutral band a score of -0.01 renders as a bearish ▼ next to
    text saying the metric improved, which reads as a contradiction.
    """
    if score > deadzone:
        return "bullish"
    if score < -deadzone:
        return "bearish"
    return "neutral"


# ---------------------------------------------------------------------------
# axes
# ---------------------------------------------------------------------------

def _score_subscription(d: IPOData, today: date) -> tuple[float, list[Evidence], list[str]]:
    parts: list[Evidence] = []
    gaps: list[str] = []
    if d.sub_total is None:
        return 0.0, parts, ["subscription figures not yet published"]

    # Where are we in the bidding window?
    #
    # `closing_day` is a calendar fact; `book_final` is the one that matters.
    # Bidding runs until about 17:00 IST, and the book fills in those last
    # hours - measured on 27 Aug 2026, Hy Tech Engineers went from 51.76x at
    # midday to 247.39x by the close, and Symbiotec's institutional book went
    # from 0.66x to 181.20x on the same afternoon. Reading a morning snapshot
    # on the closing day as if bidding had finished produces exactly the wrong
    # conclusion, so the harsh judgements below wait for `book_final`.
    closing_day = bool(d.close_date and today >= d.close_date)
    final_day = d.book_is_final
    day_num = ((today - d.open_date).days + 1) if d.open_date else None
    if final_day:
        stage = "the close"
    elif closing_day:
        stage = "the closing day, bidding still open"
    else:
        stage = f"day {day_num}" if day_num else "mid-window"

    # How far through the window are we? An issue that opened this morning has
    # a near-empty book by definition. Penalising that as weak demand confuses
    # "nobody wants it" with "it opened an hour ago", and books fill fastest on
    # the final day, so an undersubscription penalty only earns its keep as the
    # close approaches.
    progress = 1.0
    if d.open_date and d.close_date and not closing_day:
        span = max(1, (d.close_date - d.open_date).days)
        progress = clip(max(0.0, (today - d.open_date).days) / span, 0.0, 1.0)

    total = d.sub_total
    score = clip((total - 1.0) / 8.0)
    if total < 1:
        if final_day:
            # Bidding has ended and the issue is still not covered. This is
            # the only point at which an unfilled book is genuinely damning.
            text = (f"Overall subscription finished at {total:.2f}x - the issue "
                    f"was not fully spoken for even at the close, so there is "
                    f"no allotment scarcity and listing demand looks soft")
            score = clip(-0.5 + total * 0.4)
        elif closing_day:
            # Bidding is still open on the last day, when most of the book
            # arrives. Penalising now would be reading the race at the turn.
            score = clip(-0.2)
            text = (f"Overall subscription is {total:.2f}x, but bidding is "
                    f"still open on the closing day and books fill fastest in "
                    f"the final hours - re-check after the close before "
                    f"reading anything into this")
        elif progress < 0.5:
            # Too early to read anything into an unfilled book.
            score = clip(-0.15 * progress * 2)
            text = (f"Overall subscription is {total:.2f}x on {stage}, but "
                    f"bidding has only just opened - books fill fastest on the "
                    f"final day, so this figure carries little information yet")
        else:
            text = (f"Overall subscription is {total:.2f}x on {stage} - the issue "
                    f"is not yet fully spoken for, so there is no allotment "
                    f"scarcity and listing demand looks soft")
            score = clip(-0.35 + total * 0.3)
    elif total < 3:
        text = (f"Overall subscription is {total:.2f}x on {stage} - covered, "
                f"but without the heavy oversubscription that usually precedes "
                f"a strong listing")
    elif total < 15:
        text = (f"Overall subscription is {total:.1f}x on {stage} - solid demand "
                f"across the book")
    else:
        text = (f"Overall subscription is {total:.1f}x on {stage} - very heavy "
                f"demand, though it also means allotment odds are thin")
    parts.append(Evidence("subscription", text, _dir(score), score))

    # Momentum between snapshots. Books normally fill fastest on the final
    # day, so a book that has barely moved between sessions is weaker than
    # the cumulative figure alone suggests.
    momentum = d.sub_momentum
    if momentum and not closing_day and progress >= 0.5:
        delta = momentum["delta"]
        if delta >= 1.0:
            parts.append(Evidence(
                "subscription",
                f"Demand accelerated from {momentum['from']:.2f}x to "
                f"{momentum['to']:.2f}x since the previous session",
                "bullish", clip(delta / 6.0),
            ))
        elif delta <= 0.05:
            parts.append(Evidence(
                "subscription",
                f"The book has barely moved ({momentum['from']:.2f}x to "
                f"{momentum['to']:.2f}x) since the previous session - interest "
                f"is stalling rather than building",
                "bearish", -0.35,
            ))

    # QIB, read with the timing caveat that matters most.
    if d.sub_qib is not None:
        if final_day:
            qib_score = clip((d.sub_qib - 1.0) / 5.0)
            if d.sub_qib < 1:
                text = (f"Institutional (QIB) book is only {d.sub_qib:.2f}x on the "
                        f"final day. Institutions have had the full window to do "
                        f"their diligence and have not filled their portion - "
                        f"the single most informative negative here")
                qib_score = -0.7
            else:
                text = (f"Institutional (QIB) book is {d.sub_qib:.2f}x at the close - "
                        f"institutions who did the diligence committed capital")
        elif closing_day:
            # The most dangerous moment to misread. On the closing morning the
            # institutional book is routinely near zero and then fills within
            # hours: Symbiotec Pharmalab sat at 0.66x at 11:00 on 27 Aug 2026
            # and closed at 181.20x. Treat this figure as unformed, and say so.
            qib_score = clip((d.sub_qib - 1.0) / 6.0) * 0.15
            text = (f"Institutional (QIB) book stands at {d.sub_qib:.2f}x, but "
                    f"bidding is still open and institutions overwhelmingly bid "
                    f"in the last hours of the final day. This number is not yet "
                    f"a verdict on the issue - check it again after the close")
        else:
            qib_score = clip((d.sub_qib - 1.0) / 6.0) * 0.3
            text = (f"Institutional (QIB) book is {d.sub_qib:.2f}x, but QIB bids "
                    f"almost always arrive on the final day, so this figure "
                    f"carries little information yet")
        parts.append(Evidence("subscription", text, _dir(qib_score), qib_score))
    else:
        gaps.append("QIB subscription")

    if d.sub_rii is not None and d.sub_nii is not None:
        if d.sub_rii > 3 and d.sub_qib is not None and d.sub_qib < 1 and final_day:
            parts.append(Evidence(
                "subscription",
                f"Retail is {d.sub_rii:.1f}x while institutions sit at "
                f"{d.sub_qib:.2f}x - demand is coming from the least informed "
                f"part of the book, which historically is a warning rather "
                f"than an endorsement",
                "bearish", -0.5,
            ))

    return _mean(parts), parts, gaps


def _score_valuation(d: IPOData) -> tuple[float, list[Evidence], list[str]]:
    parts: list[Evidence] = []
    gaps: list[str] = []
    pe = d.post_pe_ratio or d.pe_ratio
    if pe is None:
        gaps.append("price/earnings ratio")
    else:
        label = "post-issue" if d.post_pe_ratio else "pre-issue"
        if pe <= 0:
            parts.append(Evidence("valuation",
                                  f"The company is loss-making, so no meaningful "
                                  f"P/E can be computed - you are buying a story, "
                                  f"not current earnings", "bearish", -0.6))
        elif pe < 15:
            parts.append(Evidence("valuation",
                                  f"Priced at {pe:.1f}x {label} earnings - "
                                  f"undemanding for a listed peer group",
                                  "bullish", 0.7))
        elif pe < 25:
            parts.append(Evidence("valuation",
                                  f"Priced at {pe:.1f}x {label} earnings - "
                                  f"a reasonable, mid-range multiple",
                                  "bullish", 0.3))
        elif pe < 40:
            parts.append(Evidence("valuation",
                                  f"Priced at {pe:.1f}x {label} earnings - "
                                  f"full. The business has to keep growing "
                                  f"just to justify the issue price",
                                  "bearish", -0.25))
        else:
            parts.append(Evidence("valuation",
                                  f"Priced at {pe:.1f}x {label} earnings - "
                                  f"expensive. At this multiple most of the "
                                  f"next few years of growth is already in "
                                  f"the price", "bearish", -0.65))

    # Dilution: post-issue EPS below pre-issue means your slice shrinks.
    if d.eps and d.eps_post:
        dilution = (d.eps_post - d.eps) / d.eps
        if dilution < -0.05:
            parts.append(Evidence(
                "valuation",
                f"EPS falls from {d.eps:.2f} to {d.eps_post:.2f} post-issue "
                f"({dilution:.0%}) - the new shares dilute existing earnings, "
                f"which is normal but means the effective multiple you pay is "
                f"higher than the headline",
                "bearish", clip(dilution * 2),
            ))
    return _mean(parts), parts, gaps


def _score_financials(d: IPOData) -> tuple[float, list[Evidence], list[str]]:
    parts: list[Evidence] = []
    gaps: list[str] = []

    if d.ronw is not None:
        score = clip((d.ronw - 12.0) / 15.0)
        trend = ""
        if d.ronw_prev is not None:
            direction = "improved from" if d.ronw > d.ronw_prev else "declined from"
            trend = f", {direction} {d.ronw_prev:.1f}%"
            if d.ronw < d.ronw_prev:
                score -= 0.2
        parts.append(Evidence(
            "financials",
            f"Return on net worth is {d.ronw:.1f}%{trend}"
            + (" - comfortably above the ~12% that marks a business earning "
               "more than its cost of capital" if d.ronw >= 15 else
               " - modest returns on the capital employed"),
            _dir(score), clip(score),
        ))
    else:
        gaps.append("return on net worth")

    if d.pat_margin is not None:
        score = clip((d.pat_margin - 6.0) / 12.0)
        trend = ""
        if d.pat_margin_prev is not None:
            if d.pat_margin < d.pat_margin_prev:
                trend = f", down from {d.pat_margin_prev:.1f}%"
                score -= 0.15
            else:
                trend = f", up from {d.pat_margin_prev:.1f}%"
        note = ""
        if d.pat_margin < 3:
            note = (" - a very thin margin leaves almost no cushion if costs "
                    "rise or pricing weakens")
        parts.append(Evidence(
            "financials",
            f"Net profit margin is {d.pat_margin:.1f}%{trend}{note}",
            _dir(score), clip(score),
        ))
    else:
        gaps.append("profit margin")

    if d.debt_equity is not None:
        score = clip((1.0 - d.debt_equity) / 1.5)
        parts.append(Evidence(
            "financials",
            f"Debt-to-equity is {d.debt_equity:.2f}"
            + (" - a conservatively financed balance sheet" if d.debt_equity < 0.5
               else " - meaningful leverage, which amplifies both good and bad years"),
            _dir(score), score,
        ))
    else:
        gaps.append("debt/equity")

    return _mean(parts), parts, gaps


def _score_gmp(d: IPOData, cap: float) -> tuple[float, list[Evidence], list[str]]:
    if d.gmp_pct is None:
        return 0.0, [], ["grey market premium"]
    pct = d.gmp_pct
    score = clip(pct / 40.0)
    if pct <= 0:
        text = (f"Grey market premium is {pct:.1f}% - the unofficial market "
                f"expects a flat-to-negative listing")
    elif pct < 10:
        text = (f"Grey market premium is {pct:.1f}% - a slim expected listing "
                f"gain, easily erased by a weak market on listing day")
    elif pct < 30:
        text = (f"Grey market premium is {pct:.1f}%, implying a listing near "
                f"{d.estimated_listing:,.0f}" if d.estimated_listing else
                f"Grey market premium is {pct:.1f}%")
    else:
        text = (f"Grey market premium is {pct:.1f}%"
                + (f", implying a listing near {d.estimated_listing:,.0f}"
                   if d.estimated_listing else "")
                + " - strong unofficial demand, but GMP is an unregulated, "
                  "thinly traded quote that can be moved cheaply and often "
                  "compresses sharply in the final days")
    parts = [Evidence("gmp", text, _dir(score), score)]

    # Direction matters more than level. A premium draining away through the
    # bidding window is the market quietly revising its view downward, and it
    # frequently precedes a weak listing even when the headline number is
    # still positive.
    trend = d.gmp_trend
    if trend:
        if trend["direction"] == "falling":
            parts.append(Evidence(
                "gmp",
                f"The premium has fallen from {trend['earlier']:.1f}% to "
                f"{trend['latest']:.1f}% over the last {trend['days']} day(s) - "
                f"sentiment is draining out of the issue as the close "
                f"approaches, which matters more than the level itself",
                "bearish", -0.5,
            ))
        elif trend["direction"] == "rising":
            parts.append(Evidence(
                "gmp",
                f"The premium has risen from {trend['earlier']:.1f}% to "
                f"{trend['latest']:.1f}% over the last {trend['days']} day(s) - "
                f"unofficial demand is building into the close",
                "bullish", 0.4,
            ))
        else:
            parts.append(Evidence(
                "gmp",
                f"The premium has held roughly flat near {trend['latest']:.1f}% "
                f"across {trend['points']} days of quotes - stable, if "
                f"unexciting, sentiment",
                "neutral", 0.0,
            ))
    return _mean(parts), parts, []


def _score_qualitative(d: IPOData) -> tuple[float, list[Evidence], list[str]]:
    parts: list[Evidence] = []
    gaps: list[str] = []

    share = d.ofs_share
    if share is None:
        gaps.append("fresh-issue vs offer-for-sale split")
    else:
        if share > 0.75:
            parts.append(Evidence(
                "qualitative",
                f"{share:.0%} of the issue is an offer for sale - most of the "
                f"money you pay goes to existing shareholders cashing out, not "
                f"into the business",
                "bearish", -0.7,
            ))
        elif share > 0.4:
            parts.append(Evidence(
                "qualitative",
                f"{share:.0%} of the issue is an offer for sale, so only "
                f"{1 - share:.0%} of the proceeds fund the company itself",
                "bearish", -0.25,
            ))
        else:
            parts.append(Evidence(
                "qualitative",
                f"{1 - share:.0%} of the issue is fresh capital going into the "
                f"business rather than to selling shareholders",
                "bullish", 0.5,
            ))

    # Anchor book. Anchors are institutions that commit the day before bidding
    # opens, having read the prospectus and met management. Their absence on a
    # mainboard issue is a real signal: it usually means the bankers could not
    # place the stock with informed money at this price. Measured 27 Aug 2026,
    # Annu Projects was the only open mainboard issue with no anchor book -
    # and the only one whose book failed to fill.
    if d.anchor_status is None:
        gaps.append("anchor investor participation")
    elif d.anchor_status == 0:
        parts.append(Evidence(
            "qualitative",
            "No anchor investors took part. Anchors commit a day before "
            "bidding opens after seeing the prospectus and meeting "
            "management, so an empty anchor book means informed institutional "
            "money declined this price before retail was ever asked",
            "bearish", -0.6 if d.ipo_type.upper() != "SME" else -0.35,
        ))
    elif d.anchor_shares:
        parts.append(Evidence(
            "qualitative",
            f"{d.anchor_shares:,} shares were placed with anchor investors "
            f"ahead of the issue, so institutions committed capital after "
            f"seeing the prospectus",
            "bullish", 0.35,
        ))

    if d.promoter_post is not None and d.promoter_pre is not None:
        if d.promoter_post <= 1.0:
            parts.append(Evidence(
                "qualitative",
                f"Promoter holding goes from {d.promoter_pre:.1f}% to "
                f"{d.promoter_post:.1f}% - the promoters are exiting entirely. "
                f"Whatever the stated rationale, nobody knows the business "
                f"better, and they are choosing not to own it",
                "bearish", -0.9,
            ))
        elif d.promoter_post < 40:
            parts.append(Evidence(
                "qualitative",
                f"Promoter holding falls from {d.promoter_pre:.1f}% to "
                f"{d.promoter_post:.1f}%, leaving limited skin in the game",
                "bearish", -0.3,
            ))
        else:
            parts.append(Evidence(
                "qualitative",
                f"Promoters retain {d.promoter_post:.1f}% after the issue "
                f"(from {d.promoter_pre:.1f}%), keeping their interests "
                f"aligned with yours",
                "bullish", 0.4,
            ))
    else:
        gaps.append("promoter shareholding")

    return _mean(parts), parts, gaps


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def analyse_ipo(d: IPOData, cfg: dict, today: date | None = None) -> IPOAnalysis:
    today = today or date.today()

    # An InvIT or REIT is a yield vehicle, not a share in a growing business.
    # P/E, return on net worth, promoter dilution and offer-for-sale share all
    # mean something different or nothing at all for one. Scoring it on the
    # equity framework yields a confident answer built on inapplicable inputs -
    # backtesting showed four such trusts all landing on APPLY and then
    # averaging roughly flat. Refuse to rate them rather than mislead.
    if not d.is_equity:
        label = "REIT" if d.instrument == "REIT" else "InvIT / investment trust"
        return IPOAnalysis(
            data=d, score=0.0, verdict="NOT RATED", confidence=0.0,
            axis_scores={}, unavailable=["equity valuation does not apply"],
            evidence=[Evidence(
                "qualitative",
                f"This is a {label}, not an equity IPO. It is a yield vehicle "
                f"holding infrastructure or property, so price/earnings, return "
                f"on net worth and promoter dilution do not describe it. Judge "
                f"it on distribution yield, asset quality and leverage instead - "
                f"this tool deliberately does not score it rather than apply a "
                f"framework built for company shares.",
                "neutral", 0.0,
            )],
        )

    icfg = cfg["ipo"]
    weights = icfg["weights"]

    axes = {}
    evidence: list[Evidence] = []
    gaps: list[str] = []

    for name, (score, parts, missing) in {
        "subscription": _score_subscription(d, today),
        "valuation": _score_valuation(d),
        "financials": _score_financials(d),
        "gmp": _score_gmp(d, icfg["gmp_max_influence"]),
        "qualitative": _score_qualitative(d),
    }.items():
        axes[name] = score
        evidence.extend(parts)
        gaps.extend(missing)

    live = {k: v for k, v in axes.items() if k not in _empty_axes(axes, evidence)}
    # Confidence must come from the weight that genuinely had data. An
    # earlier `or 1.0` fallback here made an issue with NO usable axes report
    # 100% confidence, which is precisely backwards.
    live_weight = sum(weights[k] for k in live)
    all_weight = sum(weights.values())
    composite = (
        sum(axes[k] * weights[k] for k in live) / live_weight if live_weight else 0.0
    )
    confidence = (live_weight / all_weight) if all_weight else 0.0
    # Axis coverage is the main term, but an axis can be "live" while still
    # missing fields inside it - e.g. financials computed from RoNW and margin
    # with debt/equity absent. Reporting 100% confidence directly above a
    # "not available" list is self-contradictory, so each named gap shaves a
    # little off, floored so field gaps can never dominate axis coverage.
    if gaps:
        confidence *= max(0.6, 1.0 - 0.02 * len(set(gaps)))

    if d.ipo_type.upper() == "SME":
        penalty = icfg["sme_score_penalty"]
        composite -= penalty
        evidence.append(Evidence(
            "qualitative",
            f"This is an SME issue. SME listings trade in large lot sizes with "
            f"far thinner liquidity and lighter disclosure than mainboard "
            f"stocks, so the score carries a {penalty:.2f} risk penalty and "
            f"exiting can be genuinely difficult",
            "bearish", -penalty,
        ))

    thresholds = icfg["thresholds"]

    def verdict_for(value: float) -> str:
        if value >= thresholds["apply"]:
            return "APPLY"
        if value >= thresholds["consider"]:
            return "CONSIDER"
        if value <= thresholds["avoid"]:
            return "AVOID"
        return "NEUTRAL"

    verdict = verdict_for(composite)

    # With almost nothing published yet, a composite of 0.00 is the absence of
    # evidence, not a balanced verdict. Saying NEUTRAL there implies we looked
    # and found the case evenly matched, which is a lie about our own data.
    if confidence < 0.25:
        verdict = "NO DATA"

    # Grey market premium is an unregulated, thinly traded quote. It may
    # support a decision the fundamentals already justify, but it must never
    # be the reason an issue earns APPLY. Recompute without the GMP axis: if
    # the recommendation survives on its own merits, keep it; if it collapses,
    # step the verdict down and say exactly why.
    if verdict == "APPLY" and "gmp" in live and axes["gmp"] > 0:
        ex_gmp = {k: v for k, v in live.items() if k != "gmp"}
        ex_weight = sum(weights[k] for k in ex_gmp)
        if ex_weight:
            without_gmp = sum(axes[k] * weights[k] for k in ex_gmp) / ex_weight
            if verdict_for(without_gmp) != "APPLY":
                verdict = "CONSIDER"
                evidence.append(Evidence(
                    "gmp",
                    f"Stripping out grey market premium, the score falls from "
                    f"{composite:+.2f} to {without_gmp:+.2f} - not enough to "
                    f"justify applying on fundamentals alone. GMP is unofficial "
                    f"and can move sharply before listing, so this is held at "
                    f"CONSIDER rather than APPLY.",
                    "bearish", -0.3,
                ))

    # Nothing to act on once bidding has closed.
    if d.close_date and today > d.close_date:
        verdict = "CLOSED"

    evidence.sort(key=lambda e: -abs(e.score))
    return IPOAnalysis(
        data=d, score=float(composite), verdict=verdict, evidence=evidence,
        axis_scores=axes, confidence=float(confidence),
        unavailable=sorted(set(gaps)),
    )


def _empty_axes(axes: dict, evidence: list[Evidence]) -> set[str]:
    """Axes that produced no evidence at all are excluded from the weighting."""
    present = {e.axis for e in evidence}
    return {k for k in axes if k not in present}


def analyse_ipos(
    config: dict,
    cache=None,
    today: date | None = None,
    include_upcoming: bool = False,
    upcoming_within_days: int = 14,
) -> list[IPOAnalysis]:
    """Collect IPOs open for subscription (optionally upcoming) and score each."""
    issues = collect_ipos(
        cache,
        open_only=True,
        include_upcoming=include_upcoming,
        upcoming_within_days=upcoming_within_days,
    )
    return [analyse_ipo(d, config, today) for d in issues]
