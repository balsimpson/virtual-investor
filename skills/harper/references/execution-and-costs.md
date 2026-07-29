# Execution, Costs, and Corporate Actions

## Virtual Fill Contract

The positional price supplied to trade is the observed sourced quote, not the
execution price. The engine:

1. finds the latest quote for the ticker
2. rejects missing, stale, or materially different quote inputs
3. applies adverse slippage by action and liquidity bucket
4. calculates the execution price and modeled fees
5. applies cash, cost-basis, realized-P&L, and cost-ledger effects
6. records quote price, fill price, fees, slippage, and simulation mode

Use whole shares. Never adjust the supplied price to imitate slippage; that
would apply slippage twice.

## Default Cost Model

| Parameter | Default |
|---|---:|
| Fee estimate per leg | 12.5 bps |
| Large-cap slippage per leg | 10 bps |
| Mid-cap slippage per leg | 30 bps |
| Small-cap slippage per leg | 80 bps |
| Maximum quote age for a trade | 15 minutes |
| Maximum difference from latest quote | 25 bps |

These defaults approximate combined friction; they are not a broker contract or
tax statement. Review current STT, exchange charges, SEBI charges, GST, stamp
duty, and brokerage before claiming real-world executability.

Change only strategy parameters through learn params. Do not hardcode local
broker assumptions into cron prompts.

## Slippage Direction

- BUY executes above the observed quote.
- SELL executes below the observed quote.

Classify liquidity from actual turnover, spread, free float, and order-book
quality. Do not assume every NIFTY constituent is always a large-liquidity fill
during shocks.

## Pre-Trade Gates

For any trade:

- require a public quote URL and timezone-aware as-of timestamp
- require the supplied quote to match the latest stored quote
- require positive whole shares and a reason
- require an official same-day market-session confirmation
- require the current IST time to be inside the confirmed session
- require sufficient cash for buys including fees
- require SELL quantity not to exceed the existing positive holding

Before BUY:

- require the complete current thesis contract
- require explicit `INTRADAY` or `POSITION` style
- block a new intraday entry inside the configured pre-close cutoff
- enforce position count and single-position notional
- enforce sector and gross exposure
- enforce per-thesis invalidation loss plus gap buffer
- enforce total portfolio heat
- reject additions while any holding lacks numeric risk data
- recalculate reward/risk and expected return from the latest quote after the
  round-trip cost estimate; reject a setup that has become too expensive

Never let a minimum holding period prevent SELL. Risk-reducing exits
do not need a force override.

## Intraday Reconciliation

An `INTRADAY` position must be sold during the same confirmed trading day. The
close run cannot finish while an intraday holding or active intraday thesis
remains. If the binary outcome is not yet observable, close the position and
move the thesis to `PENDING_RESOLUTION`; do not carry the holding overnight.

## Cost Accounting

Report:

- gross realized P&L from fills and corporate cash flows
- fees deducted from cash and net realized P&L
- slippage embedded in execution prices
- cumulative trading_costs = fees + modeled slippage
- marked unrealized P&L from fresh market quotes
- net NAV after all modeled cash effects

Opening fees are realized expenses. Entry slippage appears immediately through
the difference between fill cost basis and market quote.

## Corporate Actions

Use corporate-action for:

- DIVIDEND with amount per share
- SPLIT with the total-share ratio after the action
- BONUS with the total-share ratio after the action

For a dividend, add cash. For a split or bonus, multiply shares and divide cost
basis by the same ratio.

After a split or bonus, the engine blocks valuation and further trading until a
newly recorded, post-action quote exists. Source every action from an exchange
or issuer filing. Apply an action only on or after its ex-date.

The engine does not yet automate rights issues, tender offers, mergers,
demergers, delistings, or buybacks. Record those as risk events, stop adding
exposure, and handle the accounting explicitly before resuming normal marks.

## Daily Execution Checks

Before the first session:

- verify exchange holiday and session status
- identify price bands, suspensions, surveillance status, and corporate actions
- refresh all held symbols

After every fill:

- inspect status for valuation freshness and portfolio heat
- confirm the journal includes quote, fill, fee, and rationale

At the close:

- refresh marks
- apply known corporate actions
- snapshot NAV and benchmark
- reconcile cash, holdings, net P&L, and total costs
