#!/usr/bin/env python3
"""Dispatch Harper sessions in the adapter's IANA market timezone.

This is a zero-agent scheduling shim. It does not create jobs and it does not
run unless the canonical profile explicitly has automation_preference=ENABLED.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
DEFAULT_DB = HERMES_HOME / "data" / "virtual-investor" / "portfolio.db"
DEFAULT_STATE = HERMES_HOME / "data" / "virtual-investor" / "schedule-dispatch.json"


def _load_contract(database: Path) -> dict:
    if not database.exists():
        return {"enabled": False, "reason": "portfolio database does not exist"}
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        profile = conn.execute(
            """SELECT market, user_timezone, research_access, automation_preference
               FROM investor_profile WHERE id=1"""
        ).fetchone()
        if not profile or profile["automation_preference"] != "ENABLED":
            return {"enabled": False, "reason": "automation is not enabled in the Harper profile"}
        if profile["research_access"] != "FULL":
            return {"enabled": False, "reason": "verified full web research is unavailable"}
        adapter = conn.execute(
            """SELECT market_id, display_name, market_timezone, session_schedule_json
               FROM market_adapters WHERE market_id=?""",
            (profile["market"],),
        ).fetchone()
    if not adapter or not adapter["market_timezone"]:
        return {"enabled": False, "reason": "market adapter has no verified timezone"}
    schedule = json.loads(adapter["session_schedule_json"] or "{}")
    sessions = schedule.get("sessions") or []
    if not sessions:
        return {"enabled": False, "reason": "market adapter has no session schedule"}
    return {
        "enabled": True,
        "market_id": adapter["market_id"],
        "display_name": adapter["display_name"],
        "market_timezone": adapter["market_timezone"],
        "user_timezone": profile["user_timezone"],
        "sessions": sessions,
    }


def _parse_reference(value: str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("--at must include a timezone")
    return parsed.astimezone(timezone.utc)


def _load_state(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {"dispatched": {}}
    return value if isinstance(value, dict) else {"dispatched": {}}


def _write_state(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True))
    temporary.replace(path)


def due_sessions(contract: dict, reference: datetime) -> list[dict]:
    if not contract.get("enabled"):
        return []
    market_now = reference.astimezone(ZoneInfo(contract["market_timezone"]))
    due = []
    for session in contract["sessions"]:
        weekdays = session.get("weekdays", [0, 1, 2, 3, 4])
        if market_now.weekday() not in weekdays:
            continue
        hour, minute = (int(part) for part in str(session["time"]).split(":"))
        if (market_now.hour, market_now.minute) != (hour, minute):
            continue
        due.append({
            "label": str(session["label"]),
            "market_date": market_now.date().isoformat(),
            "market_time": market_now.strftime("%H:%M"),
            "market_timezone": contract["market_timezone"],
            "dispatch_key": (
                f"{contract['market_id']}:{market_now.date().isoformat()}:"
                f"{session['label']}"
            ),
        })
    return due


def _job_map(value: str | None) -> dict[str, str]:
    raw = value or os.environ.get("HARPER_SESSION_JOB_IDS", "")
    if not raw:
        return {}
    path = Path(raw).expanduser()
    if path.exists():
        raw = path.read_text()
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("job map must be a JSON object of session labels to Hermes job IDs")
    return {str(key): str(item) for key, item in parsed.items() if str(item).strip()}


def _trigger(job_id: str) -> bool:
    agent_root = HERMES_HOME / "hermes-agent"
    if str(agent_root) not in sys.path:
        sys.path.insert(0, str(agent_root))
    from cron.jobs import trigger_job, update_job

    update_job(job_id, {"fire_claim": None})
    return trigger_job(job_id) is not None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=os.environ.get("VIRTUAL_INVESTOR_DB", str(DEFAULT_DB)))
    parser.add_argument("--state", default=os.environ.get("HARPER_SCHEDULE_STATE", str(DEFAULT_STATE)))
    parser.add_argument("--at", help="Timezone-aware ISO timestamp for deterministic checks")
    parser.add_argument("--trigger", action="store_true", help="Queue mapped Hermes jobs")
    parser.add_argument("--job-map", help="JSON object or path mapping session labels to job IDs")
    args = parser.parse_args()

    database = Path(args.db).expanduser()
    state_path = Path(args.state).expanduser()
    contract = _load_contract(database)
    reference = _parse_reference(args.at)
    candidates = due_sessions(contract, reference)
    state = _load_state(state_path)
    dispatched = state.setdefault("dispatched", {})
    pending = [item for item in candidates if item["dispatch_key"] not in dispatched]
    mapping = _job_map(args.job_map)
    results = []
    for item in pending:
        job_id = mapping.get(item["label"])
        if not args.trigger:
            results.append({**item, "job_id": job_id, "queued": False, "preview": True})
            continue
        if args.trigger and not job_id:
            results.append({**item, "queued": False, "reason": "session has no mapped job ID"})
            continue
        # Persist intent before interacting with Hermes so repeated ticks cannot
        # duplicate a session after an uncertain trigger result.
        dispatched[item["dispatch_key"]] = {
            "claimed_at": datetime.now(timezone.utc).isoformat(),
            "job_id": job_id,
            "status": "CLAIMED",
        }
        _write_state(state_path, state)
        queued = _trigger(job_id)
        dispatched[item["dispatch_key"]]["status"] = "QUEUED" if queued else "UNCERTAIN"
        dispatched[item["dispatch_key"]]["finished_at"] = datetime.now(timezone.utc).isoformat()
        _write_state(state_path, state)
        results.append({**item, "job_id": job_id, "queued": queued})

    print(json.dumps({
        "contract": contract,
        "checked_at": reference.isoformat(),
        "due": candidates,
        "new_dispatches": results,
        "trigger_enabled": bool(args.trigger),
    }, indent=2))


if __name__ == "__main__":
    main()
