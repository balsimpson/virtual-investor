# Portable Runtime Baseline

The portfolio engine uses the Python standard library at runtime. It does not
ship a virtual environment, database, credentials, dashboard, broker
integration, or Hermes cron configuration. Harper's research workflow requires
Hermes Web Search & Extract, configured separately from the skill.

## Paths

| Purpose | Default | Override |
|---|---|---|
| Hermes home | `~/.hermes` | `HERMES_HOME` |
| Portfolio ledger | `~/.hermes/data/virtual-investor/portfolio.db` | `VIRTUAL_INVESTOR_DB` |
| Archive ledger | `~/.hermes/data/virtual-investor/archive.db` | `VIRTUAL_INVESTOR_ARCHIVE_DB` |
| Hermes accounting | `~/.hermes/state.db` | `VIRTUAL_INVESTOR_HERMES_STATE_DB` |
| Hermes jobs | `~/.hermes/cron/jobs.json` | `VIRTUAL_INVESTOR_HERMES_CRON_JOBS` |

## First run

Install the folder at `~/.hermes/skills/finance/virtual-investor`, then run:

```bash
cd ~/.hermes/skills/finance/virtual-investor
python3 scripts/portfolio.py init
python3 scripts/portfolio.py diagnostics config
python3 scripts/portfolio.py diagnostics schedule
python3 scripts/portfolio.py status
```

## Required web research setup

Harper may initialize its ledger before web setup, but onboarding is not
complete and automated sessions remain unavailable until both `web_search` and
`web_extract` succeed. Configure them through the Hermes wizard:

```bash
hermes tools
```

Open **Web Search & Extract** and select one full provider. Tavily and
Firecrawl each provide both search and extraction, so users do not need both.
Nous Portal may provide managed Firecrawl without a separate API key. DDGS and
SearXNG are search-only and therefore do not complete Harper onboarding unless
they are paired with a working extraction provider.

For a deliberate split configuration, keep credentials in `~/.hermes/.env`
and select capabilities in `~/.hermes/config.yaml`:

```yaml
web:
  search_backend: tavily
  extract_backend: firecrawl
```

Never store provider credentials in the skill, Harper's SQLite ledger, a cron
prompt, or chat. Determine readiness by exercising the actual tools rather
than checking only for `TAVILY_API_KEY` or `FIRECRAWL_API_KEY`; Hermes may use a
managed gateway, another supported provider, or a self-hosted backend.

The onboarding agent must persist the observed result:

```bash
python3 scripts/portfolio.py profile set --research-access FULL
python3 scripts/portfolio.py profile set --research-access LIMITED
python3 scripts/portfolio.py profile set --research-access UNAVAILABLE
```

Use `FULL` only when a useful search and a targeted official-page extraction
both succeed. A missing or failing provider preserves the portfolio but blocks
research, BUY/ADD decisions, and automation until the user configures web
access and the agent verifies it again.

Install `entrypoints/harper` beside it at
`~/.hermes/skills/finance/harper`. The public first-use path is `/harper`; its
companion skill initializes the canonical engine idempotently and routes the
conversation from `profile show`. It does not alter global personality or
create scheduled jobs.

Initialization creates the local ledger, but the user chooses its virtual
starting cash during onboarding after confirming the reporting currency.
Harper offers a round currency-aware default, such as 100,000 INR or 10,000
USD, which must be explicitly confirmed or replaced. It never connects to a
broker or places real orders.

## Default safety parameters

| Parameter | Default |
|---|---:|
| Initial virtual cash | User-selected; Harper offers a currency-aware suggestion |
| Risk per thesis | 1% of NAV |
| Maximum portfolio heat | 5% of NAV |
| Maximum position weight | 20% of NAV |
| Maximum sector weight | 30% of NAV |
| Maximum gross exposure | 100% of NAV |
| Maximum open positions | 8 |
| Maximum quote age | 15 minutes |
| Maximum quote mismatch | 25 bps |
| Fee estimate per leg | 12.5 bps |

For the India adapter, use `references/cron-prompt.md`,
`references/morning-pulse-prompt.md`, and
`references/weekend-prompt.md` to create jobs with the local Hermes CLI or UI.
Delivery destinations, model names, and absolute workdirs must come from the
target installation. Other markets must use their adapter-defined sessions and
the timezone-aware dispatcher described in `references/market-adapters.md`.
