---
description: Run the BSE morning analysis and interpret the results
---

Run the deterministic analyser, then interpret what it found.

## Steps

1. Run the analyser from the project root:

   ```
   python morning.py --quiet
   ```

   It prints the paths it wrote. If it exits non-zero, the portfolio file
   could not be read — show the user the error and stop; do not proceed with
   a partial analysis.

2. Read the facts bundle it just wrote:
   `data/reports/<session-date>/facts.json`

   This contains every computed number and every piece of evidence, already
   derived from live BSE and Yahoo data. It is your only source of figures.

3. Write the interpretation directly in the terminal.

## What to write

The Python engine already produced per-stock scores and evidence. Your job is
the layer it cannot do: **synthesis and judgement across the whole picture.**

Cover, briefly and in this order:

- **The one thing that matters most today.** Lead with it. If nothing needs
  action, say so plainly in a sentence — a quiet morning is a valid finding
  and should not be padded into a page of prose.
- **Anything requiring a decision today**, especially: a breached stop, an
  IPO closing today or tomorrow, a holding whose action changed from the raw
  chart signal, or a capital-gains date about to be crossed.
- **Cross-holding patterns the per-stock analysis structurally cannot see.**
  Three holdings each down 8% is one observation, not three — is it sector
  rotation, a market-wide drawdown, or three unrelated stories? The engine
  scores each stock in isolation; you are the only part that sees them
  together.
- **Where the evidence conflicts**, and which side you find more convincing
  and why. A composite score of −0.15 built from a strong trend signal and a
  strong opposing mean-reversion signal is a genuinely different situation
  from one where every axis is mildly negative, and the single number hides
  that. Look at the per-axis scores, not just the composite.
- **What would change the conclusion.** Name the specific, observable thing.

## Rules

- **Never invent a number.** Every figure you cite must come from the bundle.
  If something is not in there, say it is not available. The bundle marks
  unavailable indicators explicitly and records why — respect that rather
  than filling the gap.
- **Do not silently overrule the engine.** If you disagree with a computed
  action, say so explicitly, give the reason, and keep the original visible.
- **Respect the confidence field.** A holding analysed at 60% confidence has
  had axes dropped for missing data; do not present it as firmly as one at
  100%.
- **Check `data_issues`, `unresolved`, and `load_warnings`.** An unresolved
  symbol means a holding was not analysed at all — surface that prominently
  rather than reporting on the remainder as if it were the whole portfolio.
  A symbol usually goes unresolved because it was renamed or the company
  demerged, and the bundle carries suggested replacements.
- **Corporate actions invalidate cost basis.** If a position carries a
  `corporate_actions_since_buy` entry, its P&L is suspect — say so before
  reasoning from that P&L.
- **On IPOs**, remember the engine already caps grey-market-premium
  influence and discounts QIB figures before the final day. Do not undo that
  by treating a high GMP as decisive.
- Keep it tight. This is read once, before the market opens, by someone who
  wants to know what to do.

## Closing line

End with a one-line reminder that this is decision support, not financial
advice — you are reading price history and published figures, not predicting
the market.
