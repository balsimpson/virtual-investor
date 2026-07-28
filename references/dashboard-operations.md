# Optional Dashboard Sync

The skill works without a web dashboard. SQLite at
`~/.hermes/data/virtual-investor/portfolio.db` is always the source of truth.
Leave `CONVEX_URL` unset for a standalone Hermes installation.

## Enable a compatible Convex dashboard

Only enable sync after deploying a Convex project whose public HTTP API accepts
the complete `sync:syncDashboard` payload produced by `scripts/portfolio.py`.
The dashboard is a replaceable read model and is not a backup of SQLite.

Configure the target in the Hermes runtime environment:

```bash
export CONVEX_URL="https://your-deployment.convex.cloud"
```

If the deployed mutation verifies bearer credentials, also configure:

```bash
export CONVEX_AUTH_TOKEN="your-runtime-token"
```

Never commit either value. Confirm the server enforces the intended write
authorization before exposing the endpoint publicly. Then test one explicit
sync:

```bash
python3 scripts/portfolio.py convex-sync
```

State-changing commands attempt a best-effort sync only when Convex is
configured; a sync failure never replaces or rolls back the local ledger.

## Disable sync explicitly

For tests, migrations, or a local-only installation:

```bash
export VIRTUAL_INVESTOR_DISABLE_SYNC=1
```

Do not reconstruct a missing local database from a dashboard. The sync payload
does not contain every strategy parameter, evidence claim, or archive record.
