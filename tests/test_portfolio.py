import json
import importlib.util
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "skills" / "harper" / "scripts" / "portfolio.py"
IST = timezone(timedelta(hours=5, minutes=30))


def run_cli(tmp_path, *args, check=True, bypass_market=True, extra_env=None):
    env = os.environ.copy()
    env["PYTHONPATH"] = ""
    env["VIRTUAL_INVESTOR_DB"] = str(tmp_path / "portfolio.db")
    env["VIRTUAL_INVESTOR_ARCHIVE_DB"] = str(tmp_path / "archive.db")
    env["VIRTUAL_INVESTOR_DISABLE_SYNC"] = "1"
    if bypass_market:
        env["VIRTUAL_INVESTOR_TEST_BYPASS_MARKET_SESSION"] = "1"
    else:
        env.pop("VIRTUAL_INVESTOR_TEST_BYPASS_MARKET_SESSION", None)
    if extra_env:
        env.update(extra_env)
    result = subprocess.run(
        [sys.executable, str(SCRIPT), *map(str, args)],
        capture_output=True,
        text=True,
        env=env,
    )
    if check and result.returncode != 0:
        raise AssertionError(result.stdout + result.stderr)
    return result


def read_json(result):
    return json.loads(result.stdout)


def status(tmp_path):
    return read_json(run_cli(tmp_path, "status"))


def quote(tmp_path, ticker="RELIANCE.NS", price=100):
    run_cli(tmp_path, "quote", ticker, price, f"https://www.nseindia.com/{ticker}")


def set_thesis(
    tmp_path,
    ticker="RELIANCE.NS",
    confidence=65,
    entry=100,
    target=None,
    invalidation=None,
    sector="Energy",
    trade_style="POSITION",
):
    target = target if target is not None else 120
    invalidation = invalidation if invalidation is not None else 95
    primary = f"https://www.nseindia.com/filings/{ticker}"
    secondary = f"https://www.reuters.com/markets/{ticker}"
    resolution_date = (datetime.now(IST).date() + timedelta(days=90)).isoformat()
    return run_cli(
        tmp_path,
        "thesis",
        "set",
        ticker,
        "--direction",
        "LONG",
        "--trade-style",
        trade_style,
        "--confidence",
        confidence,
        "--horizon",
        "90d",
        "--target",
        target,
        "--invalidation",
        "The numeric invalidation is confirmed after the catalyst",
        "--catalyst",
        "Quarterly results on 2026-10-15",
        "--variant",
        "The market expects flat margins; primary evidence supports expansion",
        "--sources",
        f"{primary},{secondary}",
        "--primary-sources",
        primary,
        "--event",
        "Quarterly EBITDA margin exceeds 18 percent",
        "--resolution-date",
        resolution_date,
        "--resolution-source",
        primary,
        "--entry-reference",
        entry,
        "--invalidation-price",
        invalidation,
        "--sector",
        sector,
        "--counter-thesis",
        "Input costs prevent the expected margin expansion",
        "--financial-summary",
        "Operating cash flow covers capex; leverage and working capital are stable.",
    )


def open_long(tmp_path, shares=10):
    run_cli(tmp_path, "init")
    quote(tmp_path)
    set_thesis(tmp_path)
    return read_json(
        run_cli(tmp_path, "trade", "BUY", "RELIANCE.NS", shares, 100, "Risk-gated entry")
    )


def test_init_uses_conservative_defaults_and_preserves_local_db_override(tmp_path):
    run_cli(tmp_path, "init")
    params = read_json(run_cli(tmp_path, "learn", "params"))
    assert params["max_position_weight"] == 0.20
    assert "max_short_weight" not in params
    assert params["risk_per_thesis"] == 0.01
    assert params["max_portfolio_heat"] == 0.05
    assert params["intraday_entry_cutoff_minutes"] == 30.0
    assert (tmp_path / "portfolio.db").exists()


def test_fresh_harper_onboarding_persists_and_resumes_one_question_at_a_time(tmp_path):
    run_cli(tmp_path, "init")
    fresh = read_json(run_cli(tmp_path, "profile", "show"))
    assert fresh["stage"] == "NEEDS_NAME"
    assert fresh["missing"] == [
        "preferred_name", "market", "base_currency", "initial_cash",
        "user_timezone", "research_access",
    ]
    assert fresh["portfolio"]["cash"] is None
    assert fresh["portfolio"]["valuation_status"] == "PENDING_STARTING_CASH"
    assert "virtual portfolio" in fresh["suggested_response"]
    assert fresh["suggested_response"].count("?") == 1

    named = read_json(run_cli(
        tmp_path, "profile", "set", "--preferred-name", "  Bál  "
    ))
    assert named["profile"]["preferred_name"] == "Bál"
    assert named["stage"] == "NEEDS_MARKET_CURRENCY"
    assert named["missing"] == [
        "market", "base_currency", "initial_cash", "user_timezone",
        "research_access",
    ]

    resumed = read_json(run_cli(tmp_path, "profile", "show"))
    assert resumed["stage"] == "NEEDS_MARKET_CURRENCY"
    assert resumed["profile"]["preferred_name"] == "Bál"

    capital = read_json(run_cli(
        tmp_path, "profile", "set",
        "--market", "India", "--base-currency", "inr",
    ))
    assert capital["stage"] == "NEEDS_STARTING_CASH"
    assert capital["suggested_initial_cash"] == 100000.0
    assert "₹100,000.00" in capital["suggested_response"]
    assert capital["suggested_response"].count("?") == 1
    ready = read_json(run_cli(
        tmp_path, "profile", "set", "--initial-cash", "250000"
    ))
    assert ready["stage"] == "NEEDS_TIMEZONE"
    assert ready["profile"]["initial_cash"] == 250000.0
    assert ready["suggested_initial_cash"] is None
    with sqlite3.connect(tmp_path / "portfolio.db") as conn:
        assert conn.execute(
            "SELECT value FROM state WHERE key='cash'"
        ).fetchone()[0] == "250000.0"
        assert conn.execute(
            "SELECT value FROM state WHERE key='initial_cash'"
        ).fetchone()[0] == "250000.0"
    ready = read_json(run_cli(
        tmp_path, "profile", "set", "--user-timezone", "Asia/Kolkata"
    ))
    assert ready["stage"] == "NEEDS_RESEARCH_ACCESS"
    assert ready["complete"] is False
    assert ready["research_check_required"] is True
    assert ready["profile"]["onboarding_completed_at"] is None

    blocked_automation = run_cli(
        tmp_path, "profile", "set", "--automation", "ENABLED", check=False
    )
    assert blocked_automation.returncode == 1
    assert "verified FULL web search" in blocked_automation.stderr

    limited = read_json(run_cli(
        tmp_path, "profile", "set", "--research-access", "LIMITED"
    ))
    assert limited["stage"] == "NEEDS_RESEARCH_ACCESS"
    assert "hermes tools" in limited["suggested_response"]
    assert "Never paste an API key into chat" in limited["suggested_response"]

    ready = read_json(run_cli(
        tmp_path, "profile", "set", "--research-access", "FULL"
    ))
    assert ready["stage"] == "READY"
    assert ready["complete"] is True
    assert ready["research_check_required"] is False
    assert ready["profile"]["research_access"] == "FULL"
    assert ready["profile"]["research_checked_at"]
    assert ready["profile"]["market"] == "INDIA_NSE_BSE"
    assert ready["profile"]["base_currency"] == "INR"
    assert ready["profile"]["onboarding_completed_at"]
    assert "₹250,000.00" in ready["suggested_response"]
    assert "no positions" in ready["suggested_response"]
    assert ready["automation_offer_pending"] is True
    assert ready["delivery_offer_pending"] is False
    assert ready["dashboard_offer_pending"] is False
    assert "automatic schedule" in ready["suggested_response"]
    assert "won't create or enable jobs without your confirmation" in ready["suggested_response"]

    repeated = read_json(run_cli(tmp_path, "profile", "show"))
    assert repeated["stage"] == "READY"
    assert repeated["profile"]["onboarding_completed_at"] == ready["profile"]["onboarding_completed_at"]
    with sqlite3.connect(tmp_path / "portfolio.db") as conn:
        assert conn.execute("SELECT COUNT(*) FROM investor_profile").fetchone()[0] == 1

    blocked_delivery = run_cli(
        tmp_path,
        "profile", "set", "--delivery-target", "telegram:-1001234567890",
        check=False,
    )
    assert blocked_delivery.returncode == 1
    assert "only after automated sessions are enabled" in blocked_delivery.stderr

    delivery_offer = read_json(run_cli(
        tmp_path, "profile", "set", "--automation", "ENABLED"
    ))
    assert delivery_offer["automation_offer_pending"] is False
    assert delivery_offer["delivery_offer_pending"] is True
    assert delivery_offer["dashboard_offer_pending"] is False
    assert "destinations already configured in Hermes" in delivery_offer["suggested_response"]

    dashboard_offer = read_json(run_cli(
        tmp_path,
        "profile", "set", "--delivery-target", "telegram:-1001234567890",
    ))
    assert dashboard_offer["delivery_offer_pending"] is False
    assert dashboard_offer["dashboard_offer_pending"] is True
    assert dashboard_offer["profile"]["delivery_preference"] == "MESSAGING"
    assert dashboard_offer["profile"]["delivery_target"] == "telegram:-1001234567890"
    assert dashboard_offer["profile"]["delivery_confirmed_at"]
    assert dashboard_offer["profile"]["onboarding_completed_at"] == (
        ready["profile"]["onboarding_completed_at"]
    )
    assert "optional private web dashboard" in dashboard_offer["suggested_response"]

    automation_skipped = read_json(run_cli(
        tmp_path, "profile", "set", "--automation", "SKIPPED"
    ))
    assert automation_skipped["profile"]["delivery_preference"] == "NOT_ASKED"
    assert automation_skipped["profile"]["delivery_target"] is None
    assert automation_skipped["profile"]["delivery_confirmed_at"] is None
    dashboard_offer = automation_skipped
    assert dashboard_offer["automation_offer_pending"] is False
    assert dashboard_offer["dashboard_offer_pending"] is True
    assert "optional private web dashboard" in dashboard_offer["suggested_response"]
    assert "won't create cloud resources" in dashboard_offer["suggested_response"]

    dashboard_skipped = read_json(run_cli(
        tmp_path, "profile", "set", "--dashboard", "SKIPPED"
    ))
    assert dashboard_skipped["dashboard_offer_pending"] is False
    assert dashboard_skipped["profile"]["dashboard_preference"] == "SKIPPED"
    assert "quick market brief" in dashboard_skipped["suggested_response"]


def test_existing_india_portfolio_upgrade_seeds_scope_without_guessing_name(tmp_path):
    database = tmp_path / "portfolio.db"
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE state (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("INSERT INTO state(key, value) VALUES ('cash', '73500')")
        conn.execute("INSERT INTO state(key, value) VALUES ('initial_cash', '90000')")
    upgraded = read_json(run_cli(tmp_path, "profile", "show"))
    assert upgraded["stage"] == "NEEDS_NAME"
    assert upgraded["missing"] == ["preferred_name", "research_access"]
    assert upgraded["profile"]["market"] == "INDIA_NSE_BSE"
    assert upgraded["profile"]["base_currency"] == "INR"
    assert upgraded["profile"]["initial_cash"] == 90000.0
    assert upgraded["profile"]["preferred_name"] is None
    assert upgraded["profile"]["delivery_preference"] == "NOT_ASKED"
    assert upgraded["profile"]["delivery_target"] is None
    assert upgraded["profile"]["dashboard_preference"] == "NOT_ASKED"
    assert upgraded["portfolio"]["cash"] == 73500.0


def test_delivery_target_supports_local_and_rejects_invalid_values(tmp_path):
    run_cli(tmp_path, "init")
    run_cli(
        tmp_path,
        "profile", "set",
        "--preferred-name", "Alex",
        "--market", "India",
        "--base-currency", "INR",
        "--initial-cash", "100000",
        "--user-timezone", "Asia/Kolkata",
        "--research-access", "FULL",
        "--automation", "ENABLED",
    )

    local = read_json(run_cli(
        tmp_path, "profile", "set", "--delivery-target", "local"
    ))
    assert local["profile"]["delivery_preference"] == "LOCAL"
    assert local["profile"]["delivery_target"] == "local"
    assert local["delivery_offer_pending"] is False
    assert local["dashboard_offer_pending"] is True

    invalid = run_cli(
        tmp_path,
        "profile", "set", "--delivery-target", "Slack Investments",
        check=False,
    )
    assert invalid.returncode == 1
    assert "without whitespace" in invalid.stderr


def test_dashboard_status_derives_site_endpoint_without_exposing_token(tmp_path):
    result = read_json(run_cli(
        tmp_path,
        "dashboard",
        extra_env={
            "CONVEX_URL": "https://private-install.eu-west-1.convex.cloud",
            "HARPER_SYNC_TOKEN": "a" * 32,
        },
    ))
    assert result["configured"] is True
    assert result["contract_version"] == 2
    assert result["sync_url"] == (
        "https://private-install.eu-west-1.convex.site/harper-sync"
    )
    assert result["sync_token_configured"] is True
    assert "a" * 32 not in json.dumps(result)


def test_dashboard_sync_v2_carries_global_profile_adapter_and_canonical_nav(
    tmp_path, monkeypatch, capsys
):
    run_cli(tmp_path, "init")
    run_cli(
        tmp_path,
        "profile",
        "set",
        "--preferred-name",
        "Alex",
        "--market",
        "United States equities",
        "--base-currency",
        "USD",
        "--initial-cash",
        "25000",
        "--user-timezone",
        "Europe/London",
    )

    spec = importlib.util.spec_from_file_location("portfolio_sync_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    monkeypatch.setenv("VIRTUAL_INVESTOR_DB", str(tmp_path / "portfolio.db"))
    monkeypatch.setenv("VIRTUAL_INVESTOR_ARCHIVE_DB", str(tmp_path / "archive.db"))
    monkeypatch.setenv("CONVEX_URL", "https://private-install.eu-west-1.convex.cloud")
    monkeypatch.setenv("HARPER_SYNC_TOKEN", "a" * 32)
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"synced":true,"contractVersion":2}'

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)
    module.cmd_convex_sync(None)
    capsys.readouterr()

    body = json.loads(captured["request"].data)
    payload = body["payload"]
    assert body["contractVersion"] == 2
    assert payload["profile"] == {
        "preferred_name": "Alex",
        "user_timezone": "Europe/London",
        "portfolio_currency": "USD",
        "initial_capital": 25000.0,
    }
    assert payload["portfolio_config"]["market_id"] == "UNITED_STATES_EQUITIES"
    assert payload["market_adapter"]["status"] == "DISCOVERY"
    assert payload["valuation"]["portfolio_currency"] == "USD"
    assert payload["nav_history"] == []
    assert payload["sync_metadata"]["complete"] is True
    assert captured["timeout"] == 30


def test_profile_accepts_global_scope_and_validates_currency_timezone_and_names(tmp_path):
    run_cli(tmp_path, "init")
    global_scope = read_json(run_cli(
        tmp_path, "profile", "set", "--market", "United States equities",
        "--base-currency", "USD",
    ))
    assert global_scope["profile"]["market"] == "UNITED_STATES_EQUITIES"
    assert global_scope["market_adapter"]["status"] == "DISCOVERY"
    invalid_currency = run_cli(
        tmp_path, "profile", "set", "--base-currency", "US", check=False
    )
    assert invalid_currency.returncode == 1
    assert "three-letter ISO 4217" in invalid_currency.stderr
    invalid_timezone = run_cli(
        tmp_path, "profile", "set", "--user-timezone", "Mars/Olympus", check=False
    )
    assert invalid_timezone.returncode == 1
    assert "valid IANA name" in invalid_timezone.stderr
    empty_name = run_cli(
        tmp_path, "profile", "set", "--preferred-name", "   ", check=False
    )
    assert empty_name.returncode == 1
    assert "cannot be empty" in empty_name.stderr
    profile = read_json(run_cli(tmp_path, "profile", "show"))
    assert profile["stage"] == "NEEDS_NAME"


def test_starting_cash_uses_currency_default_and_requires_confirmation(tmp_path):
    run_cli(tmp_path, "init")
    run_cli(tmp_path, "profile", "set", "--preferred-name", "Ana")
    profile = read_json(run_cli(
        tmp_path, "profile", "set", "--market", "United States equities",
        "--base-currency", "USD",
    ))
    assert profile["stage"] == "NEEDS_STARTING_CASH"
    assert profile["suggested_initial_cash"] == 10000.0
    assert "$10,000.00" in profile["suggested_response"]

    for invalid in ("0", "-100", "nan", "inf"):
        rejected = run_cli(
            tmp_path, "profile", "set", "--initial-cash", invalid, check=False
        )
        assert rejected.returncode == 1
        assert "positive finite amount" in rejected.stderr

    profile = read_json(run_cli(
        tmp_path, "profile", "set", "--initial-cash", "25000"
    ))
    assert profile["stage"] == "NEEDS_TIMEZONE"
    assert profile["profile"]["initial_cash"] == 25000.0

    changed = read_json(run_cli(
        tmp_path, "profile", "set", "--base-currency", "BRL",
        "--confirm-scope-change", "CHANGE-HARPER-SCOPE",
    ))
    assert changed["stage"] == "NEEDS_STARTING_CASH"
    assert changed["profile"]["initial_cash"] is None
    assert changed["suggested_initial_cash"] == 100000.0
    assert "BRL 100,000.00" in changed["suggested_response"]


def test_optional_preferences_are_explicit_and_portfolio_reset_keeps_profile(tmp_path):
    run_cli(tmp_path, "init")
    run_cli(tmp_path, "profile", "set", "--preferred-name", "Bal")
    run_cli(
        tmp_path, "profile", "set", "--market", "NSE/BSE", "--base-currency", "INR"
    )
    run_cli(tmp_path, "profile", "set", "--initial-cash", "250000")
    run_cli(tmp_path, "profile", "set", "--user-timezone", "Asia/Kolkata")
    run_cli(tmp_path, "profile", "set", "--research-access", "FULL")
    saved = read_json(run_cli(
        tmp_path, "profile", "preference", "set",
        "explanation_depth", "beginner",
    ))
    assert saved["profile"]["optional_preferences"] == {
        "explanation_depth": "beginner"
    }

    run_cli(tmp_path, "reset", "--confirm", "RESET-HARPER")
    retained = read_json(run_cli(tmp_path, "profile", "show"))
    assert retained["stage"] == "READY"
    assert retained["profile"]["preferred_name"] == "Bal"
    assert retained["profile"]["optional_preferences"] == {
        "explanation_depth": "beginner"
    }
    assert retained["portfolio"]["cash"] == 250000.0
    assert retained["profile"]["initial_cash"] == 250000.0

    cleared = read_json(run_cli(
        tmp_path, "profile", "preference", "reset",
        "--confirm", "RESET-HARPER-PREFERENCES",
    ))
    assert cleared["profile"]["optional_preferences"] == {}
    assert cleared["portfolio"]["cash"] == 250000.0


def test_global_discovery_adapter_operates_without_benchmark_cost_or_regulatory_data(tmp_path):
    run_cli(tmp_path, "init")
    run_cli(tmp_path, "profile", "set", "--preferred-name", "Ana")
    run_cli(
        tmp_path, "profile", "set", "--market", "Brazil equities",
        "--base-currency", "BRL",
    )
    run_cli(tmp_path, "profile", "set", "--initial-cash", "100000")
    profile = read_json(run_cli(
        tmp_path, "profile", "set", "--user-timezone", "America/Sao_Paulo"
    ))
    assert profile["stage"] == "NEEDS_RESEARCH_ACCESS"
    profile = read_json(run_cli(
        tmp_path, "profile", "set", "--research-access", "FULL"
    ))
    assert profile["stage"] == "READY"
    assert profile["market_adapter"]["status"] == "DISCOVERY"
    assert "conservative cost assumptions" in profile["suggested_response"]

    adapter = read_json(run_cli(tmp_path, "market-adapter", "show", "Brazil equities"))
    assert adapter["health"]["operable"] is True
    assert adapter["health"]["benchmark_mode"] == "ABSOLUTE_RETURN_ONLY"
    assert adapter["health"]["cost_mode"] == "CONSERVATIVE_FALLBACK"
    assert adapter["health"]["regulatory_mode"] == "UNVERIFIED_VIRTUAL_SIMULATION"
    assert adapter["effective_cost_model"]["fee_bps"] == 25.0

    quote = run_cli(
        tmp_path, "quote", "PETR4.SA", "38.50", "https://example.com/petr4"
    )
    assert "BRL 38.50" in quote.stdout
    review = read_json(run_cli(tmp_path, "review"))
    assert review["benchmark"] is None
    assert review["active_return_pct"] is None


def test_market_adapter_updates_are_versioned_sourced_and_non_destructive(tmp_path):
    run_cli(tmp_path, "init")
    run_cli(tmp_path, "profile", "set", "--preferred-name", "Ana")
    run_cli(
        tmp_path, "profile", "set", "--market", "United States equities",
        "--base-currency", "USD",
    )
    run_cli(tmp_path, "profile", "set", "--user-timezone", "America/Los_Angeles")
    updated = read_json(run_cli(
        tmp_path, "market-adapter", "set", "United States equities",
        "--display-name", "United States listed equities",
        "--market-timezone", "America/New_York",
        "--ticker-pattern", "^[A-Z.]{1,10}$",
        "--market-open", "09:30", "--market-close", "16:00",
        "--sessions-json", json.dumps([
            {"label": "open-execution", "time": "09:35", "purpose": "Opening review"},
            {"label": "closing-snapshot", "time": "16:05", "purpose": "Closing snapshot"},
        ]),
        "--source-kind", "calendar", "--source", "https://example.com/exchange-hours",
        "--status", "LIMITED",
    ))
    assert updated["version"] == 2
    assert updated["market_timezone"] == "America/New_York"
    assert updated["health"]["automation_available"] is True
    assert updated["evidence"][0]["source_url"] == "https://example.com/exchange-hours"

    profile = read_json(run_cli(tmp_path, "profile", "show"))
    assert profile["profile"]["user_timezone"] == "America/Los_Angeles"
    assert profile["profile"]["base_currency"] == "USD"
    with sqlite3.connect(tmp_path / "portfolio.db") as conn:
        assert conn.execute("SELECT COUNT(*) FROM holdings").fetchone()[0] == 0
        assert conn.execute("SELECT value FROM state WHERE key='cash'").fetchone()[0] == "100000.0"


def test_market_capability_updates_reject_unsourced_facts(tmp_path):
    run_cli(tmp_path, "init")
    run_cli(
        tmp_path, "profile", "set", "--market", "United States equities",
        "--base-currency", "USD",
    )
    rejected = run_cli(
        tmp_path, "market-adapter", "set", "United States equities",
        "--market-timezone", "America/New_York",
        "--sessions-json", json.dumps([
            {"label": "open-execution", "time": "09:35"}
        ]),
        check=False,
    )
    assert rejected.returncode == 1
    assert "require a public --source URL" in rejected.stderr
    adapter = read_json(run_cli(
        tmp_path, "market-adapter", "show", "United States equities"
    ))
    assert adapter["market_timezone"] is None
    assert adapter["health"]["automation_available"] is False


def test_scope_change_is_blocked_once_financial_history_exists(tmp_path):
    run_cli(tmp_path, "init")
    run_cli(tmp_path, "profile", "set", "--preferred-name", "Ana")
    run_cli(
        tmp_path, "profile", "set", "--market", "United States equities",
        "--base-currency", "USD", "--user-timezone", "America/New_York",
        "--initial-cash", "25000", "--research-access", "FULL",
    )
    with sqlite3.connect(tmp_path / "portfolio.db") as conn:
        conn.execute(
            """INSERT INTO snapshots(
                   cash, holdings_value, total, holdings_json, timestamp
               ) VALUES (?, ?, ?, ?, ?)""",
            (100000.0, 0.0, 100000.0, "[]", "2026-07-28T16:00:00+00:00"),
        )

    rejected = run_cli(
        tmp_path, "profile", "set", "--market", "Brazil equities",
        "--base-currency", "BRL", "--confirm-scope-change", "CHANGE-HARPER-SCOPE",
        check=False,
    )
    assert rejected.returncode == 1
    assert "separate sourced migration" in rejected.stderr
    rejected_cash = run_cli(
        tmp_path, "profile", "set", "--initial-cash", "30000", check=False
    )
    assert rejected_cash.returncode == 1
    assert "cannot change after financial history" in rejected_cash.stderr
    unchanged = read_json(run_cli(tmp_path, "profile", "show"))
    assert unchanged["profile"]["market"] == "UNITED_STATES_EQUITIES"
    assert unchanged["profile"]["base_currency"] == "USD"


def test_adapter_schedule_preview_uses_market_and_user_timezones_without_creating_jobs(tmp_path):
    run_cli(tmp_path, "init")
    run_cli(tmp_path, "profile", "set", "--preferred-name", "Ana")
    run_cli(
        tmp_path, "profile", "set", "--market", "United States equities",
        "--base-currency", "USD", "--user-timezone", "Asia/Kolkata",
    )
    sessions = [{"label": "open-execution", "time": "09:35", "purpose": "Opening review"}]
    run_cli(
        tmp_path, "market-adapter", "set", "United States equities",
        "--market-timezone", "America/New_York",
        "--sessions-json", json.dumps(sessions),
        "--source-kind", "calendar", "--source", "https://example.com/calendar",
    )
    preview = read_json(run_cli(tmp_path, "market-adapter", "schedule"))
    assert preview["available"] is True
    assert preview["installation_mode"] == "TIMEZONE_AWARE_DISPATCHER"
    assert preview["daylight_saving_safe"] is True
    assert preview["requires_confirmation"] is True
    assert preview["creates_jobs"] is False


def test_new_thesis_requires_resolvable_event_primary_evidence_and_numeric_risk(tmp_path):
    run_cli(tmp_path, "init")
    legacy = run_cli(
        tmp_path,
        "thesis",
        "set",
        "RELIANCE.NS",
        "--direction",
        "LONG",
        "--confidence",
        "65",
        "--horizon",
        "90d",
        "--target",
        "120",
        "--invalidation",
        "Margins miss",
        "--catalyst",
        "Quarterly results",
        "--variant",
        "Margins expand",
        "--sources",
        "https://www.nseindia.com/a,https://www.reuters.com/b",
        check=False,
    )
    assert legacy.returncode != 0
    assert "--event" in legacy.stderr or "--primary-sources" in legacy.stderr


def test_trade_applies_slippage_fees_and_reports_portfolio_heat(tmp_path):
    trade = open_long(tmp_path)
    data = status(tmp_path)
    assert trade["execution_price"] == 100.1
    assert trade["fees"] == 1.25
    assert trade["slippage"] == 1.0
    assert data["cash"] == 98997.75
    assert data["nav"] == 99997.75
    assert data["trading_costs"] == 2.25
    assert data["portfolio_heat_pct"] == 0.06
    assert data["valuation_status"] == "FRESH"


def test_risk_budget_rejects_oversized_trade(tmp_path):
    run_cli(tmp_path, "init")
    quote(tmp_path)
    set_thesis(tmp_path)
    blocked = run_cli(
        tmp_path, "trade", "BUY", "RELIANCE.NS", 200, 100, "Too much invalidation risk",
        check=False,
    )
    assert blocked.returncode == 1
    assert "risks" in blocked.stderr
    assert status(tmp_path)["holdings"] == []


def test_projected_heat_includes_the_first_position(tmp_path):
    run_cli(tmp_path, "init")
    quote(tmp_path)
    set_thesis(tmp_path)
    run_cli(
        tmp_path, "learn", "params", "set",
        "risk_per_thesis", "0.02", "max_portfolio_heat", "0.005",
    )
    blocked = run_cli(
        tmp_path, "trade", "BUY", "RELIANCE.NS", 100, 100,
        "Would breach aggregate heat", check=False,
    )
    assert blocked.returncode == 1
    assert "portfolio heat" in blocked.stderr
    assert status(tmp_path)["holdings"] == []


def test_entry_economics_are_rechecked_at_the_latest_price(tmp_path):
    run_cli(tmp_path, "init")
    set_thesis(tmp_path)
    quote(tmp_path, price=114)
    blocked = run_cli(
        tmp_path, "trade", "BUY", "RELIANCE.NS", 1, 114,
        "Price has consumed the expected upside", check=False,
    )
    assert blocked.returncode == 1
    assert "reward/risk" in blocked.stderr


def test_trade_requires_fresh_matching_quote_and_never_falls_back_to_cost(tmp_path):
    open_long(tmp_path)
    mismatch = run_cli(
        tmp_path, "trade", "BUY", "RELIANCE.NS", 1, 110, "Mismatched quote", check=False
    )
    assert mismatch.returncode == 1
    assert "latest sourced quote" in mismatch.stderr
    with sqlite3.connect(tmp_path / "portfolio.db") as conn:
        conn.execute("DELETE FROM quotes WHERE ticker='RELIANCE.NS'")
    missing = run_cli(tmp_path, "status", check=False)
    assert missing.returncode == 1
    assert "cost basis is never used" in missing.stderr


def test_quote_rejects_materially_future_asof(tmp_path):
    run_cli(tmp_path, "init")
    future = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    result = run_cli(
        tmp_path, "quote", "RELIANCE.NS", 100,
        "https://www.nseindia.com/RELIANCE.NS", "--asof", future,
        check=False,
    )
    assert result.returncode == 1
    assert "future" in result.stderr


def test_short_theses_and_trades_are_not_available(tmp_path):
    run_cli(tmp_path, "init")
    quote(tmp_path, "TATAMOTORS.NS", 100)
    thesis = run_cli(
        tmp_path, "thesis", "set", "TATAMOTORS.NS",
        "--direction", "SHORT", check=False,
    )
    assert thesis.returncode != 0
    assert "invalid choice" in thesis.stderr
    trade = run_cli(
        tmp_path, "trade", "SHORT", "TATAMOTORS.NS", 1, 100,
        "Shorting is disabled", check=False,
    )
    assert trade.returncode != 0
    assert "invalid choice" in trade.stderr


def test_run_start_is_idempotent_per_date_and_session(tmp_path):
    run_cli(tmp_path, "init")
    first = read_json(run_cli(tmp_path, "run", "start", "2026-07-22", "--session", "morning"))
    second = read_json(run_cli(tmp_path, "run", "start", "2026-07-22", "--session", "morning"))
    midday = read_json(run_cli(tmp_path, "run", "start", "2026-07-22", "--session", "mid-day"))
    assert first["created"] is True
    assert second["created"] is False
    assert second["run_id"] == first["run_id"]
    assert midday["run_id"] != first["run_id"]


def test_usage_import_tracks_jobs_chat_subagents_models_tokens_and_cost(tmp_path):
    hermes_state = tmp_path / "hermes-state.db"
    with sqlite3.connect(hermes_state) as conn:
        conn.executescript(
            """
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                chat_id TEXT,
                parent_session_id TEXT,
                model TEXT,
                started_at REAL,
                ended_at REAL,
                billing_provider TEXT,
                billing_mode TEXT,
                api_call_count INTEGER,
                input_tokens INTEGER,
                output_tokens INTEGER,
                cache_read_tokens INTEGER,
                cache_write_tokens INTEGER,
                reasoning_tokens INTEGER,
                estimated_cost_usd REAL,
                actual_cost_usd REAL,
                cost_status TEXT
            );
            CREATE TABLE session_model_usage (
                session_id TEXT,
                model TEXT,
                billing_provider TEXT,
                billing_mode TEXT,
                task TEXT,
                api_call_count INTEGER,
                input_tokens INTEGER,
                output_tokens INTEGER,
                cache_read_tokens INTEGER,
                cache_write_tokens INTEGER,
                reasoning_tokens INTEGER,
                estimated_cost_usd REAL,
                actual_cost_usd REAL,
                cost_status TEXT
            );
            """
        )
        timestamp = datetime.now(timezone.utc).timestamp()
        sessions = [
            (
                "cron_harperjob_20260722_090000", "cron", None, None,
                "example-free-model", timestamp, timestamp + 30,
                "example-provider", "", 2, 100, 20, 300, 0, 5, 0, 0, "unknown",
            ),
            (
                "child-session", "subagent", None,
                "cron_harperjob_20260722_090000", "priced-model", timestamp,
                timestamp + 10, "openrouter", "", 1, 40, 10, 0, 0, 2,
                0.25, 0, "estimated",
            ),
            (
                "harper-chat", "local", "portfolio", None,
                "example-free-model", timestamp, timestamp + 5,
                "example-provider", "", 1, 30, 5, 20, 0, 1, 0, 0, "unknown",
            ),
            (
                "cron_otherjob_20260722_090000", "cron", None, None,
                "unrelated-model", timestamp, timestamp + 5,
                "other", "", 1, 999, 999, 0, 0, 0, 9, 0, "estimated",
            ),
        ]
        conn.executemany(
            "INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            sessions,
        )
        conn.executemany(
            "INSERT INTO session_model_usage VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    "cron_harperjob_20260722_090000", "example-free-model",
                    "example-provider", "", "", 2, 100, 20, 300, 0, 5, 0, 0,
                    "unknown",
                ),
                (
                    "child-session", "priced-model", "openrouter", "", "research",
                    1, 40, 10, 0, 0, 2, 0.25, 0, "estimated",
                ),
                (
                    "harper-chat", "example-free-model", "example-provider", "", "",
                    1, 30, 5, 20, 0, 1, 0, 0, "unknown",
                ),
                (
                    "cron_otherjob_20260722_090000", "unrelated-model", "other", "", "",
                    1, 999, 999, 0, 0, 0, 9, 0, "estimated",
                ),
            ],
        )

    jobs = tmp_path / "jobs.json"
    jobs.write_text(json.dumps({
        "jobs": [{
            "id": "harperjob",
            "name": "harper",
            "skill": "harper",
            "skills": ["harper"],
            "provider": "example-provider",
            "deliver": "local:portfolio",
        }],
    }))
    result = read_json(run_cli(
        tmp_path,
        "usage",
        extra_env={
            "VIRTUAL_INVESTOR_HERMES_STATE_DB": str(hermes_state),
            "VIRTUAL_INVESTOR_HERMES_CRON_JOBS": str(jobs),
        },
    ))

    assert result["this_week"]["sessions"] == 3
    assert result["this_week"]["api_calls"] == 4
    assert result["this_week"]["input_tokens"] == 170
    assert result["this_week"]["output_tokens"] == 35
    assert result["this_week"]["cache_read_tokens"] == 320
    assert result["this_week"]["total_tokens"] == 525
    assert result["this_week"]["cost_usd"] == 0.25
    assert result["this_week"]["cost_status"] == "estimated"
    assert {model["model"] for model in result["this_week"]["models"]} == {
        "example-free-model", "priced-model",
    }

    with sqlite3.connect(tmp_path / "portfolio.db") as conn:
        imported = conn.execute(
            "SELECT session_id, job_name, cost_status FROM llm_usage ORDER BY session_id"
        ).fetchall()
    assert imported == [
        ("child-session", "harper", "estimated"),
        ("cron_harperjob_20260722_090000", "harper", "free"),
        ("harper-chat", "Harper chat", "free"),
    ]


def test_trade_requires_same_day_official_market_session_confirmation(tmp_path):
    run_cli(tmp_path, "init")
    quote(tmp_path)
    set_thesis(tmp_path)
    blocked = run_cli(
        tmp_path, "trade", "BUY", "RELIANCE.NS", 1, 100,
        "Session must be confirmed", check=False, bypass_market=False,
    )
    assert blocked.returncode == 1
    assert "market-session confirm" in blocked.stderr


def test_close_run_requires_intraday_position_and_thesis_resolution(tmp_path):
    run_cli(tmp_path, "init")
    quote(tmp_path)
    set_thesis(tmp_path, trade_style="INTRADAY")
    run_cli(tmp_path, "trade", "BUY", "RELIANCE.NS", 10, 100, "Intraday setup")
    run = read_json(run_cli(
        tmp_path, "run", "start", datetime.now(IST).date().isoformat(),
        "--session", "close",
    ))
    blocked = run_cli(
        tmp_path, "run", "finish", run["run_id"], "Premature close",
        check=False,
    )
    assert blocked.returncode == 1
    assert "INTRADAY position" in blocked.stderr
    quote(tmp_path)
    run_cli(tmp_path, "trade", "SELL", "RELIANCE.NS", 10, 100, "Exit before close")
    still_active = run_cli(
        tmp_path, "run", "finish", run["run_id"], "Thesis still active",
        check=False,
    )
    assert still_active.returncode == 1
    assert "INTRADAY thesis" in still_active.stderr
    run_cli(
        tmp_path, "thesis", "close", "RELIANCE.NS",
        "--outcome", "LOSS", "--lesson", "Costs exceeded the flat price move",
        "--exit-reason", "catalyst_played_out", "--timing", "on_time",
        "--event-outcome", "NO",
    )
    run_cli(
        tmp_path, "journal", "daily",
        "Intraday position closed; flat tape did not confirm the event.",
        "--run-id", run["run_id"],
    )
    finished = read_json(run_cli(
        tmp_path, "run", "finish", run["run_id"], "Intraday cycle reconciled",
    ))
    assert finished["status"] == "COMPLETED"


def test_brier_scores_event_not_trade_return(tmp_path):
    open_long(tmp_path)
    quote(tmp_path, price=100)
    run_cli(tmp_path, "trade", "SELL", "RELIANCE.NS", 10, 100, "Close before resolution")
    run_cli(
        tmp_path,
        "thesis",
        "close",
        "RELIANCE.NS",
        "--outcome",
        "LOSS",
        "--lesson",
        "The event occurred but costs made the trade lose money",
        "--exit-reason",
        "catalyst_played_out",
        "--timing",
        "on_time",
        "--event-outcome",
        "YES",
    )
    review = read_json(run_cli(tmp_path, "review"))
    assert review["win_rate_pct"] == 0.0
    assert review["brier_score"] == 0.1225
    assert review["resolved_forecasts"] == 1


def test_open_position_blocks_thesis_resolution(tmp_path):
    open_long(tmp_path)
    result = run_cli(
        tmp_path, "thesis", "close", "RELIANCE.NS",
        "--outcome", "WIN", "--lesson", "Too early",
        "--exit-reason", "catalyst_played_out", "--timing", "on_time",
        "--event-outcome", "YES", check=False,
    )
    assert result.returncode == 1
    assert "close the RELIANCE.NS position" in result.stderr


def test_unresolved_forecast_stays_pending_and_cannot_authorize_risk(tmp_path):
    open_long(tmp_path)
    quote(tmp_path)
    run_cli(tmp_path, "trade", "SELL", "RELIANCE.NS", 10, 100, "Risk exit")
    pending = read_json(run_cli(
        tmp_path, "thesis", "close", "RELIANCE.NS",
        "--outcome", "LOSS", "--lesson", "Event evidence is not published yet",
        "--exit-reason", "risk_exit", "--timing", "early",
        "--event-outcome", "UNRESOLVED",
    ))
    assert pending["status"] == "PENDING_RESOLUTION"
    blocked = run_cli(
        tmp_path, "trade", "BUY", "RELIANCE.NS", 1, 100,
        "Pending thesis must not reopen risk", check=False,
    )
    assert blocked.returncode == 1
    assert "active LONG thesis" in blocked.stderr
    resolved = read_json(run_cli(
        tmp_path, "thesis", "close", "RELIANCE.NS",
        "--outcome", "LOSS", "--lesson", "Official filing resolved the event",
        "--exit-reason", "risk_exit", "--timing", "early",
        "--event-outcome", "YES",
    ))
    assert resolved["status"] == "CLOSED"
    assert resolved["brier_component"] == 0.1225


def test_no_trade_is_a_first_class_sourced_decision(tmp_path):
    run_cli(tmp_path, "init")
    result = read_json(run_cli(
        tmp_path, "decision", "record", "NO_TRADE",
        "--rationale", "No setup cleared evidence, expected-value, liquidity, and risk gates.",
        "--sources", "https://www.nseindia.com/market-data",
    ))
    assert result["action"] == "NO_TRADE"
    with sqlite3.connect(tmp_path / "portfolio.db") as conn:
        assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 1


def test_evidence_is_scored_for_accuracy_not_portfolio_outcome(tmp_path):
    run_cli(tmp_path, "init")
    added = read_json(run_cli(
        tmp_path, "evidence", "add", "--ticker", "RELIANCE.NS",
        "--claim", "Board meeting is scheduled for October 15",
        "--source", "https://www.nseindia.com/filing",
        "--tier", "1", "--published-at", "2026-07-22T09:00:00+05:30",
    ))
    resolved = read_json(run_cli(
        tmp_path, "evidence", "resolve", added["evidence_id"],
        "--outcome", "ACCURATE", "--note", "Confirmed by the filed results calendar",
    ))
    assert resolved["status"] == "ACCURATE"
    listed = read_json(run_cli(tmp_path, "evidence", "list"))
    assert listed[0]["status"] == "ACCURATE"
    rewrite = run_cli(
        tmp_path, "evidence", "resolve", added["evidence_id"],
        "--outcome", "INACCURATE", "--note", "Attempt to rewrite history",
        check=False,
    )
    assert rewrite.returncode == 1
    assert "already resolved" in rewrite.stderr


def test_corporate_actions_adjust_cash_and_positions_then_require_new_quote(tmp_path):
    open_long(tmp_path)
    ex_date = datetime.now(IST).date().isoformat()
    run_cli(
        tmp_path, "corporate-action", "DIVIDEND", "RELIANCE.NS",
        "--amount-per-share", "2", "--source", "https://www.nseindia.com/dividend",
        "--ex-date", ex_date,
    )
    assert status(tmp_path)["cash"] == 99017.75
    run_cli(
        tmp_path, "corporate-action", "SPLIT", "RELIANCE.NS",
        "--ratio", "2", "--source", "https://www.nseindia.com/split",
        "--ex-date", ex_date,
    )
    blocked = run_cli(tmp_path, "status", check=False)
    assert "refresh its quote" in blocked.stderr
    blocked_trade = run_cli(
        tmp_path, "trade", "SELL", "RELIANCE.NS", 1, 100,
        "Old pre-split quote must not execute", check=False,
    )
    assert "after the recorded corporate action" in blocked_trade.stderr
    quote(tmp_path, price=50)
    holding = status(tmp_path)["holdings"][0]
    assert holding["shares"] == 20
    assert holding["avg_cost_basis"] == 50.05


def test_review_uses_tri_active_return_and_does_not_mislabel_alpha(tmp_path):
    run_cli(tmp_path, "init")
    quote(tmp_path, "NIFTY50-TRI", 30000)
    run_cli(tmp_path, "snapshot")
    quote(tmp_path, "NIFTY50-TRI", 30300)
    review = read_json(run_cli(tmp_path, "review"))
    assert review["benchmark"] == "NIFTY50-TRI"
    assert review["benchmark_return_pct"] == 1.0
    assert review["active_return_pct"] == -1.0
    assert review["alpha_pct"] is None


def test_historical_simulation_is_labeled_replay_and_cost_adjusted(tmp_path):
    run_cli(tmp_path, "init")
    with sqlite3.connect(tmp_path / "portfolio.db") as conn:
        conn.executemany(
            "INSERT INTO historical_prices(ticker,date,close,volume) VALUES (?,?,?,?)",
            [("RELIANCE.NS", "2026-01-01", 100, 10), ("RELIANCE.NS", "2026-02-01", 110, 10)],
        )
    replay = read_json(run_cli(
        tmp_path, "learn", "historical", "simulate", "RELIANCE.NS",
        "--direction", "LONG", "--entry", "2026-01-01", "--exit", "2026-02-01",
    ))
    assert replay["analysis_type"] == "EX_POST_PRICE_REPLAY_NOT_BACKTEST"
    assert replay["gross_return_pct"] == 10.0
    assert replay["net_return_pct"] < replay["gross_return_pct"]
    assert replay["limitations"]


def test_historical_analysis_flags_pullback_without_calling_it_a_buy_signal(tmp_path):
    run_cli(tmp_path, "init")
    start = datetime(2026, 1, 1)
    closes = [100 + index for index in range(50)] + [148, 145, 141, 137, 133]
    with sqlite3.connect(tmp_path / "portfolio.db") as conn:
        conn.executemany(
            "INSERT INTO historical_prices(ticker,date,close,volume) VALUES (?,?,?,?)",
            [
                (
                    "RELIANCE.NS",
                    (start + timedelta(days=index)).date().isoformat(),
                    close,
                    10,
                )
                for index, close in enumerate(closes)
            ],
        )
    analysis = read_json(run_cli(
        tmp_path, "learn", "historical", "analyze", "RELIANCE.NS",
    ))
    assert analysis["analysis_type"] == "HISTORICAL_PULLBACK_CONTEXT_NOT_BUY_SIGNAL"
    assert analysis["dip_flag"] is True
    assert analysis["pullback_from_52w_high_pct"] < -5
    assert "cannot authorize a trade" in analysis["decision_rule"]


def test_historical_analysis_requires_enough_data(tmp_path):
    run_cli(tmp_path, "init")
    result = run_cli(
        tmp_path, "learn", "historical", "analyze", "RELIANCE.NS", check=False,
    )
    assert result.returncode == 1
    assert "learn historical fetch RELIANCE.NS --years 2" in result.stderr


def test_export_includes_new_audit_tables(tmp_path):
    run_cli(tmp_path, "init")
    destination = tmp_path / "export.json"
    run_cli(tmp_path, "export", destination)
    exported = json.loads(destination.read_text())
    assert {
        "corporate_actions", "decisions", "evidence_claims",
        "candidate_evaluations", "candidate_outcomes", "opportunity_audits",
        "investor_profile", "market_adapters", "market_adapter_evidence",
    }.issubset(exported)


def test_parameter_updates_are_validated(tmp_path):
    run_cli(tmp_path, "init")
    updated = read_json(run_cli(
        tmp_path, "learn", "params", "set",
        "risk_per_thesis", "0.008", "max_positions", "6",
    ))
    assert updated["risk_per_thesis"] == 0.008
    assert updated["max_positions"] == 6
    rejected = run_cli(
        tmp_path, "learn", "params", "set", "made_up_limit", "2", check=False
    )
    assert rejected.returncode == 1
    assert "unknown strategy parameter" in rejected.stderr


def test_reset_clears_legacy_history_and_preserves_only_feed_definitions(tmp_path):
    open_long(tmp_path)
    with sqlite3.connect(tmp_path / "portfolio.db") as conn:
        conn.execute(
            """INSERT INTO intel_sources(
                   name, feed_url, source_type, enabled, added_at,
                   total_fetched, unique_count, duplicate_count, ticker_mentions,
                   reason_disabled
               ) VALUES (?, ?, 'rss', 0, ?, 20, 5, 15, 3, 'legacy score')""",
            ("Test Feed", "https://example.com/feed", datetime.now(timezone.utc).isoformat()),
        )
        conn.execute(
            """INSERT INTO intel_articles(
                   source_id, fingerprint, title, link, created_at
               ) VALUES (1, 'legacy', 'Old item', 'https://example.com/old', ?)""",
            (datetime.now(timezone.utc).isoformat(),),
        )
    result = read_json(run_cli(
        tmp_path, "reset", "--confirm", "RESET-HARPER",
    ))
    assert result["reset"] is True
    data = status(tmp_path)
    assert data["cash"] == 100000.0
    assert data["holdings"] == []
    assert data["return_pct"] == 0.0
    with sqlite3.connect(tmp_path / "portfolio.db") as conn:
        assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM theses").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM intel_articles").fetchone()[0] == 0
        source = conn.execute(
            "SELECT enabled,total_fetched,unique_count,duplicate_count,ticker_mentions,reason_disabled"
            " FROM intel_sources WHERE feed_url='https://example.com/feed'"
        ).fetchone()
        assert source == (1, 0, 0, 0, 0, None)


def test_maintenance_archives_cold_working_data_and_protects_active_evidence(tmp_path):
    run_cli(tmp_path, "init")
    set_thesis(tmp_path)
    old = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
    protected_url = "https://www.nseindia.com/filings/RELIANCE.NS"
    with sqlite3.connect(tmp_path / "portfolio.db") as conn:
        conn.execute(
            """INSERT INTO intel_articles
               (source_id, fingerprint, title, link, created_at)
               VALUES (1, 'protected', 'Protected item', ?, ?)""",
            (protected_url, old),
        )
        conn.execute(
            """INSERT INTO intel_articles
               (source_id, fingerprint, title, link, created_at)
               VALUES (1, 'cold', 'Cold item', 'https://example.com/cold', ?)""",
            (old,),
        )
        conn.execute(
            """INSERT INTO market_feed
               (source_type, observation, source_urls, created_at)
               VALUES ('macro', 'Old observation', 'https://example.com/feed', ?)""",
            (old,),
        )
        conn.execute(
            """INSERT INTO research_library
               (ticker, topic, findings, sources_json, created_at)
               VALUES ('TCS.NS', 'Old topic', 'Old finding', '[\"https://example.com/r\"]', ?)""",
            (old,),
        )
        conn.execute(
            """INSERT INTO quotes
               (ticker, price, source, asof, recorded_at)
               VALUES ('TCS.NS', 100, 'https://example.com/q', ?, ?)""",
            (old, old),
        )

    result = read_json(run_cli(tmp_path, "maintain"))
    assert result["archived_by_table"] == {
        "intel_articles": 1,
        "market_feed": 1,
        "research_library": 1,
        "quotes": 1,
        "historical_prices": 0,
    }
    assert result["status"]["archived_rows"] == 4
    with sqlite3.connect(tmp_path / "portfolio.db") as conn:
        links = {
            row[0] for row in conn.execute("SELECT link FROM intel_articles")
        }
        assert links == {protected_url}
    with sqlite3.connect(tmp_path / "archive.db") as conn:
        assert conn.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 4
        assert conn.execute(
            "SELECT COUNT(*) FROM dedupe_tombstones WHERE dedupe_key='cold'"
        ).fetchone()[0] == 1


def test_maintenance_dry_run_does_not_move_rows(tmp_path):
    run_cli(tmp_path, "init")
    old = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
    with sqlite3.connect(tmp_path / "portfolio.db") as conn:
        conn.execute(
            """INSERT INTO intel_articles
               (source_id, fingerprint, title, link, created_at)
               VALUES (1, 'cold', 'Cold item', 'https://example.com/cold', ?)""",
            (old,),
        )
    result = read_json(run_cli(tmp_path, "maintain", "--dry-run"))
    assert result["archived_rows"] == 1
    with sqlite3.connect(tmp_path / "portfolio.db") as conn:
        assert conn.execute("SELECT COUNT(*) FROM intel_articles").fetchone()[0] == 1


def test_candidate_funnel_records_ranks_and_binding_rejections(tmp_path):
    run_cli(tmp_path, "init")
    quote(tmp_path, "NIFTY50-TRI", 100)
    candidates = [
        ("RELIANCE.NS", 88, "QUALITY"),
        ("TCS.NS", 82, "MOMENTUM"),
        ("HDFCBANK.NS", 75, "VALUE"),
    ]
    for ticker, score, thesis_type in candidates:
        quote(tmp_path, ticker, 100)
        run_cli(
            tmp_path,
            "candidate", "screen", ticker,
            "--score", score,
            "--thesis-type", thesis_type,
            "--sources", f"https://www.nseindia.com/{ticker}",
        )

    ranked = read_json(run_cli(tmp_path, "candidate", "rank", "--top", 2))
    assert [row["ticker"] for row in ranked["top"]] == ["RELIANCE.NS", "TCS.NS"]

    run_cli(
        tmp_path,
        "candidate", "screen", "RELIANCE.NS",
        "--score", "91",
        "--thesis-type", "QUALITY",
        "--research-depth", "DEEP",
        "--status", "REJECTED",
        "--binding-rejection-gate", "valuation",
        "--gate-outcomes", '{"liquidity":"PASS","valuation":"FAIL"}',
        "--sources", "https://www.nseindia.com/RELIANCE.NS",
    )
    report = read_json(run_cli(tmp_path, "decision", "rejection-report"))
    assert report["deep_researched"] == 1
    assert report["deep_documentation_pct"] == 100.0
    assert report["most_common_rejection_gate"] == "valuation"
    assert report["rejected_by_one_gate"] == 1


def test_candidate_screen_accepts_run_id_for_batch_and_single_forms(tmp_path):
    run_cli(tmp_path, "init")
    run = read_json(run_cli(
        tmp_path, "run", "start", "2026-07-28", "--session", "open-pulse"
    ))
    run_id = str(run["run_id"])
    batch_path = tmp_path / "candidates.json"
    batch_path.write_text(json.dumps([{
        "ticker": "TCS.NS",
        "thesis_type": "QUALITY",
        "research_depth": "SCREENED",
        "status": "WATCHLIST",
        "preliminary_score": 72,
        "quote_price": 3000,
        "quote_source": "https://www.nseindia.com/TCS.NS",
        "quote_asof": "2026-07-28T10:00:00+05:30",
        "gate_outcomes": {"liquidity": "PASS"},
        "sources": ["https://www.nseindia.com/TCS.NS"],
        "snapshot": {"sector": "Technology"},
    }]))
    batch = read_json(run_cli(
        tmp_path, "candidate", "screen", "--input", str(batch_path),
        "--run-id", run_id,
    ))
    assert batch["recorded"] == 1

    single = read_json(run_cli(
        tmp_path, "candidate", "screen", "RELIANCE.NS",
        "--run-id", run_id,
        "--score", "78.5",
        "--thesis-type", "QUALITY",
        "--research-depth", "DEEP",
        "--status", "REJECTED",
        "--quote-price", "1500",
        "--quote-source", "https://www.nseindia.com/RELIANCE.NS",
        "--quote-asof", "2026-07-28T10:00:00+05:30",
        "--binding-rejection-gate", "AUTHORITATIVE_EVIDENCE",
        "--gate-outcomes", '{"authoritative_evidence":"FAIL"}',
        "--hard-gates", '{"QUOTE_TRADABILITY":"PASS","FINANCIAL_INTEGRITY":"PASS","SIZING_VALIDITY":"PASS","PORTFOLIO_RISK":"PASS","AUTHORITATIVE_EVIDENCE":"FAIL"}',
        "--score-components", '{"catalyst_clarity":70,"financial_quality":85,"valuation":75,"trend":65,"source_quality":55,"reward_risk":80,"portfolio_fit":75}',
        "--legacy-result", "REJECTED",
        "--sources", "https://www.nseindia.com/RELIANCE.NS",
        "--snapshot", '{"sector":"Energy"}',
    ))
    assert single["recorded"] == 1

    with sqlite3.connect(tmp_path / "portfolio.db") as conn:
        rows = conn.execute(
            "SELECT ticker, run_id, research_depth FROM candidate_evaluations ORDER BY id"
        ).fetchall()
    assert rows == [("TCS.NS", int(run_id), "SCREENED"), ("RELIANCE.NS", int(run_id), "DEEP")]


def test_candidate_rejection_requires_binding_gate(tmp_path):
    run_cli(tmp_path, "init")
    quote(tmp_path)
    blocked = run_cli(
        tmp_path,
        "candidate", "screen", "RELIANCE.NS",
        "--score", "80",
        "--status", "REJECTED",
        "--gate-outcomes", '{"valuation":"FAIL","trend":"FAIL"}',
        "--sources", "https://www.nseindia.com/RELIANCE.NS",
        check=False,
    )
    assert blocked.returncode == 1
    assert "binding_rejection_gate" in blocked.stderr


def test_candidate_outcomes_mark_forward_returns_and_tri_outperformance(tmp_path):
    run_cli(tmp_path, "init")
    evaluation_asof = "2026-01-01T10:00:00+05:30"
    run_cli(
        tmp_path,
        "candidate", "screen", "RELIANCE.NS",
        "--score", "85",
        "--status", "REJECTED",
        "--research-depth", "DEEP",
        "--binding-rejection-gate", "valuation",
        "--gate-outcomes", '{"valuation":"FAIL"}',
        "--quote-price", "100",
        "--quote-source", "https://www.nseindia.com/RELIANCE.NS",
        "--quote-asof", evaluation_asof,
        "--benchmark-price", "100",
        "--benchmark-source", "https://www.niftyindices.com/indices/equity/broad-based-indices/NIFTY-50",
        "--benchmark-asof", evaluation_asof,
        "--sources", "https://www.nseindia.com/RELIANCE.NS",
        "--evaluated-at", evaluation_asof,
    )
    start = datetime(2026, 1, 1)
    with sqlite3.connect(tmp_path / "portfolio.db") as conn:
        candidate_rows = []
        benchmark_rows = []
        for index in range(1, 21):
            date = (start + timedelta(days=index)).date().isoformat()
            candidate_rows.append(("RELIANCE.NS", date, 100 + index, 10))
            benchmark_rows.append(("NIFTY50-TRI", date, 100 + index * 0.5, 10))
        conn.executemany(
            "INSERT INTO historical_prices(ticker,date,close,volume) VALUES (?,?,?,?)",
            candidate_rows + benchmark_rows,
        )

    result = read_json(run_cli(
        tmp_path, "candidate", "mark-outcomes", "--as-of", "2026-02-01"
    ))
    assert result["marked"] == 3
    report = read_json(run_cli(tmp_path, "decision", "rejection-report"))
    assert report["forward_outperformance"]["5"]["marked"] == 1
    assert report["forward_outperformance"]["5"]["outperformed_pct"] == 100.0


def test_opportunity_audit_triggers_after_five_low_exposure_snapshots(tmp_path):
    run_cli(tmp_path, "init")
    with sqlite3.connect(tmp_path / "portfolio.db") as conn:
        for index in range(5):
            conn.execute(
                """INSERT INTO snapshots(cash,holdings_value,total,holdings_json,timestamp)
                   VALUES (?,?,?,?,?)""",
                (90_000, 10_000, 100_000, "[]", f"2026-01-0{index + 1}T10:00:00+00:00"),
            )
    result = read_json(run_cli(tmp_path, "candidate", "opportunity-audit"))
    assert result["triggered"] is True
    assert result["low_exposure_sessions"] == 5
    assert "funnel_too_narrow" in result["diagnostics"]

    with sqlite3.connect(tmp_path / "portfolio.db") as conn:
        stored = conn.execute(
            "SELECT triggered, diagnostics_json FROM opportunity_audits ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert stored[0] == 1
    assert "funnel_too_narrow" in stored[1]



def test_phase2_separates_event_and_investment_probabilities(tmp_path):
    run_cli(tmp_path, "init")
    quote(tmp_path)
    result = read_json(run_cli(
        tmp_path,
        "thesis", "set", "RELIANCE.NS",
        "--direction", "LONG", "--trade-style", "POSITION",
        "--confidence", "80", "--investment-success-probability", "55",
        "--horizon", "90d", "--target", "120",
        "--invalidation", "Numeric invalidation is confirmed",
        "--catalyst", "Quarterly results on 2026-10-15",
        "--variant", "Margins expand more than expected",
        "--sources", "https://www.nseindia.com/a,https://www.reuters.com/b",
        "--primary-sources", "https://www.nseindia.com/a",
        "--event", "Quarterly EBITDA margin exceeds 18 percent",
        "--resolution-date", (datetime.now(IST).date() + timedelta(days=90)).isoformat(),
        "--resolution-source", "https://www.nseindia.com/a",
        "--entry-reference", "100", "--invalidation-price", "95",
        "--sector", "Energy", "--counter-thesis", "Input costs rise",
        "--financial-summary", "Cash flow covers capex and leverage is stable.",
    ))
    assert result["event_confidence_pct"] == 80
    assert result["investment_success_probability_pct"] == 55
    with sqlite3.connect(tmp_path / "portfolio.db") as conn:
        row = conn.execute(
            "SELECT confidence, investment_success_probability, ev_model FROM theses"
        ).fetchone()
    assert row == (80, 55.0, "TARGET_STOP")


def test_phase2_scenario_ev_is_cost_adjusted(tmp_path):
    run_cli(tmp_path, "init")
    quote(tmp_path)
    result = read_json(run_cli(
        tmp_path,
        "thesis", "set", "RELIANCE.NS",
        "--direction", "LONG", "--trade-style", "POSITION",
        "--confidence", "70", "--investment-success-probability", "60",
        "--horizon", "90d", "--target", "120",
        "--invalidation", "Numeric invalidation is confirmed",
        "--catalyst", "Quarterly results on 2026-10-15",
        "--variant", "Margins expand more than expected",
        "--sources", "https://www.nseindia.com/a,https://www.reuters.com/b",
        "--primary-sources", "https://www.nseindia.com/a",
        "--event", "Quarterly EBITDA margin exceeds 18 percent",
        "--resolution-date", (datetime.now(IST).date() + timedelta(days=90)).isoformat(),
        "--resolution-source", "https://www.nseindia.com/a",
        "--entry-reference", "100", "--invalidation-price", "95",
        "--sector", "Energy", "--counter-thesis", "Input costs rise",
        "--financial-summary", "Cash flow covers capex and leverage is stable.",
        "--bear-return-pct", "-8", "--base-return-pct", "6", "--bull-return-pct", "20",
        "--bear-probability", "20", "--base-probability", "50", "--bull-probability", "30",
    ))
    assert result["ev_model"] == "SCENARIO"
    assert result["expected_move_pct"] == pytest.approx(6.95, abs=0.01)


def test_phase2_hard_gates_and_weighted_score_run_in_shadow_mode(tmp_path):
    run_cli(tmp_path, "init")
    quote(tmp_path)
    hard_gates = {gate: "PASS" for gate in (
        "QUOTE_TRADABILITY", "FINANCIAL_INTEGRITY", "SIZING_VALIDITY",
        "PORTFOLIO_RISK", "AUTHORITATIVE_EVIDENCE",
    )}
    components = {
        "catalyst_clarity": 80, "financial_quality": 80, "valuation": 80,
        "trend": 80, "source_quality": 80, "reward_risk": 80, "portfolio_fit": 80,
    }
    read_json(run_cli(
        tmp_path, "candidate", "screen", "RELIANCE.NS", "--score", "50",
        "--quote-price", "100", "--quote-source", "https://www.nseindia.com/q",
        "--sources", "https://www.nseindia.com/q",
        "--hard-gates", json.dumps(hard_gates),
        "--score-components", json.dumps(components),
        "--legacy-result", "REJECTED", "--status", "REJECTED",
        "--binding-rejection-gate", "legacy_expected_value",
    ))
    rows = read_json(run_cli(tmp_path, "candidate", "list"))
    assert rows[0]["hard_gate_pass"] is True
    assert rows[0]["weighted_score"] == 80.0
    assert rows[0]["legacy_result"] == "REJECTED"
    assert rows[0]["shadow_recommendation"] == "APPROVED"
    report = read_json(run_cli(tmp_path, "decision", "comparison-report"))
    assert report["mode"] == "SHADOW_ONLY"
    assert report["transitions"]["REJECTED->APPROVED"] == 1


def test_phase2_failed_hard_gate_cannot_receive_shadow_approval(tmp_path):
    run_cli(tmp_path, "init")
    quote(tmp_path)
    hard_gates = {gate: "PASS" for gate in (
        "QUOTE_TRADABILITY", "FINANCIAL_INTEGRITY", "SIZING_VALIDITY",
        "PORTFOLIO_RISK", "AUTHORITATIVE_EVIDENCE",
    )}
    hard_gates["FINANCIAL_INTEGRITY"] = "FAIL"
    components = {name: 100 for name in (
        "catalyst_clarity", "financial_quality", "valuation", "trend",
        "source_quality", "reward_risk", "portfolio_fit",
    )}
    read_json(run_cli(
        tmp_path, "candidate", "screen", "RELIANCE.NS", "--score", "100",
        "--quote-price", "100", "--quote-source", "https://www.nseindia.com/q",
        "--sources", "https://www.nseindia.com/q",
        "--hard-gates", json.dumps(hard_gates),
        "--score-components", json.dumps(components),
    ))
    rows = read_json(run_cli(tmp_path, "candidate", "list"))
    assert rows[0]["hard_gate_pass"] is False
    assert rows[0]["shadow_recommendation"] == "REJECTED"


def test_quality_thesis_uses_review_contract_without_binary_event(tmp_path):
    run_cli(tmp_path, "init")
    quote(tmp_path, "TCS.NS", 100)
    review_date = (datetime.now(IST).date() + timedelta(days=90)).isoformat()
    result = read_json(run_cli(
        tmp_path, "thesis", "set", "TCS.NS",
        "--direction", "LONG", "--trade-style", "POSITION",
        "--thesis-type", "QUALITY", "--confidence", "65", "--horizon", "180d",
        "--target", "120", "--invalidation", "Cash conversion deteriorates",
        "--catalyst", "Quarterly review", "--variant", "ROCE compounds faster",
        "--sources", "https://www.nseindia.com/a,https://www.reuters.com/b",
        "--primary-sources", "https://www.nseindia.com/a",
        "--review-date", review_date, "--quality-trajectory", "ROCE and FCF conversion improve",
        "--entry-reference", "100", "--invalidation-price", "94", "--sector", "IT",
        "--counter-thesis", "Growth slows", "--financial-summary", "Strong balance sheet",
        "--investment-success-probability", "60",
    ))
    assert result["thesis_type"] == "QUALITY"
    assert result["thesis_contract"]["quality_trajectory"]
    with sqlite3.connect(tmp_path / "portfolio.db") as conn:
        row = conn.execute("SELECT forecast_event, thesis_type, review_date FROM theses WHERE ticker='TCS.NS'").fetchone()
    assert row == (None, "QUALITY", review_date)


def test_starter_position_is_capped_and_add_requires_confirmation(tmp_path):
    run_cli(tmp_path, "init")
    quote(tmp_path)
    set_thesis(tmp_path)
    blocked = run_cli(tmp_path, "trade", "BUY", "RELIANCE.NS", 31, 100,
                      "Too large starter", "--starter", check=False)
    assert blocked.returncode == 1
    assert "starter position exceeds" in blocked.stderr
    run_cli(tmp_path, "trade", "BUY", "RELIANCE.NS", 20, 100,
            "Starter entry", "--starter")
    add = run_cli(tmp_path, "trade", "BUY", "RELIANCE.NS", 1, 100,
                  "Unconfirmed add", check=False)
    assert add.returncode == 1
    assert "confirmation-source" in add.stderr
    confirmed = read_json(run_cli(
        tmp_path, "trade", "BUY", "RELIANCE.NS", 1, 100, "Confirmed add",
        "--confirmation-source", "https://www.nseindia.com/confirmation",
    ))
    assert confirmed["confirmation_source"].startswith("https://")


def test_exposure_regime_and_cash_reason_are_reported(tmp_path):
    run_cli(tmp_path, "init")
    regime = read_json(run_cli(tmp_path, "regime", "set", "DEFENSIVE", "--reason", "Weak breadth"))
    assert regime["min_exposure_pct"] == 25.0
    run_cli(
        tmp_path, "decision", "record", "NO_TRADE",
        "--rationale", "Waiting for evidence", "--sources", "https://www.nseindia.com/market-data",
        "--cash-reason", "AWAITING_CONFIRMATION",
    )
    data = status(tmp_path)
    assert data["exposure_regime"]["name"] == "DEFENSIVE"
    assert data["latest_cash_reason"]["cash_reason"] == "AWAITING_CONFIRMATION"


def test_schedule_diagnostics_and_versioned_audit_records(tmp_path):
    run_cli(tmp_path, "init")
    schedule = read_json(run_cli(tmp_path, "diagnostics", "schedule"))
    assert schedule["schedule_version"] == "2026.1"
    assert [item["time"] for item in schedule["sessions"]] == [
        "08:55", "09:15", "09:20", "12:30", "15:20", "15:35"
    ]
    run = read_json(run_cli(
        tmp_path, "run", "start", "2026-07-27", "--session", "preparation"
    ))
    assert run["decision_model_version"] == "3.0-multi-thesis-shadow"
    assert run["parameter_version"].startswith("params-")
    decision = read_json(run_cli(
        tmp_path, "decision", "record", "NO_TRADE",
        "--rationale", "Preparation found no executable setup.",
        "--sources", "https://www.nseindia.com/market-data",
        "--run-id", run["run_id"],
    ))
    with sqlite3.connect(tmp_path / "portfolio.db") as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT decision_model_version, parameter_version FROM decisions WHERE id=?",
            (decision["decision_id"],),
        ).fetchone()
    assert row["decision_model_version"] == "3.0-multi-thesis-shadow"
    assert row["parameter_version"].startswith("params-")


def test_diagnostics_config_reports_versions_and_deviations(tmp_path):
    run_cli(tmp_path, "init")
    baseline = read_json(run_cli(tmp_path, "diagnostics", "config"))
    assert baseline["schema_version"] >= 14
    assert baseline["decision_model_version"]
    assert baseline["parameter_version"].startswith("params-")
    assert baseline["deviations_from_defaults"] == {}

    run_cli(tmp_path, "learn", "params", "set", "risk_per_thesis", "0.008")
    changed = read_json(run_cli(tmp_path, "diagnostics", "config"))
    assert changed["deviations_from_defaults"]["risk_per_thesis"] == {
        "default": 0.01,
        "effective": 0.008,
    }
    assert changed["parameter_version"] != baseline["parameter_version"]


def test_learning_report_combines_cash_rejections_and_process_versions(tmp_path):
    run_cli(tmp_path, "init")
    quote(tmp_path, "NIFTY50-TRI", 30000)
    run_cli(tmp_path, "snapshot")
    quote(tmp_path, "NIFTY50-TRI", 30300)
    run_cli(tmp_path, "snapshot")
    run_cli(
        tmp_path,
        "decision",
        "record",
        "NO_TRADE",
        "--rationale",
        "No candidate cleared the opportunity threshold.",
        "--sources",
        "https://www.nseindia.com/market-data",
        "--cash-reason",
        "NO_QUALIFYING_SETUP",
    )
    report = read_json(run_cli(tmp_path, "learn", "report"))
    assert report["cash_drag"]["available"] is True
    assert report["cash_drag"]["benchmark_return_pct"] == 1.0
    assert report["cash_reasons"]["NO_QUALIFYING_SETUP"] == 1
    assert report["process_compliance"]["automatic_parameter_adaptation"].startswith("LOCKED")
    assert "false_negative_by_horizon" in report["rejections"]


def test_intel_quality_reports_queue_age_and_diagnostic_recommendation(tmp_path):
    run_cli(tmp_path, "init")
    old = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()
    with sqlite3.connect(tmp_path / "portfolio.db") as conn:
        conn.execute(
            """INSERT INTO intel_sources(
                   name, feed_url, source_type, enabled, added_at,
                   total_fetched, unique_count, duplicate_count, ticker_mentions,
                   relevance_pass_rate, relevance_checked, llm_rescued_count
               ) VALUES (?, ?, 'rss', 1, ?, 100, 5, 95, 0, 0.10, 60, 0)""",
            ("Noisy Feed", "https://example.com/feed", old),
        )
        source_id = conn.execute("SELECT id FROM intel_sources").fetchone()[0]
        conn.execute(
            """INSERT INTO intel_relevance_staging(
                   source_id, title, link, summary, staged_at, batch_id
               ) VALUES (?, 'Old item', 'https://example.com/a', '', ?, NULL)""",
            (source_id, old),
        )
    quality = read_json(run_cli(tmp_path, "intel-sources", "quality"))
    assert quality["queue"]["backlog_alert"] is True
    assert quality["queue"]["oldest_age_hours"] >= 29
    assert quality["sources"][0]["recommendation"] in {
        "REVIEW_LOW_RELEVANCE", "REVIEW_DUPLICATE_HEAVY"
    }


def test_release_preflight_reports_clean_database_and_versions(tmp_path):
    run_cli(tmp_path, "init")
    result = read_json(run_cli(tmp_path, "release", "preflight"))
    assert result["ready"] is True
    assert result["checks"]["database_integrity"] is True
    assert result["checks"]["schema_current"] is True
    assert result["decision_model_version"]
    assert result["parameter_version"].startswith("params-")
    assert result["schedule_version"]
    assert result["warnings"]  # benchmark is intentionally uninitialized


def test_release_verify_backup_and_controlled_clean_start(tmp_path):
    open_long(tmp_path)
    backup = tmp_path / "release-backup.db"
    clean = read_json(run_cli(
        tmp_path, "release", "clean-start",
        "--backup", backup,
        "--confirm", "START-HARPER-FRESH",
    ))
    assert clean["clean_start"] is True
    assert clean["backup"]["integrity"] == "ok"
    assert backup.exists()
    assert clean["portfolio_counts"] == {
        "holdings": 0, "trades": 0, "theses": 0, "decisions": 0,
    }
    data = status(tmp_path)
    assert data["cash"] == 100000.0
    verified = read_json(run_cli(tmp_path, "release", "verify-backup", backup))
    assert verified["valid"] is True
    with sqlite3.connect(backup) as conn:
        assert conn.execute("SELECT COUNT(*) FROM holdings").fetchone()[0] == 1


def test_release_clean_start_requires_explicit_confirmation(tmp_path):
    run_cli(tmp_path, "init")
    result = run_cli(
        tmp_path, "release", "clean-start",
        "--backup", tmp_path / "blocked.db",
        "--confirm", "WRONG",
        check=False,
    )
    assert result.returncode == 1
    assert "START-HARPER-FRESH" in result.stderr
    assert not (tmp_path / "blocked.db").exists()
