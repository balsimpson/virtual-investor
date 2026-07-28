# CLI setup, commands, and validation

Read this reference before first-run setup, invoking an unfamiliar portfolio
command, resetting or syncing state, or validating the skill.

## First Run

Resolve the target Hermes home instead of reusing another operator's paths or
IDs. From the installed skill directory, run:

~~~bash
python3 scripts/portfolio.py init
python3 scripts/portfolio.py diagnostics config
python3 scripts/portfolio.py diagnostics schedule
python3 scripts/portfolio.py status
~~~

The runtime uses only the Python standard library. Do not copy a virtual
environment, database, delivery target, model name, or cron job ID from another
installation. Read `references/runtime-baseline.md` for path overrides and
`references/release-runbook.md` before replacing an existing installation.

## Core Commands

Run commands from the skill directory with PYTHONPATH empty:

~~~bash
python3 scripts/portfolio.py init
python3 scripts/portfolio.py profile show
python3 scripts/portfolio.py profile set --preferred-name "NAME"
python3 scripts/portfolio.py profile set --market "MARKET" --base-currency ISO_CODE
python3 scripts/portfolio.py profile set --initial-cash AMOUNT
python3 scripts/portfolio.py profile set --user-timezone "AREA/CITY"
python3 scripts/portfolio.py market-adapter show "MARKET"
python3 scripts/portfolio.py market-adapter schedule
python3 scripts/portfolio.py market-session confirm DATE --status OPEN --source OFFICIAL_URL
python3 scripts/portfolio.py status
python3 scripts/portfolio.py learn briefing
python3 scripts/portfolio.py learn report
python3 scripts/portfolio.py learn params
python3 scripts/portfolio.py learn historical analyze TICKER.NS
python3 scripts/portfolio.py candidate screen --input candidates.json
python3 scripts/portfolio.py candidate rank --top 10
python3 scripts/portfolio.py decision rejection-report --mark-outcomes --refresh
python3 scripts/portfolio.py snapshot
python3 scripts/portfolio.py review
python3 scripts/portfolio.py usage
python3 scripts/portfolio.py maintain --dry-run
python3 scripts/portfolio.py intel-sources quality
~~~

The local ledger does not require a dashboard. Run `dashboard` to inspect the
optional connection. Run `convex-sync` only after
deploying and configuring the compatible endpoint described in
`references/dashboard-operations.md`.

Use `reset --confirm RESET-HARPER` only for an explicitly authorized fresh
start. It clears portfolio and learning history while retaining feed URLs as
operational configuration. Sync immediately afterward to replace cloud data.

Use decision record NO_TRADE for a sourced rejection decision. Use
evidence add and evidence resolve to score source claims by subsequent
accuracy rather than by portfolio return. Use corporate-action for dividends,
splits, and bonuses; refresh the quote after a share-count adjustment.

Use candidate screen to preserve every point-in-time screen and deep-research
decision. Rejected candidates require one binding rejection gate. The close
maintenance run marks available 5-, 10-, and 20-session forward outcomes and
checks whether exposure stayed below 25% for five snapshots. That audit diagnoses
the opportunity funnel and never authorizes a purchase.

Read references/execution-and-costs.md before filing or executing trades.

## Validation

Run offline engine tests without dashboard sync:

~~~bash
VIRTUAL_INVESTOR_DISABLE_SYNC=1 PYTHONPATH="" python3 -m pytest tests/ -q
~~~

Do not use the live portfolio database for tests. Do not run live research,
cron delivery, or Convex mutation checks as part of offline validation.
