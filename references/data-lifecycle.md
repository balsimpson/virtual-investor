# Data Lifecycle

Keep Harper's working database small without deleting evidence that an active
decision still depends on.

## Storage roles

- `portfolio.db` is the hot working set and the only SQLite source synced to
  Convex.
- `archive.db` is a local-only warm archive. Never sync it to Convex or load it
  during a normal research briefing.
- Convex stores one current lifecycle-status record so the dashboard can show
  whether cleanup is running.

## Hot-store policy

| Data | Hot limit |
|---|---|
| Raw intel articles | 7 days or 500 rows |
| Verified market-feed observations | 14 days or 200 rows |
| Durable research | 250 total and 20 per ticker; archive off-radar entries after 30 days |
| Quotes | 30 days or 200 per ticker |
| Daily historical prices | 260 per ticker; archive stale off-radar tickers |

An active holding, active or pending thesis, unresolved evidence claim, and the
two benchmark indices remain on the active radar. Keep a raw article hot while
an active thesis or unresolved claim references its URL.

## Archive and purge

Run:

~~~bash
python3 scripts/portfolio.py maintain --dry-run
python3 scripts/portfolio.py maintain --quiet
~~~

The intel sweep runs maintenance automatically. Raw articles, feed
observations, and quotes expire from the archive after 90 days; superseded
research after 180 days; re-fetchable historical prices after 365 days.
Fingerprint tombstones remain for 365 days so an archived recurring headline
does not immediately re-enter the hot store.

Never archive or purge trades, decisions, theses, evidence claims, learning
logs, source-accuracy aggregates, runs, NAV snapshots, candidate evaluations,
candidate forward outcomes, or opportunity audits through routine maintenance.
Candidate snapshots must survive raw-article expiry so false-negative analysis
remains point-in-time auditable. `reset --confirm RESET-HARPER` is the explicit
exception and clears both the hot database and local archive while preserving
feed definitions.
