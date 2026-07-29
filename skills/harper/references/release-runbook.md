# Installation, Upgrade, and Recovery

## Install

1. Install the skill with
   `hermes skills install balsimpson/virtual-investor/skills/harper`, or copy
   `skills/harper` to `~/.hermes/skills/finance/harper` for local development.
2. Do not copy a `.venv`, `.env`, database, global `SOUL.md`, personality
   configuration, or another operator's cron config.
3. Start a new conversation if the installed Hermes version caches skill
   discovery, then type `/harper` in Desktop, CLI/TUI, or a configured gateway.
4. Confirm first use asks only for a preferred name, then market/reporting
   currency, then starting cash with a currency-aware suggested default, then
   confirmation of an IANA user timezone. The user must confirm or replace the
   cash suggestion. Harper must then test live web search and source extraction;
   this capability check is an action, not another user questionnaire.
5. Create scheduled jobs only after research access is verified, the user opts
   in, chooses a destination returned by `hermes send --list --json` or chooses
   local-only delivery, and reviews the schedule and destination together.

`/harper` initializes and reads the local ledger idempotently. Installation and
onboarding do not create, enable, or modify scheduled jobs. Do not assert an
initial cash balance or empty holdings from release defaults; the ready response
must use `profile show`, which reads the current canonical portfolio database.

The engine runs with `python3` and the standard library. Install `pytest` only
when you want to run the development test suite.

## Upgrade an existing installation

Back up the existing skill folder and both SQLite databases before replacing
source files. Preserve the target installation's environment and cron jobs.
Then run:

```bash
python3 scripts/portfolio.py release preflight
python3 scripts/portfolio.py diagnostics config
python3 scripts/portfolio.py diagnostics schedule
python3 scripts/portfolio.py status
python3 scripts/portfolio.py profile show
```

Existing India-only ledgers are migrated with the engine's existing
India/NSE-BSE and INR invariants. Their preferred name remains unset unless it
was already stored in Harper's canonical profile. The migration does not touch
holdings, trades, cash, research, schedules, or global personality settings;
their recorded initial cash is adopted as the confirmed starting amount.

New markets create a local discovery adapter during onboarding. Missing
benchmark, market-specific cost, preferred-source, or regulatory data does not
block installation. Review `market-adapter show`, then let Harper add sourced,
versioned facts progressively using `references/market-adapters.md`.

Automation remains off unless the user explicitly opts in. Before installing
the dispatcher or session jobs, run `market-adapter schedule`, show both market
and user-local times, discover this installation's delivery destinations with
`hermes send --list --json`, persist the user's explicit choice, and obtain
confirmation of the schedule and destination together. A test message is an
external send and requires confirmation. Never change the global Hermes
timezone to accommodate one portfolio.

## Clean start

Use this only after the operator explicitly authorizes a fresh virtual ledger:

```bash
python3 scripts/portfolio.py release clean-start \
  --backup ~/.hermes/data/virtual-investor/pre-reset-portfolio.db \
  --confirm START-HARPER-FRESH
```

Verify the backup:

```bash
python3 scripts/portfolio.py release verify-backup \
  ~/.hermes/data/virtual-investor/pre-reset-portfolio.db
```

Never delete a database to simulate a reset.

## Optional services

- Dashboard sync is disabled until `CONVEX_URL`, `HARPER_SYNC_URL`, and
  `HARPER_SYNC_TOKEN` are configured. Read `references/dashboard-operations.md`
  before deploying the separate companion or enabling its authenticated sync.
- Failed-run recovery discovers jobs named `harper` or beginning with
  `harper-`; legacy `virtual-investor` names remain supported. Set
  `HARPER_JOB_IDS` to a comma-separated list only when using different names.
- Delivery targets and models belong to the target Hermes installation and
  must never be copied from another operator. Persist only a target returned by
  `hermes send --list --json`, or `local`; never persist platform credentials.
- Global markets use `scripts/market_schedule_dispatcher.py` rather than static
  timezone-converted cron expressions. Supply `--trigger` and reviewed session
  job IDs only after the user enables automation.
