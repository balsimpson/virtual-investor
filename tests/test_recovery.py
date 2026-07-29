import importlib.util
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "skills" / "harper" / "scripts" / "harper_recovery.py"
SPEC = importlib.util.spec_from_file_location("harper_recovery", SCRIPT)
recovery = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = recovery
SPEC.loader.exec_module(recovery)
TEST_JOB_ID = "virtual-investor-test"


def create_databases(tmp_path, execution_status="unknown", run_status="STARTED"):
    executions = tmp_path / "executions.db"
    portfolio = tmp_path / "portfolio.db"
    with sqlite3.connect(executions) as conn:
        conn.execute(
            """CREATE TABLE executions (
                id TEXT PRIMARY KEY,
                job_id TEXT,
                status TEXT,
                claimed_at TEXT
            )"""
        )
        conn.execute(
            "INSERT INTO executions VALUES (?, ?, ?, ?)",
            ("execution-1", TEST_JOB_ID, execution_status, "2026-07-23T12:30:00+05:30"),
        )
    with sqlite3.connect(portfolio) as conn:
        conn.execute(
            """CREATE TABLE runs (
                id INTEGER PRIMARY KEY,
                market_date TEXT,
                session_label TEXT,
                status TEXT
            )"""
        )
        if run_status is not None:
            conn.execute(
                "INSERT INTO runs VALUES (?, ?, ?, ?)",
                (3, "2026-07-23", "midday-review", run_status),
            )
    return executions, portfolio


def test_interrupted_started_run_is_recoverable(tmp_path, monkeypatch):
    executions, portfolio = create_databases(tmp_path)
    monkeypatch.setattr(recovery, "EXECUTIONS_DB", executions)
    monkeypatch.setattr(recovery, "PORTFOLIO_DB", portfolio)
    monkeypatch.setattr(recovery, "_discover_harper_job_ids", lambda: (TEST_JOB_ID,))

    candidate = recovery.find_recovery_candidate(
        datetime(2026, 7, 23, 12, 35, tzinfo=recovery.INDIA_TZ)
    )

    assert candidate is not None
    assert candidate.session == "midday-review"
    assert candidate.run_id == 3
    assert candidate.retry_key == "run:3"


def test_late_catch_up_maps_to_most_recent_trading_session():
    claimed_at = datetime(2026, 7, 23, 15, 34, tzinfo=recovery.INDIA_TZ)
    assert recovery._session_for_claim(claimed_at) == "final-decisions"


def test_completed_app_run_is_not_retried(tmp_path, monkeypatch):
    executions, portfolio = create_databases(tmp_path, run_status="COMPLETED")
    monkeypatch.setattr(recovery, "EXECUTIONS_DB", executions)
    monkeypatch.setattr(recovery, "PORTFOLIO_DB", portfolio)
    monkeypatch.setattr(recovery, "_discover_harper_job_ids", lambda: (TEST_JOB_ID,))

    candidate = recovery.find_recovery_candidate(
        datetime(2026, 7, 23, 12, 35, tzinfo=recovery.INDIA_TZ)
    )

    assert candidate is None


def test_latest_running_execution_is_not_retried(tmp_path, monkeypatch):
    executions, portfolio = create_databases(tmp_path, execution_status="running")
    monkeypatch.setattr(recovery, "EXECUTIONS_DB", executions)
    monkeypatch.setattr(recovery, "PORTFOLIO_DB", portfolio)
    monkeypatch.setattr(recovery, "_discover_harper_job_ids", lambda: (TEST_JOB_ID,))

    candidate = recovery.find_recovery_candidate(
        datetime(2026, 7, 23, 12, 35, tzinfo=recovery.INDIA_TZ)
    )

    assert candidate is None


def test_retry_is_recorded_and_capped_per_session(tmp_path, monkeypatch):
    executions, portfolio = create_databases(tmp_path)
    state_file = tmp_path / "recovery-state.json"
    monkeypatch.setattr(recovery, "EXECUTIONS_DB", executions)
    monkeypatch.setattr(recovery, "PORTFOLIO_DB", portfolio)
    monkeypatch.setattr(recovery, "STATE_FILE", state_file)
    monkeypatch.setattr(recovery, "_discover_harper_job_ids", lambda: (TEST_JOB_ID,))
    monkeypatch.setattr(recovery, "trigger_harper_job", lambda job_id: True)
    candidate = recovery.find_recovery_candidate(
        datetime(2026, 7, 23, 12, 35, tzinfo=recovery.INDIA_TZ)
    )
    assert candidate is not None

    assert recovery.queue_recovery(candidate) is True
    assert recovery.queue_recovery(candidate) is False
    state = json.loads(state_file.read_text())
    assert state["retry_counts"]["run:3"] == 1
    assert state["attempted_execution_ids"] == ["execution-1"]


def test_uncertain_command_failure_still_consumes_retry_attempt(tmp_path, monkeypatch):
    executions, portfolio = create_databases(tmp_path)
    state_file = tmp_path / "recovery-state.json"
    monkeypatch.setattr(recovery, "EXECUTIONS_DB", executions)
    monkeypatch.setattr(recovery, "PORTFOLIO_DB", portfolio)
    monkeypatch.setattr(recovery, "STATE_FILE", state_file)
    monkeypatch.setattr(recovery, "_discover_harper_job_ids", lambda: (TEST_JOB_ID,))
    monkeypatch.setattr(recovery, "trigger_harper_job", lambda job_id: False)
    candidate = recovery.find_recovery_candidate(
        datetime(2026, 7, 23, 12, 35, tzinfo=recovery.INDIA_TZ)
    )
    assert candidate is not None

    try:
        recovery.queue_recovery(candidate)
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected recovery command failure")

    state = json.loads(state_file.read_text())
    assert state["retry_counts"]["run:3"] == 1
    assert state["attempted_execution_ids"] == ["execution-1"]


def test_recovery_maps_claims_in_the_active_adapter_timezone(tmp_path, monkeypatch):
    executions = tmp_path / "executions.db"
    portfolio = tmp_path / "portfolio.db"
    with sqlite3.connect(executions) as conn:
        conn.execute(
            "CREATE TABLE executions (id TEXT PRIMARY KEY, job_id TEXT, status TEXT, claimed_at TEXT)"
        )
        conn.execute(
            "INSERT INTO executions VALUES (?, ?, ?, ?)",
            ("execution-us", TEST_JOB_ID, "unknown", "2026-07-15T13:35:00+00:00"),
        )
    with sqlite3.connect(portfolio) as conn:
        conn.execute(
            "CREATE TABLE runs (id INTEGER PRIMARY KEY, market_date TEXT, session_label TEXT, status TEXT)"
        )
        conn.execute(
            "INSERT INTO runs VALUES (4, '2026-07-15', 'open-execution', 'STARTED')"
        )
        conn.execute(
            "CREATE TABLE investor_profile (id INTEGER PRIMARY KEY, market TEXT, user_timezone TEXT)"
        )
        conn.execute(
            "INSERT INTO investor_profile VALUES (1, 'UNITED_STATES_EQUITIES', 'Asia/Kolkata')"
        )
        conn.execute(
            """CREATE TABLE market_adapters (
                   market_id TEXT PRIMARY KEY, market_timezone TEXT, session_schedule_json TEXT
               )"""
        )
        conn.execute(
            "INSERT INTO market_adapters VALUES (?, ?, ?)",
            (
                "UNITED_STATES_EQUITIES", "America/New_York",
                json.dumps({"sessions": [
                    {"label": "open-execution", "time": "09:35"},
                    {"label": "closing-snapshot", "time": "16:05"},
                ]}),
            ),
        )
    monkeypatch.setattr(recovery, "EXECUTIONS_DB", executions)
    monkeypatch.setattr(recovery, "PORTFOLIO_DB", portfolio)
    monkeypatch.setattr(recovery, "_discover_harper_job_ids", lambda: (TEST_JOB_ID,))
    candidate = recovery.find_recovery_candidate(
        datetime(2026, 7, 15, 13, 40, tzinfo=timezone.utc)
    )
    assert candidate is not None
    assert candidate.market_date == "2026-07-15"
    assert candidate.session == "open-execution"
    assert candidate.run_id == 4
