# Investment Policy

## Objective

Seek positive cost-adjusted return from a small, virtual Indian-equity
portfolio while preserving capital and producing an auditable decision record.
Use variant perception as a research method, not as a requirement to disagree
with the market. Hold cash whenever evidence or expected value is inadequate.

## Permitted Decisions

- NO_TRADE
- OPEN
- ADD
- REDUCE
- CLOSE
- INVALIDATE

Record every decision with a rationale and at least one public evidence URL.
Judge decision quality separately from the subsequent price outcome.

## Complete Thesis Contract

Before adding risk, record:

| Field | Requirement |
|---|---|
| Direction | LONG only |
| Trade style | INTRADAY or POSITION |
| Confidence | Probability that the binary forecast event occurs |
| Sector | Consistent portfolio exposure category |
| Horizon | Monitoring period |
| Market expectation | Sourced consensus or explicitly labeled hypothesis |
| Variant view | Evidence-backed reason the expectation may be wrong |
| Catalyst | Dated event that can close the expectation gap |
| Forecast event | One observable YES/NO statement |
| Resolution | Date and authoritative URL |
| Entry reference | Sourced market price used for thesis math |
| Target | Price if the thesis works |
| Invalidation | Observable condition and numeric price |
| Counter-thesis | Strongest case for the market being right |
| Financial summary | Cash quality, balance sheet, earnings quality, valuation |
| Evidence | Two or more URLs, including at least one primary source |

Do not call an unsourced assertion “consensus.” Do not combine multiple events
inside one probability. Re-file the thesis if its event or resolution rule
changes; never rewrite a forecast after evidence arrives.

## Expected-Value Gate

- reward = target - entry
- risk = entry - invalidation

Estimate the round-trip fee and adverse-slippage cost. Then require
directionally valid prices, net reward/risk of at least 1.5, and:

expected move = confidence × reward percent − (1 − confidence) × risk percent − costs

Require expected move to be positive after modeled costs. Recalculate both
gates from the latest quote immediately before adding risk; a good thesis can
become a bad trade after the price moves.

## Risk Sizing

Calculate:

risk per share = absolute(latest quote − invalidation) + gap buffer

maximum shares = floor(NAV × risk-per-thesis limit / risk per share)

Apply every additional engine gate:

- default per-thesis loss budget: 1% of NAV
- default total portfolio heat: 5% of NAV
- default long notional cap: 20% of NAV
- default sector cap: 30% of NAV
- default gross exposure cap: 100% of NAV
- default open-position count: 8

Treat these as conservative defaults, not promises of safety. Existing database
values remain authoritative until explicitly changed with learn params.

## Trade Styles

Use `INTRADAY` only when the entire position can be bought and sold during the
same confirmed exchange session. Stop new entries at least 30 minutes before
the confirmed close. Exit by 15:20 IST during a normal session and resolve or
pend the forecast before finishing the close run.

Use `POSITION` for a long intended to remain open across sessions. Continue to
refresh its quote, catalyst, invalidation, corporate actions, and thesis risk
during every scheduled review. Do not convert an intraday loss into a position
trade to avoid realizing it; file a new thesis only after the intraday cycle is
closed and reconciled.

## Exit Policy

Reduce or exit immediately when:

- the observable invalidation condition is met
- evidence supporting the thesis is shown to be inaccurate
- quote freshness or tradability prevents reliable valuation
- portfolio heat exceeds its limit because of gaps or correlation
- the catalyst resolves and remaining expected value no longer clears costs
- a corporate action materially changes the thesis

Never block a risk-reducing trade with a minimum holding period. Close the
position before resolving the thesis.

## Long-Only Boundary

The permitted order actions are BUY and SELL. SELL may only reduce an existing
positive holding. Do not create synthetic negative positions, derivative
exposure, leveraged positions, or margin-funded trades.

## Rejection Discipline

Research coverage is a process target, not an exposure target. During the open
pulse, screen 40-100 liquid names across multiple sectors when reliable data
permits, rank the top 10, and deeply research five. Persist the point-in-time
candidate score, quote, source set, gate outcomes, and snapshot before the raw
research material can expire. A weak broad market is context, not an automatic
rejection of every long; continue looking for company-specific catalysts,
relative strength, defensive exposure, or direct beneficiaries of the observed
regime.

Record NO_TRADE when:

- the event cannot be resolved objectively
- no primary evidence supports the material claim
- consensus is invented or stale
- the reward/risk or expected-value gate fails
- invalidation is narrative rather than numeric and observable
- position, sector, heat, liquidity, quote, or cost gates fail
- the trade only exists to reduce cash

For each deeply researched candidate that does not qualify, record exactly one
binding rejection reason while preserving all gate outcomes. Do not weaken
evidence, expected-value, or risk gates merely to satisfy the research-coverage
target.

Mark rejected candidates after 5, 10, and 20 trading sessions when data becomes
available. Compare the candidate return with NIFTY50-TRI over the same horizon,
report candidates rejected by only one gate, and measure whether rejection
quality was good without penalizing inactivity.

If exposure remains below 25% for five portfolio snapshots, run an opportunity
audit. Diagnose screen breadth, ranking depth, deep-research coverage, and gate
concentration. The audit must not place a trade or relax a hard control.

## Phase 2 Shadow Decision Model

Research ranking uses five hard gates. A failed hard gate always produces a
shadow rejection regardless of score:

1. `QUOTE_TRADABILITY`
2. `FINANCIAL_INTEGRITY`
3. `SIZING_VALIDITY`
4. `PORTFOLIO_RISK`
5. `AUTHORITATIVE_EVIDENCE`

When all five pass, score catalyst clarity, financial quality, valuation,
trend, source quality, reward/risk, and portfolio fit using scoring model
`2.0-shadow`. The initial shadow threshold is 70/100. The score is diagnostic
only until at least 20 sessions are reviewed; deterministic trade gates remain
final.

Event confidence and investment-payoff probability answer different questions.
Store event confidence for forecast calibration. Use
`investment_success_probability` or explicit bear/base/bull scenario
probabilities for expected return. Never calculate trade EV from the event
probability merely because both events appear related.

## Thesis Types

- **CATALYST:** requires one observable binary event, resolution date, and authoritative source.
- **QUALITY:** requires a dated review and measurable financial-quality trajectory.
- **VALUE:** requires a valuation gap, dated review, and explicit rerating conditions.
- **MOMENTUM:** requires a trend-persistence condition, dated review, and technical invalidation.

Only catalyst theses receive Brier scoring. All thesis types retain numeric price invalidation, positive cost-adjusted investment EV, source requirements, and portfolio risk controls.

## Starter Positions

An initial starter may be no more than 3% of NAV by default. A starter is not an exemption from hard gates. Adding to a starter requires a new public confirmation source and all current risk gates to pass.

## Exposure Regimes

Use diagnostic exposure bands: DEFENSIVE 25–50%, NORMAL 50–75%, and STRONG_OPPORTUNITY 70–90%. Falling below a band triggers an opportunity audit rather than an automatic trade. Record an explicit cash reason with each NO_TRADE decision.
