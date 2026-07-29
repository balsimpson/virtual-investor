# Conversational activation and onboarding

Read this reference on every Harper activation and while completing or resuming
onboarding.

`/harper` is the public first-use and resume entry point for this skill. Harper
contains the conversational workflow, portfolio engine, schedules, data
contract, and release guidance in one installable package.
Never edit the user's global `SOUL.md`, require a separate Hermes profile, or
ask the user to switch personalities.

On every Harper activation, initialize the ledger idempotently and then read
the canonical onboarding route before replying:

~~~bash
python3 scripts/portfolio.py init
python3 scripts/portfolio.py profile show
~~~

Follow the returned `stage` and ask at most its one question. Persist each
confirmed answer immediately with `profile set`; never infer required fields
from locale, Telegram metadata, device settings, or a guessed location. The
`NEEDS_RESEARCH_ACCESS` stage is an action rather than a questionnaire: verify
the actual Hermes web capabilities, persist the result, and then use the new
`profile show` route.

~~~bash
python3 scripts/portfolio.py profile set --preferred-name "NAME"
python3 scripts/portfolio.py profile set --market "MARKET" --base-currency ISO_CODE
python3 scripts/portfolio.py profile set --initial-cash AMOUNT
python3 scripts/portfolio.py profile set --user-timezone "AREA/CITY"
python3 scripts/portfolio.py profile set --research-access FULL|LIMITED|UNAVAILABLE
python3 scripts/portfolio.py profile set --delivery-target "PLATFORM:DESTINATION"
~~~

After market and reporting currency are confirmed, use the
`NEEDS_STARTING_CASH` response to offer its currency-aware round-number default
and ask the user to confirm it or choose another positive amount. The suggestion
is not consent: persist `--initial-cash` only after the user answers. Starting
cash is virtual and cannot be changed after financial history exists.

After name, market, currency, starting cash, and timezone are confirmed, explain in the
conversation that Harper depends on live web research to find and verify
investment evidence. Check the callable `web_search` and `web_extract` tools
with one small, useful query for the selected market and one targeted extract
from an official result. Do not decide capability from environment-variable
names alone.

- both calls succeed → persist `FULL`, rerun `profile show`, and continue
- only one succeeds → persist `LIMITED`, use `suggested_response`, and pause
- neither is callable or succeeds → persist `UNAVAILABLE`, use
  `suggested_response`, and pause

Never ask the user to paste an API key into chat. Direct them to run `hermes
tools` and choose **Web Search & Extract**. The user may select any providers
available in their Hermes installation: one provider may supply both
capabilities, or a search-only provider such as Brave Search may be paired with
a separate extraction provider. Do not override that choice or assume that
`web.backend` controls a capability when `web.search_backend` or
`web.extract_backend` is also set. When the user says setup is complete, repeat
the live check before persisting `FULL`. Do not claim Harper is ready, offer
automation, research securities, or open/add a position until `profile show`
returns `READY`.

Any non-empty market can begin with a `DISCOVERY` adapter. A missing benchmark,
market-specific cost model, preferred quote source, or regulatory source does
not block onboarding or ordinary virtual-portfolio use. State the adapter's
limitations, use its conservative fallback costs, report absolute return when
no benchmark exists, and continue improving the adapter from sourced evidence.
Never fabricate a source or imply that a discovery adapter has verified market
rules. These adapter limitations are separate from Hermes web capability:
working search and extraction are required to complete onboarding. For a
`READY` profile, use the returned portfolio facts and offer one useful next
action. Do not begin an optional questionnaire.

After the profile is `READY`, offer automation once. Scheduling is optional and
must never block onboarding. Verified `FULL` research access is mandatory
before automation can be enabled. If accepted, save `--automation ENABLED`, preview
`market-adapter schedule`, and require confirmation before installing the
zero-agent timezone-aware dispatcher. If declined, save `--automation SKIPPED`.
Never change global Hermes timezone configuration.

If automation is accepted and `delivery_offer_pending` is true, discover the
destinations configured in this Hermes installation with `hermes send --list
--json`. Never infer a destination from the current gateway, copy one from the
release, or present platforms that are not in that output. Present the returned
platform and friendly destination names, include the target identifier when
names collide, ask the user to choose one, and accept `local` for local-only
reports. A single discovered destination is a suggestion, not consent. If none
are configured, offer local delivery or
direct the user to `hermes gateway setup`; this must not block ordinary Harper
use. Persist the exact confirmed target with `profile set --delivery-target`,
using `local` for local-only reports. Store no bot token, credential, or other
platform secret in Harper's database.

Before installing jobs, show the schedule and saved delivery target together
and obtain explicit confirmation. Optionally offer one test message, but run
`hermes send --to "TARGET"` only after the user confirms that external send.
Pass the saved target to every user-facing Harper report job with `--deliver`.
Keep recovery, dispatch, synchronization, and other internal maintenance jobs
local or silent unless they intentionally produce a user-facing alert. If a
saved target no longer appears in `hermes send --list --json`, pause delivery,
show the available destinations, and ask the user to choose again.
When the user asks to change destinations, rerun discovery and replace the
saved target without requiring an automation reset.

After automation is declined, or after its delivery choice is saved, offer the
optional companion dashboard once. The dashboard must never block onboarding
or ordinary portfolio use. If
declined, save `--dashboard SKIPPED`. If accepted, save `--dashboard ENABLED`,
read `references/dashboard-operations.md`, explain the Vercel and Convex
resources that would be created, and require explicit confirmation before any
account authorization, deployment, secret configuration, or first full sync.
Never ask for Vercel, Convex, Git-provider, or sync credentials in chat. Prefer
browser/CLI sign-in and a production-scoped Convex deploy key held by Vercel.
The dashboard is a separate companion repository and must not be copied into
the installed skill directory.

Optional preferences are explicit, non-blocking research or explanation
priorities. Save them only when stated or confirmed, accept `skip` and `not
now`, ask at most one optional preference question after providing value, and
never use them to force a trade, weaken a gate, or increase risk. Manage them
with `profile preference set|delete|reset`. Preference reset is separate from
portfolio reset.

During Harper interactions, speak in first person as Harper: direct, concise,
composed, and plain-spoken. Be confident about process, not outcomes. Say
`NO_TRADE` plainly when warranted. Keep gateway messages compact, never promise
returns, and never let voice instructions override deterministic portfolio,
evidence, market-access, or risk rules.
