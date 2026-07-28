# Opportunity Funnel and Rejection Analytics

The candidate ledger measures both search breadth and false negatives without
weakening trade safety. It is an audit surface, not a trade authorization layer.

## Candidate workflow

1. Build a quantitative screen of 40-100 liquid Indian equities.
2. Feed those verified results as JSONL to `scripts/build_candidate_screen.py`.
   The builder validates 40-100 rows and writes
   `/tmp/harper-candidates-<RUN_ID>.json` atomically. Do not invoke `--input`
   before the builder succeeds.
3. Run `candidate screen --input <JSON_FILE> --run-id <RUN_ID>`.
4. Run `candidate rank --run-id <RUN_ID> --top 10`.
5. Deeply research five names and record a new `DEEP` evaluation for each with
   the single-candidate `candidate screen <TICKER>` form below.
6. Mark each deep candidate `APPROVED`, `WATCHLIST`, or `REJECTED`.
7. A rejected candidate must have exactly one binding rejection gate while all
   gate outcomes remain in `gate_outcomes`.
8. Record the portfolio-level action separately with `decision record`.

A candidate evaluation is immutable point-in-time evidence. Record a new row
when research, price, score, status, or gate outcomes materially change.

The funnel counts persisted evaluations, not names mentioned in a report or
journal. `NO_CANDIDATE` means no candidate qualified after screening; it must
not be represented as zero screened rows when reliable screening data existed.
Before finishing an open pulse, run `candidate list --run-id <ID>` and reconcile
the stored rows with the reported screen. If the list is unexpectedly empty,
treat it as an operational failure and repair persistence; do not wait for later
sessions to make the funnel populate automatically.

## Batch input

`candidate screen --input candidates.json --run-id <ID>` accepts a JSON array.
Each object supports:

```json
{
  "ticker": "RELIANCE.NS",
  "thesis_type": "QUALITY",
  "research_depth": "SCREENED",
  "status": "WATCHLIST",
  "preliminary_score": 78.5,
  "quote_price": 1500.0,
  "quote_source": "https://example.com/quote",
  "quote_asof": "2026-07-27T09:20:00+05:30",
  "benchmark_price": 30000.0,
  "benchmark_source": "https://example.com/nifty-tri",
  "benchmark_asof": "2026-07-27T09:20:00+05:30",
  "gate_outcomes": {
    "liquidity": "PASS",
    "financial_quality": "PASS",
    "valuation": "REVIEW"
  },
  "sources": ["https://example.com/primary"],
  "snapshot": {
    "sector": "Energy",
    "trend_20d_pct": 4.2,
    "valuation_percentile": 61
  }
}
```

The snapshot should contain only compact, reproducible features used for the
screen. Do not store article bodies in it.

Build and validate the run-scoped file before importing it. Put exactly one
complete candidate object on each line between the `JSONL` markers; repeat the
row for all 40-100 verified names with their actual values:

```bash
python3 scripts/build_candidate_screen.py --output /tmp/harper-candidates-RUN_ID.json <<'JSONL'
{"ticker":"RELIANCE.NS","thesis_type":"QUALITY","research_depth":"SCREENED","status":"WATCHLIST","preliminary_score":78.5,"quote_price":1500.0,"quote_source":"https://www.nseindia.com/get-quotes/equity?symbol=RELIANCE","quote_asof":"2026-07-28T09:20:00+05:30","gate_outcomes":{"liquidity":"PASS","financial_quality":"PASS","valuation":"REVIEW"},"sources":["https://www.nseindia.com/get-quotes/equity?symbol=RELIANCE"],"snapshot":{"sector":"Energy","trend_20d_pct":4.2,"valuation_percentile":61}}
JSONL
python3 scripts/portfolio.py candidate screen --input /tmp/harper-candidates-RUN_ID.json --run-id RUN_ID
python3 scripts/portfolio.py candidate rank --run-id RUN_ID --top 10
```

If a batch file cannot be constructed safely, record each screened row with
the single-candidate command instead. Do not reduce coverage merely to avoid
building the file.

## Single-candidate and deep evaluations

The deployed CLI has no `candidate evaluate` subcommand. `candidate screen`
also records an individual evaluation without a JSON file:

```bash
python3 scripts/portfolio.py candidate screen TICKER.NS \
  --run-id RUN_ID \
  --score SCORE \
  --thesis-type QUALITY \
  --research-depth SCREENED \
  --status WATCHLIST \
  --quote-price PRICE \
  --quote-source QUOTE_URL \
  --quote-asof ISO_TIMESTAMP \
  --gate-outcomes '{"liquidity":"PASS","evidence":"REVIEW"}' \
  --sources 'https://www.nseindia.com/get-quotes/equity?symbol=TICKER' \
  --snapshot '{"sector":"SECTOR","screen_reason":"REASON"}'
```

After deep research, run the same command with `--research-depth DEEP`, the
current score, quote, gates, sources, and snapshot. Use `--status REJECTED
--binding-rejection-gate GATE` for a rejection, or `--status WATCHLIST` when it
remains unresolved. Supply `--hard-gates`, `--score-components`, and
`--legacy-result` for Phase 2 shadow scoring. Every invocation creates a new
immutable point-in-time evaluation. In this single-candidate CLI form,
`--gate-outcomes`, `--hard-gates`, `--score-components`, and `--snapshot` take
JSON objects, while `--sources` takes one URL or a comma-separated URL list.
The batch-file `sources` field remains a JSON array.

## Forward outcomes

`candidate mark-outcomes --refresh` refreshes rejected-candidate historical
prices, then marks candidates after 5, 10, and 20 trading sessions when enough
prices exist. It stores candidate return,
NIFTY50-TRI return when a benchmark mark is available, and active return.

The close workflow should run the refresh marker before `maintain --quiet`.
Maintenance also retries marking from locally available rows. Missing future
prices remain pending rather than being estimated.

## Rejection report

Run:

```bash
python3 scripts/portfolio.py decision rejection-report --mark-outcomes --refresh
```

The report includes:

- deep-research documentation coverage
- rejection counts by binding gate
- the most common rejection gate
- candidates rejected by only one gate
- 5-, 10-, and 20-session outperformance rates versus NIFTY50-TRI
- the latest low-exposure opportunity audit

Do not infer that a subsequently rising rejected candidate was necessarily a
bad rejection. Review the information and price available at the evaluation
time.

## Low-exposure audit

`candidate opportunity-audit` checks the latest five portfolio snapshots. If
exposure was below 25% in all five, it diagnoses:

- screen breadth below 40
- ranked set below 10
- deep-research set below 5
- one rejection gate dominating more than half of rejections
- a full funnel that still produced no qualifying setup

The audit never buys, relaxes a hard gate, changes parameters, or sets an
exposure target.

## Phase 2 shadow fields

A candidate may include:

- `hard_gates`: all five named gate outcomes
- `score_components`: seven values from 0 to 100
- `legacy_result`: WATCHLIST, REJECTED, or APPROVED

The engine records `weighted_score`, `hard_gate_pass`, model version, and a
`shadow_recommendation`. Run:

```bash
python3 scripts/portfolio.py decision comparison-report
```

Shadow recommendations are audit data and cannot authorize a BUY.
