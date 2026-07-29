# India Market Mechanics

Use this reference for execution constraints. Verify the current NSE/BSE/SEBI
calendar and circulars before relying on any schedule, expiry, settlement, lot
size, price-band, or fee detail: those rules can change.

## Trading day and clock

All times are IST (UTC+5:30).

| Phase | Typical NSE cash-market time | Harper's treatment |
|---|---:|---|
| Pre-open order collection | 09:00-09:08 | Price discovery, not a continuous-market quote |
| Pre-open matching/buffer | 09:08-09:15 | Do not assume a displayed price is executable |
| Normal market | 09:15-15:30 | Trades may be simulated from a fresh sourced quote |
| Closing session | Exchange-announced window after 15:30 | Do not invent an execution without a valid quote |

The 09:00 cron run begins during pre-open. Research and refresh the evidence,
but do not label a pre-open indication as a normal-market fill. The 09:15 pulse
is an open pulse, not a pre-market pulse.

Before any session:

1. Verify that today is an exchange trading day.
2. Check for special sessions, Muhurat trading, shortened sessions, or halts.
3. Record the official result with `market-session confirm`; use the announced
   open and close times for a special session.
4. Check the timestamp, exchange, and source of each quote.
5. Treat a quote as evidence of price, not proof that size was executable.

## Symbols and benchmarks

- Use `.NS` for NSE equities and `.BO` only when BSE is intentionally selected.
- Yahoo's Nifty 50 price-index symbol is `^NSEI`; `NIFTY50.NS` is not the
  correct Yahoo symbol.
- Use **NIFTY50-TRI** as the investment-performance benchmark because it
  includes dividends. Use `^NSEI` only as price-index market context.
- Compare a portfolio return with the same start/end timestamps and currency as
  the benchmark. If a trustworthy TRI mark is unavailable, report active return
  as unavailable rather than substituting the price index.

## Settlement and tradability

- The normal Indian equity settlement cycle is generally T+1, with optional
  faster-settlement facilities applying only where the exchange and broker make
  them available. Verify the current security-level rules.
- Price bands, market-wide circuit breakers, surveillance measures, trading
  halts, and illiquidity can make a printed price non-executable.
- F&O expiry days, eligible securities, lot sizes, and physical/cash settlement
  rules are exchange-defined and change. Never hard-code them into a thesis.
- Harper trades whole shares with available virtual cash. The engine does not
  model margin funding, pledged collateral, leverage, or broker-specific RMS.

## Long-only boundary

Harper does not short. BUY opens or adds to a positive cash-equity holding.
SELL can only reduce that holding. Do not use derivatives, negative quantities,
borrowed shares, leverage, or margin funding as substitutes.

## Corporate actions

Splits, bonuses, dividends, rights issues, demergers, mergers, and delistings can
distort raw price-return calculations.

- Record a dividend, split, or bonus through `corporate-action` with an official
  source and ex-date.
- After a split or bonus, obtain a new post-action quote before valuation or
  another trade.
- For unsupported actions, stop and document the limitation. Do not manually
  force a trade to make the ledger appear correct.

## Transaction costs

Real costs vary by instrument, side, venue, broker, and regulation. They may
include brokerage, STT, exchange charges, SEBI turnover fees, GST, stamp duty,
DP charges, and impact cost.

The engine uses configurable fee and slippage estimates for decision hygiene;
they are not a broker contract note. Review current official schedules before
using the simulation to estimate real-money profitability.

## Market context worth checking

| Signal | Preferred source | What it may explain |
|---|---|---|
| FPI/DII flows | NSE/NSDL/official releases | Institutional flow, with publication lag |
| India VIX | NSE | Option-implied market volatility |
| USD/INR | RBI or reliable market data | Import costs and foreign-flow sensitivity |
| GIFT Nifty | NSE IX/official market data | Overnight index context, not a forecast |
| Crude oil | Reliable commodity venue/data | Import bill and sector margin pressure |
| RBI policy and liquidity | RBI releases | Rates, credit, currency, and bank effects |
| Company disclosures | NSE/BSE issuer filings | Primary catalyst and resolution evidence |

Flows, futures, broker ratings, and headlines are context, not self-validating
signals. A trade still needs company-specific evidence, a priced expectation gap,
a binary event, and a defined loss point.

## Pre-trade India checklist

- Exchange open today and normal session confirmed.
- Ticker/exchange mapping confirmed.
- Fresh timestamped quote from an identified source.
- No halt, band, or corporate action invalidating the quote.
- BUY or SELL only; SELL quantity does not exceed the positive holding.
- INTRADAY positions have enough session time to exit and are closed by 15:20.
- Whole-share size passes all portfolio risk gates.
- Official filing or equivalent Tier-1 primary source supports the catalyst.
- NIFTY50-TRI reserved for performance comparison, not replaced by `^NSEI`.
