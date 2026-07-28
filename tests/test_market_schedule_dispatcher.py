import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).parents[1]
PORTFOLIO = ROOT / "scripts" / "portfolio.py"
DISPATCHER = ROOT / "scripts" / "market_schedule_dispatcher.py"
SPEC = importlib.util.spec_from_file_location("market_schedule_dispatcher", DISPATCHER)
dispatcher = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(dispatcher)


def run_cli(tmp_path, *args):
    env = os.environ.copy()
    env["PYTHONPATH"] = ""
    env["VIRTUAL_INVESTOR_DB"] = str(tmp_path / "portfolio.db")
    env["VIRTUAL_INVESTOR_ARCHIVE_DB"] = str(tmp_path / "archive.db")
    env["VIRTUAL_INVESTOR_DISABLE_SYNC"] = "1"
    result = subprocess.run(
        [sys.executable, str(PORTFOLIO), *args], capture_output=True, text=True, env=env
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout) if result.stdout.lstrip().startswith(("{", "[")) else result.stdout


def configured_profile(tmp_path, automation="NOT_ASKED"):
    run_cli(tmp_path, "init")
    run_cli(tmp_path, "profile", "set", "--preferred-name", "Ana")
    run_cli(
        tmp_path, "profile", "set", "--market", "United States equities",
        "--base-currency", "USD", "--user-timezone", "Asia/Kolkata",
        "--research-access", "FULL",
        "--automation", automation,
    )
    run_cli(
        tmp_path, "market-adapter", "set", "United States equities",
        "--market-timezone", "America/New_York",
        "--sessions-json", json.dumps([
            {"label": "open-execution", "time": "09:35", "purpose": "Opening review"}
        ]),
        "--source-kind", "calendar", "--source", "https://example.com/calendar",
    )


def test_dispatcher_requires_explicit_automation_opt_in(tmp_path):
    configured_profile(tmp_path)
    contract = dispatcher._load_contract(tmp_path / "portfolio.db")
    assert contract["enabled"] is False
    assert "not enabled" in contract["reason"]


def test_dispatcher_rechecks_research_access_at_runtime(tmp_path):
    configured_profile(tmp_path, automation="ENABLED")
    with sqlite3.connect(tmp_path / "portfolio.db") as conn:
        conn.execute(
            "UPDATE investor_profile SET research_access='UNAVAILABLE' WHERE id=1"
        )
    contract = dispatcher._load_contract(tmp_path / "portfolio.db")
    assert contract["enabled"] is False
    assert "research" in contract["reason"]


def test_dispatcher_uses_adapter_timezone_across_daylight_saving(tmp_path):
    configured_profile(tmp_path, automation="ENABLED")
    contract = dispatcher._load_contract(tmp_path / "portfolio.db")
    winter = dispatcher.due_sessions(
        contract, datetime(2026, 1, 15, 14, 35, tzinfo=timezone.utc)
    )
    summer = dispatcher.due_sessions(
        contract, datetime(2026, 7, 15, 13, 35, tzinfo=timezone.utc)
    )
    assert winter[0]["label"] == "open-execution"
    assert summer[0]["label"] == "open-execution"
    assert winter[0]["market_time"] == summer[0]["market_time"] == "09:35"


def test_dispatcher_preview_does_not_claim_or_trigger_a_session(tmp_path):
    configured_profile(tmp_path, automation="ENABLED")
    state = tmp_path / "dispatcher-state.json"
    result = subprocess.run(
        [
            sys.executable, str(DISPATCHER), "--db", str(tmp_path / "portfolio.db"),
            "--state", str(state), "--at", "2026-07-15T13:35:00+00:00",
        ],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["new_dispatches"][0]["preview"] is True
    assert payload["new_dispatches"][0]["queued"] is False
    assert state.exists() is False
