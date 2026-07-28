# Operating Schedule and Controlled Release

All times are IST.

| Time | Session label | Trading | Purpose |
|---:|---|---|---|
| 08:55 | `preparation` | No | Calendar, holdings, evidence, risk and opportunity preparation |
| 09:15 | `open-pulse` | No | Market-open reconnaissance and ranked candidates |
| 09:20 | `open-execution` | Yes | First execution review using fresh normal-market quotes |
| 12:30 | `midday-review` | Yes | Holdings, evidence, candidates and portfolio-risk review |
| 15:20 | `final-decisions` | Yes | Close all intraday positions and make final eligible decisions |
| 15:35 | `closing-snapshot` | No | Post-close marks, NAV, benchmark, outcomes and maintenance |
| 20:00 | `intel-postmarket` | No | Classify the day’s staged intelligence |

The scheduler, recovery mapping, run labels and dashboard must use these exact labels. The recovery watchdog maps a failed execution to the latest scheduled main-portfolio session at or before its claim timestamp.

## Weekend split

- Saturday 11:00: deep research, financial analysis and historical context.
- Sunday 11:00: watchlist refresh, counter-theses, data maintenance and Monday preparation.

Neither weekend session trades or files an immediately executable thesis.

## Release controls

1. Deploy the matching Convex schema before syncing new fields.
2. Keep the Phase 2 scoring model in shadow authorization until a reviewed release decision.
3. Attach decision-model, parameter and schedule versions to every new run and decision.
4. Back up SQLite before migration and retain the previous source packages for rollback.
5. Verify preparation, final-decisions and closing-snapshot recovery mappings manually.
6. Review at 30, 60 and 90 days: deployment, active return, drawdown, false negatives, cash drag and process compliance.

No low-exposure condition or underconfidence diagnostic may automatically increase risk.

## Phase 9 release commands

Run `release preflight` before any production migration or scheduler change. A fresh portfolio must use `release clean-start` rather than a bare reset so a verified backup exists first. See `references/release-runbook.md` for the exact deployment and rollback sequence.
