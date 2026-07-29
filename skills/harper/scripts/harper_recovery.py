#!/usr/bin/env python3
"""Queue one safe retry for Harper after a failed or interrupted cron run."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

INDIA_TZ = timezone(timedelta(hours=5, minutes=30))
HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
HERMES_AGENT_ROOT = HERMES_HOME / "hermes-agent"
PORTFOLIO_DB = Path(
    os.environ.get(
        "VIRTUAL_INVESTOR_DB",
        HERMES_HOME / "data" / "virtual-investor" / "portfolio.db",
    )
)
EXECUTIONS_DB = Path(
    os.environ.get(
        "HARPER_EXECUTIONS_DB",
        HERMES_HOME / "cron" / "executions.db",
    )
)
STATE_FILE = Path(
    os.environ.get(
        "HARPER_RECOVERY_STATE",
        HERMES_HOME / "data" / "virtual-investor" / "recovery-state.json",
    )
)
HARPER_JOB_IDS = tuple(
    job_id.strip()
    for job_id in os.environ.get("HARPER_JOB_IDS", "").split(",")
    if job_id.strip()
)
MAX_RETRIES_PER_SESSION = 2
RECOVERY_LOOKBACK_HOURS = 18
SESSION_SCHEDULE = (
    ((8, 55), "preparation"),
    ((9, 20), "open-execution"),
    ((12, 30), "midday-review"),
    ((15, 20), "final-decisions"),
    ((15, 35), "closing-snapshot"),
)


@dataclass(frozen=True)
class RecoveryCandidate:
    job_id: str
    execution_id: str
    execution_status: str
    claimed_at: str
    market_date: str
    session: str
    run_id: int | None
    run_status: str | None

    @property
    def retry_key(self) -> str:
        if self.run_id is not None:
            return f"run:{self.run_id}"
        return f"session:{self.market_date}:{self.session}"


def _load_market_contract() -> tuple[timezone | ZoneInfo, tuple[tuple[tuple[int, int], str], ...]]:
    """Read the active adapter without making recovery depend on migration state."""
    if not PORTFOLIO_DB.exists():
        return INDIA_TZ, SESSION_SCHEDULE
    try:
        with _connect_query_only(PORTFOLIO_DB) as conn:
            profile = conn.execute(
                "SELECT market, user_timezone FROM investor_profile WHERE id=1"
            ).fetchone()
            adapter = conn.execute(
                """SELECT market_timezone, session_schedule_json
                   FROM market_adapters WHERE market_id=?""",
                (profile["market"],),
            ).fetchone() if profile and profile["market"] else None
    except sqlite3.Error:
        return INDIA_TZ, SESSION_SCHEDULE
    timezone_name = (
        adapter["market_timezone"] if adapter and adapter["market_timezone"]
        else profile["user_timezone"] if profile and profile["user_timezone"]
        else None
    )
    market_timezone = ZoneInfo(str(timezone_name)) if timezone_name else INDIA_TZ
    try:
        sessions = json.loads(adapter["session_schedule_json"] or "{}").get("sessions", [])
        parsed = tuple(
            (
                tuple(int(part) for part in str(item["time"]).split(":")),
                str(item["label"]),
            )
            for item in sessions
            if item.get("label") != "open-pulse"
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        parsed = ()
    return market_timezone, parsed or SESSION_SCHEDULE


def _parse_timestamp(value: str, market_timezone=INDIA_TZ) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(market_timezone)


def _session_for_claim(
    claimed_at: datetime,
    session_schedule: tuple[tuple[tuple[int, int], str], ...] = SESSION_SCHEDULE,
) -> str | None:
    minute_of_day = claimed_at.hour * 60 + claimed_at.minute
    eligible = [
        (hour * 60 + minute, label)
        for (hour, minute), label in session_schedule
        if hour * 60 + minute <= minute_of_day
    ]
    if not eligible or claimed_at.hour > 18:
        return None
    return max(eligible, key=lambda item: item[0])[1]


def _connect_query_only(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _discover_harper_job_ids() -> tuple[str, ...]:
    """Return explicitly configured IDs or discover public-skill job names."""
    if HARPER_JOB_IDS:
        return HARPER_JOB_IDS
    jobs_file = HERMES_HOME / "cron" / "jobs.json"
    try:
        payload = json.loads(jobs_file.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return ()
    jobs = payload.get("jobs", []) if isinstance(payload, dict) else payload
    if not isinstance(jobs, list):
        return ()
    return tuple(
        str(job["id"])
        for job in jobs
        if isinstance(job, dict)
        and job.get("id")
        and (
            job.get("name") in {"harper", "virtual-investor"}
            or str(job.get("name", "")).startswith(("harper-", "virtual-investor-"))
        )
    )


def _load_hermes_cron_modules():
    root = str(HERMES_AGENT_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    from cron.executions import recover_interrupted_executions
    from cron.jobs import trigger_job, update_job

    return recover_interrupted_executions, trigger_job, update_job


def reconcile_abandoned_executions() -> None:
    recover_interrupted, _, _ = _load_hermes_cron_modules()
    recover_interrupted()


def trigger_harper_job(job_id: str) -> bool:
    """Clear a stale direct-fire claim and make Harper due on the next tick."""
    _, trigger_job, update_job = _load_hermes_cron_modules()
    update_job(job_id, {"fire_claim": None})
    return trigger_job(job_id) is not None


def find_recovery_candidate(now_ist: datetime | None = None) -> RecoveryCandidate | None:
    """Return the newest retryable Harper execution, if it is safe to resume."""
    if not PORTFOLIO_DB.exists() or not EXECUTIONS_DB.exists():
        return None
    job_ids = _discover_harper_job_ids()
    if not job_ids:
        return None
    market_timezone, session_schedule = _load_market_contract()
    now_ist = (now_ist or datetime.now(market_timezone)).astimezone(market_timezone)

    with _connect_query_only(EXECUTIONS_DB) as conn:
        placeholders = ",".join("?" for _ in job_ids)
        latest = conn.execute(
            f"""SELECT id, job_id, status, claimed_at
               FROM executions
               WHERE job_id IN ({placeholders})
               ORDER BY claimed_at DESC, id DESC
               LIMIT 1""",
            job_ids,
        ).fetchone()
    if not latest or latest["status"] not in {"failed", "unknown"}:
        return None

    claimed_at = _parse_timestamp(latest["claimed_at"], market_timezone)
    age = now_ist - claimed_at
    if age < timedelta(0) or age > timedelta(hours=RECOVERY_LOOKBACK_HOURS):
        return None
    session = _session_for_claim(claimed_at, session_schedule)
    if session is None:
        return None

    market_date = claimed_at.date().isoformat()
    with _connect_query_only(PORTFOLIO_DB) as conn:
        run = conn.execute(
            """SELECT id, status
               FROM runs
               WHERE market_date=? AND session_label=?
               ORDER BY id DESC
               LIMIT 1""",
            (market_date, session),
        ).fetchone()
    if run and run["status"] == "COMPLETED":
        return None

    return RecoveryCandidate(
        job_id=latest["job_id"],
        execution_id=latest["id"],
        execution_status=latest["status"],
        claimed_at=latest["claimed_at"],
        market_date=market_date,
        session=session,
        run_id=int(run["id"]) if run else None,
        run_status=run["status"] if run else None,
    )


def _load_state() -> dict:
    try:
        data = json.loads(STATE_FILE.read_text())
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    temporary.replace(STATE_FILE)


def queue_recovery(candidate: RecoveryCandidate) -> bool:
    """Queue Harper for the next scheduler tick, at most twice per session."""
    state = _load_state()
    attempted = set(state.get("attempted_execution_ids", []))
    retry_counts = dict(state.get("retry_counts", {}))
    if candidate.execution_id in attempted:
        return False
    retry_count = int(retry_counts.get(candidate.retry_key, 0))
    if retry_count >= MAX_RETRIES_PER_SESSION:
        return False

    # Record intent before invoking Hermes. `hermes cron run` executes the job
    # synchronously; if the gateway dies during that call, its side effects are
    # uncertain and the same execution must not be queued again blindly.
    attempted.add(candidate.execution_id)
    retry_counts[candidate.retry_key] = retry_count + 1
    state.update({
        "attempted_execution_ids": sorted(attempted)[-50:],
        "retry_counts": retry_counts,
        "last_queued_at": datetime.now(timezone.utc).isoformat(),
        "last_candidate": asdict(candidate),
    })
    _save_state(state)

    if not trigger_harper_job(candidate.job_id):
        raise RuntimeError("could not mark the Harper job due")

    state["last_recovery_finished_at"] = datetime.now(timezone.utc).isoformat()
    _save_state(state)
    return True


def main() -> int:
    try:
        reconcile_abandoned_executions()
    except (ImportError, OSError, sqlite3.Error) as exc:
        print(f"harper-recovery: ERROR: could not reconcile executions: {exc}", file=sys.stderr)
        return 1
    candidate = find_recovery_candidate()
    if candidate is None:
        return 0
    try:
        queued = queue_recovery(candidate)
    except (ImportError, OSError, RuntimeError, sqlite3.Error) as exc:
        print(f"harper-recovery: ERROR: {exc}", file=sys.stderr)
        return 1
    if queued:
        print(
            "harper-recovery: queued "
            f"{candidate.market_date} {candidate.session} "
            f"after {candidate.execution_status} execution {candidate.execution_id}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
