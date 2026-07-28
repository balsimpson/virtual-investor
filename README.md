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
- Optional Convex dashboard sync and timezone-aware Hermes automation
- Python standard-library runtime with local SQLite storage

## Requirements

- Python 3
- Hermes Agent with Web Search & Extract configured for full research use
- `pytest` only for running the development test suite

The runtime does not require third-party Python packages. Portfolio data,
credentials, Hermes configuration, schedules, and virtual environments are not
included in this repository.

## Install

Install the canonical skill and its `/harper` conversational entry point under
the active Hermes home:

```bash
mkdir -p ~/.hermes/skills/finance/virtual-investor
mkdir -p ~/.hermes/skills/finance/harper
rsync -a --exclude '.git' ./ ~/.hermes/skills/finance/virtual-investor/
rsync -a entrypoints/harper/ ~/.hermes/skills/finance/harper/
```

Then start a new Hermes conversation and invoke `/harper`. Harper will ask for
one onboarding detail at a time, verify live research access, and initialize
the local ledger idempotently.

To configure research access, run:

```bash
hermes tools
```

Choose **Web Search & Extract** and configure a provider that supports both
capabilities. Harper verifies the callable tools before enabling research or
automation; credentials should never be pasted into chat or committed here.

## First-run checks

From the installed `virtual-investor` directory:

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

See [references/runtime-baseline.md](references/runtime-baseline.md) for path
overrides and the complete portable runtime contract.

## Using Harper

`/harper` is the public conversational entry point. The `virtual-investor`
skill remains the canonical engine and policy definition.

For direct engine inspection:

```bash
python3 scripts/portfolio.py --help
python3 scripts/portfolio.py profile show
python3 scripts/portfolio.py market-adapter show "India"
python3 scripts/portfolio.py status
```

Automation is optional. Harper only offers it after full research access is
verified, and it requires explicit confirmation before installing or enabling
Hermes jobs.

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

The full operating contract is in [SKILL.md](SKILL.md). Market-specific
mechanics must be sourced through the adapter system rather than assumed.

## Development

Run the test suite with:

```bash
python3 -m pip install pytest
python3 -m pytest -q
```

Run the built-in release readiness check against the configured ledger with:

```bash
python3 scripts/portfolio.py release preflight
```

Before replacing an existing installation, back up its skill folder and both
SQLite databases, then follow
[references/release-runbook.md](references/release-runbook.md). Never delete a
database to simulate a clean start.

## Repository layout

```text
.
├── SKILL.md                 Canonical Harper policy and workflow
├── entrypoints/harper/      Public /harper conversational entry point
├── scripts/                 Portfolio engine and supporting utilities
├── references/              Runtime, market, research, and release contracts
├── tests/                   Development test suite
└── agents/openai.yaml       Skill interface metadata
```

## License

[MIT](LICENSE) © 2026 Virtual Investor contributors.
