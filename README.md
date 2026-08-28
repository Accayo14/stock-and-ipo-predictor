# BSE IPO & Portfolio Analyser

### Live dashboard

- **[Hosted copy](https://claude.ai/code/artifact/1914a218-f89e-4d3e-94b3-1829d245f433)** — works now
- **[GitHub Pages](https://accayo14.github.io/stock-and-ipo-predictor/)** — once Pages is enabled for this repo
  (Settings → Pages → Deploy from a branch → `main` / `/docs`)

> ### Not financial advice
> This is a personal research tool. It applies a fixed scoring formula to
> public data and shows its reasoning. It is published so the logic can be
> inspected, not so the output can be followed. The author is not a
> SEBI-registered adviser. IPOs regularly list below their issue price,
> including heavily subscribed ones with large grey market premiums — **you
> can lose money.** See [DISCLAIMER.md](DISCLAIMER.md).

Decides whether to apply to today's IPOs, and analyses a BSE portfolio before
the open. Every verdict shows the reasoning and the actual numbers behind it.

Your holdings stay on your machine — `config/portfolio.csv` and every report
that renders it are gitignored and never published.

---

## Daily use — IPOs

Two commands, in this order:

```bash
python ipo.py --fresh --show     # fetch today's issues, open the dashboard
```

Then in Claude Code:

```
/ipo
```

`/ipo` re-runs the fetch, researches each open issue on the web, writes the
findings to `data/ipo_notes/<date>.json`, merges them into the dashboard, and
records the calls to the track record.

That split is the whole design. Python computes what is computable. It cannot
read a news story, a prospectus risk factor, a pledged-share disclosure, or
notice that two lead managers sit on both sides of a deal — so those are
researched separately and shown in their own panels, never blended into the
computed score.

| Command | What it does |
|---|---|
| `python ipo.py` | Open + upcoming issues, writes dashboard and facts |
| `python ipo.py --fresh` | Ignores cache — **use this on a closing day** |
| `python ipo.py --show` | Opens the dashboard in your browser |
| `python ipo.py --send` | Pushes the summary to Telegram/email |
| `python ipo.py --open-only` | Only what you can apply to right now |
| `/ipo` in Claude Code | All of the above plus live news research |

Output lands in `data/reports/YYYY-MM-DD/`:
`ipo_dashboard.html`, `ipos.json`.

### What the dashboard shows per company

Verdict and confidence, a per-axis score breakdown, subscription bars with
day-over-day momentum, a grey-market-premium sparkline with trend direction,
estimated retail allotment odds, the full timetable, application tiers for
retail/sHNI/bHNI, **every reason behind the verdict**, and **every data field
held** — with missing values shown as missing rather than quietly omitted.

### How a verdict is reached

Five weighted axes: valuation (0.30), financials (0.25), subscription (0.20),
grey market (0.15), issue structure (0.10). Axes with no published data are
dropped and lower the confidence figure rather than being guessed at.

Five judgements are deliberately built in:

- **QIB timing.** Institutional bids land almost entirely on the final day. A
  0.5× QIB book on day one means nothing; the same figure at the close means
  institutions looked and declined. Scored completely differently.
- **Window awareness.** An issue that opened this morning has a near-empty
  book by definition. The undersubscription penalty only applies once the
  window is half elapsed, so "nobody wants it" is never confused with "it
  opened an hour ago."
- **Grey market premium is capped.** If removing GMP would drop an issue
  below the APPLY bar, the verdict is held at CONSIDER and says so
  explicitly. Sentiment can support a decision; it can never be the reason
  for one.
- **Who gets the money.** An offer-for-sale pays exiting shareholders; a
  fresh issue funds the business. Heavy-OFS issues and near-total promoter
  exits are marked down regardless of how strong demand looks.
- **The anchor book.** Anchor investors commit a day before bidding opens,
  after reading the prospectus and meeting management. An empty anchor
  book on a mainboard issue means informed money declined at this price
  before retail was ever asked. Measured 27 Aug 2026: of six open issues,
  the only one with no anchor book was also the only one whose book
  failed to fill.

SME issues carry an explicit penalty for thin liquidity and lighter
disclosure — and note their lot sizes routinely exceed ₹1,00,000.

A verdict of **NO DATA** means exactly that. An issue with nothing published
yet scores 0.00, and reporting that as NEUTRAL would imply we looked and
found the case evenly balanced, which would be a lie about our own coverage.

## Track record

```bash
python decisions.py record                                   # log today's calls
python decisions.py show                                     # the record so far
python decisions.py outcome "Hy Tech Engineers" --listing-price 61.5
```

A recommendation nobody scores is one nobody learns from. Calls are logged on
the day they are made and **frozen once an outcome is recorded** — revising a
prediction after seeing the result is the one thing that would make this
worthless. Before an outcome exists a call can still be completed, which is
how the research layer gets attached.

Over time `show` reports whether grey market premium has been over- or
under-optimistic in percentage points, and how APPLY/CONSIDER calls fared
against AVOID/NEUTRAL ones. That is the honest feedback loop.

## The live site

```bash
python publish.py            # copy the latest dashboard into docs/
python publish.py --push     # ...and commit + push to GitHub
```

`docs/` is served by GitHub Pages at
<https://accayo14.github.io/stock-and-ipo-predictor/>. The site is always
reachable; its contents change only when the analysis is run and pushed.
There is deliberately no scheduled rebuild - an automated attempt published
an empty dashboard, because the data sources return nothing when queried from
a datacenter address. Only the **IPO**
dashboard is published — it contains public market data and analysis of it.
The portfolio report renders real holdings and is excluded twice over: by
`.gitignore`, and by a check in `publish.py` that refuses to publish if
anything matching a personal-data filename appears in `docs/`.

Every published page carries the disclaimer banner; it cannot be read without
the warning attached.

## Backtest

```bash
python backtest.py --limit 30              # decision at the close
python backtest.py --limit 30 --strict     # no look-ahead at all
python backtest.py --csv out.csv           # per-issue detail
```

Reconstructs each past issue's inputs from its own page, runs the engine as if
standing at the close of bidding, and compares the verdict with the stock's
actual first-day close.

Result over 30 issues that listed in August 2026:

| Verdict | N | Avg listing gain | Win rate |
|---|---:|---:|---:|
| APPLY | 2 | **+58.1%** | 100% |
| CONSIDER | 16 | +38.4% | 94% |
| NEUTRAL | 6 | +6.0% | 50% |
| AVOID | 2 | +2.1% | 50% |

APPLY/CONSIDER averaged **+40.6%** against AVOID/NEUTRAL at **+5.0%** — a
35.6pp spread, and the ordering is monotonic across all four buckets. With
strict no-look-ahead data (previous day's book) the spread narrows to
**24.4pp**, which is the honest figure. That narrowing is itself a finding:
the final-day surge carries real signal, so decide as late in the window as
you can.

**Read this with heavy scepticism.** Thirty issues in a single, unusually
bullish month is weak evidence — applying to *everything* returned +25.6%
with 24 of 30 positive, so almost any rule looks good here. APPLY and AVOID
have two observations each, which is not a sample. The honest claim is only
that the engine is not obviously broken and orders issues sensibly; it is not
that these returns will repeat.

### What the backtest caught

It found a real bug immediately. Four **InvITs and REITs** were being scored
on the equity framework and all four landed on APPLY, then averaged roughly
flat (Cube Highways Trust listed −20.7%). They dragged the APPLY bucket to
+18.7%, *below* CONSIDER — the ordering was inverted. A yield vehicle holding
infrastructure has no meaningful P/E, promoter dilution or earnings growth, so
these are now classified and returned as **NOT RATED** rather than scored.
Removing them restored APPLY to +58.1%.

### Two findings that challenge the design

- **Grey market premium correlated +0.81 with actual listing gains** across
  the 14 issues that had it, and *understated* the outcome by 7.4pp on
  average. The engine deliberately caps GMP's influence on the grounds that
  it is unofficial and manipulable. On this evidence that cap costs accuracy.
  It has not been changed: fourteen observations in a hot market is not a
  reason to start trusting an unregulated quote, and the cap exists for when
  the market turns. Worth revisiting as the track record grows.
- **The two issues with no anchor book averaged −14.5%** against +28.4% for
  the 28 with one. Consistent with the anchor rule, but n=2.

## Daily use — portfolio

```bash
python morning.py --open        # or /morning in Claude Code
```

Holdings live in `config/portfolio.csv` (gitignored):

```csv
symbol,scrip_code,quantity,avg_buy_price,buy_date,target_price,stop_loss
RELIANCE,500325,25,1180.50,2025-03-14,1500,
```

Only `symbol`, `quantity` and `avg_buy_price` are required. `scrip_code`
resolves automatically. `buy_date` enables capital-gains timing warnings.

Five axes — trend (0.28), relative strength vs Sensex (0.24), momentum
(0.22), mean reversion (0.14), volume (0.12) — then a position overlay that
a chart cannot know:

- A breached stop loss **you** set forces EXIT, whatever the chart says
- A holding above your position-size limit cannot produce a buy signal
- A sale inside the capital-gains window raises a timing caveat
- A split or bonus after your buy date flags that your cost basis is stale

## Phone delivery

```bash
cp config/secrets.env.example config/secrets.env
```

Fill in Telegram (about two minutes — the file has the steps) or SMTP, then
enable it in `config.yaml` under `delivery:`. Then `python ipo.py --send`
puts the summary on your phone, ordered by what closes first.

## Data sources

| Source | Used for |
|---|---|
| BSE public issues API | The definitive list of open IPOs, dates, price bands |
| InvestorGain | Lot size, subscription, GMP history, IPO financials |
| BSE API | Prices, EPS/PE, sector, market cap, corporate actions |
| BSE bhavcopy | Whole-market EOD, symbol → scrip code (~4,900 scrips) |
| Yahoo Finance | Daily OHLCV history for indicators |

No API keys. No paid tiers. Notes on two sources:

**Yahoo `.BO` is unreliable per symbol.** Measured 2026-08-26: `RELIANCE.BO`
returned 29 usable daily bars while `RELIANCE.NS` returned 1240; `TCS.BO` was
fine. Each candidate is fetched, graded, and the healthiest kept — recording
which exchange supplied it. BSE and NSE prices track within ~0.1%, well below
any indicator's noise floor.

**Peer comparison is not available.** InvestorGain flags issues as having
peer data, but the field comes back empty on every issue tested, so relative
valuation against listed peers is not in the score. Where a broker review
supplies it, it appears in the research notes instead.

**Timing matters more than anything else on a closing day.** Bidding runs
to about 17:00 IST and the book fills in the final hours. On 27 Aug 2026
Hy Tech went from 51.8x at midday to 247.4x at the close, and Symbiotec's
institutional book went from 0.66x to 181.2x that same afternoon. The
engine therefore withholds its harsh demand judgements until the data is
timestamped after the close, and the dashboard marks provisional figures.
**Re-run after 17:00 IST before acting on a closing-day issue.**

**IPO subscription and GMP come from scraping** InvestorGain's rendered
pages. Every field is optional and its absence is reported rather than
guessed. If IPO detail goes blank one morning, a page restructure is the
likely cause.

## Deliberately not used

- **OpenBB** — its 33 providers contain no Indian source. The only one
  touching India is `yfinance`, which we already call directly, and doing so
  is what surfaced the broken `.BO` history above. It has no Indian IPO data
  at all.
- **Chittorgarh for GMP** — its GMP report now resolves to an unrelated page,
  and its own site links out to InvestorGain.
- **Tapetide MCP** — real and useful, but its free tier is ~50 tool calls per
  day, too few for bulk work. Left disabled; sensible for low-volume
  enrichment if you want it.

## Tests

```bash
python tests/test_indicators.py   # indicator maths vs pandas + textbook refs
python tests/test_risk.py         # position overlay rules
python tests/test_ipo.py          # IPO scoring, timing and caps
python tests/smoke_bse.py         # live BSE endpoints
python tests/smoke_engine.py      # full portfolio pipeline
```

---

## What this cannot do

It reads published figures and researched reporting. It cannot see an
accounting fraud, a pending regulatory action nobody has written about, or
next month.

**This is decision support, not financial advice.** IPOs list below their
issue price regularly, including heavily subscribed ones with large grey
market premiums. A rigorous process reduces the chance of an avoidable
mistake; it does not make an outcome safe. Anything here that reads like a
guarantee is a bug in the wording.
