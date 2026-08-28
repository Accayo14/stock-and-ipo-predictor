# Disclaimer

**This project is not financial advice, and nothing it produces is a
recommendation to buy or sell any security.**

## What this is

A personal research tool. It gathers publicly available information about
Indian IPOs and listed shares — subscription figures, grey market premiums,
prospectus financials, price history — applies a fixed set of rules to it, and
shows the reasoning behind the result.

It is published so its logic can be inspected, not so its output can be
followed.

## What this is not

- **Not investment advice.** The author is not a SEBI-registered investment
  adviser or research analyst. Nothing here is tailored to your financial
  situation, goals, or risk tolerance.
- **Not a prediction.** Words like "APPLY" and "AVOID" are labels for the
  output of a scoring formula. They are not forecasts, and they carry no
  assurance of any outcome.
- **Not a substitute for the prospectus.** Every IPO carries a Red Herring
  Prospectus setting out its risk factors in detail. This tool cannot read it
  for you, and it does not try.

## Known limitations

- **It cannot see most of what matters.** Accounting irregularities, pending
  litigation or regulatory action, management quality, and anything that has
  not yet happened are all invisible to it.
- **Data comes from third parties and from scraping.** Fields go missing,
  sources restructure their pages, and figures can be stale or wrong. The tool
  labels missing data rather than guessing, but it cannot detect a value that
  is present and incorrect.
- **Grey market premium is unofficial.** It is an unregulated, thinly traded,
  easily manipulated quote. It is displayed because it is information, not
  because it is reliable.
- **Backtest results are weak evidence.** The published backtest covers a few
  dozen issues in a single, unusually favourable month, during which applying
  to *everything* would have shown a gain. Some verdict categories have two
  observations. Past results do not predict future ones, and this sample is
  far too small to establish that the method works at all.

## Risk

IPOs regularly list below their issue price, including heavily oversubscribed
ones with large grey market premiums. SME issues are additionally illiquid,
often requiring more than ₹1,00,000 for a single application, and can be
genuinely difficult to exit.

**You can lose money, including a substantial part of what you invest.**

## Your responsibility

Any decision you make is yours alone. If you are unsure, consult a
SEBI-registered investment adviser. The author accepts no liability for any
loss arising from use of this software or its output.
