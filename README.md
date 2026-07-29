# Virtual Investor — Harper

Harper is a long-only virtual portfolio engine and conversational investing
skill for Hermes. It combines sourced market research with deterministic risk
checks, virtual execution, marked-to-market accounting, forecast calibration,
and an auditable SQLite ledger.

Harper can research and simulate intraday or longer-held positions across
markets through versioned market adapters. It never places real orders,
connects to a broker, uses leverage, or opens short positions.

## Highlights

- Evidence-backed `BUY`, `SELL`, and `NO_TRADE` decisions
- Long-only risk controls enforced by the portfolio engine
- Intraday and position-style theses with explicit invalidation rules
- Sourced quotes, market sessions, catalysts, and corporate actions
- Candidate screening, research memory, and forecast scoring
- Market adapters with transparent fallbacks for newly configured markets
- Optional Convex dashboard sync and timezone-aware Hermes automation with a
  user-selected configured messaging destination or local-only reports
- Python standard-library runtime with local SQLite storage

## Requirements

- Python 3
- Hermes Agent with Web Search & Extract configured for full research use
- `pytest` only for running the development test suite

The runtime does not require third-party Python packages. Portfolio data,
credentials, Hermes configuration, schedules, and virtual environments are not
included in this repository.

## Install

Install Harper directly from GitHub with Hermes:

```bash
hermes skills install balsimpson/virtual-investor/skills/harper
```

Start a new Hermes conversation, or run `/reset` in the current conversation,
then invoke `/harper`. Harper will ask for
one onboarding detail at a time, including the virtual starting cash after the
reporting currency is known. It offers a sensible currency-aware default that
the user can confirm or replace, verifies live research access, and initializes
the local ledger idempotently.

Verify or update the installation with:

```bash
hermes skills list
hermes skills check
hermes skills update harper
```

For local development, copy the skill directory instead:

```bash
mkdir -p ~/.hermes/skills/finance/harper
rsync -a skills/harper/ ~/.hermes/skills/finance/harper/
```

To configure research access, run:

```bash
hermes tools
```

Choose **Web Search & Extract** and configure a provider that supports both
capabilities. Harper verifies the callable tools before enabling research or
automation; credentials should never be pasted into chat or committed here.

## First-run checks

From the installed `harper` directory:

```bash
python3 scripts/portfolio.py init
python3 scripts/portfolio.py diagnostics config
python3 scripts/portfolio.py diagnostics schedule
python3 scripts/portfolio.py status
```

By default, Harper stores its canonical ledger at:

```text
~/.hermes/data/virtual-investor/portfolio.db
```

See
[skills/harper/references/runtime-baseline.md](skills/harper/references/runtime-baseline.md)
for path overrides and the complete portable runtime contract.

## Using Harper

Harper is the virtual-investor skill. `/harper` starts or resumes its
conversational portfolio experience.

For direct engine inspection:

```bash
python3 scripts/portfolio.py --help
python3 scripts/portfolio.py profile show
python3 scripts/portfolio.py market-adapter show "India"
python3 scripts/portfolio.py status
```

Automation is optional. Harper only offers it after full research access is
verified, and it requires explicit confirmation before installing or enabling
Hermes jobs. If accepted, Harper discovers the messaging destinations already
configured in Hermes, asks where updates should go, and also supports
local-only reports. The release never bundles another operator's platform or
destination settings.

## Optional web dashboard

Harper works entirely from its local SQLite ledger. For a private web view of
the portfolio, deploy the separate Nuxt dashboard into your own Vercel and
Convex accounts.

[![Deploy Harper Dashboard with Vercel](https://vercel.com/button)](https://vercel.com/clone?repository-url=https%3A%2F%2Fgithub.com%2Fbalsimpson%2Fharper-dashboard&project-name=harper-dashboard&repository-name=harper-dashboard&env=CONVEX_DEPLOY_KEY&envDescription=Add%20a%20production-scoped%20Convex%20deploy%20key%20for%20this%20dashboard.&envLink=https%3A%2F%2Fdocs.convex.dev%2Fproduction%2Fhosting%2Fvercel)

[View the dashboard repository](https://github.com/balsimpson/harper-dashboard)
or follow its
[deployment guide](https://github.com/balsimpson/harper-dashboard/blob/main/DEPLOYMENT.md).
The button uses the manual Convex deploy-key path; the guide also covers the
recommended Vercel Marketplace integration.

The deployment creates a Nuxt site, a Convex backend, and a private sync
endpoint. It does not upload portfolio data. After deploying, run this from the
installed `harper` directory for a safe, credential-free setup checklist:

```bash
python3 scripts/portfolio.py dashboard --guide
```

Harper offers the dashboard once after the automation choice is complete. The
dashboard is never bundled into the skill, never blocks normal use, and is not
a backup of the authoritative SQLite ledger. The first sync requires separate,
explicit approval of the exact production target.

## Safety model

Harper is a simulation, not a brokerage or investment-advice service. Its core
constraints include:

- no real-money execution or broker integration
- no shorts, derivatives, leverage, or margin funding
- no buy without an active, evidence-backed long thesis
- at least two sources, including one primary source, for a trade thesis
- fresh sourced quotes and confirmed exchange sessions before virtual fills
- deterministic limits for thesis risk, portfolio heat, position size, sector
  concentration, gross exposure, and position count
- cash and `NO_TRADE` are valid outcomes when no setup clears every gate

The full operating contract is in [skills/harper/SKILL.md](skills/harper/SKILL.md). Market-specific
mechanics must be sourced through the adapter system rather than assumed.

## Development

Run the test suite with:

```bash
python3 -m pip install pytest
python3 -m pytest -q
```

Run the built-in release readiness check against the configured ledger with:

```bash
python3 skills/harper/scripts/portfolio.py release preflight
```

Before replacing an existing installation, back up its skill folder and both
SQLite databases, then follow
[skills/harper/references/release-runbook.md](skills/harper/references/release-runbook.md). Never delete a
database to simulate a clean start.

## Repository layout

```text
.
├── skills/harper/
│   ├── SKILL.md             Harper policy and workflow
│   ├── scripts/             Portfolio engine and supporting utilities
│   └── references/          Runtime, market, research, and release contracts
└── tests/                   Development test suite
```

## License

[MIT](LICENSE) © 2026 Virtual Investor contributors.
