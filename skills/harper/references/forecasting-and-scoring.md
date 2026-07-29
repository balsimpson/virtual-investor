# Forecasting and Scoring

## Separate Three Questions

Track independently:

1. Did the predeclared event occur?
2. Did the investment make money after costs?
3. Was the decision process compliant with evidence and risk rules?

Never infer one answer from another.

Segment process and outcome review by `INTRADAY` versus `POSITION`. Do not infer
that a rule learned from same-day execution applies to multi-session holdings,
or vice versa, without enough observations in both groups.

## Forecast Contract

Before a trade, lock:

- one binary event
- confidence from 1 to 99
- resolution date
- authoritative resolution URL
- exact YES and NO criteria

Make the event observable without interpreting the price outcome. Avoid compound
events such as “earnings beat and the price rises.” Split those into separate
forecasts if both matter.

At resolution use:

- YES: the defined event occurred
- NO: the defined event did not occur
- UNRESOLVED: authoritative evidence is unavailable or the event became
  impossible to judge under the original rule

UNRESOLVED events do not receive a Brier component. Their theses move to
PENDING_RESOLUTION, cannot authorize new risk, and must later be resolved YES
or NO when the declared source makes the outcome observable.

## Brier Score

For each resolved event:

Brier component = (confidence decimal − observed outcome) squared

Use observed outcome 1 for YES and 0 for NO. Average components across resolved
events. Lower is better.

Do not map WIN to 1 or LOSS/FLAT to 0. Do not ask the agent whether it “was
calibrated”; calculate the score from the locked probability and event result.

Do not apply universal labels such as “excellent” to raw Brier values without a
base rate. Event difficulty and prevalence matter.

## Brier Skill

Compare against a base-rate forecast:

Brier skill = 1 − model Brier / base-rate Brier

Only show this after at least five resolved forecasts and when the base-rate
Brier is non-zero. Treat five as display eligibility, not statistical
significance.

## Calibration Review

Show:

- resolved forecast count
- average confidence
- observed event rate
- event-rate minus confidence drift
- confidence buckets with count, average confidence, and event rate
- Brier score and eligible Brier skill

Do not draw strategic conclusions from tiny buckets.

Lock automated adaptation until at least 30 resolved forecasts. Prefer larger
samples and confidence intervals. After the minimum:

- sustained overconfidence may justify probability recalibration
- sustained underconfidence triggers review, not larger position limits
- position size continues to come from invalidation risk and portfolio heat

## Trade Outcome

Record separately:

- WIN: positive thesis-level net outcome under the documented return rule
- LOSS: negative thesis-level net outcome
- FLAT: outcome within the documented flat band or economically immaterial

Use net results after modeled costs. Record why the trade ended:

- invalidation_triggered
- catalyst_played_out
- risk_exit
- other

Record timing as early, on_time, or late relative to the original event window.
Do not target a percentage of exits that must be invalidation-triggered. Measure
whether a triggered invalidation was acted on promptly.

## Resolution Workflow

1. Exit the open position.
2. Open the authoritative resolution source.
3. Compare the original event wording with the observation.
4. Record YES, NO, or UNRESOLVED without rewriting the event. If unresolved,
   revisit the pending thesis rather than silently dropping it.
5. Record trade outcome and process lesson separately.
6. Resolve supporting evidence claims whose accuracy is now observable.
7. Run review and inspect sample size before interpreting calibration.

## Review Fields

| Field | Meaning |
|---|---|
| return_pct | Net portfolio return |
| benchmark_return_pct | NIFTY 50 TRI return |
| active_return_pct | Portfolio return minus benchmark return |
| alpha_pct | Null until risk-adjusted alpha is implemented |
| max_drawdown_pct | Worst observed peak-to-trough snapshot decline |
| trading_costs | Modeled fees plus slippage |
| win_rate_pct | Return outcomes, excluding FLAT |
| brier_score | Binary event forecast accuracy |
| brier_skill_score | Eligible base-rate-relative forecast skill |
| portfolio_heat_pct | Loss at all active invalidations plus gap buffers |

Snapshot frequently enough to make drawdown meaningful. Sparse snapshots can
understate real peak-to-trough loss.


## Combined Learning Report

Run `python3 scripts/portfolio.py learn report` to review portfolio results,
forecast calibration, thesis-type/trade-style segments, rejected-candidate
forward outcomes, cash opportunity-cost proxy, process-version coverage, and
intel queue/source health in one deterministic report.

Treat the cash opportunity-cost figure as a diagnostic proxy, not proof that
every uninvested rupee should have tracked the benchmark. Separate deliberate
defensive cash from missed-opportunity cash using recorded cash-reason codes.
Do not unlock automated parameter adaptation before the configured resolved-
forecast minimum.
