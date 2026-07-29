# Optional Companion Dashboard

The skill works without a web dashboard. SQLite at
`~/.hermes/data/virtual-investor/portfolio.db` is always the source of truth.
Dashboard setup must never block onboarding, research, or virtual portfolio use.

The official companion is maintained separately at:

```text
https://github.com/balsimpson/harper-dashboard
```

Do not copy the Nuxt application into the installed Hermes skill. It has its
own dependencies, cloud resources, release cycle, and credential boundary.

## Onboarding boundary

Offer the dashboard once, after the automation choice is saved. If the user
declines, persist:

```bash
python3 scripts/portfolio.py profile set --dashboard SKIPPED
```

If the user accepts, persist `ENABLED`, explain the exact resources that will
be created, and obtain explicit confirmation before each external change:

```bash
python3 scripts/portfolio.py profile set --dashboard ENABLED
```

Dashboard acceptance is not authorization to deploy, configure credentials,
or upload data. Prefer the companion repository's Vercel Deploy Button or the
native Convex Vercel integration. Use browser or CLI sign-in; never ask the
user to paste Vercel, Convex, or Git-provider credentials into chat.

## Runtime configuration

The deployed dashboard exposes an authenticated Convex HTTP Action. Configure
the production target in the Hermes runtime environment:

```dotenv
CONVEX_URL=https://your-deployment.convex.cloud
HARPER_SYNC_URL=https://your-deployment.convex.site/harper-sync
HARPER_SYNC_TOKEN=your-unique-random-token
```

These are examples of the required shapes. Use the URLs from the user's own
production Convex deployment. `HARPER_SYNC_TOKEN` must be a unique random value
of at least 32 characters and must match the secret stored in that Convex
deployment. Never commit it, store it in the SQLite ledger, or print it in
status output.

`CONVEX_DEPLOY_KEY` belongs in Vercel only. It must be scoped to the user's
dashboard deployment and must never be copied into Hermes.

Show the human-readable setup checklist without exposing the token:

```bash
python3 scripts/portfolio.py dashboard --guide
```

For machine-readable connection status, omit `--guide` to receive JSON.

## First sync

The companion dashboard must support dashboard contract version 2. The payload
includes the user display time zone, reporting currency, market-adapter
metadata, configured benchmark, cost mode, canonical valuation freshness, and
cash/holdings components for each NAV snapshot. SQLite-calculated values remain
authoritative; the dashboard must not reprice positions or substitute cost basis
when a current valuation is unavailable.

The sync endpoint authenticates the bearer token and invokes an internal Convex
mutation; there is no public replacement mutation.

Before the first sync, name the exact Convex production deployment and explain
that the operation replaces the dashboard read model. Obtain explicit approval,
then run:

```bash
python3 scripts/portfolio.py convex-sync
```

State-changing commands attempt best-effort sync only after all three runtime
values are configured. A sync failure never replaces or rolls back SQLite.

## Disable sync

Remove `CONVEX_URL`, `HARPER_SYNC_URL`, and `HARPER_SYNC_TOKEN` from the Hermes
runtime environment, or set this for an isolated command:

```bash
export VIRTUAL_INVESTOR_DISABLE_SYNC=1
```

Do not reconstruct a missing local database from the dashboard. The sync
payload is intentionally incomplete and the Convex data is replaceable.
