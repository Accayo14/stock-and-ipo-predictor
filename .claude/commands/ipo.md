---
description: Analyse today's IPOs — computed facts plus live news research
---

Produce today's IPO decisions. This runs two layers and keeps them separate:
Python computes the facts; you research what Python cannot see.

## Step 1 — compute the facts

```
python ipo.py --fresh --quiet
```

`--fresh` matters: subscription and grey market premium move hourly, and a
cached figure on the closing day of an issue is worse than no figure.

Then read `data/reports/<today>/ipos.json`.

## Step 2 — decide what needs research

Research every issue that is **open now or opens today/tomorrow**. Skip
anything further out unless the user asks — it will be re-run before it
matters, and stale research is a liability.

Prioritise by `urgency`: `closes-today` first, then `closes-tomorrow`, then
`open`, then imminent `upcoming`.

## Step 3 — research each one

For each issue, run web searches. Actual searches — never answer from
memory, and never carry forward yesterday's notes as if they were today's.
Useful angles:

- `"<company> IPO review analysis"` — broker views, subscribe/avoid calls
- `"<company> IPO risk factors prospectus"` — concentration, litigation,
  related-party dealings, pledged shares
- `"<company> IPO anchor investors"` — a book anchored by serious long-only
  funds is a different proposition from one anchored by unknown names
- `"<company> IPO GMP subscription day"` — to sanity-check our own figures

You are looking specifically for what the numbers structurally cannot show:

- Related-party conflicts (lead managers on both sides of the deal, as with
  Symbiotec Pharmalab)
- Pledged promoter shares
- Litigation, tax disputes, regulatory action
- Customer or supplier concentration
- Anchor book quality, and whether anchors are locked in
- Sector conditions and how comparable recent listings performed
- Any discrepancy between our computed figures and what sources report — if
  our subscription number disagrees with a news source, say so and check
  which is more recent

## Step 4 — write the notes

Write `data/ipo_notes/<today>.json` in this shape:

```json
{
  "generated": "<YYYY-MM-DD> research pass",
  "by": "claude-code",
  "method": "one line on what you searched",
  "notes": {
    "<Exact company name from ipos.json>": {
      "summary": "two sentences on what the numbers miss",
      "items": [
        {"text": "specific finding with figures",
         "direction": "bullish|bearish|neutral",
         "source": "publication or document"}
      ],
      "adjustment_reason": "whether you agree with the computed verdict, and if not, what your call is and why"
    }
  }
}
```

Then re-render so the dashboard picks it up:

```
python ipo.py --quiet
```

## Step 5 — record the decision

```
python decisions.py record
```

This appends today's calls to the track record so their quality can be
reviewed later. Do not skip it — a recommendation nobody scores is a
recommendation nobody learns from.

## Step 6 — publish the site

```
python publish.py --push
```

This copies the dashboard into `docs/`, injects the disclaimer banner,
archives a dated copy, and pushes to GitHub. The live site is
<https://accayo14.github.io/stock-and-ipo-predictor/>.

Only the IPO dashboard is published. The portfolio report renders real
holdings and must never reach `docs/` - `publish.py` refuses to publish if it
finds anything matching a personal-data filename there, and `.gitignore`
excludes them too. Do not weaken either check.

Commits are authored by the repository owner. **Never add a co-author trailer
or any tool attribution to a commit message in this repository.**

## Step 7 — report

In the terminal, lead with what needs a decision **today**, in this order:

1. Anything closing today, with your final call and the single most
   important reason
2. Where you disagree with the computed verdict, and why
3. Anything opening tomorrow worth preparing for
4. One line on what you could not find out

## Rules

- **Every factual claim needs a source.** If research turns up nothing on a
  company, write that plainly in the notes rather than padding with
  plausible-sounding generalities. "I could not find independent coverage of
  this SME issue" is a genuine and useful finding.
- **Keep volatile figures out of the notes.** Subscription multiples and
  grey market premium move by the hour - one issue went from 0.66x to 181x
  institutional in a single afternoon. The dashboard shows those live. Notes
  should carry only what stays true: prospectus risks, conflicts,
  concentration, peer valuation, broker positioning.
- **On a closing day, check the clock.** Bidding runs to about 17:00 IST and
  the book fills in the final hours. A morning reading is not a verdict; say
  so, and tell the user to re-run after the close.
- **Never invent figures.** Numbers come from `ipos.json` or from a named
  source. If a source contradicts our data, surface the conflict.
- **State disagreement, never blend it away.** If the computed verdict says
  CONSIDER and your research says AVOID, say exactly that and give the
  reason. The computed verdict stays visible.
- **Respect the caps already applied.** The engine deliberately discounts
  QIB before the final day and caps grey market premium influence. Do not
  quietly undo those by treating a high GMP as decisive.
- **SME issues need extra scepticism.** Lot sizes run past ₹1,00,000, so a
  single application is a large, illiquid, hard-to-exit position. Thin
  independent coverage is normal and is itself a risk.
- **Never say a listing gain is likely or assured.** Grey market premium is
  sentiment, not a forecast, and issues list below their price regularly.
- Close with a one-line reminder that this is decision support, not
  financial advice.
