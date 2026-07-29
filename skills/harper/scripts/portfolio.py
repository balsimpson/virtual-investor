#!/usr/bin/env python3
"""Harper virtual portfolio engine.

All money is virtual. Quotes must be supplied with a public source URL before
valuation or trading; the engine never invents market data.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import hashlib
from collections import Counter
import json
import math
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

LEGACY_INITIAL_CASH = 100_000.0
SUGGESTED_INITIAL_CASH_BY_CURRENCY = {
    "AUD": 10_000.0,
    "BRL": 100_000.0,
    "CAD": 10_000.0,
    "CHF": 10_000.0,
    "CNY": 100_000.0,
    "EUR": 10_000.0,
    "GBP": 10_000.0,
    "HKD": 100_000.0,
    "IDR": 100_000_000.0,
    "INR": 100_000.0,
    "JPY": 1_000_000.0,
    "KRW": 10_000_000.0,
    "MXN": 100_000.0,
    "NZD": 10_000.0,
    "SGD": 10_000.0,
    "THB": 100_000.0,
    "TWD": 100_000.0,
    "USD": 10_000.0,
    "VND": 100_000_000.0,
    "ZAR": 100_000.0,
}
BENCHMARK_TICKER = "NIFTY50-TRI"
MAX_POSITION_WEIGHT = 0.20
MIN_HOLD_DAYS = 0
SCHEMA_VERSION = 19
ONBOARDING_VERSION = 4
SUPPORTED_MARKET = "INDIA_NSE_BSE"
SUPPORTED_CURRENCY = "INR"
INDIA_TZ = timezone(timedelta(hours=5, minutes=30))
DEFAULT_DB = Path.home() / ".hermes" / "data" / "virtual-investor" / "portfolio.db"
DEFAULT_ARCHIVE_DB = DEFAULT_DB.with_name("archive.db")
DEFAULT_HERMES_STATE_DB = Path.home() / ".hermes" / "state.db"
DEFAULT_HERMES_CRON_JOBS = Path.home() / ".hermes" / "cron" / "jobs.json"
DEFAULT_MARKET_OPEN = "09:15"
DEFAULT_MARKET_CLOSE = "15:30"
DEFAULT_INTRADAY_EXIT = "15:20"
LIFECYCLE_POLICY_VERSION = 1
HOT_INTEL_DAYS = 7
HOT_INTEL_ROWS = 500
HOT_MARKET_FEED_DAYS = 14
HOT_MARKET_FEED_ROWS = 200
HOT_RESEARCH_ROWS = 250
HOT_RESEARCH_PER_TICKER = 20
HOT_QUOTES_DAYS = 30
HOT_QUOTES_PER_TICKER = 200
HOT_PRICES_PER_TICKER = 260
ARCHIVE_RETENTION_DAYS = {
    "intel_articles": 90,
    "market_feed": 90,
    "research_library": 180,
    "quotes": 90,
    "historical_prices": 365,
}
FINGERPRINT_RETENTION_DAYS = 365
PARAM_DEFAULTS = {
    "max_position_weight": MAX_POSITION_WEIGHT,
    "max_gross_exposure": 1.00,
    "max_sector_weight": 0.30,
    "risk_per_thesis": 0.01,
    "max_portfolio_heat": 0.05,
    "gap_buffer_pct": 0.01,
    "quote_max_age_hours": 0.25,
    "max_quote_deviation_bps": 25.0,
    "fee_bps": 12.5,
    "slippage_large_bps": 10.0,
    "slippage_mid_bps": 30.0,
    "slippage_small_bps": 80.0,
    "intraday_entry_cutoff_minutes": 30.0,
    "max_positions": 8.0,
    "min_forecasts_for_adaptation": 30.0,
    "starter_position_weight": 0.03,
}

CANDIDATE_THESIS_TYPES = ("CATALYST", "QUALITY", "VALUE", "MOMENTUM")
CANDIDATE_DEPTHS = ("SCREENED", "RANKED", "DEEP")
CANDIDATE_STATUSES = ("WATCHLIST", "REJECTED", "APPROVED")
CANDIDATE_OUTCOME_HORIZONS = (5, 10, 20)
SCORING_MODEL_VERSION = "2.0-shadow"
DECISION_MODEL_VERSION = "3.0-multi-thesis-shadow"
OPERATING_SCHEDULE_VERSION = "2026.1"
DASHBOARD_CONTRACT_VERSION = 2
OPERATING_SESSIONS = (
    {"label": "preparation", "time": "08:55", "purpose": "Research, risk and market-session preparation; no trade."},
    {"label": "open-pulse", "time": "09:15", "purpose": "Market-open reconnaissance; no trade."},
    {"label": "open-execution", "time": "09:20", "purpose": "First eligible execution review."},
    {"label": "midday-review", "time": "12:30", "purpose": "Holdings, candidates and risk review."},
    {"label": "final-decisions", "time": "15:20", "purpose": "Intraday exits and final trading decisions."},
    {"label": "closing-snapshot", "time": "15:35", "purpose": "Post-close marks, NAV snapshot and maintenance; no trade."},
)
HARD_GATE_CLASSES = (
    "QUOTE_TRADABILITY",
    "FINANCIAL_INTEGRITY",
    "SIZING_VALIDITY",
    "PORTFOLIO_RISK",
    "AUTHORITATIVE_EVIDENCE",
)
SCORE_WEIGHTS = {
    "catalyst_clarity": 0.15,
    "financial_quality": 0.20,
    "valuation": 0.15,
    "trend": 0.10,
    "source_quality": 0.15,
    "reward_risk": 0.15,
    "portfolio_fit": 0.10,
}
SCORE_THRESHOLD = 70.0
GENERIC_COST_FALLBACK = {
    "fee_bps": 25.0,
    "slippage_large_bps": 25.0,
    "slippage_mid_bps": 50.0,
    "slippage_small_bps": 100.0,
    "source": "CONSERVATIVE_FALLBACK_UNTIL_MARKET_EVIDENCE_IS_RECORDED",
}
INDIA_ADAPTER = {
    "market_id": SUPPORTED_MARKET,
    "display_name": "India (NSE/BSE equities)",
    "market_timezone": "Asia/Kolkata",
    "native_currency": "INR",
    "benchmark_ticker": BENCHMARK_TICKER,
    "ticker_pattern": r"^(?:[A-Z0-9&.-]+\.(?:NS|BO)|\^NSEI|\^BSESN|NIFTY50-TRI)$",
    "session_schedule": {
        "market_open": DEFAULT_MARKET_OPEN,
        "market_close": DEFAULT_MARKET_CLOSE,
        "intraday_exit": DEFAULT_INTRADAY_EXIT,
        "sessions": list(OPERATING_SESSIONS),
    },
    "cost_model": {
        "fee_bps": 12.5,
        "slippage_large_bps": 10.0,
        "slippage_mid_bps": 30.0,
        "slippage_small_bps": 80.0,
        "source": "VIRTUAL_INVESTOR_AUDITED_BASELINE",
    },
    "capabilities": {
        "quotes": True,
        "historical_prices": True,
        "market_calendar": True,
        "benchmark": True,
        "cost_model": True,
        "regulatory_context": True,
    },
}


def _market_id(value: str) -> str:
    label = value.strip()
    if not label:
        raise ValueError("market cannot be empty")
    compact = "".join(character for character in label.upper() if character.isalnum())
    if compact in {
        "INDIA", "INDIAN", "NSE", "BSE", "NSEBSE", "INDIANSEBSE",
        "INDIANEQUITIES", "INDIANSTOCKMARKET",
    } or label.upper() == SUPPORTED_MARKET:
        return SUPPORTED_MARKET
    identifier = re.sub(r"[^A-Z0-9]+", "_", label.upper()).strip("_")
    if not identifier or len(identifier) > 64:
        raise ValueError("market must produce an identifier of 1 to 64 characters")
    return identifier


def _currency_code(value: str) -> str:
    code = value.upper().strip()
    if not re.fullmatch(r"[A-Z]{3}", code):
        raise ValueError("currency must be a three-letter ISO 4217 code")
    return code


def _timezone_name(value: str) -> str:
    name = value.strip()
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(
            "timezone must be a valid IANA name such as Asia/Kolkata or America/New_York"
        ) from exc
    return name


def _adapter_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    result = dict(row)
    for field in (
        "session_schedule_json", "cost_model_json", "capabilities_json", "sources_json"
    ):
        result[field.removesuffix("_json")] = json.loads(result.pop(field) or "{}")
    return result


def _get_adapter(conn: sqlite3.Connection, market_id: str | None = None) -> dict | None:
    if market_id is None:
        profile = conn.execute(
            "SELECT market FROM investor_profile WHERE id = 1"
        ).fetchone()
        market_id = str(profile["market"] or SUPPORTED_MARKET) if profile else SUPPORTED_MARKET
    if not market_id:
        return None
    return _adapter_dict(conn.execute(
        "SELECT * FROM market_adapters WHERE market_id = ?", (market_id,)
    ).fetchone())


def _ensure_discovery_adapter(
    conn: sqlite3.Connection,
    market_id: str,
    display_name: str,
    *,
    timezone_name: str | None = None,
    currency: str | None = None,
) -> dict:
    existing = _get_adapter(conn, market_id)
    if existing:
        return existing
    stamp = now()
    conn.execute(
        """INSERT INTO market_adapters(
               market_id, display_name, market_timezone, native_currency,
               benchmark_ticker, ticker_pattern, session_schedule_json,
               cost_model_json, capabilities_json, sources_json, status,
               version, created_at, updated_at
           ) VALUES (?, ?, ?, ?, NULL, NULL, '{}', '{}', '{}', '{}',
                     'DISCOVERY', 1, ?, ?)""",
        (market_id, display_name.strip(), timezone_name, currency, stamp, stamp),
    )
    return _get_adapter(conn, market_id) or {}


def _market_timezone(conn: sqlite3.Connection) -> ZoneInfo:
    adapter = _get_adapter(conn)
    if adapter and adapter.get("market_timezone"):
        return ZoneInfo(str(adapter["market_timezone"]))
    profile = conn.execute(
        "SELECT user_timezone FROM investor_profile WHERE id = 1"
    ).fetchone()
    if profile and profile["user_timezone"]:
        return ZoneInfo(str(profile["user_timezone"]))
    return ZoneInfo("UTC")


def _market_today(conn: sqlite3.Connection):
    return datetime.now(_market_timezone(conn)).date()


def _benchmark_ticker(conn: sqlite3.Connection) -> str | None:
    adapter = _get_adapter(conn)
    return str(adapter["benchmark_ticker"]) if adapter and adapter.get("benchmark_ticker") else None


def _reporting_currency(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT base_currency FROM investor_profile WHERE id = 1"
    ).fetchone()
    return str(row["base_currency"] or "CUR") if row else "CUR"


def _money(conn: sqlite3.Connection, value: float) -> str:
    code = _reporting_currency(conn)
    symbols = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥"}
    symbol = symbols.get(code)
    return f"{symbol}{value:,.2f}" if symbol else f"{code} {value:,.2f}"


def _suggested_initial_cash(currency: str | None) -> float:
    return SUGGESTED_INITIAL_CASH_BY_CURRENCY.get(
        str(currency or "").upper(), 10_000.0
    )


def _chosen_initial_cash(conn: sqlite3.Connection) -> float:
    row = conn.execute(
        "SELECT initial_cash, base_currency FROM investor_profile WHERE id = 1"
    ).fetchone()
    if row and row["initial_cash"] is not None:
        return float(row["initial_cash"])
    return state_float(
        conn,
        "initial_cash",
        _suggested_initial_cash(row["base_currency"] if row else None),
    )


def _effective_cost_bps(conn: sqlite3.Connection, key: str) -> float:
    adapter = _get_adapter(conn)
    model = adapter.get("cost_model", {}) if adapter else {}
    if key in model:
        return float(model[key])
    if adapter and adapter["market_id"] != SUPPORTED_MARKET:
        return float(GENERIC_COST_FALLBACK[key])
    return state_float(conn, f"strategy_{key}", PARAM_DEFAULTS[key])


def _validate_ticker(conn: sqlite3.Connection, ticker: str) -> str:
    normalized = ticker.upper().strip()
    if not normalized or len(normalized) > 32 or not re.fullmatch(r"[A-Z0-9.^=_:&-]+", normalized):
        raise ValueError(f"invalid market ticker {ticker!r}")
    adapter = _get_adapter(conn)
    pattern = str(adapter.get("ticker_pattern") or "") if adapter else ""
    if pattern and not re.fullmatch(pattern, normalized):
        market = adapter.get("display_name") or adapter.get("market_id")
        raise ValueError(f"ticker {ticker!r} does not match the {market} adapter")
    return normalized


def _validate_indian_ticker(ticker: str) -> str:
    """Compatibility name for callers; validation now comes from the active adapter."""
    with connect() as conn:
        return _validate_ticker(conn, ticker)


def db_path() -> Path:
    return Path(os.environ.get("VIRTUAL_INVESTOR_DB", DEFAULT_DB)).expanduser()


def archive_db_path() -> Path:
    return Path(
        os.environ.get("VIRTUAL_INVESTOR_ARCHIVE_DB", DEFAULT_ARCHIVE_DB)
    ).expanduser()


def hermes_state_db_path() -> Path:
    return Path(
        os.environ.get("VIRTUAL_INVESTOR_HERMES_STATE_DB", DEFAULT_HERMES_STATE_DB)
    ).expanduser()


def hermes_cron_jobs_path() -> Path:
    return Path(
        os.environ.get("VIRTUAL_INVESTOR_HERMES_CRON_JOBS", DEFAULT_HERMES_CRON_JOBS)
    ).expanduser()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def india_today():
    with connect() as conn:
        return _market_today(conn)


def _parse_hhmm(value: str, label: str) -> tuple[int, int]:
    try:
        parsed = datetime.strptime(value, "%H:%M")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be HH:MM in the market timezone") from exc
    return parsed.hour, parsed.minute


def _minutes_since_midnight(value: str, label: str) -> int:
    hour, minute = _parse_hhmm(value, label)
    return hour * 60 + minute


def _trade_style(horizon: str | None) -> str:
    prefix = (horizon or "").split(":", 1)[0].upper()
    if prefix not in {"INTRADAY", "POSITION"}:
        raise ValueError("thesis horizon is missing an INTRADAY or POSITION trade style")
    return prefix


def _parse_timestamp(value: str, label: str = "timestamp") -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _quote_age_hours(quote: sqlite3.Row) -> float:
    return max(0.0, (
        datetime.now(timezone.utc) - _parse_timestamp(quote["asof"], "quote as-of")
    ).total_seconds() / 3_600)


def _state_text(conn: sqlite3.Connection, key: str, default: str) -> str:
    row = conn.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
    return str(row["value"]) if row else default


def connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    ensure_schema(conn)
    return conn


def columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def ensure_schema(conn: sqlite3.Connection) -> None:
    existing_tables = {
        str(row["name"])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    upgrading_existing_portfolio = bool(
        {"state", "holdings", "trades"}.intersection(existing_tables)
    )
    previous_schema_version = 0
    if "state" in existing_tables:
        previous_version_row = conn.execute(
            "SELECT value FROM state WHERE key = 'schema_version'"
        ).fetchone()
        try:
            previous_schema_version = int(previous_version_row["value"])
        except (TypeError, ValueError):
            previous_schema_version = 0
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS holdings (
            ticker TEXT PRIMARY KEY,
            shares REAL NOT NULL,
            avg_cost_basis REAL NOT NULL,
            last_updated TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            action TEXT NOT NULL,
            shares REAL NOT NULL,
            price REAL NOT NULL,
            total REAL NOT NULL,
            reason TEXT NOT NULL,
            timestamp TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS decision_journal (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_type TEXT NOT NULL,
            content TEXT NOT NULL,
            timestamp TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cash REAL NOT NULL,
            holdings_value REAL NOT NULL,
            total REAL NOT NULL,
            holdings_json TEXT NOT NULL,
            timestamp TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS quotes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            price REAL NOT NULL CHECK(price > 0),
            source TEXT NOT NULL,
            asof TEXT NOT NULL,
            recorded_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_quotes_ticker_id ON quotes(ticker, id DESC);
        CREATE TABLE IF NOT EXISTS theses (
            ticker TEXT PRIMARY KEY,
            direction TEXT NOT NULL CHECK(direction = 'LONG'),
            confidence INTEGER NOT NULL CHECK(confidence BETWEEN 1 AND 99),
            horizon TEXT NOT NULL,
            target REAL NOT NULL CHECK(target > 0),
            invalidation TEXT NOT NULL,
            catalyst TEXT NOT NULL,
            variant_view TEXT NOT NULL,
            sources_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'ACTIVE',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_date TEXT NOT NULL,
            session_label TEXT DEFAULT '',
            status TEXT NOT NULL,
            report TEXT,
            created_at TEXT NOT NULL,
            completed_at TEXT,
            decision_model_version TEXT,
            parameter_version TEXT,
            schedule_version TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_runs_market_date ON runs(market_date);
        CREATE TABLE IF NOT EXISTS research_library (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            sector TEXT,
            topic TEXT NOT NULL,
            findings TEXT NOT NULL,
            sources_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            run_id INTEGER
        );
        CREATE TABLE IF NOT EXISTS learning_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period_start TEXT,
            period_end TEXT,
            summary TEXT NOT NULL,
            alpha_pct REAL,
            win_rate_pct REAL,
            brier_score REAL,
            calibration_drift REAL,
            lessons TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS source_scores (
            domain TEXT PRIMARY KEY,
            wins INTEGER DEFAULT 0,
            losses INTEGER DEFAULT 0,
            flats INTEGER DEFAULT 0,
            last_updated TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS market_feed (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_type TEXT NOT NULL,
            observation TEXT NOT NULL,
            source_urls TEXT NOT NULL,
            created_at TEXT NOT NULL,
            run_id INTEGER
        );
        CREATE TABLE IF NOT EXISTS intel_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            feed_url TEXT NOT NULL UNIQUE,
            source_type TEXT NOT NULL DEFAULT 'rss',
            enabled INTEGER NOT NULL DEFAULT 1,
            added_at TEXT NOT NULL,
            last_fetch_at TEXT,
            total_fetched INTEGER DEFAULT 0,
            unique_count INTEGER DEFAULT 0,
            duplicate_count INTEGER DEFAULT 0,
            ticker_mentions INTEGER DEFAULT 0,
            reason_disabled TEXT
        );
        CREATE TABLE IF NOT EXISTS intel_articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER NOT NULL,
            fingerprint TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            link TEXT NOT NULL,
            summary TEXT,
            source_domain TEXT,
            tickers TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_intel_articles_fp
            ON intel_articles(fingerprint);

        -- Relevance filtering tables
        CREATE TABLE IF NOT EXISTS intel_relevance_patterns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pattern TEXT UNIQUE NOT NULL,
            pattern_type TEXT NOT NULL DEFAULT 'entity'
                CHECK(pattern_type IN ('ticker','entity','keyword','global_event_chain')),
            weight INTEGER NOT NULL DEFAULT 10,
            source TEXT NOT NULL DEFAULT 'manual'
                CHECK(source IN ('manual','llm_rescue','harper_suggestion')),
            match_count INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            last_matched_at TEXT
        );
        CREATE TABLE IF NOT EXISTS intel_relevance_staging (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            link TEXT NOT NULL,
            summary TEXT,
            staged_at TEXT NOT NULL,
            batch_id TEXT
        );
        CREATE TABLE IF NOT EXISTS intel_relevance_batches (
            id TEXT PRIMARY KEY,
            total_articles INTEGER NOT NULL DEFAULT 0,
            passed INTEGER NOT NULL DEFAULT 0,
            rejected INTEGER NOT NULL DEFAULT 0,
            reviewed_for_patterns INTEGER NOT NULL DEFAULT 0,
            new_patterns INTEGER NOT NULL DEFAULT 0,
            input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_intel_articles_created
            ON intel_articles(created_at);
        CREATE TABLE IF NOT EXISTS historical_prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            date TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL NOT NULL,
            volume INTEGER DEFAULT 0,
            UNIQUE(ticker, date)
        );
        CREATE INDEX IF NOT EXISTS idx_hist_ticker_date ON historical_prices(ticker, date);
        CREATE TABLE IF NOT EXISTS evidence_claims (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT,
            claim TEXT NOT NULL,
            source_url TEXT NOT NULL,
            source_tier INTEGER NOT NULL CHECK(source_tier BETWEEN 1 AND 7),
            published_at TEXT,
            fetched_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'UNRESOLVED'
                CHECK(status IN ('UNRESOLVED', 'ACCURATE', 'INACCURATE')),
            resolution_note TEXT,
            resolved_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_evidence_source_status
            ON evidence_claims(source_url, status);
        CREATE TABLE IF NOT EXISTS corporate_actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            action_type TEXT NOT NULL CHECK(action_type IN ('DIVIDEND', 'SPLIT', 'BONUS')),
            amount_per_share REAL,
            ratio REAL,
            cash_effect REAL NOT NULL DEFAULT 0,
            source_url TEXT NOT NULL,
            ex_date TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            UNIQUE(ticker, action_type, ex_date, source_url)
        );
        CREATE TABLE IF NOT EXISTS decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER,
            action TEXT NOT NULL CHECK(action IN
                ('NO_TRADE', 'OPEN', 'ADD', 'REDUCE', 'CLOSE', 'INVALIDATE')),
            ticker TEXT,
            rationale TEXT NOT NULL,
            evidence_json TEXT NOT NULL,
            cash_reason TEXT,
            decision_model_version TEXT,
            parameter_version TEXT,
            timestamp TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS candidate_evaluations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER,
            ticker TEXT NOT NULL,
            thesis_type TEXT NOT NULL
                CHECK(thesis_type IN ('CATALYST', 'QUALITY', 'VALUE', 'MOMENTUM')),
            research_depth TEXT NOT NULL DEFAULT 'SCREENED'
                CHECK(research_depth IN ('SCREENED', 'RANKED', 'DEEP')),
            status TEXT NOT NULL DEFAULT 'WATCHLIST'
                CHECK(status IN ('WATCHLIST', 'REJECTED', 'APPROVED')),
            preliminary_score REAL NOT NULL CHECK(preliminary_score BETWEEN 0 AND 100),
            rank INTEGER,
            quote_price REAL NOT NULL CHECK(quote_price > 0),
            quote_source TEXT NOT NULL,
            quote_asof TEXT NOT NULL,
            benchmark_price REAL,
            benchmark_source TEXT,
            benchmark_asof TEXT,
            binding_rejection_gate TEXT,
            gate_outcomes_json TEXT NOT NULL DEFAULT '{}',
            sources_json TEXT NOT NULL,
            snapshot_json TEXT NOT NULL DEFAULT '{}',
            evaluated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_candidate_eval_run_rank
            ON candidate_evaluations(run_id, rank, preliminary_score DESC);
        CREATE INDEX IF NOT EXISTS idx_candidate_eval_ticker_time
            ON candidate_evaluations(ticker, evaluated_at DESC);
        CREATE INDEX IF NOT EXISTS idx_candidate_eval_status_depth
            ON candidate_evaluations(status, research_depth);
        CREATE TABLE IF NOT EXISTS candidate_outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            evaluation_id INTEGER NOT NULL,
            horizon_sessions INTEGER NOT NULL CHECK(horizon_sessions IN (5, 10, 20)),
            outcome_date TEXT NOT NULL,
            candidate_price REAL NOT NULL CHECK(candidate_price > 0),
            benchmark_price REAL,
            candidate_return_pct REAL NOT NULL,
            benchmark_return_pct REAL,
            active_return_pct REAL,
            marked_at TEXT NOT NULL,
            UNIQUE(evaluation_id, horizon_sessions),
            FOREIGN KEY(evaluation_id) REFERENCES candidate_evaluations(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_candidate_outcome_horizon
            ON candidate_outcomes(horizon_sessions, active_return_pct);
        CREATE TABLE IF NOT EXISTS opportunity_audits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            triggered INTEGER NOT NULL CHECK(triggered IN (0, 1)),
            sessions_required INTEGER NOT NULL,
            sessions_observed INTEGER NOT NULL,
            low_exposure_sessions INTEGER NOT NULL,
            exposure_threshold_pct REAL NOT NULL,
            average_exposure_pct REAL,
            screened_candidates INTEGER NOT NULL DEFAULT 0,
            ranked_candidates INTEGER NOT NULL DEFAULT 0,
            deep_candidates INTEGER NOT NULL DEFAULT 0,
            approved_candidates INTEGER NOT NULL DEFAULT 0,
            rejected_candidates INTEGER NOT NULL DEFAULT 0,
            top_rejection_gate TEXT,
            diagnostics_json TEXT NOT NULL DEFAULT '[]',
            window_start TEXT,
            window_end TEXT,
            generated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS llm_usage (
            usage_key TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            root_session_id TEXT NOT NULL,
            job_id TEXT,
            job_name TEXT NOT NULL,
            source TEXT NOT NULL,
            model TEXT NOT NULL,
            provider TEXT NOT NULL,
            task TEXT,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            api_calls INTEGER NOT NULL DEFAULT 0,
            input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            cache_read_tokens INTEGER NOT NULL DEFAULT 0,
            cache_write_tokens INTEGER NOT NULL DEFAULT 0,
            reasoning_tokens INTEGER NOT NULL DEFAULT 0,
            estimated_cost_usd REAL NOT NULL DEFAULT 0,
            actual_cost_usd REAL NOT NULL DEFAULT 0,
            cost_status TEXT NOT NULL DEFAULT 'unknown',
            imported_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_llm_usage_started
            ON llm_usage(started_at DESC);
        CREATE INDEX IF NOT EXISTS idx_llm_usage_session
            ON llm_usage(session_id);
        CREATE TABLE IF NOT EXISTS market_adapters (
            market_id TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            market_timezone TEXT,
            native_currency TEXT,
            benchmark_ticker TEXT,
            ticker_pattern TEXT,
            session_schedule_json TEXT NOT NULL DEFAULT '{}',
            cost_model_json TEXT NOT NULL DEFAULT '{}',
            capabilities_json TEXT NOT NULL DEFAULT '{}',
            sources_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'DISCOVERY'
                CHECK(status IN ('DISCOVERY', 'LIMITED', 'OPERATIONAL')),
            version INTEGER NOT NULL DEFAULT 1,
            last_validated_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS market_adapter_evidence (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_id TEXT NOT NULL,
            evidence_type TEXT NOT NULL,
            value_json TEXT NOT NULL,
            source_url TEXT NOT NULL,
            effective_at TEXT,
            recorded_at TEXT NOT NULL,
            FOREIGN KEY(market_id) REFERENCES market_adapters(market_id)
        );
        CREATE INDEX IF NOT EXISTS idx_adapter_evidence_market_type
            ON market_adapter_evidence(market_id, evidence_type, id DESC);
        CREATE TABLE IF NOT EXISTS investor_profile (
            id INTEGER PRIMARY KEY CHECK(id = 1),
            preferred_name TEXT,
            market TEXT,
            base_currency TEXT,
            initial_cash REAL CHECK(initial_cash > 0),
            user_timezone TEXT,
            research_access TEXT NOT NULL DEFAULT 'NOT_CHECKED'
                CHECK(research_access IN ('NOT_CHECKED', 'FULL', 'LIMITED', 'UNAVAILABLE')),
            research_checked_at TEXT,
            automation_preference TEXT NOT NULL DEFAULT 'NOT_ASKED',
            delivery_preference TEXT NOT NULL DEFAULT 'NOT_ASKED'
                CHECK(delivery_preference IN ('NOT_ASKED', 'MESSAGING', 'LOCAL')),
            delivery_target TEXT,
            delivery_confirmed_at TEXT,
            dashboard_preference TEXT NOT NULL DEFAULT 'NOT_ASKED'
                CHECK(dashboard_preference IN ('NOT_ASKED', 'ENABLED', 'SKIPPED')),
            onboarding_version INTEGER NOT NULL DEFAULT 1,
            onboarding_completed_at TEXT,
            optional_preferences_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    stamp = now()
    conn.execute(
        """INSERT OR IGNORE INTO market_adapters(
               market_id, display_name, market_timezone, native_currency,
               benchmark_ticker, ticker_pattern, session_schedule_json,
               cost_model_json, capabilities_json, sources_json, status,
               version, last_validated_at, created_at, updated_at
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '{}', 'OPERATIONAL', 1, ?, ?, ?)""",
        (
            INDIA_ADAPTER["market_id"], INDIA_ADAPTER["display_name"],
            INDIA_ADAPTER["market_timezone"], INDIA_ADAPTER["native_currency"],
            INDIA_ADAPTER["benchmark_ticker"], INDIA_ADAPTER["ticker_pattern"],
            json.dumps(INDIA_ADAPTER["session_schedule"]),
            json.dumps(INDIA_ADAPTER["cost_model"]),
            json.dumps(INDIA_ADAPTER["capabilities"]), stamp, stamp, stamp,
        ),
    )
    profile_cols = columns(conn, "investor_profile")
    if "user_timezone" not in profile_cols:
        conn.execute("ALTER TABLE investor_profile ADD COLUMN user_timezone TEXT")
    if "initial_cash" not in profile_cols:
        conn.execute("ALTER TABLE investor_profile ADD COLUMN initial_cash REAL")
    if "automation_preference" not in profile_cols:
        conn.execute(
            "ALTER TABLE investor_profile ADD COLUMN automation_preference "
            "TEXT NOT NULL DEFAULT 'NOT_ASKED'"
        )
    if "delivery_preference" not in profile_cols:
        conn.execute(
            "ALTER TABLE investor_profile ADD COLUMN delivery_preference "
            "TEXT NOT NULL DEFAULT 'NOT_ASKED'"
        )
    if "delivery_target" not in profile_cols:
        conn.execute("ALTER TABLE investor_profile ADD COLUMN delivery_target TEXT")
    if "delivery_confirmed_at" not in profile_cols:
        conn.execute(
            "ALTER TABLE investor_profile ADD COLUMN delivery_confirmed_at TEXT"
        )
    if "dashboard_preference" not in profile_cols:
        conn.execute(
            "ALTER TABLE investor_profile ADD COLUMN dashboard_preference "
            "TEXT NOT NULL DEFAULT 'NOT_ASKED'"
        )
    if "research_access" not in profile_cols:
        conn.execute(
            "ALTER TABLE investor_profile ADD COLUMN research_access "
            "TEXT NOT NULL DEFAULT 'NOT_CHECKED'"
        )
    if "research_checked_at" not in profile_cols:
        conn.execute(
            "ALTER TABLE investor_profile ADD COLUMN research_checked_at TEXT"
        )
    profile_row = conn.execute(
        "SELECT 1 FROM investor_profile WHERE id = 1"
    ).fetchone()
    if not profile_row:
        conn.execute(
            """INSERT INTO investor_profile(
                   id, preferred_name, market, base_currency, user_timezone,
                   onboarding_version, onboarding_completed_at,
                   optional_preferences_json, created_at, updated_at
               ) VALUES (1, NULL, ?, ?, ?, ?, NULL, '{}', ?, ?)""",
            (
                SUPPORTED_MARKET if upgrading_existing_portfolio else None,
                SUPPORTED_CURRENCY if upgrading_existing_portfolio else None,
                "Asia/Kolkata" if upgrading_existing_portfolio else None,
                ONBOARDING_VERSION,
                stamp,
                stamp,
            ),
        )
    # Add relevance-scoring columns to intel_sources (safe for existing DBs)
    intel_cols = columns(conn, "intel_sources")
    for col, dtype in (("relevance_pass_rate", "REAL DEFAULT 1.0"),
                       ("relevance_checked", "INTEGER DEFAULT 0"),
                       ("llm_rescued_count", "INTEGER DEFAULT 0")):
        if col not in intel_cols:
            conn.execute(f"ALTER TABLE intel_sources ADD COLUMN {col} {dtype}")
    holding_cols = columns(conn, "holdings")
    if "opened_at" not in holding_cols:
        conn.execute("ALTER TABLE holdings ADD COLUMN opened_at TEXT")
        conn.execute("UPDATE holdings SET opened_at = COALESCE(opened_at, last_updated)")
    if "quote_required_after" not in columns(conn, "holdings"):
        conn.execute("ALTER TABLE holdings ADD COLUMN quote_required_after TEXT")
    thesis_cols = columns(conn, "theses")
    thesis_columns = {
        "outcome": "TEXT", "lesson": "TEXT", "closed_at": "TEXT",
        "exit_reason": "TEXT", "timing_accuracy": "TEXT", "was_calibrated": "INTEGER",
        "forecast_event": "TEXT", "resolution_date": "TEXT", "resolution_source": "TEXT",
        "event_outcome": "TEXT", "brier_component": "REAL", "invalidation_price": "REAL",
        "entry_reference": "REAL", "sector": "TEXT", "counter_thesis": "TEXT",
        "financial_summary": "TEXT", "primary_sources_json": "TEXT",
        "investment_success_probability": "REAL",
        "ev_model": "TEXT", "scenario_json": "TEXT",
        "expected_return_pct": "REAL",
        "thesis_type": "TEXT", "thesis_contract_json": "TEXT",
        "review_date": "TEXT",
    }
    for name, col_type in thesis_columns.items():
        if name not in thesis_cols:
            conn.execute(f"ALTER TABLE theses ADD COLUMN {name} {col_type}")
    candidate_cols = columns(conn, "candidate_evaluations")
    candidate_columns = {
        "hard_gates_json": "TEXT NOT NULL DEFAULT '{}'",
        "hard_gate_pass": "INTEGER NOT NULL DEFAULT 0",
        "score_components_json": "TEXT NOT NULL DEFAULT '{}'",
        "weighted_score": "REAL",
        "scoring_model_version": "TEXT",
        "legacy_result": "TEXT",
        "shadow_recommendation": "TEXT",
    }
    for name, col_type in candidate_columns.items():
        if name not in candidate_cols:
            conn.execute(f"ALTER TABLE candidate_evaluations ADD COLUMN {name} {col_type}")
    decision_cols = columns(conn, "decisions")
    if "cash_reason" not in decision_cols:
        conn.execute("ALTER TABLE decisions ADD COLUMN cash_reason TEXT")
    for name in ("decision_model_version", "parameter_version"):
        if name not in decision_cols:
            conn.execute(f"ALTER TABLE decisions ADD COLUMN {name} TEXT")
    run_cols = columns(conn, "runs")
    for name in ("decision_model_version", "parameter_version", "schedule_version"):
        if name not in run_cols:
            conn.execute(f"ALTER TABLE runs ADD COLUMN {name} TEXT")
    theses_sql_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='theses'"
    ).fetchone()
    theses_sql = (theses_sql_row["sql"] or "") if theses_sql_row else ""
    if "'SHORT'" in theses_sql.upper():
        conn.executescript(
            """
            DROP TABLE IF EXISTS theses_long_only;
            CREATE TABLE theses_long_only (
                ticker TEXT PRIMARY KEY,
                direction TEXT NOT NULL CHECK(direction = 'LONG'),
                confidence INTEGER NOT NULL CHECK(confidence BETWEEN 1 AND 99),
                horizon TEXT NOT NULL,
                target REAL NOT NULL CHECK(target > 0),
                invalidation TEXT NOT NULL,
                catalyst TEXT NOT NULL,
                variant_view TEXT NOT NULL,
                sources_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'ACTIVE',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                outcome TEXT,
                lesson TEXT,
                closed_at TEXT,
                exit_reason TEXT,
                timing_accuracy TEXT,
                was_calibrated INTEGER,
                forecast_event TEXT,
                resolution_date TEXT,
                resolution_source TEXT,
                event_outcome TEXT,
                brier_component REAL,
                invalidation_price REAL,
                entry_reference REAL,
                sector TEXT,
                counter_thesis TEXT,
                financial_summary TEXT,
                primary_sources_json TEXT
            );
            INSERT INTO theses_long_only
            SELECT ticker, direction, confidence, horizon, target, invalidation,
                   catalyst, variant_view, sources_json, status, created_at, updated_at,
                   outcome, lesson, closed_at, exit_reason, timing_accuracy, was_calibrated,
                   forecast_event, resolution_date, resolution_source, event_outcome,
                   brier_component, invalidation_price, entry_reference, sector,
                   counter_thesis, financial_summary, primary_sources_json
            FROM theses WHERE direction = 'LONG';
            DROP TABLE theses;
            ALTER TABLE theses_long_only RENAME TO theses;
            """
        )
    trade_cols = columns(conn, "trades")
    for name, col_type in {
        "quote_price": "REAL", "fees": "REAL NOT NULL DEFAULT 0",
        "slippage": "REAL NOT NULL DEFAULT 0", "liquidity_bucket": "TEXT",
        "simulation_mode": "TEXT",
    }.items():
        if name not in trade_cols:
            conn.execute(f"ALTER TABLE trades ADD COLUMN {name} {col_type}")
    if "benchmark_price" not in columns(conn, "snapshots"):
        conn.execute("ALTER TABLE snapshots ADD COLUMN benchmark_price REAL")
    if "active_return_pct" not in columns(conn, "learning_log"):
        conn.execute("ALTER TABLE learning_log ADD COLUMN active_return_pct REAL")
    journal_cols = columns(conn, "decision_journal")
    if "run_id" not in journal_cols:
        conn.execute("ALTER TABLE decision_journal ADD COLUMN run_id INTEGER")
    conn.execute(
        "INSERT OR IGNORE INTO state(key, value) VALUES ('cash', ?)",
        (str(LEGACY_INITIAL_CASH),),
    )
    conn.execute(
        "INSERT OR IGNORE INTO state(key, value) VALUES ('initial_cash', ?)",
        (str(LEGACY_INITIAL_CASH),),
    )
    if upgrading_existing_portfolio and previous_schema_version < 18:
        existing_initial_cash = state_float(
            conn, "initial_cash", state_float(conn, "cash", LEGACY_INITIAL_CASH)
        )
        conn.execute(
            """UPDATE investor_profile
               SET initial_cash = COALESCE(initial_cash, ?), onboarding_version = ?
               WHERE id = 1""",
            (existing_initial_cash, ONBOARDING_VERSION),
        )
    conn.execute("INSERT OR IGNORE INTO state(key, value) VALUES ('realized_pnl', '0')")
    conn.execute("INSERT OR IGNORE INTO state(key, value) VALUES ('gross_realized_pnl', '0')")
    conn.execute("INSERT OR IGNORE INTO state(key, value) VALUES ('trading_costs', '0')")
    for key, value in (
        ("lifecycle_policy_version", str(LIFECYCLE_POLICY_VERSION)),
        ("lifecycle_last_archived", "0"),
        ("lifecycle_last_purged", "0"),
    ):
        conn.execute(
            "INSERT OR IGNORE INTO state(key, value) VALUES (?, ?)", (key, value)
        )
    for key, val in (
        ('strategy_max_position_weight', '0.20'),
        ('strategy_min_hold_days', '0'),
        ('strategy_max_positions', '8'),
        ('strategy_max_gross_exposure', '1.00'),
        ('strategy_max_sector_weight', '0.30'),
        ('strategy_risk_per_thesis', '0.01'),
        ('strategy_starter_position_weight', '0.03'),
        ('portfolio_regime', 'NORMAL'),
        ('strategy_max_portfolio_heat', '0.05'),
        ('strategy_gap_buffer_pct', '0.01'),
        ('strategy_quote_max_age_hours', '0.25'),
        ('strategy_max_quote_deviation_bps', '25'),
        ('strategy_fee_bps', '12.5'),
        ('strategy_slippage_large_bps', '10'),
        ('strategy_slippage_mid_bps', '30'),
        ('strategy_slippage_small_bps', '80'),
        ('strategy_intraday_entry_cutoff_minutes', '30'),
        ('strategy_min_forecasts_for_adaptation', '30'),
    ):
        conn.execute(
            "INSERT OR IGNORE INTO state(key, value) VALUES (?, ?)", (key, val)
        )
    runs_sql_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='runs'"
    ).fetchone()
    runs_sql = (runs_sql_row["sql"] or "") if runs_sql_row else ""
    if "MARKET_DATE TEXT NOT NULL UNIQUE" in runs_sql.upper():
        session_expr = "session_label" if "session_label" in columns(conn, "runs") else "''"
        conn.executescript("""
            DROP TABLE IF EXISTS runs_new;
            CREATE TABLE runs_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_date TEXT NOT NULL,
                session_label TEXT DEFAULT '',
                status TEXT NOT NULL,
                report TEXT,
                created_at TEXT NOT NULL,
                completed_at TEXT
            );
        """)
        conn.execute(
            f"""INSERT INTO runs_new
                (id, market_date, session_label, status, report, created_at, completed_at)
                SELECT id, market_date, {session_expr}, status, report, created_at, completed_at
                FROM runs"""
        )
        conn.executescript("""
            DROP TABLE runs;
            ALTER TABLE runs_new RENAME TO runs;
            CREATE INDEX IF NOT EXISTS idx_runs_market_date ON runs(market_date);
        """)
    conn.execute("INSERT OR REPLACE INTO state(key, value) VALUES ('schema_version', ?)",
                 (str(SCHEMA_VERSION),))
    conn.commit()


def state_float(conn: sqlite3.Connection, key: str, default: float) -> float:
    row = conn.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
    return float(row["value"]) if row else default


def _load_harper_cron_jobs() -> tuple[dict[str, dict], set[str]]:
    """Return Harper cron jobs and their delivery chat ids."""
    path = hermes_cron_jobs_path()
    if not path.exists():
        return {}, set()
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}, set()

    jobs: dict[str, dict] = {}
    delivery_chat_ids: set[str] = set()
    for job in payload.get("jobs", []):
        skills = job.get("skills") or []
        configured_skills = {str(job.get("skill") or ""), *(str(s) for s in skills)}
        if not configured_skills.intersection({"harper", "virtual-investor"}):
            continue
        job_id = str(job.get("id") or "").strip()
        if not job_id:
            continue
        jobs[job_id] = {
            "name": str(job.get("name") or "Harper scheduled run"),
            "provider": str(job.get("provider") or "unknown"),
        }
        delivery = str(job.get("deliver") or "")
        if ":" in delivery:
            delivery_chat_ids.add(delivery.split(":", 1)[1])
    return jobs, delivery_chat_ids


def _iso_from_epoch(value: float | int | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(float(value), timezone.utc).isoformat()


def _usage_cost_status(
    model: str,
    reported_status: str | None,
    estimated_cost: float,
    actual_cost: float,
) -> str:
    if actual_cost > 0:
        return "actual"
    if estimated_cost > 0:
        return reported_status or "estimated"
    normalized_model = model.lower().replace(":", "-").replace("/", "-")
    if normalized_model.endswith("-free") or "-free-" in normalized_model:
        return "free"
    return reported_status or "unknown"


def _read_harper_usage_from_hermes() -> list[dict]:
    """Read Harper sessions from Hermes' accounting DB without mutating it.

    Scheduled sessions are matched from cron job ids. Manual conversations in
    the dedicated Harper delivery chat and recursively spawned subagents are
    included as well. Per-model rows are used so fallbacks and auxiliary calls
    remain attributable without double-counting parent session cost rollups.
    """
    state_path = hermes_state_db_path()
    jobs, delivery_chat_ids = _load_harper_cron_jobs()
    if not state_path.exists() or (not jobs and not delivery_chat_ids):
        return []

    uri = f"file:{state_path.as_posix()}?mode=ro"
    source = sqlite3.connect(uri, uri=True)
    source.row_factory = sqlite3.Row
    try:
        session_columns = {
            row["name"] for row in source.execute("PRAGMA table_info(sessions)")
        }
        if not {"id", "source", "started_at", "model"}.issubset(session_columns):
            return []

        sessions = [dict(row) for row in source.execute(
            "SELECT id, source, chat_id, parent_session_id, model, started_at, ended_at,"
            " billing_provider, billing_mode, api_call_count, input_tokens, output_tokens,"
            " cache_read_tokens, cache_write_tokens, reasoning_tokens,"
            " estimated_cost_usd, actual_cost_usd, cost_status FROM sessions"
        )]
        children: dict[str, list[dict]] = {}
        for session in sessions:
            parent_id = session.get("parent_session_id")
            if parent_id:
                children.setdefault(str(parent_id), []).append(session)

        roots: dict[str, dict] = {}
        for session in sessions:
            session_id = str(session["id"])
            matched_job_id = next(
                (
                    job_id
                    for job_id in jobs
                    if session_id.startswith(f"cron_{job_id}_")
                ),
                None,
            )
            if matched_job_id:
                roots[session_id] = {
                    "job_id": matched_job_id,
                    "job_name": jobs[matched_job_id]["name"],
                    "provider": jobs[matched_job_id]["provider"],
                }
            elif (
                session.get("source") != "cron"
                and str(session.get("chat_id") or "") in delivery_chat_ids
            ):
                roots[session_id] = {
                    "job_id": None,
                    "job_name": "Harper chat",
                    "provider": str(session.get("billing_provider") or "unknown"),
                }

        matched: dict[str, tuple[dict, dict]] = {}
        queue = [(root_id, root_id, meta) for root_id, meta in roots.items()]
        while queue:
            session_id, root_id, meta = queue.pop(0)
            session = next((item for item in sessions if item["id"] == session_id), None)
            if session is None or session_id in matched:
                continue
            matched[session_id] = (session, {**meta, "root_session_id": root_id})
            queue.extend(
                (str(child["id"]), root_id, meta)
                for child in children.get(session_id, [])
            )

        if not matched:
            return []

        usage_table_exists = source.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='session_model_usage'"
        ).fetchone()
        model_rows_by_session: dict[str, list[dict]] = {}
        if usage_table_exists:
            session_ids = list(matched)
            for start in range(0, len(session_ids), 400):
                chunk = session_ids[start:start + 400]
                placeholders = ",".join("?" for _ in chunk)
                rows = source.execute(
                    f"SELECT session_id, model, billing_provider, billing_mode, task,"
                    " api_call_count, input_tokens, output_tokens, cache_read_tokens,"
                    " cache_write_tokens, reasoning_tokens, estimated_cost_usd,"
                    f" actual_cost_usd, cost_status FROM session_model_usage"
                    f" WHERE session_id IN ({placeholders})",
                    chunk,
                ).fetchall()
                for row in rows:
                    model_rows_by_session.setdefault(str(row["session_id"]), []).append(dict(row))

        imported_at = now()
        result: list[dict] = []
        for session_id, (session, meta) in matched.items():
            usage_rows = model_rows_by_session.get(session_id)
            if not usage_rows:
                usage_rows = [{
                    "session_id": session_id,
                    "model": session.get("model") or "unknown",
                    "billing_provider": session.get("billing_provider") or meta["provider"],
                    "billing_mode": session.get("billing_mode") or "",
                    "task": "",
                    "api_call_count": session.get("api_call_count") or 0,
                    "input_tokens": session.get("input_tokens") or 0,
                    "output_tokens": session.get("output_tokens") or 0,
                    "cache_read_tokens": session.get("cache_read_tokens") or 0,
                    "cache_write_tokens": session.get("cache_write_tokens") or 0,
                    "reasoning_tokens": session.get("reasoning_tokens") or 0,
                    "estimated_cost_usd": session.get("estimated_cost_usd") or 0,
                    "actual_cost_usd": session.get("actual_cost_usd") or 0,
                    "cost_status": session.get("cost_status"),
                }]

            for usage in usage_rows:
                model = str(usage.get("model") or session.get("model") or "unknown")
                provider = str(
                    usage.get("billing_provider")
                    or session.get("billing_provider")
                    or meta["provider"]
                    or "unknown"
                )
                task = str(usage.get("task") or "")
                estimated_cost = float(usage.get("estimated_cost_usd") or 0)
                actual_cost = float(usage.get("actual_cost_usd") or 0)
                usage_key = json.dumps(
                    [
                        session_id,
                        model,
                        provider,
                        str(usage.get("billing_mode") or ""),
                        task,
                    ],
                    separators=(",", ":"),
                )
                result.append({
                    "usage_key": usage_key,
                    "session_id": session_id,
                    "root_session_id": meta["root_session_id"],
                    "job_id": meta["job_id"],
                    "job_name": meta["job_name"],
                    "source": str(session.get("source") or "unknown"),
                    "model": model,
                    "provider": provider,
                    "task": task or None,
                    "started_at": _iso_from_epoch(session.get("started_at")) or imported_at,
                    "ended_at": _iso_from_epoch(session.get("ended_at")),
                    "api_calls": int(usage.get("api_call_count") or 0),
                    "input_tokens": int(usage.get("input_tokens") or 0),
                    "output_tokens": int(usage.get("output_tokens") or 0),
                    "cache_read_tokens": int(usage.get("cache_read_tokens") or 0),
                    "cache_write_tokens": int(usage.get("cache_write_tokens") or 0),
                    "reasoning_tokens": int(usage.get("reasoning_tokens") or 0),
                    "estimated_cost_usd": estimated_cost,
                    "actual_cost_usd": actual_cost,
                    "cost_status": _usage_cost_status(
                        model,
                        usage.get("cost_status"),
                        estimated_cost,
                        actual_cost,
                    ),
                    "imported_at": imported_at,
                })
        return result
    finally:
        source.close()


def refresh_llm_usage(conn: sqlite3.Connection) -> dict:
    rows = _read_harper_usage_from_hermes()
    for row in rows:
        conn.execute(
            """INSERT INTO llm_usage (
                   usage_key, session_id, root_session_id, job_id, job_name, source,
                   model, provider, task, started_at, ended_at, api_calls,
                   input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
                   reasoning_tokens, estimated_cost_usd, actual_cost_usd,
                   cost_status, imported_at
               ) VALUES (
                   :usage_key, :session_id, :root_session_id, :job_id, :job_name, :source,
                   :model, :provider, :task, :started_at, :ended_at, :api_calls,
                   :input_tokens, :output_tokens, :cache_read_tokens, :cache_write_tokens,
                   :reasoning_tokens, :estimated_cost_usd, :actual_cost_usd,
                   :cost_status, :imported_at
               ) ON CONFLICT(usage_key) DO UPDATE SET
                   ended_at=excluded.ended_at,
                   api_calls=excluded.api_calls,
                   input_tokens=excluded.input_tokens,
                   output_tokens=excluded.output_tokens,
                   cache_read_tokens=excluded.cache_read_tokens,
                   cache_write_tokens=excluded.cache_write_tokens,
                   reasoning_tokens=excluded.reasoning_tokens,
                   estimated_cost_usd=excluded.estimated_cost_usd,
                   actual_cost_usd=excluded.actual_cost_usd,
                   cost_status=excluded.cost_status,
                   imported_at=excluded.imported_at""",
            row,
        )
    imported_at = now()
    conn.execute(
        "INSERT OR REPLACE INTO state(key, value) VALUES ('llm_usage_last_imported_at', ?)",
        (imported_at,),
    )
    conn.execute("DELETE FROM state WHERE key='llm_usage_last_error'")
    conn.commit()
    return {
        "rows_refreshed": len(rows),
        "sessions": len({row["session_id"] for row in rows}),
        "imported_at": imported_at,
    }


def _usage_period_start(period: str, reference: datetime | None = None) -> datetime | None:
    with connect() as conn:
        profile = conn.execute(
            "SELECT user_timezone FROM investor_profile WHERE id=1"
        ).fetchone()
        local_zone = ZoneInfo(str(profile["user_timezone"])) if profile and profile["user_timezone"] else _market_timezone(conn)
    local_now = (reference or datetime.now(timezone.utc)).astimezone(local_zone)
    if period == "week":
        local_start = (local_now - timedelta(days=local_now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
    elif period == "month":
        local_start = local_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        return None
    return local_start.astimezone(timezone.utc)


def _summarize_usage_rows(rows: list[dict], period: str) -> dict:
    period_start = _usage_period_start(period)
    selected = [
        row for row in rows
        if period_start is None or (_utc_datetime(row.get("started_at")) or period_start) >= period_start
    ]
    models: dict[tuple[str, str], dict] = {}
    for row in selected:
        key = (str(row["model"]), str(row["provider"]))
        model = models.setdefault(key, {
            "model": key[0], "provider": key[1], "tokens": 0, "cost_usd": 0.0,
        })
        model["tokens"] += (
            int(row["input_tokens"]) + int(row["output_tokens"])
            + int(row["cache_read_tokens"]) + int(row["cache_write_tokens"])
        )
        model["cost_usd"] += float(row["actual_cost_usd"] or row["estimated_cost_usd"] or 0)

    totals = {
        "sessions": len({row["session_id"] for row in selected}),
        "api_calls": sum(int(row["api_calls"]) for row in selected),
        "input_tokens": sum(int(row["input_tokens"]) for row in selected),
        "output_tokens": sum(int(row["output_tokens"]) for row in selected),
        "cache_read_tokens": sum(int(row["cache_read_tokens"]) for row in selected),
        "cache_write_tokens": sum(int(row["cache_write_tokens"]) for row in selected),
        "reasoning_tokens": sum(int(row["reasoning_tokens"]) for row in selected),
        "cost_usd": round(sum(
            float(row["actual_cost_usd"] or row["estimated_cost_usd"] or 0)
            for row in selected
        ), 8),
    }
    totals["total_tokens"] = (
        totals["input_tokens"] + totals["output_tokens"]
        + totals["cache_read_tokens"] + totals["cache_write_tokens"]
    )
    statuses = {str(row["cost_status"]) for row in selected}
    totals["cost_status"] = (
        "none" if not selected
        else "partial" if "unknown" in statuses
        else "actual" if "actual" in statuses
        else "estimated" if any(status != "free" for status in statuses)
        else "free"
    )
    return {
        **totals,
        "models": sorted(models.values(), key=lambda item: item["tokens"], reverse=True),
    }


def _ensure_archive_schema(conn: sqlite3.Connection) -> None:
    """Attach the local-only archive and create its bounded storage tables."""
    path = archive_db_path()
    if path.resolve() == db_path().resolve():
        raise ValueError("archive database must differ from the live database")
    path.parent.mkdir(parents=True, exist_ok=True)
    attached = {row[1] for row in conn.execute("PRAGMA database_list")}
    if "archive_store" not in attached:
        conn.execute("ATTACH DATABASE ? AS archive_store", (str(path),))
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS archive_store.records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_table TEXT NOT NULL,
            source_id INTEGER NOT NULL,
            archived_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            reason TEXT NOT NULL,
            dedupe_key TEXT,
            payload_json TEXT NOT NULL,
            UNIQUE(source_table, source_id)
        );
        CREATE INDEX IF NOT EXISTS archive_store.idx_records_expiry
            ON records(expires_at);
        CREATE INDEX IF NOT EXISTS archive_store.idx_records_table
            ON records(source_table, archived_at);
        CREATE TABLE IF NOT EXISTS archive_store.dedupe_tombstones (
            source_table TEXT NOT NULL,
            dedupe_key TEXT NOT NULL,
            first_seen_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            PRIMARY KEY(source_table, dedupe_key)
        );
        CREATE INDEX IF NOT EXISTS archive_store.idx_tombstones_expiry
            ON dedupe_tombstones(expires_at);
        """
    )


def _utc_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _urls_from_stored(value: str | None) -> set[str]:
    if not value:
        return set()
    try:
        decoded = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        decoded = [part.strip() for part in str(value).split(",")]
    if isinstance(decoded, str):
        decoded = [decoded]
    if not isinstance(decoded, list):
        return set()
    return {
        str(item).strip()
        for item in decoded
        if str(item).strip().startswith(("http://", "https://"))
    }


def _protected_hot_urls(conn: sqlite3.Connection) -> set[str]:
    """Keep raw evidence hot while an active thesis or claim still depends on it."""
    protected: set[str] = set()
    for row in conn.execute(
        """SELECT sources_json, primary_sources_json, resolution_source
           FROM theses WHERE status IN ('ACTIVE', 'PENDING_RESOLUTION')"""
    ):
        protected.update(_urls_from_stored(row["sources_json"]))
        protected.update(_urls_from_stored(row["primary_sources_json"]))
        protected.update(_urls_from_stored(row["resolution_source"]))
    for row in conn.execute(
        "SELECT source_url FROM evidence_claims WHERE status='UNRESOLVED'"
    ):
        protected.update(_urls_from_stored(row["source_url"]))
    return protected


def _active_radar_tickers(conn: sqlite3.Connection) -> set[str]:
    tickers = {
        str(row[0])
        for row in conn.execute("SELECT ticker FROM holdings WHERE shares > 0")
    }
    tickers.update(
        str(row[0])
        for row in conn.execute(
            "SELECT ticker FROM theses WHERE status IN ('ACTIVE','PENDING_RESOLUTION')"
        )
    )
    tickers.update(
        str(row[0])
        for row in conn.execute(
            "SELECT ticker FROM evidence_claims WHERE status='UNRESOLVED' AND ticker IS NOT NULL"
        )
    )
    benchmark_ticker = _benchmark_ticker(conn)
    if benchmark_ticker:
        tickers.add(benchmark_ticker)
    if (_get_adapter(conn) or {}).get("market_id") == SUPPORTED_MARKET:
        tickers.update({"^NSEI", "^BSESN"})
    return tickers


def _maintenance_candidates(
    conn: sqlite3.Connection, reference_time: datetime
) -> dict[str, list[tuple[sqlite3.Row, str]]]:
    protected_urls = _protected_hot_urls(conn)
    active_tickers = _active_radar_tickers(conn)
    candidates: dict[str, list[tuple[sqlite3.Row, str]]] = {
        table: [] for table in ARCHIVE_RETENTION_DAYS
    }

    intel_cutoff = reference_time - timedelta(days=HOT_INTEL_DAYS)
    for rank, row in enumerate(conn.execute(
        "SELECT * FROM intel_articles ORDER BY id DESC"
    )):
        if row["link"] in protected_urls:
            continue
        too_old = (_utc_datetime(row["created_at"]) or reference_time) < intel_cutoff
        if too_old or rank >= HOT_INTEL_ROWS:
            reason = "older_than_hot_window" if too_old else "hot_row_cap"
            candidates["intel_articles"].append((row, reason))

    feed_cutoff = reference_time - timedelta(days=HOT_MARKET_FEED_DAYS)
    for rank, row in enumerate(conn.execute(
        "SELECT * FROM market_feed ORDER BY id DESC"
    )):
        too_old = (_utc_datetime(row["created_at"]) or reference_time) < feed_cutoff
        if too_old or rank >= HOT_MARKET_FEED_ROWS:
            reason = "older_than_hot_window" if too_old else "hot_row_cap"
            candidates["market_feed"].append((row, reason))

    research_cutoff = reference_time - timedelta(days=30)
    ticker_ranks: dict[str, int] = {}
    for global_rank, row in enumerate(conn.execute(
        "SELECT * FROM research_library ORDER BY id DESC"
    )):
        ticker = str(row["ticker"])
        ticker_rank = ticker_ranks.get(ticker, 0)
        ticker_ranks[ticker] = ticker_rank + 1
        too_old_off_radar = (
            ticker not in active_tickers
            and (_utc_datetime(row["created_at"]) or reference_time) < research_cutoff
        )
        if too_old_off_radar or ticker_rank >= HOT_RESEARCH_PER_TICKER or global_rank >= HOT_RESEARCH_ROWS:
            if too_old_off_radar:
                reason = "off_active_radar"
            elif ticker_rank >= HOT_RESEARCH_PER_TICKER:
                reason = "ticker_row_cap"
            else:
                reason = "hot_row_cap"
            candidates["research_library"].append((row, reason))

    quote_cutoff = reference_time - timedelta(days=HOT_QUOTES_DAYS)
    quote_ranks: dict[str, int] = {}
    for row in conn.execute("SELECT * FROM quotes ORDER BY ticker, id DESC"):
        ticker = str(row["ticker"])
        rank = quote_ranks.get(ticker, 0)
        quote_ranks[ticker] = rank + 1
        too_old = (_utc_datetime(row["recorded_at"]) or reference_time) < quote_cutoff
        if too_old or rank >= HOT_QUOTES_PER_TICKER:
            reason = "older_than_hot_window" if too_old else "ticker_row_cap"
            candidates["quotes"].append((row, reason))

    price_ranks: dict[str, int] = {}
    latest_dates = {
        str(row["ticker"]): str(row["latest_date"])
        for row in conn.execute(
            "SELECT ticker, MAX(date) AS latest_date FROM historical_prices GROUP BY ticker"
        )
    }
    inactive_cutoff = (reference_time - timedelta(days=30)).date().isoformat()
    for row in conn.execute("SELECT * FROM historical_prices ORDER BY ticker, date DESC"):
        ticker = str(row["ticker"])
        rank = price_ranks.get(ticker, 0)
        price_ranks[ticker] = rank + 1
        off_radar = ticker not in active_tickers and latest_dates.get(ticker, "") < inactive_cutoff
        if off_radar or rank >= HOT_PRICES_PER_TICKER:
            reason = "off_active_radar" if off_radar else "ticker_row_cap"
            candidates["historical_prices"].append((row, reason))

    return candidates


def _record_urls(record: sqlite3.Row) -> set[str]:
    try:
        payload = json.loads(record["payload_json"])
    except json.JSONDecodeError:
        return set()
    urls: set[str] = set()
    for key in ("link", "source", "source_url", "source_urls", "sources_json"):
        value = payload.get(key)
        if isinstance(value, str):
            urls.update(_urls_from_stored(value))
    return urls


def lifecycle_status(conn: sqlite3.Connection) -> dict:
    _ensure_archive_schema(conn)
    archived_rows = int(
        conn.execute("SELECT COUNT(*) FROM archive_store.records").fetchone()[0]
    )
    return {
        "policy_version": LIFECYCLE_POLICY_VERSION,
        "hot_intel_articles": int(conn.execute("SELECT COUNT(*) FROM intel_articles").fetchone()[0]),
        "hot_market_feed": int(conn.execute("SELECT COUNT(*) FROM market_feed").fetchone()[0]),
        "hot_research": int(conn.execute("SELECT COUNT(*) FROM research_library").fetchone()[0]),
        "hot_quotes": int(conn.execute("SELECT COUNT(*) FROM quotes").fetchone()[0]),
        "hot_historical_prices": int(conn.execute("SELECT COUNT(*) FROM historical_prices").fetchone()[0]),
        "archived_rows": archived_rows,
        "last_archived_rows": int(state_float(conn, "lifecycle_last_archived", 0)),
        "last_purged_rows": int(state_float(conn, "lifecycle_last_purged", 0)),
        "last_maintained_at": _state_text(conn, "lifecycle_last_maintained_at", "") or None,
    }


def run_maintenance(conn: sqlite3.Connection, *, dry_run: bool = False) -> dict:
    """Archive cold working data and permanently remove expired raw records."""
    _ensure_archive_schema(conn)
    reference_time = datetime.now(timezone.utc)
    candidates = _maintenance_candidates(conn, reference_time)
    protected_urls = _protected_hot_urls(conn)
    expiry_rows = list(conn.execute(
        "SELECT * FROM archive_store.records WHERE expires_at <= ? ORDER BY id",
        (reference_time.isoformat(),),
    ))
    purgeable = [
        row for row in expiry_rows if not (_record_urls(row) & protected_urls)
    ]
    archived_by_table = {table: len(rows) for table, rows in candidates.items()}
    archived_total = sum(archived_by_table.values())
    tombstones_to_purge = int(conn.execute(
        "SELECT COUNT(*) FROM archive_store.dedupe_tombstones WHERE expires_at <= ?",
        (reference_time.isoformat(),),
    ).fetchone()[0])

    if not dry_run:
        for table, rows in candidates.items():
            retention_days = ARCHIVE_RETENTION_DAYS[table]
            expires_at = (reference_time + timedelta(days=retention_days)).isoformat()
            for row, reason in rows:
                payload = dict(row)
                dedupe_key = str(row["fingerprint"]) if table == "intel_articles" else None
                conn.execute(
                    """INSERT OR REPLACE INTO archive_store.records
                       (source_table, source_id, archived_at, expires_at, reason,
                        dedupe_key, payload_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        table,
                        int(row["id"]),
                        reference_time.isoformat(),
                        expires_at,
                        reason,
                        dedupe_key,
                        json.dumps(payload, separators=(",", ":"), default=str),
                    ),
                )
                if dedupe_key:
                    conn.execute(
                        """INSERT INTO archive_store.dedupe_tombstones
                           (source_table, dedupe_key, first_seen_at, expires_at)
                           VALUES (?, ?, ?, ?)
                           ON CONFLICT(source_table, dedupe_key) DO UPDATE SET
                             expires_at=excluded.expires_at""",
                        (
                            table,
                            dedupe_key,
                            str(row["created_at"]),
                            (reference_time + timedelta(days=FINGERPRINT_RETENTION_DAYS)).isoformat(),
                        ),
                    )
                conn.execute(f"DELETE FROM {table} WHERE id=?", (int(row["id"]),))

        for row in purgeable:
            conn.execute("DELETE FROM archive_store.records WHERE id=?", (row["id"],))
        conn.execute(
            "DELETE FROM archive_store.dedupe_tombstones WHERE expires_at <= ?",
            (reference_time.isoformat(),),
        )
        for key, value in (
            ("lifecycle_last_maintained_at", reference_time.isoformat()),
            ("lifecycle_last_archived", str(archived_total)),
            ("lifecycle_last_purged", str(len(purgeable))),
            ("lifecycle_policy_version", str(LIFECYCLE_POLICY_VERSION)),
        ):
            conn.execute("INSERT OR REPLACE INTO state(key, value) VALUES (?, ?)", (key, value))

    return {
        "dry_run": dry_run,
        "archived_rows": archived_total,
        "archived_by_table": archived_by_table,
        "purged_rows": len(purgeable),
        "purged_tombstones": tombstones_to_purge,
        "status": lifecycle_status(conn),
    }


def latest_quote(conn: sqlite3.Connection, ticker: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT ticker, price, source, asof, recorded_at FROM quotes WHERE ticker = ? ORDER BY id DESC LIMIT 1",
        (ticker.upper(),),
    ).fetchone()


def _public_urls(value: str, label: str, minimum: int = 1) -> list[str]:
    urls = [item.strip() for item in value.split(",") if item.strip()]
    if len(urls) < minimum or any(not url.startswith(("http://", "https://")) for url in urls):
        raise ValueError(f"{label} requires at least {minimum} public http(s) URL(s)")
    return urls


def _json_object(value: str | dict | None, label: str) -> dict:
    if value is None or value == "":
        return {}
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} must be a JSON object") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"{label} must be a JSON object")
    return parsed


def _normalize_gate_result(value: object) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().upper()
    return normalized in {"PASS", "PASSED", "ALLOW", "ALLOWED", "TRUE", "1"}


def _hard_gate_summary(value: str | dict | None) -> tuple[dict[str, bool], bool, list[str]]:
    supplied = _json_object(value, "hard_gates")
    unknown = sorted(set(supplied) - set(HARD_GATE_CLASSES))
    if unknown:
        raise ValueError("unknown hard gate(s): " + ", ".join(unknown))
    normalized = {gate: _normalize_gate_result(supplied.get(gate, False)) for gate in HARD_GATE_CLASSES}
    failures = [gate for gate, passed in normalized.items() if not passed]
    return normalized, not failures, failures


def _weighted_candidate_score(value: str | dict | None) -> tuple[dict[str, float], float | None]:
    components = _json_object(value, "score_components")
    if not components:
        return {}, None
    unknown = sorted(set(components) - set(SCORE_WEIGHTS))
    if unknown:
        raise ValueError("unknown score component(s): " + ", ".join(unknown))
    normalized: dict[str, float] = {}
    for name in SCORE_WEIGHTS:
        raw = float(components.get(name, 0))
        if not 0 <= raw <= 100:
            raise ValueError(f"score component {name} must be between 0 and 100")
        normalized[name] = raw
    score = sum(normalized[name] * SCORE_WEIGHTS[name] for name in SCORE_WEIGHTS)
    return normalized, round(score, 4)


def _scenario_expected_return(args: argparse.Namespace, round_trip_cost_pct: float) -> tuple[str, dict, float, float]:
    investment_probability = (
        float(args.investment_success_probability)
        if args.investment_success_probability is not None
        else float(args.confidence)
    )
    if not 1 <= investment_probability <= 99:
        raise ValueError("investment success probability must be between 1 and 99")
    scenario_values = (
        args.bear_return_pct, args.base_return_pct, args.bull_return_pct,
        args.bear_probability, args.base_probability, args.bull_probability,
    )
    if any(value is not None for value in scenario_values):
        if any(value is None for value in scenario_values):
            raise ValueError("scenario EV requires bear/base/bull returns and probabilities")
        probs = [float(args.bear_probability), float(args.base_probability), float(args.bull_probability)]
        if any(prob < 0 or prob > 100 for prob in probs) or abs(sum(probs) - 100) > 0.01:
            raise ValueError("scenario probabilities must each be 0-100 and sum to 100")
        scenario = {
            "bear": {"probability_pct": probs[0], "return_pct": float(args.bear_return_pct)},
            "base": {"probability_pct": probs[1], "return_pct": float(args.base_return_pct)},
            "bull": {"probability_pct": probs[2], "return_pct": float(args.bull_return_pct)},
        }
        gross = sum(item["probability_pct"] / 100 * item["return_pct"] for item in scenario.values())
        return "SCENARIO", scenario, investment_probability, gross - round_trip_cost_pct
    return "TARGET_STOP", {}, investment_probability, float("nan")


def _candidate_gate_failures(gate_outcomes: dict) -> list[str]:
    failures = []
    for gate, outcome in gate_outcomes.items():
        normalized = str(outcome).strip().upper()
        if normalized in {"FAIL", "FAILED", "REJECT", "REJECTED", "BLOCKED", "FALSE", "0"}:
            failures.append(str(gate))
    return failures


def _india_date_from_timestamp(value: str) -> str:
    with connect() as conn:
        return _parse_timestamp(value).astimezone(_market_timezone(conn)).date().isoformat()


def _valid_date(value: str, label: str) -> str:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date().isoformat()
    except ValueError as exc:
        raise ValueError(f"{label} must be YYYY-MM-DD") from exc


def _thesis_payoff(entry: float, target: float, invalidation: float) -> dict:
    if not invalidation < entry < target:
        raise ValueError("LONG thesis requires invalidation_price < entry_reference < target")
    reward, risk = target - entry, entry - invalidation
    return {"reward_pct": reward / entry * 100, "risk_pct": risk / entry * 100,
            "reward_risk": reward / risk}


def _enforce_market_session(conn: sqlite3.Connection, *, opening_intraday: bool = False) -> None:
    """Require a same-day, sourced exchange-session confirmation before a fill."""
    if os.environ.get("VIRTUAL_INVESTOR_TEST_BYPASS_MARKET_SESSION") == "1":
        return
    market_timezone = _market_timezone(conn)
    current = datetime.now(market_timezone)
    confirmed_date = _state_text(conn, "market_session_date", "")
    if confirmed_date != current.date().isoformat():
        raise ValueError(
            "confirm today's official exchange session with market-session confirm before trading"
        )
    status = _state_text(conn, "market_session_status", "").upper()
    if status not in {"OPEN", "SPECIAL"}:
        raise ValueError(f"market session is {status or 'UNCONFIRMED'}; trading is disabled")
    source = _state_text(conn, "market_session_source", "")
    if not source.startswith(("http://", "https://")):
        raise ValueError("market-session confirmation is missing an official public source")
    adapter = _get_adapter(conn) or {}
    adapter_schedule = adapter.get("session_schedule") or {}
    open_time = _state_text(
        conn, "market_session_open", str(adapter_schedule.get("market_open") or DEFAULT_MARKET_OPEN)
    )
    close_time = _state_text(
        conn, "market_session_close", str(adapter_schedule.get("market_close") or DEFAULT_MARKET_CLOSE)
    )
    open_minute = _minutes_since_midnight(open_time, "market open")
    close_minute = _minutes_since_midnight(close_time, "market close")
    current_minute = current.hour * 60 + current.minute
    if not open_minute <= current_minute < close_minute:
        raise ValueError(
            f"trades are allowed only during the confirmed {open_time}-{close_time} "
            f"{market_timezone.key} session"
        )
    if opening_intraday:
        cutoff = state_float(conn, "strategy_intraday_entry_cutoff_minutes", 30.0)
        if close_minute - current_minute < cutoff:
            raise ValueError(
                f"new intraday entries stop {cutoff:g} minutes before the confirmed market close"
            )


def _intraday_positions(conn: sqlite3.Connection) -> list[dict]:
    positions = []
    market_timezone = _market_timezone(conn)
    today = datetime.now(market_timezone).date()
    for row in conn.execute(
        """SELECT h.ticker, h.shares, h.opened_at, t.horizon
           FROM holdings h JOIN theses t ON t.ticker=h.ticker AND t.status='ACTIVE'
           WHERE h.shares > 0"""
    ):
        if not str(row["horizon"] or "").upper().startswith("INTRADAY:"):
            continue
        opened_at = row["opened_at"]
        opened_date = (
            _parse_timestamp(opened_at, "holding opened-at").astimezone(market_timezone).date()
            if opened_at else None
        )
        positions.append({
            "ticker": row["ticker"],
            "shares": float(row["shares"]),
            "opened_at": opened_at,
            "overdue": opened_date is None or opened_date < today,
        })
    return positions


def _fresh_trade_quote(conn: sqlite3.Connection, ticker: str, observed_price: float) -> sqlite3.Row:
    quote = latest_quote(conn, ticker)
    if not quote:
        raise ValueError(f"no sourced quote exists for {ticker}; record a quote before trading")
    max_age = state_float(conn, "strategy_quote_max_age_hours", 0.25)
    age = _quote_age_hours(quote)
    if age > max_age:
        raise ValueError(f"quote for {ticker} is {age:.1f}h old; maximum trade age is {max_age:g}h")
    holding = conn.execute(
        "SELECT quote_required_after FROM holdings WHERE ticker=?", (ticker,)
    ).fetchone()
    if holding and holding["quote_required_after"]:
        required = _parse_timestamp(holding["quote_required_after"], "corporate-action timestamp")
        if (_parse_timestamp(quote["recorded_at"], "quote recorded-at") <= required
                or _parse_timestamp(quote["asof"], "quote as-of") <= required):
            raise ValueError(
                f"refresh the {ticker} quote after the recorded corporate action before trading"
            )
    max_deviation = state_float(conn, "strategy_max_quote_deviation_bps", 100.0) / 10_000
    source_price = float(quote["price"])
    if abs(observed_price - source_price) / source_price > max_deviation + 1e-12:
        raise ValueError(
            f"trade quote {_money(conn, observed_price)} differs from latest sourced quote "
            f"{_money(conn, source_price)} by more than {max_deviation * 100:.2f}%"
        )
    return quote


def _position_heat(conn: sqlite3.Connection, nav: float,
                   overrides: dict[str, float] | None = None) -> tuple[float, list[str]]:
    overrides = overrides or {}
    heat, missing = 0.0, []
    processed: set[str] = set()
    gap = state_float(conn, "strategy_gap_buffer_pct", 0.01)
    for row in conn.execute(
        """SELECT h.ticker, h.shares, t.invalidation_price
           FROM holdings h LEFT JOIN theses t
             ON t.ticker=h.ticker AND t.status='ACTIVE'"""
    ):
        processed.add(row["ticker"])
        signed_shares = overrides.get(row["ticker"], float(row["shares"]))
        if abs(signed_shares) < 1e-12:
            continue
        quote = latest_quote(conn, row["ticker"])
        if not quote or row["invalidation_price"] is None:
            missing.append(row["ticker"])
            continue
        price = float(quote["price"])
        heat += abs(signed_shares) * (
            abs(price - float(row["invalidation_price"])) + price * gap
        )
    for ticker, signed_shares in overrides.items():
        if ticker in processed or abs(signed_shares) < 1e-12:
            continue
        thesis = conn.execute(
            "SELECT invalidation_price FROM theses WHERE ticker=? AND status='ACTIVE'",
            (ticker,),
        ).fetchone()
        quote = latest_quote(conn, ticker)
        if not quote or not thesis or thesis["invalidation_price"] is None:
            missing.append(ticker)
            continue
        price = float(quote["price"])
        heat += abs(signed_shares) * (
            abs(price - float(thesis["invalidation_price"])) + price * gap
        )
    return ((heat / nav) if nav else 0.0), sorted(set(missing))


def _enforce_opening_risk(
    conn: sqlite3.Connection,
    ticker: str,
    shares: float,
    new_signed: float,
    source_price: float,
    thesis: sqlite3.Row,
    existing: sqlite3.Row | None,
    round_trip_cost_pct: float,
) -> None:
    required = (
        "invalidation_price", "entry_reference", "target", "confidence", "sector",
        "counter_thesis", "financial_summary", "primary_sources_json",
    )
    thesis_type = str(thesis["thesis_type"] or "CATALYST") if "thesis_type" in thesis.keys() else "CATALYST"
    if thesis_type == "CATALYST":
        required += ("forecast_event", "resolution_date", "resolution_source")
    else:
        required += ("thesis_contract_json", "review_date")
    if any(thesis[field] in (None, "") for field in required):
        raise ValueError(
            "active thesis uses the legacy contract; re-file it with event resolution, "
            "primary evidence, entry reference, and numeric invalidation before adding risk"
        )
    current = portfolio_status(conn)
    nav = float(current["nav"])
    if nav <= 0:
        raise ValueError("portfolio NAV must be positive before opening risk")
    if not existing and current["holdings_count"] >= int(state_float(conn, "strategy_max_positions", 8)):
        raise ValueError("maximum number of open positions reached")

    payoff = _thesis_payoff(
        source_price, float(thesis["target"]), float(thesis["invalidation_price"]),
    )
    net_reward = payoff["reward_pct"] - round_trip_cost_pct
    net_risk = payoff["risk_pct"] + round_trip_cost_pct
    net_reward_risk = net_reward / net_risk if net_risk > 0 else 0.0
    probability = float(thesis["investment_success_probability"] or thesis["confidence"]) / 100
    expected_return = (
        probability * payoff["reward_pct"]
        - (1 - probability) * payoff["risk_pct"]
        - round_trip_cost_pct
    )
    if net_reward <= 0 or net_reward_risk < 1.5:
        raise ValueError(
            f"current net reward/risk is {net_reward_risk:.2f}; minimum is 1.50 after costs"
        )
    if expected_return <= 0:
        raise ValueError(
            f"current expected return is {expected_return:.2f}% after estimated costs"
        )
    max_weight = state_float(conn, "strategy_max_position_weight", MAX_POSITION_WEIGHT)
    position_weight = abs(new_signed) * source_price / nav
    if position_weight > max_weight + 1e-9:
        raise ValueError(
            f"trade exceeds {max_weight * 100:.0f}% position limit "
            f"({position_weight * 100:.1f}%)"
        )

    current_gross = float(current["gross_exposure_pct"] or 0) / 100 * nav
    max_gross = state_float(conn, "strategy_max_gross_exposure", 1.0)
    if (current_gross + shares * source_price) / nav > max_gross + 1e-9:
        raise ValueError(f"trade exceeds {max_gross * 100:.0f}% gross-exposure limit")

    sector = thesis["sector"] or "UNCLASSIFIED"
    sector_value = 0.0
    for item in current["holdings"]:
        row = conn.execute("SELECT sector FROM theses WHERE ticker=? AND status='ACTIVE'",
                           (item["ticker"],)).fetchone()
        held_sector = row["sector"] if row and row["sector"] else "UNCLASSIFIED"
        if held_sector == sector:
            sector_value += abs(float(item["market_value"]))
    max_sector = state_float(conn, "strategy_max_sector_weight", 0.30)
    if (sector_value + shares * source_price) / nav > max_sector + 1e-9:
        raise ValueError(f"trade exceeds {max_sector * 100:.0f}% sector exposure for {sector}")

    invalidation = float(thesis["invalidation_price"])
    per_share_risk = abs(source_price - invalidation) + source_price * state_float(
        conn, "strategy_gap_buffer_pct", 0.01
    )
    thesis_risk = abs(new_signed) * per_share_risk
    max_thesis_risk = state_float(conn, "strategy_risk_per_thesis", 0.01)
    if thesis_risk / nav > max_thesis_risk + 1e-9:
        allowed = math.floor((nav * max_thesis_risk) / per_share_risk)
        raise ValueError(
            f"trade risks {thesis_risk / nav * 100:.2f}% of NAV at invalidation; "
            f"limit is {max_thesis_risk * 100:.2f}% ({allowed} total shares allowed)"
        )
    projected_heat, missing_risk = _position_heat(conn, nav, {ticker: new_signed})
    if missing_risk:
        raise ValueError("cannot add risk while holdings lack numeric invalidations: " +
                         ", ".join(missing_risk))
    max_heat = state_float(conn, "strategy_max_portfolio_heat", 0.05)
    if projected_heat > max_heat + 1e-9:
        raise ValueError(
            f"trade raises portfolio heat to {projected_heat * 100:.2f}%; "
            f"limit is {max_heat * 100:.2f}%"
        )


EXPOSURE_REGIMES = {
    "DEFENSIVE": (25.0, 50.0),
    "NORMAL": (50.0, 75.0),
    "STRONG_OPPORTUNITY": (70.0, 90.0),
}

def _regime_status(conn: sqlite3.Connection, exposure_pct: float | None = None) -> dict:
    name = _state_text(conn, "portfolio_regime", "NORMAL").upper()
    if name not in EXPOSURE_REGIMES:
        name = "NORMAL"
    low, high = EXPOSURE_REGIMES[name]
    if exposure_pct is None:
        exposure_pct = float(portfolio_status(conn)["gross_exposure_pct"] or 0)
    return {
        "name": name, "min_exposure_pct": low, "max_exposure_pct": high,
        "current_exposure_pct": round(exposure_pct, 2),
        "within_band": low <= exposure_pct <= high,
        "below_band": exposure_pct < low, "above_band": exposure_pct > high,
        "reason": _state_text(conn, "portfolio_regime_reason", ""),
        "updated_at": _state_text(conn, "portfolio_regime_updated_at", ""),
    }

def cmd_regime_set(args: argparse.Namespace) -> None:
    stamp = now()
    with connect() as conn:
        conn.execute("INSERT OR REPLACE INTO state(key,value) VALUES ('portfolio_regime',?)", (args.name,))
        conn.execute("INSERT OR REPLACE INTO state(key,value) VALUES ('portfolio_regime_reason',?)", (args.reason.strip(),))
        conn.execute("INSERT OR REPLACE INTO state(key,value) VALUES ('portfolio_regime_updated_at',?)", (stamp,))
        result = _regime_status(conn)
    print(json.dumps(result, indent=2))

def cmd_regime_show(_: argparse.Namespace) -> None:
    with connect() as conn:
        print(json.dumps(_regime_status(conn), indent=2))

def cmd_init(_: argparse.Namespace) -> None:
    with connect() as conn:
        if not conn.execute("SELECT 1 FROM decision_journal LIMIT 1").fetchone():
            conn.execute(
                "INSERT INTO decision_journal(entry_type, content, timestamp) VALUES (?, ?, ?)",
                (
                    "initial",
                    "Virtual portfolio initialized; starting cash is confirmed during onboarding.",
                    now(),
                ),
            )
    print(f"Database ready at {db_path()}")


def _normalize_market(value: str) -> str:
    return _market_id(value)


def _normalize_currency(value: str) -> str:
    return _currency_code(value)


def _profile_complete(row: sqlite3.Row | dict) -> bool:
    return bool(
        str(row["preferred_name"] or "").strip()
        and str(row["market"] or "").strip()
        and re.fullmatch(r"[A-Z]{3}", str(row["base_currency"] or ""))
        and row["initial_cash"] is not None
        and float(row["initial_cash"]) > 0
        and str(row["user_timezone"] or "").strip()
        and str(row["research_access"] or "") == "FULL"
        and int(row["onboarding_version"]) == ONBOARDING_VERSION
    )


def _portfolio_onboarding_context(conn: sqlite3.Connection) -> dict:
    cash = round(state_float(conn, "cash", LEGACY_INITIAL_CASH), 2)
    holdings_count = int(conn.execute("SELECT COUNT(*) FROM holdings").fetchone()[0])
    context = {
        "cash": cash,
        "holdings_count": holdings_count,
        "virtual": True,
    }
    try:
        current = portfolio_status(conn)
    except ValueError as exc:
        context.update({
            "nav": None,
            "valuation_status": "UNAVAILABLE",
            "valuation_note": str(exc),
        })
    else:
        context.update({
            "nav": current["nav"],
            "market_value": current["market_value"],
            "valuation_status": current["valuation_status"],
        })
    return context


def _profile_payload(conn: sqlite3.Connection) -> dict:
    row = conn.execute("SELECT * FROM investor_profile WHERE id = 1").fetchone()
    if not row:
        raise ValueError("Harper profile is unavailable")
    complete = _profile_complete(row)
    missing = []
    if not str(row["preferred_name"] or "").strip():
        missing.append("preferred_name")
    if not str(row["market"] or "").strip():
        missing.append("market")
    if not re.fullmatch(r"[A-Z]{3}", str(row["base_currency"] or "")):
        missing.append("base_currency")
    if row["initial_cash"] is None or float(row["initial_cash"]) <= 0:
        missing.append("initial_cash")
    if not str(row["user_timezone"] or "").strip():
        missing.append("user_timezone")
    if str(row["research_access"] or "") != "FULL":
        missing.append("research_access")
    preferences = json.loads(row["optional_preferences_json"] or "{}")
    portfolio = _portfolio_onboarding_context(conn)
    if row["initial_cash"] is None:
        portfolio.update({
            "cash": None,
            "nav": None,
            "valuation_status": "PENDING_STARTING_CASH",
            "valuation_note": "Starting cash has not been confirmed.",
        })
    adapter = _get_adapter(conn, row["market"]) if row["market"] else None

    if "preferred_name" in missing:
        stage = "NEEDS_NAME"
        suggested_response = (
            "I'm Harper. I manage a virtual portfolio, explain my decisions, "
            "and never touch real money. What should I call you?"
        )
    elif "market" in missing or "base_currency" in missing:
        stage = "NEEDS_MARKET_CURRENCY"
        suggested_response = (
            f"Good to meet you, {row['preferred_name']}. Which market should I operate in, "
            "and which currency should I use for your reports?"
        )
    elif "initial_cash" in missing:
        stage = "NEEDS_STARTING_CASH"
        suggested = _suggested_initial_cash(row["base_currency"])
        suggested_response = (
            "How much virtual cash should I start with? A sensible default for "
            f"{row['base_currency']} is {_money(conn, suggested)}. You can use that "
            "or choose another positive amount."
        )
    elif "user_timezone" in missing:
        stage = "NEEDS_TIMEZONE"
        suggested_response = (
            "Which timezone should I use for your reports? Use a city-based timezone "
            "such as Asia/Kolkata or America/New_York."
        )
    elif "research_access" in missing:
        stage = "NEEDS_RESEARCH_ACCESS"
        if row["research_access"] == "NOT_CHECKED":
            suggested_response = (
                "Before I can operate, I need to verify live web search and source "
                "extraction. I'll check both now. If either is unavailable, I'll show "
                "you the one-time Hermes setup."
            )
        elif row["research_access"] == "LIMITED":
            suggested_response = (
                "Web research is only partly available. Harper needs both search and "
                "source extraction to verify evidence. Run `hermes tools`, open Web "
                "Search & Extract, and configure both capabilities using the same "
                "provider or separate providers. Never paste an API key into chat. "
                "Tell me when it is ready and I'll verify it again."
            )
        else:
            suggested_response = (
                "Live web research is not configured, so I cannot responsibly research "
                "or trade investments yet. Run `hermes tools`, open Web Search & Extract, "
                "and configure both capabilities using the same provider or separate "
                "providers. Never paste an API key into chat. Tell me when it is ready "
                "and I'll verify it."
            )
    else:
        stage = "READY"
        positions = portfolio["holdings_count"]
        if positions == 0:
            state_text = (
                f"The portfolio currently has {_money(conn, portfolio['cash'])} in virtual cash "
                "with no positions."
            )
        elif portfolio.get("nav") is not None:
            state_text = (
                f"The virtual portfolio currently has {_money(conn, portfolio['cash'])} in cash, "
                f"{positions} open position{'s' if positions != 1 else ''}, and a "
                f"{_money(conn, portfolio['nav'])} NAV."
            )
        else:
            state_text = (
                f"The virtual portfolio currently has {_money(conn, portfolio['cash'])} in cash "
                f"and {positions} open position{'s' if positions != 1 else ''}. "
                "I need a fresh sourced quote before stating its NAV."
            )
        market_label = adapter["display_name"] if adapter else row["market"]
        adapter_note = ""
        if adapter and adapter["status"] != "OPERATIONAL":
            adapter_note = (
                " I can operate with conservative cost assumptions while I continue "
                "validating this market adapter and its sources."
            )
        if row["automation_preference"] == "NOT_ASKED":
            next_action = (
                " Would you like me to prepare an automatic schedule around this market's "
                "hours? I won't create or enable jobs without your confirmation."
            )
        elif (
            row["automation_preference"] == "ENABLED"
            and row["delivery_preference"] == "NOT_ASKED"
        ):
            next_action = (
                " I'll check the messaging destinations already configured in Hermes, then "
                "you can choose where to receive updates or keep them local. I won't send a "
                "test or install jobs without your confirmation."
            )
        elif row["dashboard_preference"] == "NOT_ASKED":
            next_action = (
                " Would you like the optional private web dashboard? It uses your own "
                "Vercel and Convex accounts, and I won't create cloud resources or sync "
                "portfolio data without your confirmation."
            )
        elif row["dashboard_preference"] == "ENABLED":
            next_action = (
                " Your dashboard choice is saved. I can check its connection or explain "
                "the first authenticated sync."
            )
        else:
            next_action = (
                " Want the quick market brief or the reasoning behind how I choose a trade?"
            )
        suggested_response = (
            f"{market_label}, reported in {row['base_currency']}. Research access is "
            f"verified and you're set. {state_text}"
            f"{adapter_note}{next_action}"
        )

    return {
        "stage": stage,
        "complete": complete,
        "missing": missing,
        "profile": {
            "preferred_name": row["preferred_name"],
            "market": row["market"],
            "market_label": adapter["display_name"] if adapter else row["market"],
            "base_currency": row["base_currency"],
            "initial_cash": row["initial_cash"],
            "user_timezone": row["user_timezone"],
            "research_access": row["research_access"],
            "research_checked_at": row["research_checked_at"],
            "automation_preference": row["automation_preference"],
            "delivery_preference": row["delivery_preference"],
            "delivery_target": row["delivery_target"],
            "delivery_confirmed_at": row["delivery_confirmed_at"],
            "dashboard_preference": row["dashboard_preference"],
            "onboarding_version": int(row["onboarding_version"]),
            "onboarding_completed_at": row["onboarding_completed_at"],
            "optional_preferences": preferences,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        },
        "market_adapter": adapter,
        "portfolio": portfolio,
        "automation_offer_pending": (
            complete and row["automation_preference"] == "NOT_ASKED"
        ),
        "delivery_offer_pending": (
            complete
            and row["automation_preference"] == "ENABLED"
            and row["delivery_preference"] == "NOT_ASKED"
        ),
        "dashboard_offer_pending": (
            complete
            and row["automation_preference"] != "NOT_ASKED"
            and (
                row["automation_preference"] == "SKIPPED"
                or row["delivery_preference"] != "NOT_ASKED"
            )
            and row["dashboard_preference"] == "NOT_ASKED"
        ),
        "research_check_required": row["research_access"] != "FULL",
        "suggested_initial_cash": (
            _suggested_initial_cash(row["base_currency"])
            if row["base_currency"] and row["initial_cash"] is None
            else None
        ),
        "suggested_response": suggested_response,
    }


def cmd_profile_show(_: argparse.Namespace) -> None:
    with connect() as conn:
        payload = _profile_payload(conn)
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def cmd_profile_set(args: argparse.Namespace) -> None:
    if all(value is None for value in (
        args.preferred_name, args.market, args.base_currency,
        args.initial_cash, args.user_timezone, args.research_access,
        args.automation, args.delivery_target, args.dashboard,
    )):
        raise ValueError("profile set requires at least one profile field")
    updates = {}
    if args.preferred_name is not None:
        preferred_name = args.preferred_name.strip()
        if not preferred_name:
            raise ValueError("preferred name cannot be empty")
        updates["preferred_name"] = preferred_name
    if args.market is not None:
        updates["market"] = _normalize_market(args.market)
    if args.base_currency is not None:
        updates["base_currency"] = _normalize_currency(args.base_currency)
    if args.initial_cash is not None:
        initial_cash = round(float(args.initial_cash), 2)
        if not math.isfinite(initial_cash) or initial_cash <= 0:
            raise ValueError("initial cash must be a positive finite amount")
        if initial_cash > 1_000_000_000_000_000:
            raise ValueError("initial cash must not exceed 1,000,000,000,000,000")
        updates["initial_cash"] = initial_cash
    if args.user_timezone is not None:
        updates["user_timezone"] = _timezone_name(args.user_timezone)
    if args.research_access is not None:
        updates["research_access"] = args.research_access
        updates["research_checked_at"] = now()
    if args.automation is not None:
        updates["automation_preference"] = args.automation
        if args.automation == "SKIPPED":
            updates["delivery_preference"] = "NOT_ASKED"
            updates["delivery_target"] = None
            updates["delivery_confirmed_at"] = None
    if args.delivery_target is not None:
        delivery_target = args.delivery_target.strip()
        if not delivery_target:
            raise ValueError("delivery target cannot be empty")
        if len(delivery_target) > 512 or any(char.isspace() for char in delivery_target):
            raise ValueError(
                "delivery target must be a configured Hermes target without whitespace"
            )
        if delivery_target.lower() == "local":
            updates["delivery_preference"] = "LOCAL"
            updates["delivery_target"] = "local"
        else:
            if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*(?::[^\s]+)?", delivery_target):
                raise ValueError(
                    "delivery target must use platform or platform:destination format"
                )
            updates["delivery_preference"] = "MESSAGING"
            updates["delivery_target"] = delivery_target
        updates["delivery_confirmed_at"] = now()
    if args.dashboard is not None:
        updates["dashboard_preference"] = args.dashboard

    with connect() as conn:
        current = conn.execute("SELECT * FROM investor_profile WHERE id = 1").fetchone()
        if not current:
            raise ValueError("Harper profile is unavailable")
        effective_research_access = updates.get(
            "research_access", current["research_access"]
        )
        effective_automation = updates.get(
            "automation_preference", current["automation_preference"]
        )
        effective_currency = updates.get("base_currency", current["base_currency"])
        if args.initial_cash is not None and not effective_currency:
            raise ValueError("set the reporting currency before initial cash")
        if args.automation == "ENABLED" and effective_research_access != "FULL":
            raise ValueError(
                "automated sessions require verified FULL web search and source extraction"
            )
        if args.delivery_target is not None and effective_automation != "ENABLED":
            raise ValueError(
                "choose a delivery target only after automated sessions are enabled"
            )
        if "market" in updates:
            _ensure_discovery_adapter(
                conn,
                updates["market"],
                args.market.strip(),
                currency=updates.get("base_currency"),
            )
        target_market = updates.get("market") or current["market"]
        if target_market:
            adapter_updates = {}
            adapter = _get_adapter(conn, target_market)
            if adapter and not adapter.get("native_currency") and updates.get("base_currency"):
                adapter_updates["native_currency"] = updates["base_currency"]
            if adapter_updates:
                assignments = ", ".join(f"{key} = ?" for key in adapter_updates)
                conn.execute(
                    f"UPDATE market_adapters SET {assignments}, updated_at = ? WHERE market_id = ?",
                    (*adapter_updates.values(), now(), target_market),
                )
        changed_scope = any(
            field in updates and current[field] and current[field] != updates[field]
            for field in ("market", "base_currency")
        )
        financial_history = sum(
            int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in ("holdings", "trades", "snapshots")
        )
        if args.initial_cash is not None and financial_history:
            raise ValueError("initial cash cannot change after financial history exists")
        if changed_scope and financial_history:
            raise ValueError(
                "market or reporting currency cannot change after financial history exists; "
                "a separate sourced migration must preserve historical values"
            )
        if changed_scope and args.confirm_scope_change != "CHANGE-HARPER-SCOPE":
            raise ValueError(
                "changing an established market or currency requires "
                "--confirm-scope-change CHANGE-HARPER-SCOPE; historical values are not converted"
            )
        currency_changed = (
            "base_currency" in updates
            and current["base_currency"]
            and current["base_currency"] != updates["base_currency"]
        )
        if currency_changed and args.initial_cash is None:
            updates["initial_cash"] = None
            suggested = _suggested_initial_cash(updates["base_currency"])
            conn.execute(
                "INSERT OR REPLACE INTO state(key, value) VALUES ('cash', ?)",
                (str(suggested),),
            )
            conn.execute(
                "INSERT OR REPLACE INTO state(key, value) VALUES ('initial_cash', ?)",
                (str(suggested),),
            )
        elif args.initial_cash is not None:
            conn.execute(
                "INSERT OR REPLACE INTO state(key, value) VALUES ('cash', ?)",
                (str(updates["initial_cash"]),),
            )
            conn.execute(
                "INSERT OR REPLACE INTO state(key, value) VALUES ('initial_cash', ?)",
                (str(updates["initial_cash"]),),
            )
        values = dict(current)
        values.update(updates)
        values["onboarding_version"] = ONBOARDING_VERSION
        completion = (
            current["onboarding_completed_at"]
            if int(current["onboarding_version"]) == ONBOARDING_VERSION
            else None
        )
        if _profile_complete(values):
            completion = completion or now()
        else:
            completion = None
        assignments = ", ".join(f"{field} = ?" for field in updates)
        conn.execute(
            f"UPDATE investor_profile SET {assignments}, onboarding_completed_at = ?, "
            "onboarding_version = ?, updated_at = ? WHERE id = 1",
            (*updates.values(), completion, ONBOARDING_VERSION, now()),
        )
        payload = _profile_payload(conn)
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def _validate_preference_key(value: str) -> str:
    key = value.strip()
    if not key or len(key) > 80:
        raise ValueError("preference key must contain 1 to 80 characters")
    return key


def cmd_profile_preference(args: argparse.Namespace) -> None:
    with connect() as conn:
        row = conn.execute(
            "SELECT optional_preferences_json FROM investor_profile WHERE id = 1"
        ).fetchone()
        preferences = json.loads(row["optional_preferences_json"] or "{}")
        if args.preference_action == "set":
            key = _validate_preference_key(args.key)
            value = args.value.strip()
            if not value:
                raise ValueError("preference value cannot be empty")
            preferences[key] = value
        elif args.preference_action == "delete":
            preferences.pop(_validate_preference_key(args.key), None)
        elif args.preference_action == "reset":
            if args.confirm != "RESET-HARPER-PREFERENCES":
                raise ValueError(
                    "preference reset requires --confirm RESET-HARPER-PREFERENCES"
                )
            preferences = {}
        conn.execute(
            "UPDATE investor_profile SET optional_preferences_json = ?, updated_at = ? WHERE id = 1",
            (json.dumps(preferences, ensure_ascii=False, sort_keys=True), now()),
        )
        payload = _profile_payload(conn)
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def _adapter_health(adapter: dict) -> dict:
    missing_core = [
        key for key in ("native_currency",)
        if not adapter.get(key)
    ]
    schedule = adapter.get("session_schedule", {})
    sources = adapter.get("sources", {})
    cost_model = adapter.get("cost_model", {})
    missing_optional = []
    if not adapter.get("benchmark_ticker"):
        missing_optional.append("benchmark")
    if not cost_model:
        missing_optional.append("market_specific_costs")
    if not sources.get("regulatory"):
        missing_optional.append("regulatory_sources")
    if not sources.get("quote"):
        missing_optional.append("preferred_quote_sources")
    if not schedule.get("sessions"):
        missing_optional.append("automated_session_schedule")
    if not adapter.get("market_timezone"):
        missing_optional.append("verified_market_timezone")
    return {
        "operable": not missing_core,
        "missing_core": missing_core,
        "missing_optional": missing_optional,
        "benchmark_mode": "ADAPTER" if adapter.get("benchmark_ticker") else "ABSOLUTE_RETURN_ONLY",
        "cost_mode": "ADAPTER" if cost_model else "CONSERVATIVE_FALLBACK",
        "regulatory_mode": (
            "SOURCED_CONTEXT" if sources.get("regulatory") else "UNVERIFIED_VIRTUAL_SIMULATION"
        ),
        "automation_available": bool(
            adapter.get("market_timezone") and schedule.get("sessions")
        ),
        "market_time_mode": (
            "ADAPTER" if adapter.get("market_timezone") else "USER_TIMEZONE_PROVISIONAL"
        ),
    }


def _adapter_payload(conn: sqlite3.Connection, market_id: str) -> dict:
    adapter = _get_adapter(conn, market_id)
    if not adapter:
        raise ValueError(f"market adapter {market_id!r} does not exist")
    adapter["health"] = _adapter_health(adapter)
    adapter["effective_cost_model"] = (
        adapter["cost_model"] or GENERIC_COST_FALLBACK
    )
    adapter["evidence"] = [
        {
            **dict(row),
            "value": json.loads(row["value_json"]),
        }
        for row in conn.execute(
            """SELECT evidence_type, value_json, source_url, effective_at, recorded_at
               FROM market_adapter_evidence WHERE market_id=? ORDER BY id DESC LIMIT 50""",
            (market_id,),
        )
    ]
    for item in adapter["evidence"]:
        item.pop("value_json", None)
    return adapter


def cmd_market_adapter_show(args: argparse.Namespace) -> None:
    with connect() as conn:
        if args.market:
            payload = _adapter_payload(conn, _market_id(args.market))
        else:
            payload = [
                _adapter_payload(conn, str(row["market_id"]))
                for row in conn.execute(
                    "SELECT market_id FROM market_adapters ORDER BY display_name"
                )
            ]
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def _json_cli_object(value: str | None, label: str) -> dict:
    if value is None:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} must be a JSON object") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"{label} must be a JSON object")
    return parsed


def cmd_market_adapter_set(args: argparse.Namespace) -> None:
    market_id = _market_id(args.market)
    source = args.source
    factual_update_requested = any((
        args.market_timezone,
        args.benchmark_ticker is not None,
        args.ticker_pattern is not None,
        args.market_open,
        args.market_close,
        args.intraday_exit,
        args.sessions_json,
        args.fee_bps is not None,
        args.slippage_large_bps is not None,
        args.slippage_mid_bps is not None,
        args.slippage_small_bps is not None,
        args.capabilities_json,
        args.status in {"LIMITED", "OPERATIONAL"},
    ))
    if factual_update_requested and not source:
        raise ValueError(
            "market capability updates require a public --source URL; "
            "leave unknown fields unset instead of guessing"
        )
    if args.source_kind and not source:
        raise ValueError("--source-kind requires --source")
    if source is not None:
        source = _public_urls(source, "market adapter evidence", minimum=1)[0]
    with connect() as conn:
        _ensure_discovery_adapter(
            conn, market_id, args.display_name or args.market,
            timezone_name=_timezone_name(args.market_timezone) if args.market_timezone else None,
            currency=_currency_code(args.native_currency) if args.native_currency else None,
        )
        current = _get_adapter(conn, market_id) or {}
        updates: dict[str, object] = {}
        if args.display_name:
            updates["display_name"] = args.display_name.strip()
        if args.market_timezone:
            updates["market_timezone"] = _timezone_name(args.market_timezone)
        if args.native_currency:
            updates["native_currency"] = _currency_code(args.native_currency)
        if args.benchmark_ticker is not None:
            updates["benchmark_ticker"] = args.benchmark_ticker.upper().strip() or None
        if args.ticker_pattern is not None:
            try:
                re.compile(args.ticker_pattern)
            except re.error as exc:
                raise ValueError(f"ticker pattern is invalid: {exc}") from exc
            updates["ticker_pattern"] = args.ticker_pattern or None

        schedule = dict(current.get("session_schedule") or {})
        if args.market_open:
            _parse_hhmm(args.market_open, "market open")
            schedule["market_open"] = args.market_open
        if args.market_close:
            _parse_hhmm(args.market_close, "market close")
            schedule["market_close"] = args.market_close
        if args.intraday_exit:
            _parse_hhmm(args.intraday_exit, "intraday exit")
            schedule["intraday_exit"] = args.intraday_exit
        if args.sessions_json:
            sessions = json.loads(args.sessions_json)
            if not isinstance(sessions, list):
                raise ValueError("sessions-json must be a JSON array")
            schedule["sessions"] = sessions
        if schedule != current.get("session_schedule"):
            updates["session_schedule_json"] = json.dumps(schedule, ensure_ascii=False)

        cost_model = dict(current.get("cost_model") or {})
        for key in (
            "fee_bps", "slippage_large_bps", "slippage_mid_bps", "slippage_small_bps"
        ):
            value = getattr(args, key)
            if value is not None:
                if value < 0:
                    raise ValueError(f"{key.replace('_', '-')} cannot be negative")
                cost_model[key] = float(value)
        if cost_model != current.get("cost_model"):
            cost_model["source"] = source or "UNVERIFIED_USER_OR_AGENT_INPUT"
            updates["cost_model_json"] = json.dumps(cost_model, sort_keys=True)

        sources = dict(current.get("sources") or {})
        if source and args.source_kind:
            urls = list(sources.get(args.source_kind) or [])
            if source not in urls:
                urls.append(source)
            sources[args.source_kind] = urls
            updates["sources_json"] = json.dumps(sources, sort_keys=True)

        capabilities = _json_cli_object(args.capabilities_json, "capabilities-json")
        if capabilities:
            merged = dict(current.get("capabilities") or {})
            merged.update(capabilities)
            updates["capabilities_json"] = json.dumps(merged, sort_keys=True)
        if args.effective_at:
            _parse_timestamp(args.effective_at, "adapter evidence effective-at")
        if not args.status and current.get("status") == "DISCOVERY":
            prospective_timezone = updates.get("market_timezone") or current.get("market_timezone")
            prospective_currency = updates.get("native_currency") or current.get("native_currency")
            if prospective_timezone and prospective_currency and (
                schedule.get("market_open") or sources.get("quote")
            ):
                updates["status"] = "LIMITED"
        if args.status:
            updates["status"] = args.status
        if args.status == "OPERATIONAL":
            prospective_timezone = updates.get("market_timezone") or current.get("market_timezone")
            prospective_currency = updates.get("native_currency") or current.get("native_currency")
            prospective_sources = sources or current.get("sources") or {}
            if not prospective_timezone or not prospective_currency or not prospective_sources:
                raise ValueError(
                    "OPERATIONAL requires a sourced market timezone, native currency, "
                    "and at least one recorded source"
                )
        if not updates and not source:
            raise ValueError("market-adapter set requires at least one update")

        if updates:
            updates["version"] = int(current.get("version") or 0) + 1
            updates["updated_at"] = now()
            if args.status == "OPERATIONAL":
                updates["last_validated_at"] = now()
            assignments = ", ".join(f"{field} = ?" for field in updates)
            conn.execute(
                f"UPDATE market_adapters SET {assignments} WHERE market_id = ?",
                (*updates.values(), market_id),
            )
        if source:
            evidence_value = {
                key: value for key, value in updates.items()
                if key not in {"updated_at", "last_validated_at"}
            }
            conn.execute(
                """INSERT INTO market_adapter_evidence(
                       market_id, evidence_type, value_json, source_url,
                       effective_at, recorded_at
                   ) VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    market_id, args.source_kind or "general",
                    json.dumps(evidence_value, ensure_ascii=False, default=str),
                    source, args.effective_at, now(),
                ),
            )
        payload = _adapter_payload(conn, market_id)
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def cmd_market_adapter_schedule(args: argparse.Namespace) -> None:
    with connect() as conn:
        market_id = _market_id(args.market) if args.market else None
        adapter = _get_adapter(conn, market_id)
        if not adapter:
            raise ValueError("select or create a market adapter before generating a schedule")
        profile = conn.execute("SELECT user_timezone FROM investor_profile WHERE id=1").fetchone()
        user_timezone = args.user_timezone or (profile["user_timezone"] if profile else None)
        if not user_timezone:
            raise ValueError("confirm a user timezone before generating a schedule")
        user_timezone = _timezone_name(str(user_timezone))
        market_timezone = adapter.get("market_timezone")
        schedule = adapter.get("session_schedule") or {}
        sessions = schedule.get("sessions") or []
        if not market_timezone or not sessions:
            payload = {
                "available": False,
                "market_id": adapter["market_id"],
                "reason": "adapter needs a market timezone and session schedule",
                "requires_confirmation": True,
                "creates_jobs": False,
            }
        else:
            market_zone = ZoneInfo(str(market_timezone))
            user_zone = ZoneInfo(user_timezone)
            reference_date = _market_today(conn)
            converted = []
            for session in sessions:
                hour, minute = _parse_hhmm(str(session["time"]), "session time")
                market_dt = datetime.combine(
                    reference_date, datetime.min.time(), tzinfo=market_zone
                ).replace(hour=hour, minute=minute)
                local_dt = market_dt.astimezone(user_zone)
                converted.append({
                    **session,
                    "market_time": market_dt.strftime("%H:%M"),
                    "market_timezone": str(market_timezone),
                    "user_time": local_dt.strftime("%H:%M"),
                    "user_date_offset_days": (local_dt.date() - market_dt.date()).days,
                    "user_timezone": user_timezone,
                })
            payload = {
                "available": True,
                "market_id": adapter["market_id"],
                "sessions": converted,
                "installation_mode": "TIMEZONE_AWARE_DISPATCHER",
                "daylight_saving_safe": True,
                "requires_confirmation": True,
                "creates_jobs": False,
                "note": (
                    "This is a preview. A lightweight dispatcher must evaluate the adapter's "
                    "IANA market timezone; static converted cron expressions are not safe."
                ),
            }
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def cmd_market_session_confirm(args: argparse.Namespace) -> None:
    market_date = _valid_date(args.market_date, "market date")
    sources = _public_urls(args.source, "market-session confirmation", minimum=1)
    if len(sources) != 1:
        raise ValueError("market-session confirmation requires exactly one official URL")
    with connect() as conn:
        adapter = _get_adapter(conn) or {}
        schedule = adapter.get("session_schedule") or {}
        open_time = args.open_time or schedule.get("market_open")
        close_time = args.close_time or schedule.get("market_close")
        if not open_time or not close_time:
            raise ValueError(
                "this adapter has no session hours yet; provide --open-time and --close-time"
            )
        if market_date != _market_today(conn).isoformat():
            raise ValueError(
                f"market-session confirmation must be for today's {_market_timezone(conn).key} date"
            )
        open_minute = _minutes_since_midnight(open_time, "market open")
        close_minute = _minutes_since_midnight(close_time, "market close")
        if args.status in {"OPEN", "SPECIAL"} and open_minute >= close_minute:
            raise ValueError("market close must be later than market open")
        for key, value in (
            ("market_session_date", market_date),
            ("market_session_status", args.status),
            ("market_session_open", open_time),
            ("market_session_close", close_time),
            ("market_session_source", sources[0]),
            ("market_session_confirmed_at", now()),
        ):
            conn.execute("INSERT OR REPLACE INTO state(key, value) VALUES (?, ?)", (key, value))
    print(json.dumps({
        "market_date": market_date,
        "status": args.status,
        "open": open_time,
        "close": close_time,
        "source": sources[0],
    }))


def cmd_reset(args: argparse.Namespace) -> None:
    """Clear Harper's active and archived history while retaining feed definitions."""
    if args.confirm != "RESET-HARPER":
        raise ValueError("reset requires --confirm RESET-HARPER")
    reset_tables = (
        "candidate_outcomes", "candidate_evaluations", "opportunity_audits",
        "corporate_actions", "decisions", "decision_journal", "evidence_claims",
        "historical_prices", "holdings", "intel_articles", "learning_log",
        "market_feed", "quotes", "research_library", "runs", "snapshots",
        "source_scores", "state", "theses", "trades",
    )
    with connect() as conn:
        reset_initial_cash = _chosen_initial_cash(conn)
        _ensure_archive_schema(conn)
        removed = {
            table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in reset_tables
        }
        removed_archive_rows = int(
            conn.execute("SELECT COUNT(*) FROM archive_store.records").fetchone()[0]
        )
        removed_archive_tombstones = int(
            conn.execute(
                "SELECT COUNT(*) FROM archive_store.dedupe_tombstones"
            ).fetchone()[0]
        )
        preserved_sources = int(
            conn.execute("SELECT COUNT(*) FROM intel_sources").fetchone()[0]
        )
        for table in reset_tables:
            conn.execute(f"DELETE FROM {table}")
        conn.execute(
            """UPDATE intel_sources SET enabled=1, last_fetch_at=NULL,
                   total_fetched=0, unique_count=0, duplicate_count=0,
                   ticker_mentions=0, reason_disabled=NULL"""
        )
        conn.execute("DELETE FROM archive_store.records")
        conn.execute("DELETE FROM archive_store.dedupe_tombstones")
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sqlite_sequence'"
        ).fetchone():
            placeholders = ",".join("?" for _ in reset_tables)
            conn.execute(
                f"DELETE FROM sqlite_sequence WHERE name IN ({placeholders})",
                reset_tables,
            )
        ensure_schema(conn)
        conn.execute(
            "INSERT OR REPLACE INTO state(key, value) VALUES ('cash', ?)",
            (str(reset_initial_cash),),
        )
        conn.execute(
            "INSERT OR REPLACE INTO state(key, value) VALUES ('initial_cash', ?)",
            (str(reset_initial_cash),),
        )
        conn.execute(
            "INSERT INTO decision_journal(entry_type, content, timestamp) VALUES (?, ?, ?)",
            (
                "initial",
                f"Harper restarted with {_money(conn, reset_initial_cash)} in virtual cash.",
                now(),
            ),
        )
        conn.execute(
            "INSERT OR REPLACE INTO state(key, value) VALUES ('lifecycle_last_maintained_at', ?)",
            (now(),),
        )
    print(json.dumps({
        "reset": True,
        "legacy_rows_removed": (
            sum(removed.values()) + removed_archive_rows + removed_archive_tombstones
        ),
        "removed_by_table": removed,
        "removed_archive_rows": removed_archive_rows,
        "removed_archive_tombstones": removed_archive_tombstones,
        "preserved_feed_definitions": preserved_sources,
        "cash": reset_initial_cash,
    }, indent=2))


def cmd_maintain(args: argparse.Namespace) -> None:
    with connect() as conn:
        result = run_maintenance(conn, dry_run=args.dry_run)
        if args.dry_run:
            result["candidate_outcomes"] = {"marked": 0, "dry_run": True}
            result["opportunity_audit"] = run_opportunity_audit(
                conn, sessions=5, threshold_pct=25.0, persist=False
            )
        else:
            result["candidate_outcomes"] = mark_candidate_outcomes(conn)
            result["opportunity_audit"] = run_opportunity_audit(
                conn, sessions=5, threshold_pct=25.0, persist=True
            )
    if not args.quiet:
        print(json.dumps(result, indent=2))


def cmd_quote(args: argparse.Namespace) -> None:
    ticker = _validate_indian_ticker(args.ticker)
    if args.price <= 0:
        raise ValueError("price must be positive")
    if not args.source.startswith(("http://", "https://")):
        raise ValueError("quote source must be a public http(s) URL")
    stamp = args.asof or now()
    parsed_stamp = _parse_timestamp(stamp, "quote as-of")
    if parsed_stamp > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise ValueError("quote as-of cannot be more than five minutes in the future")
    with connect() as conn:
        benchmark_ticker = _benchmark_ticker(conn)
        conn.execute(
            "INSERT INTO quotes(ticker, price, source, asof, recorded_at) VALUES (?, ?, ?, ?, ?)",
            (ticker, round(args.price, 4), args.source, stamp, now()),
        )
        if benchmark_ticker and ticker == benchmark_ticker:
            if _state_text(conn, "benchmark_initial_ticker", "") != benchmark_ticker:
                conn.execute("INSERT OR REPLACE INTO state(key, value) VALUES ('benchmark_initial_price', ?)",
                             (str(round(args.price, 4)),))
                conn.execute("INSERT OR REPLACE INTO state(key, value) VALUES ('benchmark_initial_ticker', ?)",
                             (benchmark_ticker,))
        rendered_price = _money(conn, args.price)
    print(f"QUOTE {ticker} {rendered_price} as of {stamp}")


def cmd_thesis_set(args: argparse.Namespace) -> None:
    ticker = _validate_indian_ticker(args.ticker)
    if args.direction != "LONG":
        raise ValueError("Harper is long-only; thesis direction must be LONG")
    sources = _public_urls(args.sources, "a thesis", minimum=2)
    primary_sources = _public_urls(args.primary_sources, "primary evidence", minimum=1)
    if not set(primary_sources).issubset(set(sources)):
        raise ValueError("every primary source must also appear in --sources")
    thesis_type = args.thesis_type.upper()
    contract: dict[str, str] = {}
    resolution_source = None
    resolution_date = None
    if thesis_type == "CATALYST":
        if not args.event or not args.resolution_date or not args.resolution_source:
            raise ValueError("CATALYST thesis requires --event, --resolution-date, and --resolution-source")
        resolution_sources = _public_urls(args.resolution_source, "resolution source", minimum=1)
        if len(resolution_sources) != 1:
            raise ValueError("resolution source must be one authoritative public URL")
        resolution_source = resolution_sources[0]
        if resolution_source not in primary_sources:
            raise ValueError("resolution source must be included in --primary-sources")
        resolution_date = _valid_date(args.resolution_date, "resolution date")
        if datetime.strptime(resolution_date, "%Y-%m-%d").date() < india_today():
            raise ValueError("resolution date cannot be in the past")
        contract = {"event": args.event.strip(), "resolution_date": resolution_date, "resolution_source": resolution_source}
    else:
        if not args.review_date:
            raise ValueError(f"{thesis_type} thesis requires --review-date")
        resolution_date = _valid_date(args.review_date, "review date")
        if datetime.strptime(resolution_date, "%Y-%m-%d").date() < india_today():
            raise ValueError("review date cannot be in the past")
        if thesis_type == "QUALITY":
            if not args.quality_trajectory:
                raise ValueError("QUALITY thesis requires --quality-trajectory")
            contract = {"quality_trajectory": args.quality_trajectory.strip()}
        elif thesis_type == "VALUE":
            if not args.valuation_gap or not args.rerating_condition:
                raise ValueError("VALUE thesis requires --valuation-gap and --rerating-condition")
            contract = {"valuation_gap": args.valuation_gap.strip(), "rerating_condition": args.rerating_condition.strip()}
        elif thesis_type == "MOMENTUM":
            if not args.trend_condition or not args.technical_invalidation:
                raise ValueError("MOMENTUM thesis requires --trend-condition and --technical-invalidation")
            contract = {"trend_condition": args.trend_condition.strip(), "technical_invalidation": args.technical_invalidation.strip()}
        contract["review_date"] = resolution_date
    narrative_fields = {
        "horizon": args.horizon, "invalidation": args.invalidation,
        "catalyst": args.catalyst, "variant view": args.variant,
        "sector": args.sector, "counter-thesis": args.counter_thesis,
        "financial summary": args.financial_summary,
    }
    missing = [name for name, value in narrative_fields.items() if not value.strip()]
    if missing:
        raise ValueError("required thesis fields are empty: " + ", ".join(missing))
    horizon = f"{args.trade_style}:{args.horizon.strip()}"
    payoff = _thesis_payoff(
        args.entry_reference, args.target, args.invalidation_price
    )
    probability = args.confidence / 100
    stamp = now()
    with connect() as conn:
        per_leg_bps = (
            _effective_cost_bps(conn, "fee_bps")
            + _effective_cost_bps(conn, "slippage_large_bps")
        )
        round_trip_cost_pct = 2 * per_leg_bps / 100
        net_reward = payoff["reward_pct"] - round_trip_cost_pct
        net_risk = payoff["risk_pct"] + round_trip_cost_pct
        net_reward_risk = net_reward / net_risk if net_risk > 0 else 0.0
        if net_reward <= 0 or net_reward_risk < 1.5:
            raise ValueError(
                f"thesis net reward/risk is {net_reward_risk:.2f}; minimum is 1.50 after costs"
            )
        ev_model, scenario, investment_probability_pct, scenario_ev = _scenario_expected_return(
            args, round_trip_cost_pct
        )
        investment_probability = investment_probability_pct / 100
        expected_move = scenario_ev if ev_model == "SCENARIO" else (
            investment_probability * payoff["reward_pct"]
            - (1 - investment_probability) * payoff["risk_pct"]
            - round_trip_cost_pct
        )
        if expected_move <= 0:
            raise ValueError(
                f"thesis expected return is not positive after costs ({expected_move:.2f}%)"
            )
        conn.execute(
            """INSERT INTO theses(
                   ticker, direction, confidence, horizon, target, invalidation,
                   catalyst, variant_view, sources_json, status, created_at, updated_at,
                   forecast_event, resolution_date, resolution_source,
                   invalidation_price, entry_reference, sector, counter_thesis,
                   financial_summary, primary_sources_json, event_outcome, brier_component,
                   investment_success_probability, ev_model, scenario_json, expected_return_pct,
                   thesis_type, thesis_contract_json, review_date
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(ticker) DO UPDATE SET
                   direction=excluded.direction, confidence=excluded.confidence,
                   horizon=excluded.horizon, target=excluded.target,
                   invalidation=excluded.invalidation, catalyst=excluded.catalyst,
                   variant_view=excluded.variant_view, sources_json=excluded.sources_json,
                   forecast_event=excluded.forecast_event,
                   resolution_date=excluded.resolution_date,
                   resolution_source=excluded.resolution_source,
                   invalidation_price=excluded.invalidation_price,
                   entry_reference=excluded.entry_reference, sector=excluded.sector,
                   counter_thesis=excluded.counter_thesis,
                   financial_summary=excluded.financial_summary,
                   primary_sources_json=excluded.primary_sources_json,
                   investment_success_probability=excluded.investment_success_probability,
                   ev_model=excluded.ev_model, scenario_json=excluded.scenario_json,
                   expected_return_pct=excluded.expected_return_pct,
                   thesis_type=excluded.thesis_type, thesis_contract_json=excluded.thesis_contract_json,
                   review_date=excluded.review_date,
                   event_outcome=NULL, brier_component=NULL, outcome=NULL, lesson=NULL,
                   closed_at=NULL, exit_reason=NULL, timing_accuracy=NULL,
                   was_calibrated=NULL, status='ACTIVE', updated_at=excluded.updated_at""",
            (
                ticker,
                args.direction,
                args.confidence,
                horizon,
                args.target,
                args.invalidation,
                args.catalyst,
                args.variant,
                json.dumps(sources),
                stamp,
                stamp,
                args.event.strip() if args.event else None, resolution_date, resolution_source,
                args.invalidation_price, args.entry_reference, args.sector.strip(),
                args.counter_thesis.strip(), args.financial_summary.strip(),
                json.dumps(primary_sources), investment_probability_pct, ev_model,
                json.dumps(scenario, separators=(",", ":"), sort_keys=True),
                round(expected_move, 6), thesis_type,
                json.dumps(contract, separators=(",", ":"), sort_keys=True), resolution_date,
            ),
        )
    print(json.dumps({"ticker": ticker, "direction": args.direction,
                      "trade_style": args.trade_style,
                      "confidence_pct": args.confidence,
                      "net_reward_risk": round(net_reward_risk, 2),
                      "estimated_round_trip_cost_pct": round(round_trip_cost_pct, 3),
                      "expected_move_pct": round(expected_move, 2),
                      "event_confidence_pct": args.confidence,
                      "investment_success_probability_pct": investment_probability_pct,
                      "ev_model": ev_model,
                      "scenario": scenario, "thesis_type": thesis_type,
                      "thesis_contract": contract, "review_date": resolution_date}))


def cmd_thesis_close(args: argparse.Namespace) -> None:
    ticker = args.ticker.upper()
    with connect() as conn:
        row = conn.execute(
            "SELECT status, confidence, forecast_event, thesis_type FROM theses WHERE ticker=?", (ticker,)
        ).fetchone()
        if not row:
            raise ValueError(f"no thesis exists for {ticker}")
        if row["status"] not in ("ACTIVE", "PENDING_RESOLUTION"):
            raise ValueError(f"thesis for {ticker} is already closed")
        open_position = conn.execute(
            "SELECT shares FROM holdings WHERE ticker=? AND ABS(shares) > 0", (ticker,)
        ).fetchone()
        if open_position:
            raise ValueError(
                f"close the {ticker} position before resolving its thesis"
            )
        thesis_type = str(row["thesis_type"] or "CATALYST")
        event_outcome = args.event_outcome
        if thesis_type == "CATALYST" and event_outcome is None:
            raise ValueError("CATALYST thesis closure requires --event-outcome")
        if thesis_type != "CATALYST" and event_outcome is not None:
            raise ValueError(f"{thesis_type} thesis does not use binary event resolution")
        if row["status"] == "PENDING_RESOLUTION" and event_outcome == "UNRESOLVED":
            raise ValueError("a pending thesis must be resolved YES or NO")
        brier = None
        if event_outcome in ("YES", "NO"):
            observed = 1 if event_outcome == "YES" else 0
            brier = ((float(row["confidence"]) / 100) - observed) ** 2
        next_status = "PENDING_RESOLUTION" if event_outcome == "UNRESOLVED" else "CLOSED"
        stamp = now()
        conn.execute(
            """UPDATE theses
               SET status=?, outcome=?, lesson=?, exit_reason=?,
                   timing_accuracy=?, event_outcome=?, brier_component=?,
                   was_calibrated=NULL, closed_at=COALESCE(closed_at, ?), updated_at=?
               WHERE ticker=?""",
            (next_status, args.outcome, args.lesson.strip(), args.exit_reason,
             args.timing, event_outcome, brier, stamp, stamp, ticker),
        )
    print(json.dumps({"ticker": ticker, "trade_outcome": args.outcome,
                      "event_outcome": event_outcome,
                      "status": next_status,
                      "brier_component": round(brier, 4) if brier is not None else None}))



def cmd_trade(args: argparse.Namespace) -> None:
    """Execute a sourced virtual fill after deterministic cost and risk gates."""
    ticker = _validate_indian_ticker(args.ticker)
    action = args.action.upper()
    shares = float(args.shares)
    observed_price = float(args.price)
    if action not in {"BUY", "SELL"}:
        raise ValueError("Harper is long-only; action must be BUY or SELL")
    if shares <= 0 or not shares.is_integer() or observed_price <= 0 or not args.reason.strip():
        raise ValueError("positive whole shares, a positive sourced quote, and a reason are required")

    with connect() as conn:
        thesis = conn.execute(
            """SELECT direction, confidence, horizon, entry_reference, invalidation_price,
                      target, sector, counter_thesis, financial_summary,
                      forecast_event, resolution_date, resolution_source,
                      primary_sources_json, investment_success_probability, expected_return_pct,
                      thesis_type, thesis_contract_json, review_date
               FROM theses WHERE ticker=? AND status='ACTIVE'""", (ticker,)
        ).fetchone()
        trade_style = None
        if action == "BUY":
            if not thesis or thesis["direction"] != "LONG":
                raise ValueError("an active LONG thesis is required before buying")
            trade_style = _trade_style(thesis["horizon"])
            overdue = _intraday_positions(conn)
            overdue = [item["ticker"] for item in overdue if item["overdue"]]
            if overdue:
                raise ValueError(
                    "close overdue intraday positions before adding risk: " + ", ".join(overdue)
                )
        _enforce_market_session(
            conn, opening_intraday=(action == "BUY" and trade_style == "INTRADAY")
        )
        quote = _fresh_trade_quote(conn, ticker, observed_price)
        source_price = float(quote["price"])
        slip_bps = _effective_cost_bps(conn, f"slippage_{args.liquidity}_bps")
        execution_price = round(source_price * (
            1 + slip_bps / 10_000 if action == "BUY"
            else 1 - slip_bps / 10_000
        ), 4)
        total = round(shares * execution_price, 2)
        fees = round(total * _effective_cost_bps(conn, "fee_bps") / 10_000, 2)
        slippage = round(abs(execution_price - source_price) * shares, 2)

        cash = state_float(conn, "cash", LEGACY_INITIAL_CASH)
        realized_total = state_float(conn, "realized_pnl", 0.0)
        gross_realized_total = state_float(conn, "gross_realized_pnl", 0.0)
        costs_total = state_float(conn, "trading_costs", 0.0)
        existing = conn.execute(
            "SELECT shares, avg_cost_basis, opened_at FROM holdings WHERE ticker=?", (ticker,)
        ).fetchone()
        old_signed = float(existing["shares"]) if existing else 0.0
        old_cost = float(existing["avg_cost_basis"]) if existing else 0.0

        if old_signed < 0:
            raise ValueError(
                f"legacy negative holding detected for {ticker}; reset or reconcile it before trading"
            )
        if action == "BUY":
            new_signed = old_signed + shares
        else:
            if old_signed <= 0 or shares > old_signed:
                raise ValueError(f"cannot sell {shares:g}; long position is {max(old_signed, 0):g}")
            new_signed = old_signed - shares

        if action == "BUY":
            starter_limit = state_float(conn, "strategy_starter_position_weight", 0.03)
            nav_before = float(portfolio_status(conn)["nav"])
            if args.starter:
                if existing:
                    raise ValueError("--starter is only valid for the first entry in a ticker")
                if shares * source_price / nav_before > starter_limit + 1e-9:
                    raise ValueError(f"starter position exceeds {starter_limit * 100:.1f}% of NAV")
            elif existing and float(existing["shares"]) > 0:
                current_weight = float(existing["shares"]) * source_price / nav_before
                if current_weight <= starter_limit + 1e-9:
                    if not args.confirmation_source or not args.confirmation_source.startswith(("http://", "https://")):
                        raise ValueError("adding to a starter requires --confirmation-source with a public URL")
            round_trip_cost_pct = 2 * (
                _effective_cost_bps(conn, "fee_bps") + slip_bps
            ) / 100
            _enforce_opening_risk(
                conn, ticker, shares, new_signed, source_price, thesis, existing,
                round_trip_cost_pct,
            )

        if action == "BUY":
            required_cash = total + fees
            if required_cash > cash:
                raise ValueError(
                    f"insufficient cash: need {_money(conn, required_cash)}, "
                    f"have {_money(conn, cash)}"
                )
            new_cost = ((old_signed * old_cost) + total) / new_signed
            new_cash, gross_realized_delta = cash - required_cash, 0.0
        else:
            new_cost = old_cost
            new_cash = cash + total - fees
            gross_realized_delta = shares * (execution_price - old_cost)

        realized_delta = gross_realized_delta - fees
        stamp = now()
        if abs(new_signed) < 1e-9:
            conn.execute("DELETE FROM holdings WHERE ticker=?", (ticker,))
        elif existing:
            conn.execute("UPDATE holdings SET shares=?, avg_cost_basis=?, last_updated=? WHERE ticker=?",
                         (new_signed, round(new_cost, 4), stamp, ticker))
        else:
            conn.execute(
                "INSERT INTO holdings(ticker, shares, avg_cost_basis, last_updated, opened_at) VALUES (?, ?, ?, ?, ?)",
                (ticker, new_signed, round(new_cost, 4), stamp, stamp),
            )
        for key, value in (
            ("cash", new_cash), ("realized_pnl", realized_total + realized_delta),
            ("gross_realized_pnl", gross_realized_total + gross_realized_delta),
            ("trading_costs", costs_total + fees + slippage),
        ):
            conn.execute("UPDATE state SET value=? WHERE key=?", (str(round(value, 2)), key))
        simulation_mode = "VIRTUAL_CASH"
        conn.execute(
            """INSERT INTO trades(
                   ticker, action, shares, price, total, reason, timestamp,
                   quote_price, fees, slippage, liquidity_bucket, simulation_mode
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (ticker, action, shares, execution_price, total, args.reason.strip(), stamp,
             source_price, fees, slippage, args.liquidity, simulation_mode),
        )
        conn.execute(
            "INSERT INTO decision_journal(entry_type, content, timestamp) VALUES ('trade', ?, ?)",
            (f"{action} {shares:g} {ticker} quote {_money(conn, source_price)}, "
             f"fill {_money(conn, execution_price)}, fees {_money(conn, fees)} — "
             f"{args.reason.strip()}", stamp),
        )
    print(json.dumps({"action": action, "ticker": ticker, "shares": shares,
                      "trade_style": trade_style,
                      "quote_price": round(source_price, 4),
                      "execution_price": execution_price, "fees": fees,
                      "slippage": slippage, "simulation_mode": simulation_mode,
                      "starter": bool(args.starter),
                      "confirmation_source": args.confirmation_source}))


def portfolio_status(conn: sqlite3.Connection) -> dict:
    adapter = _get_adapter(conn) or {}
    adapter_health = _adapter_health(adapter) if adapter else None
    cash = state_float(conn, "cash", LEGACY_INITIAL_CASH)
    initial = state_float(conn, "initial_cash", LEGACY_INITIAL_CASH)
    realized_pnl = state_float(conn, "realized_pnl", 0.0)
    gross_realized_pnl = state_float(conn, "gross_realized_pnl", 0.0)
    trading_costs = state_float(conn, "trading_costs", 0.0)
    holdings = []
    market_value = 0.0
    gross_value = 0.0
    stale_tickers = []
    max_age = state_float(conn, "strategy_quote_max_age_hours", 0.25)
    for row in conn.execute(
        """SELECT ticker, shares, avg_cost_basis, opened_at, quote_required_after
           FROM holdings ORDER BY ticker"""
    ):
        if float(row["shares"]) < 0:
            raise ValueError(
                f"legacy negative holding detected for {row['ticker']}; Harper is now long-only"
            )
        quote = latest_quote(conn, row["ticker"])
        if not quote:
            raise ValueError(
                f"cannot value {row['ticker']}: no sourced quote exists; "
                "cost basis is never used as market value"
            )
        if row["quote_required_after"]:
            required = _parse_timestamp(row["quote_required_after"], "corporate-action timestamp")
            if (_parse_timestamp(quote["recorded_at"], "quote recorded-at") <= required
                    or _parse_timestamp(quote["asof"], "quote as-of") <= required):
                raise ValueError(
                    f"cannot value {row['ticker']}: refresh its quote after the recorded corporate action"
                )
        price = float(quote["price"])
        quote_age = _quote_age_hours(quote)
        if quote_age > max_age:
            stale_tickers.append(row["ticker"])
        value = row["shares"] * price
        pnl = row["shares"] * (price - row["avg_cost_basis"])
        market_value += value
        gross_value += abs(value)
        thesis = conn.execute(
            "SELECT horizon FROM theses WHERE ticker=? AND status='ACTIVE'",
            (row["ticker"],),
        ).fetchone()
        style = _trade_style(thesis["horizon"]) if thesis else None
        holdings.append(
            {
                "ticker": row["ticker"],
                "direction": "LONG",
                "trade_style": style,
                "shares": row["shares"],
                "signed_shares": row["shares"],
                "avg_cost_basis": round(row["avg_cost_basis"], 2),
                "market_price": round(price, 4),
                "market_value": round(value, 2),
                "unrealized_pnl": round(pnl, 2),
                "quote_source": quote["source"] if quote else None,
                "quote_asof": quote["asof"] if quote else None,
                "quote_age_hours": round(quote_age, 2),
                "opened_at": row["opened_at"],
            }
        )
    nav = cash + market_value
    heat, missing_risk = _position_heat(conn, nav)
    recent_journal = [
        dict(row)
        for row in conn.execute(
            "SELECT entry_type, content, timestamp FROM decision_journal ORDER BY id DESC LIMIT 5"
        )
    ]
    latest_run_row = conn.execute(
        "SELECT id, market_date, session_label, status, report, created_at, completed_at FROM runs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    intraday_positions = _intraday_positions(conn)
    latest_cash_decision = conn.execute(
        "SELECT cash_reason, rationale, timestamp FROM decisions WHERE action='NO_TRADE' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    regime = _regime_status(conn, (gross_value / nav) * 100 if nav else 0)
    return {
        "market_id": adapter.get("market_id"),
        "market_adapter_status": adapter.get("status"),
        "market_adapter_health": adapter_health,
        "reporting_currency": _reporting_currency(conn),
        "cash": round(cash, 2),
        "initial_cash": round(initial, 2),
        "holdings": holdings,
        "holdings_count": len(holdings),
        "market_value": round(market_value, 2),
        "nav": round(nav, 2),
        "realized_pnl": round(realized_pnl, 2),
        "gross_realized_pnl": round(gross_realized_pnl, 2),
        "trading_costs": round(trading_costs, 2),
        "gross_exposure_pct": round((gross_value / nav) * 100, 2) if nav else None,
        "net_exposure_pct": round((market_value / nav) * 100, 2) if nav else None,
        "return": round(nav - initial, 2),
        "return_pct": round(((nav - initial) / initial) * 100, 2),
        "valuation_status": "STALE" if stale_tickers else "FRESH",
        "stale_tickers": stale_tickers,
        "portfolio_heat_pct": round(heat * 100, 2),
        "risk_data_missing": missing_risk,
        "intraday_positions": intraday_positions,
        "intraday_overdue": [item["ticker"] for item in intraday_positions if item["overdue"]],
        "recent_journal": [
            {"entry_type": j["entry_type"], "content": j["content"], "timestamp": j["timestamp"]}
            for j in recent_journal
        ],
        "latest_run": dict(latest_run_row) if latest_run_row else None,
        "exposure_regime": regime,
        "latest_cash_reason": dict(latest_cash_decision) if latest_cash_decision else None,
    }


def cmd_status(_: argparse.Namespace) -> None:
    """Print current portfolio status as JSON."""
    with connect() as conn:
        data = portfolio_status(conn)
    print(json.dumps(data, indent=2))


def cmd_export(args: argparse.Namespace) -> None:
    destination = Path(args.path).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    table_names = (
        "candidate_evaluations",
        "candidate_outcomes",
        "corporate_actions",
        "decisions",
        "decision_journal",
        "evidence_claims",
        "historical_prices",
        "holdings",
        "investor_profile",
        "market_adapters",
        "market_adapter_evidence",
        "learning_log",
        "llm_usage",
        "market_feed",
        "opportunity_audits",
        "quotes",
        "research_library",
        "runs",
        "snapshots",
        "source_scores",
        "state",
        "theses",
        "trades",
    )
    with connect() as conn:
        payload = {
            table: [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]
            for table in table_names
        }
    destination.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps({"export": str(destination), "tables": len(table_names)}))


def _backup_database(destination: Path) -> dict:
    destination = destination.expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.resolve() == db_path().resolve():
        raise ValueError("backup destination must differ from the live database")
    with connect() as source, sqlite3.connect(destination) as target:
        source.backup(target)
        result = target.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise ValueError(f"backup integrity check failed: {result}")
        tables = int(target.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
        ).fetchone()[0])
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    return {
        "backup": str(destination),
        "integrity": "ok",
        "sha256": digest,
        "table_count": tables,
        "size_bytes": destination.stat().st_size,
    }


def cmd_backup(args: argparse.Namespace) -> None:
    print(json.dumps(_backup_database(Path(args.path)), indent=2))


def cmd_release_verify_backup(args: argparse.Namespace) -> None:
    path = Path(args.path).expanduser()
    if not path.exists():
        raise ValueError(f"backup not found: {path}")
    with sqlite3.connect(path) as conn:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        schema_version = conn.execute(
            "SELECT value FROM state WHERE key='schema_version'"
        ).fetchone() if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='state'"
        ).fetchone() else None
        tables = [row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )]
    payload = {
        "path": str(path),
        "integrity": integrity,
        "schema_version": schema_version[0] if schema_version else None,
        "table_count": len(tables),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "valid": integrity == "ok" and "trades" in tables and "state" in tables,
    }
    if not payload["valid"]:
        raise ValueError("backup is not a valid Harper portfolio database")
    print(json.dumps(payload, indent=2))


def _release_preflight(conn: sqlite3.Connection) -> dict:
    required_tables = {
        "candidate_evaluations", "candidate_outcomes", "decisions", "evidence_claims",
        "holdings", "investor_profile", "market_adapters", "market_adapter_evidence",
        "intel_articles", "intel_relevance_staging", "intel_sources",
        "quotes", "runs", "snapshots", "state", "theses", "trades",
    }
    present = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    missing = sorted(required_tables - present)
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    pending_runs = int(conn.execute(
        "SELECT COUNT(*) FROM runs WHERE status='STARTED'"
    ).fetchone()[0])
    active_intraday = int(conn.execute(
        "SELECT COUNT(*) FROM theses WHERE status='ACTIVE' AND horizon LIKE 'INTRADAY:%'"
    ).fetchone()[0])
    negative_holdings = int(conn.execute(
        "SELECT COUNT(*) FROM holdings WHERE shares < 0"
    ).fetchone()[0])
    schema_value = _state_text(conn, "schema_version", "")
    benchmark_ticker = _benchmark_ticker(conn)
    benchmark_quotes = int(conn.execute(
        "SELECT COUNT(*) FROM quotes WHERE ticker=?", (benchmark_ticker,)
    ).fetchone()[0]) if benchmark_ticker else 0
    checks = {
        "database_integrity": integrity == "ok",
        "schema_current": schema_value == str(SCHEMA_VERSION),
        "required_tables_present": not missing,
        "no_negative_holdings": negative_holdings == 0,
        "no_incomplete_runs": pending_runs == 0,
        "no_active_intraday_thesis": active_intraday == 0,
        "benchmark_initialized": benchmark_quotes > 0,
        "archive_path_distinct": archive_db_path().resolve() != db_path().resolve(),
    }
    blockers = [name for name, passed in checks.items() if not passed and name not in {"benchmark_initialized"}]
    warnings = []
    if benchmark_ticker and benchmark_quotes == 0:
        warnings.append(
            f"{benchmark_ticker} has no quote yet; active return will be unavailable until initialized"
        )
    elif not benchmark_ticker:
        warnings.append(
            "the active market adapter has no benchmark; Harper will report absolute returns only"
        )
    if not archive_db_path().exists():
        warnings.append("archive.db does not exist yet; it will be created by maintenance")
    return {
        "ready": not blockers,
        "checks": checks,
        "blockers": blockers,
        "warnings": warnings,
        "missing_tables": missing,
        "database": str(db_path()),
        "archive_database": str(archive_db_path()),
        "schema_version": schema_value,
        "expected_schema_version": str(SCHEMA_VERSION),
        "decision_model_version": DECISION_MODEL_VERSION,
        "parameter_version": _parameter_version(conn),
        "schedule_version": OPERATING_SCHEDULE_VERSION,
    }


def cmd_release_preflight(args: argparse.Namespace) -> None:
    with connect() as conn:
        payload = _release_preflight(conn)
    if args.strict and (not payload["ready"] or payload["warnings"]):
        print(json.dumps(payload, indent=2))
        raise ValueError("strict release preflight failed")
    print(json.dumps(payload, indent=2))


def cmd_release_clean_start(args: argparse.Namespace) -> None:
    if args.confirm != "START-HARPER-FRESH":
        raise ValueError("clean start requires --confirm START-HARPER-FRESH")
    backup_path = Path(args.backup).expanduser()
    backup = _backup_database(backup_path)
    archive_backup = None
    live_archive = archive_db_path()
    if live_archive.exists():
        archive_destination = backup_path.with_name(backup_path.stem + "-archive" + backup_path.suffix)
        archive_destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(live_archive, archive_destination)
        with sqlite3.connect(archive_destination) as archive_conn:
            archive_integrity = archive_conn.execute("PRAGMA integrity_check").fetchone()[0]
        if archive_integrity != "ok":
            raise ValueError(f"archive backup integrity check failed: {archive_integrity}")
        archive_backup = {
            "backup": str(archive_destination),
            "integrity": "ok",
            "sha256": hashlib.sha256(archive_destination.read_bytes()).hexdigest(),
            "size_bytes": archive_destination.stat().st_size,
        }
    reset_args = argparse.Namespace(confirm="RESET-HARPER")
    # Reuse the audited reset path only after the verified backup exists.
    with contextlib.redirect_stdout(io.StringIO()):
        cmd_reset(reset_args)
    with connect() as conn:
        postflight = _release_preflight(conn)
        benchmark_ticker = _benchmark_ticker(conn)
        counts = {
            "holdings": int(conn.execute("SELECT COUNT(*) FROM holdings").fetchone()[0]),
            "trades": int(conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]),
            "theses": int(conn.execute("SELECT COUNT(*) FROM theses").fetchone()[0]),
            "decisions": int(conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]),
        }
    print(json.dumps({
        "clean_start": True,
        "backup": backup,
        "archive_backup": archive_backup,
        "postflight": postflight,
        "portfolio_counts": counts,
        "next_steps": [
            (
                f"record a fresh {benchmark_ticker} quote"
                if benchmark_ticker else
                "continue with absolute-return reporting until the adapter has a sourced benchmark"
            ),
            "run diagnostics config",
            "deploy matching Convex schema before convex-sync",
            "install the revised Hermes cron schedule only after dry-run validation",
        ],
    }, indent=2))


def _parameter_version(conn: sqlite3.Connection) -> str:
    values = {
        key: state_float(conn, f"strategy_{key}", default)
        for key, default in sorted(PARAM_DEFAULTS.items())
    }
    encoded = json.dumps(values, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "params-" + hashlib.sha256(encoded).hexdigest()[:12]


def cmd_diagnostics_schedule(_: argparse.Namespace) -> None:
    with connect() as conn:
        adapter = _get_adapter(conn) or {}
        schedule = adapter.get("session_schedule") or {}
        payload = {
            "schedule_version": OPERATING_SCHEDULE_VERSION,
            "market_id": adapter.get("market_id"),
            "timezone": adapter.get("market_timezone"),
            "sessions": schedule.get("sessions") or [],
            "automation_ready": bool(
                adapter.get("market_timezone") and schedule.get("sessions")
            ),
            "recovery_rule": (
                "map a failed claim to the latest adapter-defined market session "
                "at or before its market-local timestamp"
            ),
        }
    print(json.dumps(payload, indent=2))


def cmd_diagnostics_config(_: argparse.Namespace) -> None:
    """Print effective strategy configuration with source and version metadata."""
    with connect() as conn:
        params = {
            key: state_float(conn, f"strategy_{key}", default)
            for key, default in sorted(PARAM_DEFAULTS.items())
        }
        deviations = {
            key: {"default": PARAM_DEFAULTS[key], "effective": value}
            for key, value in params.items()
            if not math.isclose(float(value), float(PARAM_DEFAULTS[key]), rel_tol=0, abs_tol=1e-12)
        }
        regime = _regime_status(conn)
        adapter = _get_adapter(conn) or {}
        payload = {
            "schema_version": SCHEMA_VERSION,
            "decision_model_version": DECISION_MODEL_VERSION,
            "scoring_model_version": SCORING_MODEL_VERSION,
            "parameter_version": _parameter_version(conn),
            "schedule_version": OPERATING_SCHEDULE_VERSION,
            "portfolio_regime": regime,
            "market_adapter": {
                "market_id": adapter.get("market_id"),
                "status": adapter.get("status"),
                "version": adapter.get("version"),
                "health": _adapter_health(adapter) if adapter else None,
            },
            "effective_cost_model": {
                key: _effective_cost_bps(conn, key)
                for key in (
                    "fee_bps", "slippage_large_bps",
                    "slippage_mid_bps", "slippage_small_bps",
                )
            },
            "parameters": params,
            "deviations_from_defaults": deviations,
            "source": "SQLite state table; explicit learn params updates override code defaults",
            "auto_adaptation_locked_until_resolved_forecasts": int(params["min_forecasts_for_adaptation"]),
        }
    print(json.dumps(payload, indent=2))


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _intel_queue_health(conn: sqlite3.Connection) -> dict:
    rows = conn.execute(
        "SELECT staged_at FROM intel_relevance_staging WHERE batch_id IS NULL"
    ).fetchall()
    current = datetime.now(timezone.utc)
    ages = []
    for row in rows:
        try:
            ages.append(max(0.0, (current - _parse_timestamp(row["staged_at"], "staged_at")).total_seconds() / 3600))
        except ValueError:
            continue
    last_batch = conn.execute(
        "SELECT id, total_articles, passed, rejected, created_at"
        " FROM intel_relevance_batches ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    return {
        "queue_depth": len(rows),
        "oldest_age_hours": round(max(ages), 2) if ages else None,
        "p50_age_hours": round(_percentile(ages, 0.50), 2) if ages else None,
        "p95_age_hours": round(_percentile(ages, 0.95), 2) if ages else None,
        "backlog_alert": bool(ages and (len(ages) > 80 or max(ages) > 24)),
        "last_batch": dict(last_batch) if last_batch else None,
    }


def _source_quality_report(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT id, name, feed_url, enabled, total_fetched, unique_count, duplicate_count,"
        " ticker_mentions, reason_disabled, relevance_pass_rate, relevance_checked,"
        " llm_rescued_count FROM intel_sources ORDER BY id"
    ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        total = int(item.get("total_fetched") or 0)
        duplicates = int(item.get("duplicate_count") or 0)
        checked = int(item.get("relevance_checked") or 0)
        pass_rate = float(item.get("relevance_pass_rate") or 0)
        duplicate_rate = duplicates / total if total else 0.0
        domain = urllib.parse.urlparse(item["feed_url"]).netloc.lower()
        verified = conn.execute(
            "SELECT COUNT(*) AS c FROM evidence_claims WHERE source_url LIKE ?"
            " AND status IN ('ACCURATE','INACCURATE')",
            (f"%{domain}%",),
        ).fetchone()["c"] if domain else 0
        recommendation = "KEEP"
        if not item["enabled"]:
            recommendation = "DISABLED"
        elif checked >= 50 and pass_rate < 0.20:
            recommendation = "REVIEW_LOW_RELEVANCE"
        elif total >= 50 and duplicate_rate > 0.90:
            recommendation = "REVIEW_DUPLICATE_HEAVY"
        item.update({
            "duplicate_rate": round(duplicate_rate, 4),
            "relevance_pass_rate": round(pass_rate, 4),
            "verified_claims": int(verified),
            "downstream_value_proxy": int(verified) + int(item.get("llm_rescued_count") or 0),
            "recommendation": recommendation,
        })
        result.append(item)
    return result


def cmd_intel_quality(_: argparse.Namespace) -> None:
    with connect() as conn:
        print(json.dumps({
            "sources": _source_quality_report(conn),
            "queue": _intel_queue_health(conn),
            "rule": "Recommendations are diagnostic only; do not auto-disable critical exchange, regulator, issuer, or specialist sources without review.",
        }, indent=2))


def cmd_learn_report(args: argparse.Namespace) -> None:
    """Combine portfolio, forecast, rejection, cash-drag, and intel diagnostics."""
    with connect() as conn:
        status = portfolio_status(conn)
        thesis_rows = conn.execute(
            "SELECT thesis_type, horizon, status, confidence, event_outcome, brier_component,"
            " outcome, expected_return_pct FROM theses"
        ).fetchall()
        thesis_segments: dict[str, dict] = {}
        for row in thesis_rows:
            thesis_type = row["thesis_type"] or "CATALYST"
            try:
                style = _trade_style(row["horizon"])
            except ValueError:
                style = "UNKNOWN"
            key = f"{thesis_type}:{style}"
            seg = thesis_segments.setdefault(key, {
                "thesis_type": thesis_type, "trade_style": style, "count": 0,
                "active": 0, "resolved_forecasts": 0, "wins": 0, "losses": 0,
                "confidence_total": 0.0, "brier_total": 0.0, "expected_return_total": 0.0,
                "expected_return_count": 0,
            })
            seg["count"] += 1
            seg["active"] += int(row["status"] == "ACTIVE")
            seg["confidence_total"] += float(row["confidence"] or 0)
            if row["event_outcome"] in ("YES", "NO") and row["brier_component"] is not None:
                seg["resolved_forecasts"] += 1
                seg["brier_total"] += float(row["brier_component"])
            seg["wins"] += int(row["outcome"] == "WIN")
            seg["losses"] += int(row["outcome"] == "LOSS")
            if row["expected_return_pct"] is not None:
                seg["expected_return_total"] += float(row["expected_return_pct"])
                seg["expected_return_count"] += 1
        segments = []
        for seg in thesis_segments.values():
            count = seg.pop("count")
            confidence_total = seg.pop("confidence_total")
            brier_total = seg.pop("brier_total")
            expected_total = seg.pop("expected_return_total")
            expected_count = seg.pop("expected_return_count")
            seg.update({
                "count": count,
                "average_confidence": round(confidence_total / count, 2) if count else None,
                "brier_score": round(brier_total / seg["resolved_forecasts"], 4) if seg["resolved_forecasts"] else None,
                "average_expected_return_pct": round(expected_total / expected_count, 2) if expected_count else None,
            })
            segments.append(seg)

        rejected = conn.execute(
            "SELECT id, binding_rejection_gate, thesis_type, weighted_score, evaluated_at"
            " FROM candidate_evaluations WHERE status='REJECTED'"
        ).fetchall()
        outcomes = conn.execute(
            "SELECT evaluation_id, horizon_sessions, active_return_pct FROM candidate_outcomes"
            " WHERE active_return_pct IS NOT NULL"
        ).fetchall()
        outcome_map: dict[int, list] = {}
        for row in outcomes:
            outcome_map.setdefault(int(row["evaluation_id"]), []).append(dict(row))
        by_gate = Counter((row["binding_rejection_gate"] or "UNSPECIFIED") for row in rejected)
        false_negative_by_horizon = {}
        for horizon in CANDIDATE_OUTCOME_HORIZONS:
            marked = [
                item for values in outcome_map.values() for item in values
                if int(item["horizon_sessions"]) == horizon
            ]
            false_negative_by_horizon[str(horizon)] = {
                "marked": len(marked),
                "outperformed": sum(float(item["active_return_pct"]) > 0 for item in marked),
                "false_negative_rate_pct": round(
                    sum(float(item["active_return_pct"]) > 0 for item in marked) / len(marked) * 100, 2
                ) if marked else None,
            }

        snapshots = conn.execute(
            "SELECT total, benchmark_price, holdings_value, timestamp FROM snapshots"
            " WHERE benchmark_price IS NOT NULL ORDER BY id"
        ).fetchall()
        cash_drag = {"available": False}
        if len(snapshots) >= 2 and float(snapshots[0]["benchmark_price"] or 0) > 0:
            portfolio_return = (float(snapshots[-1]["total"]) / float(snapshots[0]["total"]) - 1) * 100
            benchmark_return = (float(snapshots[-1]["benchmark_price"]) / float(snapshots[0]["benchmark_price"]) - 1) * 100
            avg_exposure = sum(
                float(r["holdings_value"] or 0) / float(r["total"] or 1) * 100 for r in snapshots
            ) / len(snapshots)
            cash_drag = {
                "available": True,
                "period_start": snapshots[0]["timestamp"],
                "period_end": snapshots[-1]["timestamp"],
                "portfolio_return_pct": round(portfolio_return, 2),
                "benchmark_return_pct": round(benchmark_return, 2),
                "active_return_pct": round(portfolio_return - benchmark_return, 2),
                "average_exposure_pct": round(avg_exposure, 2),
                "cash_opportunity_cost_proxy_pct": round(max(0.0, benchmark_return - portfolio_return), 2),
            }
        cash_reasons = {row["cash_reason"] or "UNSPECIFIED": row["c"] for row in conn.execute(
            "SELECT cash_reason, COUNT(*) AS c FROM decisions WHERE action='NO_TRADE' GROUP BY cash_reason"
        )}
        runs = conn.execute(
            "SELECT status, decision_model_version, parameter_version, schedule_version FROM runs"
        ).fetchall()
        completed = sum(row["status"] == "COMPLETED" for row in runs)
        versioned = sum(bool(row["decision_model_version"] and row["parameter_version"] and row["schedule_version"]) for row in runs)
        payload = {
            "generated_at": now(),
            "window_sessions": args.sessions,
            "portfolio": {
                "nav": status["nav"], "return_pct": status.get("return_pct"),
                "gross_exposure_pct": status.get("gross_exposure_pct"),
                "portfolio_heat_pct": status.get("portfolio_heat_pct"),
                "regime": status.get("exposure_regime"),
            },
            "thesis_segments": sorted(segments, key=lambda x: (x["thesis_type"], x["trade_style"])),
            "rejections": {
                "total": len(rejected), "binding_gates": dict(by_gate.most_common()),
                "false_negative_by_horizon": false_negative_by_horizon,
            },
            "cash_drag": cash_drag,
            "cash_reasons": cash_reasons,
            "process_compliance": {
                "runs": len(runs), "completed_runs": completed,
                "completion_rate_pct": round(completed / len(runs) * 100, 2) if runs else None,
                "versioned_runs": versioned,
                "version_coverage_pct": round(versioned / len(runs) * 100, 2) if runs else None,
                "automatic_parameter_adaptation": "LOCKED until configured resolved-forecast minimum",
            },
            "intel": {
                "queue": _intel_queue_health(conn),
                "sources": _source_quality_report(conn),
            },
        }
    print(json.dumps(payload, indent=2))


def cmd_decision_record(args: argparse.Namespace) -> None:
    sources = _public_urls(args.sources, "decision evidence", minimum=1)
    ticker = _validate_indian_ticker(args.ticker) if args.ticker else None
    if not args.rationale.strip():
        raise ValueError("decision rationale is required")
    if args.action != "NO_TRADE" and not ticker:
        raise ValueError("ticker is required unless action is NO_TRADE")
    with connect() as conn:
        if args.run_id is not None and not conn.execute(
            "SELECT 1 FROM runs WHERE id=?", (args.run_id,)
        ).fetchone():
            raise ValueError(f"run {args.run_id} does not exist")
        cash_reason = args.cash_reason
        if args.action == "NO_TRADE" and cash_reason is None:
            cash_reason = "NO_QUALIFYING_SETUP"
        cursor = conn.execute(
            """INSERT INTO decisions(
                   run_id, action, ticker, rationale, evidence_json, cash_reason,
                   decision_model_version, parameter_version, timestamp
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (args.run_id, args.action, ticker, args.rationale.strip(), json.dumps(sources),
             cash_reason, DECISION_MODEL_VERSION, _parameter_version(conn), now()),
        )
    print(json.dumps({"decision_id": cursor.lastrowid, "action": args.action, "ticker": ticker,
                      "cash_reason": cash_reason}))


def _candidate_sources(value: str | list[str]) -> list[str]:
    if isinstance(value, list):
        joined = ",".join(str(item) for item in value)
    else:
        joined = value
    return _public_urls(joined, "candidate evidence", minimum=1)


def _candidate_quote(
    conn: sqlite3.Connection,
    ticker: str,
    price: float | None,
    source: str | None,
    asof: str | None,
) -> tuple[float, str, str]:
    if price is None and source is None and asof is None:
        quote = latest_quote(conn, ticker)
        if quote is None:
            raise ValueError(f"record a sourced quote for {ticker} before screening it")
        return float(quote["price"]), str(quote["source"]), str(quote["asof"])
    if price is None or source is None:
        raise ValueError("candidate quote requires both price and source")
    if float(price) <= 0:
        raise ValueError("candidate quote price must be positive")
    source_urls = _public_urls(str(source), "candidate quote", minimum=1)
    stamp = asof or now()
    parsed = _parse_timestamp(stamp, "candidate quote as-of")
    if parsed > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise ValueError("candidate quote as-of cannot be more than five minutes in the future")
    return float(price), source_urls[0], stamp


def _candidate_benchmark_quote(
    conn: sqlite3.Connection,
    price: float | None,
    source: str | None,
    asof: str | None,
) -> tuple[float | None, str | None, str | None]:
    if price is None and source is None and asof is None:
        benchmark_ticker = _benchmark_ticker(conn)
        if not benchmark_ticker:
            return None, None, None
        quote = latest_quote(conn, benchmark_ticker)
        if quote is None:
            return None, None, None
        return float(quote["price"]), str(quote["source"]), str(quote["asof"])
    if price is None or source is None:
        raise ValueError("benchmark quote requires both price and source")
    if float(price) <= 0:
        raise ValueError("benchmark quote price must be positive")
    source_urls = _public_urls(str(source), "candidate benchmark quote", minimum=1)
    stamp = asof or now()
    parsed = _parse_timestamp(stamp, "candidate benchmark as-of")
    if parsed > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise ValueError("candidate benchmark as-of cannot be more than five minutes in the future")
    return float(price), source_urls[0], stamp


def _record_candidate_evaluation(conn: sqlite3.Connection, payload: dict) -> int:
    ticker = _validate_ticker(conn, str(payload.get("ticker") or ""))
    thesis_type = str(payload.get("thesis_type") or "CATALYST").upper()
    depth = str(payload.get("research_depth") or "SCREENED").upper()
    status = str(payload.get("status") or "WATCHLIST").upper()
    if thesis_type not in CANDIDATE_THESIS_TYPES:
        raise ValueError(f"thesis_type must be one of {', '.join(CANDIDATE_THESIS_TYPES)}")
    if depth not in CANDIDATE_DEPTHS:
        raise ValueError(f"research_depth must be one of {', '.join(CANDIDATE_DEPTHS)}")
    if status not in CANDIDATE_STATUSES:
        raise ValueError(f"status must be one of {', '.join(CANDIDATE_STATUSES)}")
    raw_score = payload.get("preliminary_score", payload.get("score"))
    if raw_score is None:
        raise ValueError("candidate preliminary_score is required")
    score = float(raw_score)
    if not 0 <= score <= 100:
        raise ValueError("preliminary_score must be between 0 and 100")
    run_id = payload.get("run_id")
    if run_id is not None:
        run_id = int(run_id)
        if not conn.execute("SELECT 1 FROM runs WHERE id=?", (run_id,)).fetchone():
            raise ValueError(f"run {run_id} does not exist")

    gate_outcomes = _json_object(payload.get("gate_outcomes"), "gate_outcomes")
    hard_gates, hard_gate_pass, hard_gate_failures = _hard_gate_summary(payload.get("hard_gates"))
    score_components, weighted_score = _weighted_candidate_score(payload.get("score_components"))
    snapshot = _json_object(payload.get("snapshot"), "candidate snapshot")
    legacy_result = str(payload.get("legacy_result") or status).upper()
    if legacy_result not in CANDIDATE_STATUSES:
        raise ValueError(f"legacy_result must be one of {', '.join(CANDIDATE_STATUSES)}")
    shadow_recommendation = "REJECTED" if not hard_gate_pass else (
        "APPROVED" if weighted_score is not None and weighted_score >= SCORE_THRESHOLD else "WATCHLIST"
    )
    binding_gate = str(payload.get("binding_rejection_gate") or "").strip() or None
    failures = _candidate_gate_failures(gate_outcomes)
    if status == "REJECTED" and not binding_gate:
        if len(failures) == 1:
            binding_gate = failures[0]
        else:
            raise ValueError("rejected candidates require one binding_rejection_gate")
    if status != "REJECTED" and binding_gate:
        raise ValueError("binding_rejection_gate is only valid for REJECTED candidates")

    quote_price, quote_source, quote_asof = _candidate_quote(
        conn,
        ticker,
        float(payload["quote_price"]) if payload.get("quote_price") is not None else None,
        payload.get("quote_source"),
        payload.get("quote_asof"),
    )
    benchmark_price, benchmark_source, benchmark_asof = _candidate_benchmark_quote(
        conn,
        float(payload["benchmark_price"]) if payload.get("benchmark_price") is not None else None,
        payload.get("benchmark_source"),
        payload.get("benchmark_asof"),
    )
    sources = _candidate_sources(payload.get("sources") or quote_source)
    evaluated_at = str(payload.get("evaluated_at") or now())
    parsed_evaluated_at = _parse_timestamp(evaluated_at, "candidate evaluated_at")
    if parsed_evaluated_at > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise ValueError("candidate evaluated_at cannot be more than five minutes in the future")
    rank = payload.get("rank")
    if rank is not None and int(rank) <= 0:
        raise ValueError("candidate rank must be positive")

    cursor = conn.execute(
        """INSERT INTO candidate_evaluations(
               run_id, ticker, thesis_type, research_depth, status,
               preliminary_score, rank, quote_price, quote_source, quote_asof,
               benchmark_price, benchmark_source, benchmark_asof,
               binding_rejection_gate, gate_outcomes_json, sources_json,
               snapshot_json, evaluated_at, hard_gates_json, hard_gate_pass,
               score_components_json, weighted_score, scoring_model_version,
               legacy_result, shadow_recommendation
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            run_id, ticker, thesis_type, depth, status, score,
            int(rank) if rank is not None else None,
            quote_price, quote_source, quote_asof,
            benchmark_price, benchmark_source, benchmark_asof,
            binding_gate,
            json.dumps(gate_outcomes, separators=(",", ":"), sort_keys=True),
            json.dumps(sources, separators=(",", ":")),
            json.dumps(snapshot, separators=(",", ":"), sort_keys=True),
            evaluated_at,
            json.dumps(hard_gates, separators=(",", ":"), sort_keys=True),
            int(hard_gate_pass),
            json.dumps(score_components, separators=(",", ":"), sort_keys=True),
            weighted_score, SCORING_MODEL_VERSION, legacy_result, shadow_recommendation,
        ),
    )
    return int(cursor.lastrowid)


def cmd_decision_comparison_report(args: argparse.Namespace) -> None:
    conditions = []
    params: list[object] = []
    if args.run_id is not None:
        conditions.append("run_id=?")
        params.append(args.run_id)
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    with connect() as conn:
        rows = [dict(row) for row in conn.execute(
            "SELECT legacy_result, shadow_recommendation, hard_gate_pass, weighted_score, "
            "scoring_model_version, COUNT(*) AS count FROM candidate_evaluations" + where +
            " GROUP BY legacy_result, shadow_recommendation, hard_gate_pass, weighted_score, scoring_model_version",
            params,
        )]
    transitions = Counter(f"{row['legacy_result']}->{row['shadow_recommendation']}" for row in rows for _ in range(int(row['count'])))
    print(json.dumps({
        "mode": "SHADOW_ONLY",
        "model_version": SCORING_MODEL_VERSION,
        "score_threshold": SCORE_THRESHOLD,
        "transitions": dict(transitions),
        "rows": rows,
    }, indent=2))


def cmd_candidate_screen(args: argparse.Namespace) -> None:
    with connect() as conn:
        if args.input:
            try:
                batch = json.loads(Path(args.input).read_text())
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError("candidate input must be a readable JSON array") from exc
            if not isinstance(batch, list) or not batch:
                raise ValueError("candidate input must be a non-empty JSON array")
            ids = []
            for item in batch:
                if not isinstance(item, dict):
                    raise ValueError("each candidate input row must be a JSON object")
                merged = dict(item)
                if args.run_id is not None and merged.get("run_id") is None:
                    merged["run_id"] = args.run_id
                ids.append(_record_candidate_evaluation(conn, merged))
            result = {"recorded": len(ids), "evaluation_ids": ids}
        else:
            if not args.ticker:
                raise ValueError("ticker is required unless --input is supplied")
            if args.score is None:
                raise ValueError("--score is required for an individual candidate")
            payload = {
                "ticker": args.ticker,
                "run_id": args.run_id,
                "thesis_type": args.thesis_type,
                "research_depth": args.research_depth,
                "status": args.status,
                "preliminary_score": args.score,
                "rank": args.rank,
                "quote_price": args.quote_price,
                "quote_source": args.quote_source,
                "quote_asof": args.quote_asof,
                "benchmark_price": args.benchmark_price,
                "benchmark_source": args.benchmark_source,
                "benchmark_asof": args.benchmark_asof,
                "binding_rejection_gate": args.binding_rejection_gate,
                "gate_outcomes": args.gate_outcomes,
                "hard_gates": args.hard_gates,
                "score_components": args.score_components,
                "legacy_result": args.legacy_result,
                "sources": args.sources,
                "snapshot": args.snapshot,
                "evaluated_at": args.evaluated_at,
            }
            evaluation_id = _record_candidate_evaluation(conn, payload)
            result = {"recorded": 1, "evaluation_ids": [evaluation_id]}
    print(json.dumps(result, indent=2))


def _candidate_latest_rows(conn: sqlite3.Connection, run_id: int | None = None) -> list[sqlite3.Row]:
    where = "WHERE run_id=?" if run_id is not None else ""
    params: tuple = (run_id,) if run_id is not None else ()
    return list(conn.execute(
        f"""SELECT ce.* FROM candidate_evaluations ce
            JOIN (
                SELECT ticker, MAX(id) AS latest_id
                FROM candidate_evaluations {where}
                GROUP BY ticker
            ) latest ON latest.latest_id=ce.id
            ORDER BY COALESCE(ce.weighted_score, ce.preliminary_score) DESC, ce.id""",
        params,
    ))


def cmd_candidate_rank(args: argparse.Namespace) -> None:
    if args.top <= 0:
        raise ValueError("top must be positive")
    with connect() as conn:
        rows = _candidate_latest_rows(conn, args.run_id)
        if not rows:
            raise ValueError("no candidate evaluations are available to rank")
        for rank, row in enumerate(rows, start=1):
            depth = row["research_depth"]
            if rank <= args.top and depth == "SCREENED":
                depth = "RANKED"
            conn.execute(
                "UPDATE candidate_evaluations SET rank=?, research_depth=? WHERE id=?",
                (rank, depth, row["id"]),
            )
        result = [
            {
                "rank": index,
                "evaluation_id": int(row["id"]),
                "ticker": row["ticker"],
                "score": row["weighted_score"] if row["weighted_score"] is not None else row["preliminary_score"],
                "preliminary_score": row["preliminary_score"],
                "weighted_score": row["weighted_score"],
                "hard_gate_pass": bool(row["hard_gate_pass"]),
                "shadow_recommendation": row["shadow_recommendation"],
                "status": row["status"],
            }
            for index, row in enumerate(rows[:args.top], start=1)
        ]
    print(json.dumps({"ranked": len(rows), "top": result}, indent=2))


def cmd_candidate_list(args: argparse.Namespace) -> None:
    if args.limit <= 0:
        raise ValueError("limit must be positive")
    conditions = []
    params: list = []
    if args.run_id is not None:
        conditions.append("run_id=?")
        params.append(args.run_id)
    if args.depth:
        conditions.append("research_depth=?")
        params.append(args.depth)
    if args.status:
        conditions.append("status=?")
        params.append(args.status)
    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    with connect() as conn:
        rows = [dict(row) for row in conn.execute(
            "SELECT * FROM candidate_evaluations" + where
            + " ORDER BY evaluated_at DESC, preliminary_score DESC LIMIT ?",
            (*params, args.limit),
        )]
    for row in rows:
        row["gate_outcomes"] = json.loads(row.pop("gate_outcomes_json"))
        row["sources"] = json.loads(row.pop("sources_json"))
        row["snapshot"] = json.loads(row.pop("snapshot_json"))
        row["hard_gates"] = json.loads(row.pop("hard_gates_json"))
        row["score_components"] = json.loads(row.pop("score_components_json"))
        row["hard_gate_pass"] = bool(row["hard_gate_pass"])
    print(json.dumps(rows, indent=2))


def _benchmark_outcome_price(
    conn: sqlite3.Connection, outcome_date: str
) -> tuple[float | None, str | None]:
    benchmark_ticker = _benchmark_ticker(conn)
    if not benchmark_ticker:
        return None, None
    historical = conn.execute(
        "SELECT date, close FROM historical_prices WHERE ticker=? AND date>=? ORDER BY date LIMIT 1",
        (benchmark_ticker, outcome_date),
    ).fetchone()
    if historical:
        return float(historical["close"]), str(historical["date"])
    for quote in conn.execute(
        "SELECT price, asof FROM quotes WHERE ticker=? ORDER BY asof",
        (benchmark_ticker,),
    ):
        if _india_date_from_timestamp(str(quote["asof"])) >= outcome_date:
            return float(quote["price"]), _india_date_from_timestamp(str(quote["asof"]))
    return None, None


def mark_candidate_outcomes(
    conn: sqlite3.Connection,
    *,
    as_of: str | None = None,
    refresh_missing: bool = False,
) -> dict:
    cutoff = _valid_date(as_of, "as-of") if as_of else india_today().isoformat()
    evaluations = list(conn.execute(
        "SELECT * FROM candidate_evaluations WHERE status='REJECTED' ORDER BY id"
    ))
    refreshed = []
    refresh_errors = []
    if refresh_missing:
        pending_tickers = {
            str(row["ticker"])
            for row in evaluations
            if conn.execute(
                "SELECT COUNT(*) FROM candidate_outcomes WHERE evaluation_id=?",
                (row["id"],),
            ).fetchone()[0] < len(CANDIDATE_OUTCOME_HORIZONS)
        }
        for ticker in sorted(pending_tickers):
            try:
                rows = _fetch_yahoo_historical(ticker, years=2)
                conn.executemany(
                    """INSERT OR REPLACE INTO historical_prices(
                           ticker, date, open, high, low, close, volume
                       ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    [
                        (
                            ticker, row["date"], row.get("open"), row.get("high"),
                            row.get("low"), row["close"], row.get("volume", 0),
                        )
                        for row in rows
                    ],
                )
                refreshed.append({"ticker": ticker, "rows": len(rows)})
            except (OSError, ValueError, urllib.error.URLError) as exc:
                refresh_errors.append({"ticker": ticker, "error": str(exc)})
    marked = 0
    unavailable = []
    for evaluation in evaluations:
        evaluation_date = _india_date_from_timestamp(str(evaluation["quote_asof"]))
        future_rows = list(conn.execute(
            "SELECT date, close FROM historical_prices"
            " WHERE ticker=? AND date>? AND date<=? ORDER BY date",
            (evaluation["ticker"], evaluation_date, cutoff),
        ))
        for horizon in CANDIDATE_OUTCOME_HORIZONS:
            if conn.execute(
                "SELECT 1 FROM candidate_outcomes WHERE evaluation_id=? AND horizon_sessions=?",
                (evaluation["id"], horizon),
            ).fetchone():
                continue
            if len(future_rows) < horizon:
                unavailable.append({
                    "evaluation_id": int(evaluation["id"]),
                    "ticker": evaluation["ticker"],
                    "horizon_sessions": horizon,
                    "reason": "insufficient_future_prices",
                })
                continue
            outcome = future_rows[horizon - 1]
            candidate_return = (
                (float(outcome["close"]) / float(evaluation["quote_price"])) - 1
            ) * 100
            benchmark_outcome, _ = _benchmark_outcome_price(conn, str(outcome["date"]))
            benchmark_return = None
            active_return = None
            if benchmark_outcome is not None and evaluation["benchmark_price"] is not None:
                benchmark_return = (
                    (benchmark_outcome / float(evaluation["benchmark_price"])) - 1
                ) * 100
                active_return = candidate_return - benchmark_return
            conn.execute(
                """INSERT INTO candidate_outcomes(
                       evaluation_id, horizon_sessions, outcome_date,
                       candidate_price, benchmark_price, candidate_return_pct,
                       benchmark_return_pct, active_return_pct, marked_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    evaluation["id"], horizon, outcome["date"], outcome["close"],
                    benchmark_outcome, round(candidate_return, 6),
                    round(benchmark_return, 6) if benchmark_return is not None else None,
                    round(active_return, 6) if active_return is not None else None,
                    now(),
                ),
            )
            marked += 1
    return {
        "marked": marked,
        "unavailable": unavailable,
        "as_of": cutoff,
        "refreshed": refreshed,
        "refresh_errors": refresh_errors,
    }


def cmd_candidate_mark_outcomes(args: argparse.Namespace) -> None:
    with connect() as conn:
        result = mark_candidate_outcomes(
            conn, as_of=args.as_of, refresh_missing=args.refresh
        )
    print(json.dumps(result, indent=2))


def run_opportunity_audit(
    conn: sqlite3.Connection,
    *, sessions: int = 5,
    threshold_pct: float = 25.0,
    persist: bool = True,
) -> dict:
    rows = list(conn.execute(
        "SELECT holdings_value, total, timestamp FROM snapshots ORDER BY id DESC LIMIT ?",
        (sessions,),
    ))
    exposures = [
        (float(row["holdings_value"]) / float(row["total"]) * 100)
        if float(row["total"]) > 0 else 0.0
        for row in rows
    ]
    observed = len(rows)
    low_count = sum(exposure < threshold_pct for exposure in exposures)
    triggered = observed >= sessions and low_count == sessions
    window_start = rows[-1]["timestamp"] if rows else None
    window_end = rows[0]["timestamp"] if rows else None
    candidate_history = list(conn.execute(
        "SELECT ticker, research_depth, status, binding_rejection_gate"
        " FROM candidate_evaluations WHERE evaluated_at>=? ORDER BY id DESC",
        (window_start or "",),
    ))
    latest_by_ticker: dict[str, sqlite3.Row] = {}
    for row in candidate_history:
        latest_by_ticker.setdefault(str(row["ticker"]), row)
    candidate_rows = list(latest_by_ticker.values())
    screened = {row["ticker"] for row in candidate_rows}
    ranked = {row["ticker"] for row in candidate_rows if row["research_depth"] in ("RANKED", "DEEP")}
    deep = {row["ticker"] for row in candidate_rows if row["research_depth"] == "DEEP"}
    approved = {row["ticker"] for row in candidate_rows if row["status"] == "APPROVED"}
    rejected = {row["ticker"] for row in candidate_rows if row["status"] == "REJECTED"}
    documented_deep = {
        row["ticker"] for row in candidate_rows
        if row["research_depth"] == "DEEP"
        and (
            row["status"] == "APPROVED"
            or (row["status"] == "REJECTED" and row["binding_rejection_gate"])
        )
    }
    gates = Counter(
        str(row["binding_rejection_gate"])
        for row in candidate_rows
        if row["status"] == "REJECTED" and row["binding_rejection_gate"]
    )
    top_gate = gates.most_common(1)[0][0] if gates else None
    diagnostics = []
    if triggered:
        if len(screened) < 40:
            diagnostics.append("funnel_too_narrow")
        if len(ranked) < 10:
            diagnostics.append("ranking_set_too_small")
        if len(deep) < 5:
            diagnostics.append("insufficient_deep_research")
        if deep and len(documented_deep) / len(deep) < 0.95:
            diagnostics.append("deep_candidate_documentation_below_95_pct")
        if gates and gates.most_common(1)[0][1] > max(1, len(rejected) / 2):
            diagnostics.append("single_gate_dominates_rejections")
        if not diagnostics:
            diagnostics.append("no_qualifying_opportunities_after_full_funnel")
    result = {
        "triggered": triggered,
        "sessions_required": sessions,
        "sessions_observed": observed,
        "low_exposure_sessions": low_count,
        "exposure_threshold_pct": threshold_pct,
        "average_exposure_pct": round(sum(exposures) / observed, 4) if observed else None,
        "screened_candidates": len(screened),
        "ranked_candidates": len(ranked),
        "deep_candidates": len(deep),
        "approved_candidates": len(approved),
        "rejected_candidates": len(rejected),
        "top_rejection_gate": top_gate,
        "diagnostics": diagnostics,
        "window_start": window_start,
        "window_end": window_end,
        "generated_at": now(),
    }
    if persist:
        conn.execute(
            """INSERT INTO opportunity_audits(
                   triggered, sessions_required, sessions_observed,
                   low_exposure_sessions, exposure_threshold_pct,
                   average_exposure_pct, screened_candidates, ranked_candidates,
                   deep_candidates, approved_candidates, rejected_candidates,
                   top_rejection_gate, diagnostics_json, window_start, window_end,
                   generated_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                int(triggered), sessions, observed, low_count, threshold_pct,
                result["average_exposure_pct"], len(screened), len(ranked), len(deep),
                len(approved), len(rejected), top_gate,
                json.dumps(diagnostics, separators=(",", ":")),
                window_start, window_end, result["generated_at"],
            ),
        )
    return result


def cmd_candidate_opportunity_audit(args: argparse.Namespace) -> None:
    if args.sessions <= 0:
        raise ValueError("sessions must be positive")
    if not 0 < args.threshold_pct < 100:
        raise ValueError("threshold-pct must be between 0 and 100")
    with connect() as conn:
        result = run_opportunity_audit(
            conn, sessions=args.sessions, threshold_pct=args.threshold_pct, persist=True
        )
    print(json.dumps(result, indent=2))


def _rejection_report(conn: sqlite3.Connection, run_id: int | None = None) -> dict:
    condition = " AND ce.run_id=?" if run_id is not None else ""
    params: tuple = (run_id,) if run_id is not None else ()
    evaluations = list(conn.execute(
        "SELECT ce.* FROM candidate_evaluations ce WHERE 1=1" + condition
        + " ORDER BY ce.evaluated_at DESC",
        params,
    ))
    deep = [row for row in evaluations if row["research_depth"] == "DEEP"]
    deep_documented = [
        row for row in deep
        if row["status"] == "APPROVED"
        or (row["status"] == "REJECTED" and row["binding_rejection_gate"])
    ]
    rejected = [row for row in evaluations if row["status"] == "REJECTED"]
    gates = Counter(str(row["binding_rejection_gate"]) for row in rejected if row["binding_rejection_gate"])
    one_gate = []
    for row in rejected:
        failures = _candidate_gate_failures(json.loads(row["gate_outcomes_json"]))
        if len(failures) == 1:
            one_gate.append(row)
    outcome_rows = list(conn.execute(
        """SELECT co.horizon_sessions, co.active_return_pct
           FROM candidate_outcomes co
           JOIN candidate_evaluations ce ON ce.id=co.evaluation_id
           WHERE ce.status='REJECTED'""" + (" AND ce.run_id=?" if run_id is not None else ""),
        params,
    ))
    by_horizon: dict[int, list[float]] = {horizon: [] for horizon in CANDIDATE_OUTCOME_HORIZONS}
    for row in outcome_rows:
        if row["active_return_pct"] is not None:
            by_horizon[int(row["horizon_sessions"])].append(float(row["active_return_pct"]))
    outperformance = {}
    for horizon, values in by_horizon.items():
        outperformance[str(horizon)] = {
            "marked": len(values),
            "outperformed": sum(value > 0 for value in values),
            "outperformed_pct": round(sum(value > 0 for value in values) / len(values) * 100, 2)
            if values else None,
            "average_active_return_pct": round(sum(values) / len(values), 4) if values else None,
        }
    audit = conn.execute(
        "SELECT * FROM opportunity_audits ORDER BY id DESC LIMIT 1"
    ).fetchone()
    audit_data = dict(audit) if audit else None
    if audit_data:
        audit_data["triggered"] = bool(audit_data["triggered"])
        audit_data["diagnostics"] = json.loads(audit_data.pop("diagnostics_json"))
    return {
        "run_id": run_id,
        "evaluations": len(evaluations),
        "deep_researched": len(deep),
        "deep_documented": len(deep_documented),
        "deep_documentation_pct": round(len(deep_documented) / len(deep) * 100, 2) if deep else None,
        "rejected": len(rejected),
        "approved": sum(row["status"] == "APPROVED" for row in evaluations),
        "most_common_rejection_gate": gates.most_common(1)[0][0] if gates else None,
        "rejections_by_gate": dict(gates.most_common()),
        "rejected_by_one_gate": len(one_gate),
        "near_misses": [
            {
                "evaluation_id": int(row["id"]),
                "ticker": row["ticker"],
                "score": row["preliminary_score"],
                "binding_gate": row["binding_rejection_gate"],
            }
            for row in sorted(one_gate, key=lambda item: item["preliminary_score"], reverse=True)[:10]
        ],
        "forward_outperformance": outperformance,
        "latest_opportunity_audit": audit_data,
    }


def cmd_decision_rejection_report(args: argparse.Namespace) -> None:
    with connect() as conn:
        if args.mark_outcomes:
            outcome_result = mark_candidate_outcomes(
                conn, as_of=args.as_of, refresh_missing=args.refresh
            )
        else:
            outcome_result = None
        report = _rejection_report(conn, args.run_id)
    if outcome_result is not None:
        report["outcome_marker"] = outcome_result
    print(json.dumps(report, indent=2))


def cmd_evidence(args: argparse.Namespace) -> None:
    with connect() as conn:
        if args.evidence_action == "add":
            ticker = _validate_indian_ticker(args.ticker) if args.ticker else None
            source_urls = _public_urls(args.source, "evidence", minimum=1)
            if len(source_urls) != 1:
                raise ValueError("an evidence claim must cite exactly one public URL")
            if not args.claim.strip():
                raise ValueError("evidence claim is required")
            if args.published_at:
                published = _parse_timestamp(args.published_at, "published-at")
                if published > datetime.now(timezone.utc) + timedelta(minutes=5):
                    raise ValueError("published-at cannot be in the future")
            cursor = conn.execute(
                """INSERT INTO evidence_claims(
                       ticker, claim, source_url, source_tier, published_at, fetched_at
                   ) VALUES (?, ?, ?, ?, ?, ?)""",
                (ticker, args.claim.strip(), args.source, args.tier, args.published_at, now()),
            )
            result = {"evidence_id": cursor.lastrowid, "status": "UNRESOLVED"}
        elif args.evidence_action == "resolve":
            row = conn.execute(
                "SELECT id, status FROM evidence_claims WHERE id=?", (args.evidence_id,)
            ).fetchone()
            if not row:
                raise ValueError(f"evidence claim {args.evidence_id} does not exist")
            if row["status"] != "UNRESOLVED":
                raise ValueError(
                    f"evidence claim {args.evidence_id} is already resolved as {row['status']}"
                )
            if not args.note.strip():
                raise ValueError("evidence resolution note is required")
            conn.execute(
                """UPDATE evidence_claims SET status=?, resolution_note=?, resolved_at=?
                   WHERE id=?""",
                (args.outcome, args.note.strip(), now(), args.evidence_id),
            )
            result = {"evidence_id": args.evidence_id, "status": args.outcome}
        else:
            rows = [dict(row) for row in conn.execute(
                "SELECT * FROM evidence_claims ORDER BY id DESC LIMIT ?", (args.limit,)
            )]
            print(json.dumps(rows, indent=2))
            return
    print(json.dumps(result))


def cmd_corporate_action(args: argparse.Namespace) -> None:
    ticker = _validate_indian_ticker(args.ticker)
    source_urls = _public_urls(args.source, "corporate action", minimum=1)
    if len(source_urls) != 1:
        raise ValueError("a corporate action must cite exactly one official URL")
    ex_date = _valid_date(args.ex_date, "ex-date")
    if datetime.strptime(ex_date, "%Y-%m-%d").date() > india_today():
        raise ValueError("cannot apply a corporate action before its ex-date")
    with connect() as conn:
        holding = conn.execute(
            "SELECT shares, avg_cost_basis FROM holdings WHERE ticker=?", (ticker,)
        ).fetchone()
        if not holding:
            raise ValueError(f"no holding exists for {ticker}")
        cash_effect = 0.0
        if args.action_type == "DIVIDEND":
            if args.amount_per_share is None or args.amount_per_share <= 0:
                raise ValueError("DIVIDEND requires a positive --amount-per-share")
            cash_effect = round(float(holding["shares"]) * args.amount_per_share, 2)
            cash = state_float(conn, "cash", LEGACY_INITIAL_CASH) + cash_effect
            realized = state_float(conn, "realized_pnl", 0.0) + cash_effect
            gross = state_float(conn, "gross_realized_pnl", 0.0) + cash_effect
            for key, value in (("cash", cash), ("realized_pnl", realized),
                               ("gross_realized_pnl", gross)):
                conn.execute("UPDATE state SET value=? WHERE key=?", (str(round(value, 2)), key))
            ratio = None
        else:
            if args.ratio is None or args.ratio <= 0:
                raise ValueError(f"{args.action_type} requires a positive total-share --ratio")
            ratio = args.ratio
            action_stamp = now()
            conn.execute(
                """UPDATE holdings SET shares=shares * ?, avg_cost_basis=avg_cost_basis / ?,
                       last_updated=?, quote_required_after=? WHERE ticker=?""",
                (ratio, ratio, action_stamp, action_stamp, ticker),
            )
        conn.execute(
            """INSERT INTO corporate_actions(
                   ticker, action_type, amount_per_share, ratio, cash_effect,
                   source_url, ex_date, recorded_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (ticker, args.action_type, args.amount_per_share, ratio, cash_effect,
             args.source, ex_date, now()),
        )
        conn.execute(
            "INSERT INTO decision_journal(entry_type, content, timestamp) VALUES ('corporate_action', ?, ?)",
            (
                f"{args.action_type} {ticker} ex-date {ex_date}; "
                f"cash effect {_money(conn, cash_effect)}",
                now(),
            ),
        )
    print(json.dumps({"ticker": ticker, "action": args.action_type,
                      "cash_effect": cash_effect, "ex_date": ex_date}))




def _combined_feed_rows(conn: sqlite3.Connection, limit: int) -> list[dict]:
    observations = [
        dict(row) | {"kind": "verified_observation"}
        for row in conn.execute(
            """SELECT id, source_type, observation, source_urls, created_at, run_id
               FROM market_feed ORDER BY id DESC LIMIT ?""",
            (limit,),
        )
    ]
    articles = [
        {
            "id": row["id"],
            "source_type": "intel_article",
            "observation": (
                f"{row['title']}. {row['summary']}" if row["summary"] else row["title"]
            ),
            "source_urls": row["link"],
            "created_at": row["created_at"],
            "run_id": None,
            "kind": "unverified_discovery",
        }
        for row in conn.execute(
            """SELECT id, title, link, summary, created_at
               FROM intel_articles ORDER BY id DESC LIMIT ?""",
            (limit,),
        )
    ]
    return sorted(
        observations + articles,
        key=lambda item: item["created_at"],
        reverse=True,
    )[:limit]


def cmd_learn_briefing(_: argparse.Namespace) -> None:
    """Return process, calibration, evidence, risk, and market context."""
    with connect() as conn:
        closed = conn.execute(
            """SELECT ticker, direction, horizon, confidence, outcome, event_outcome,
                      brier_component, catalyst, timing_accuracy
               FROM theses WHERE status IN ('CLOSED','PENDING_RESOLUTION')
               ORDER BY closed_at DESC"""
        ).fetchall()
        trade_scored = [row for row in closed if row["outcome"] in ("WIN", "LOSS")]
        forecasts = [row for row in closed if row["event_outcome"] in ("YES", "NO")]
        wins = sum(row["outcome"] == "WIN" for row in trade_scored)
        win_rate = wins / len(trade_scored) * 100 if trade_scored else None
        brier = (sum(float(row["brier_component"]) for row in forecasts) / len(forecasts)
                 if forecasts else None)
        avg_confidence = (sum(float(row["confidence"]) for row in forecasts) / len(forecasts)
                          if forecasts else None)
        event_rate = (sum(row["event_outcome"] == "YES" for row in forecasts) / len(forecasts)
                      if forecasts else None)
        drift = ((event_rate - avg_confidence / 100)
                 if event_rate is not None and avg_confidence is not None else None)
        base_brier = event_rate * (1 - event_rate) if event_rate is not None else None
        brier_skill = (1 - brier / base_brier
                       if len(forecasts) >= 5 and base_brier and brier is not None else None)

        by_trade_style = {"INTRADAY": [], "POSITION": []}
        for row in closed:
            style = _trade_style(row["horizon"])
            by_trade_style[style].append({
                "ticker": row["ticker"], "trade_outcome": row["outcome"],
                "event_outcome": row["event_outcome"], "confidence": row["confidence"],
                "catalyst": row["catalyst"],
            })

        buckets = []
        for low, high in ((1, 39), (40, 59), (60, 79), (80, 99)):
            rows = [row for row in forecasts if low <= row["confidence"] <= high]
            if rows:
                buckets.append({
                    "range": f"{low}-{high}", "count": len(rows),
                    "average_confidence_pct": round(sum(r["confidence"] for r in rows) / len(rows), 1),
                    "event_rate_pct": round(sum(r["event_outcome"] == "YES" for r in rows) / len(rows) * 100, 1),
                })

        source_stats: dict[str, dict] = {}
        for row in conn.execute(
            "SELECT source_url, status FROM evidence_claims WHERE status IN ('ACCURATE','INACCURATE')"
        ):
            domain = urllib.parse.urlparse(row["source_url"]).netloc.lower()
            stat = source_stats.setdefault(domain, {"accurate": 0, "inaccurate": 0})
            stat["accurate" if row["status"] == "ACCURATE" else "inaccurate"] += 1
        source_accuracy = []
        for domain, stat in source_stats.items():
            total = stat["accurate"] + stat["inaccurate"]
            if total >= 5:
                source_accuracy.append({"domain": domain, **stat,
                                        "accuracy_pct": round(stat["accurate"] / total * 100, 1)})
        source_accuracy.sort(key=lambda item: (-item["accuracy_pct"], item["domain"]))

        patterns = []
        minimum = int(state_float(conn, "strategy_min_forecasts_for_adaptation", 30))
        if len(forecasts) < minimum:
            patterns.append(f"Calibration adaptation locked: {len(forecasts)}/{minimum} resolved forecasts")
        elif drift is not None and drift < -0.10:
            patterns.append(f"Forecasts are overconfident by {abs(drift) * 100:.1f} percentage points")
        timing_rows = [row for row in closed if row["timing_accuracy"]]
        if len(timing_rows) >= 5:
            early = sum(row["timing_accuracy"] == "early" for row in timing_rows) / len(timing_rows)
            late = sum(row["timing_accuracy"] == "late" for row in timing_rows) / len(timing_rows)
            if early > 0.5:
                patterns.append(f"{early * 100:.0f}% of resolved theses were early; review horizon definitions")
            if late > 0.5:
                patterns.append(f"{late * 100:.0f}% of resolved theses were late; review horizon definitions")

        feed = _combined_feed_rows(conn, 10)
        recent_logs = [dict(row) for row in conn.execute(
            "SELECT summary, lessons, created_at FROM learning_log ORDER BY id DESC LIMIT 3"
        )]
        library_count = conn.execute("SELECT COUNT(*) FROM research_library").fetchone()[0]
        status = portfolio_status(conn)
        opportunity = _rejection_report(conn)

    print(json.dumps({
        "closed_theses": len(closed), "trade_scored_theses": len(trade_scored),
        "resolved_forecasts": len(forecasts),
        "win_rate_pct": round(win_rate, 2) if win_rate is not None else None,
        "brier_score": round(brier, 4) if brier is not None else None,
        "brier_skill_score": round(brier_skill, 4) if brier_skill is not None else None,
        "avg_confidence_pct": round(avg_confidence, 1) if avg_confidence is not None else None,
        "event_rate_pct": round(event_rate * 100, 1) if event_rate is not None else None,
        "calibration_drift": round(drift, 4) if drift is not None else None,
        "calibration_buckets": buckets, "by_trade_style": by_trade_style,
        "source_accuracy": source_accuracy, "patterns": patterns,
        "risk": {"portfolio_heat_pct": status["portfolio_heat_pct"],
                 "risk_data_missing": status["risk_data_missing"],
                 "valuation_status": status["valuation_status"],
                 "stale_tickers": status["stale_tickers"]},
        "recent_feed": feed, "recent_learning_logs": recent_logs,
        "research_library_entries": library_count,
        "opportunity_funnel": opportunity,
    }, indent=2))


def cmd_learn_research(args: argparse.Namespace) -> None:
    ticker = _validate_indian_ticker(args.ticker)
    sources = _public_urls(args.sources, "research", minimum=1)
    with connect() as conn:
        conn.execute(
            """INSERT INTO research_library(ticker, sector, topic, findings, sources_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (ticker, args.sector, args.topic, args.findings.strip(),
             json.dumps(sources), now()),
        )
    print(json.dumps({"stored": True, "ticker": ticker, "topic": args.topic}))


def cmd_learn_library(_: argparse.Namespace) -> None:
    with connect() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT id, ticker, sector, topic, findings, sources_json, created_at FROM research_library ORDER BY id DESC"
        )]
    print(json.dumps(rows, indent=2))


def cmd_learn_log_latest(_: argparse.Namespace) -> None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM learning_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not row:
            raise ValueError("no learning log entries yet")
    print(json.dumps(dict(row), indent=2))


def cmd_learn_feed_add(args: argparse.Namespace) -> None:
    _public_urls(args.sources, "market-feed observation", minimum=1)
    with connect() as conn:
        conn.execute(
            "INSERT INTO market_feed(source_type, observation, source_urls, created_at, run_id)"
            " VALUES (?, ?, ?, ?, ?)",
            (args.feed_type, args.observation.strip(), args.sources, now(), args.run_id),
        )
    print(json.dumps({"stored": True, "type": args.feed_type}))


def cmd_learn_feed_latest(args: argparse.Namespace) -> None:
    with connect() as conn:
        rows = _combined_feed_rows(conn, args.limit)
    print(json.dumps(rows, indent=2))


def cmd_learn_adapt(_: argparse.Namespace) -> None:
    """Recommend forecast recalibration only after a meaningful resolved sample."""
    with connect() as conn:
        resolved = conn.execute(
            "SELECT COUNT(*) FROM theses WHERE event_outcome IN ('YES','NO')"
        ).fetchone()[0]
        minimum = int(state_float(conn, "strategy_min_forecasts_for_adaptation", 30))
        if resolved < minimum:
            print(json.dumps({
                "proposal": None,
                "reason": f"adaptation locked until {minimum} resolved forecasts; current={resolved}",
            }))
            return
        logs = conn.execute(
            "SELECT calibration_drift, win_rate_pct, brier_score FROM learning_log"
            " WHERE calibration_drift IS NOT NULL ORDER BY id DESC LIMIT 3"
        ).fetchall()
        if len(logs) < 3:
            print(json.dumps({"proposal": None, "reason": "not enough history for adaptation"}))
            return

        # Check sustained overconfidence: drift < -0.05 (win rate 5+ pts below confidence)
        overconfident = all(
            (r["calibration_drift"] or 0) < -0.05 for r in logs
        )
        if overconfident:
            drift_points = round(abs(sum(r["calibration_drift"] for r in logs) / len(logs)) * 100, 1)
            print(json.dumps({
                "proposal": {
                    "type": "recalibrate_confidence",
                    "confidence_haircut_points": drift_points,
                    "reason": "Sustained out-of-sample overconfidence; revise probabilities, not position limits.",
                },
            }, indent=2))
            return

        # Check underconfidence: drift > 0.05
        underconfident = all(
            (r["calibration_drift"] or 0) > 0.05 for r in logs
        )
        if underconfident:
            print(json.dumps({
                "proposal": {
                    "type": "review_underconfidence",
                    "reason": "Observed event rate exceeds stated confidence; review probability estimates without increasing risk limits.",
                },
            }, indent=2))
            return

        print(json.dumps({"proposal": None, "reason": "calibration within normal range"}))


YAHOO_BASE = "https://query1.finance.yahoo.com/v8/finance/chart/"


def _fetch_yahoo_historical(ticker: str, years: int = 5) -> list[dict]:
    """Fetch historical OHLCV from Yahoo Finance for Indian tickers."""
    ranges = {1: "1y", 2: "2y", 5: "5y", 10: "10y"}
    rng = ranges.get(years, "5y")
    url = f"{YAHOO_BASE}{urllib.parse.quote(ticker)}?range={rng}&interval=1d"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode())
    result = data["chart"]["result"][0] if data.get("chart", {}).get("result") else None
    if not result:
        raise ValueError(f"No historical data for {ticker}")
    timestamps = result.get("timestamp", [])
    quotes = result["indicators"]["quote"][0]
    if not timestamps or not quotes.get("close"):
        raise ValueError(f"Empty historical data for {ticker}")
    rows = []
    for i, ts in enumerate(timestamps):
        close = quotes["close"][i]
        if close is None:
            continue
        rows.append({
            "date": datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d"),
            "open": quotes["open"][i],
            "high": quotes["high"][i],
            "low": quotes["low"][i],
            "close": close,
            "volume": quotes["volume"][i] or 0,
        })
    return rows


def cmd_learn_historical_fetch(args: argparse.Namespace) -> None:
    """Fetch historical price data from Yahoo Finance and store locally."""
    ticker = _validate_indian_ticker(args.ticker)
    rows = _fetch_yahoo_historical(ticker, args.years)
    with connect() as conn:
        conn.executemany(
            """INSERT OR REPLACE INTO historical_prices(ticker, date, open, high, low, close, volume)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [(ticker, r["date"], r["open"], r["high"], r["low"], r["close"], r["volume"]) for r in rows],
        )
    print(json.dumps({
        "ticker": ticker,
        "rows_stored": len(rows),
        "date_range": f"{rows[0]['date']} to {rows[-1]['date']}",
    }))


def cmd_learn_historical_simulate(args: argparse.Namespace) -> None:
    """Inspect an ex-post historical price path; do not label it a backtest."""
    ticker = _validate_indian_ticker(args.ticker)
    with connect() as conn:
        prices = conn.execute(
            "SELECT date, close FROM historical_prices WHERE ticker=? AND date >= ? ORDER BY date",
            (ticker, args.entry),
        ).fetchall()
        if not prices or len(prices) < 2:
            raise ValueError(
                f"Not enough historical data for {ticker} from {args.entry} — fetch it first with"
                f" 'learn historical fetch {ticker}'"
            )
        entry_price = float(prices[0]["close"])
        if args.exit:
            exit_row = conn.execute(
                "SELECT date, close FROM historical_prices WHERE ticker=? AND date <= ? ORDER BY date DESC LIMIT 1",
                (ticker, args.exit),
            ).fetchone()
            if not exit_row:
                raise ValueError(f"No data for {ticker} on or before {args.exit}")
            exit_price = float(exit_row["close"])
            exit_date = exit_row["date"]
        else:
            exit_price = float(prices[-1]["close"])
            exit_date = prices[-1]["date"]
        per_leg_bps = _effective_cost_bps(conn, "fee_bps") + _effective_cost_bps(
            conn, "slippage_large_bps"
        )
    delta = exit_price - entry_price
    gross_ret_pct = (delta / entry_price) * 100
    estimated_cost_pct = 2 * per_leg_bps / 100
    net_ret_pct = gross_ret_pct - estimated_cost_pct
    print(json.dumps({
        "analysis_type": "EX_POST_PRICE_REPLAY_NOT_BACKTEST",
        "ticker": ticker,
        "direction": "LONG",
        "entry_date": prices[0]["date"],
        "exit_date": exit_date,
        "entry_price": round(entry_price, 2),
        "exit_price": round(exit_price, 2),
        "gross_return_pct": round(gross_ret_pct, 2),
        "estimated_round_trip_cost_pct": round(estimated_cost_pct, 3),
        "net_return_pct": round(net_ret_pct, 2),
        "abs_return": round(delta, 2),
        "limitations": [
            "No point-in-time universe or thesis generation",
            "No delisting or survivorship-bias control",
            "Corporate-action accuracy depends on upstream adjusted data",
        ],
    }))


def cmd_learn_historical_analyze(args: argparse.Namespace) -> None:
    """Describe a candidate's current pullback using prior daily closes."""
    ticker = _validate_indian_ticker(args.ticker)
    with connect() as conn:
        rows = conn.execute(
            "SELECT date, close FROM historical_prices WHERE ticker=?"
            " ORDER BY date DESC LIMIT 252",
            (ticker,),
        ).fetchall()
    if len(rows) < 20:
        raise ValueError(
            f"Not enough historical data for {ticker} — fetch it first with"
            f" 'learn historical fetch {ticker} --years 2'"
        )

    rows = list(reversed(rows))
    closes = [float(row["close"]) for row in rows]
    latest = closes[-1]
    high_52w = max(closes)
    sma20 = sum(closes[-20:]) / 20
    sma50 = sum(closes[-50:]) / 50 if len(closes) >= 50 else None
    pullback_pct = (latest / high_52w - 1) * 100
    return_5d_pct = (latest / closes[-6] - 1) * 100 if len(closes) >= 6 else None
    vs_sma20_pct = (latest / sma20 - 1) * 100
    dip_flag = pullback_pct <= -5 and vs_sma20_pct < 0

    print(json.dumps({
        "analysis_type": "HISTORICAL_PULLBACK_CONTEXT_NOT_BUY_SIGNAL",
        "ticker": ticker,
        "as_of": rows[-1]["date"],
        "sessions": len(rows),
        "latest_close": round(latest, 2),
        "high_52w": round(high_52w, 2),
        "pullback_from_52w_high_pct": round(pullback_pct, 2),
        "return_5d_pct": round(return_5d_pct, 2) if return_5d_pct is not None else None,
        "sma20": round(sma20, 2),
        "pct_vs_sma20": round(vs_sma20_pct, 2),
        "sma50": round(sma50, 2) if sma50 is not None else None,
        "pct_vs_sma50": round((latest / sma50 - 1) * 100, 2) if sma50 else None,
        "dip_flag": dip_flag,
        "dip_rule": "at least 5% below the 52-week high and below the 20-session average",
        "decision_rule": (
            "Historical pullback context cannot authorize a trade; require fresh evidence,"
            " a catalyst, positive expected value, and every thesis/risk gate."
        ),
    }, indent=2))


def cmd_learn_historical_context(_: argparse.Namespace) -> None:
    """Market context from loaded historical data: Nifty trend, volatility, loaded tickers."""
    with connect() as conn:
        context = {"nifty50": None, "sensex": None, "tickers_loaded": [], "volatility": {}}
        # Index stats — try Nifty 50 and Sensex
        for idx_ticker, idx_key in [("^NSEI", "nifty50"), ("^BSESN", "sensex")]:
            rows = conn.execute(
                "SELECT date, close FROM historical_prices WHERE ticker=? ORDER BY date",
                (idx_ticker,),
            ).fetchall()
            if rows and len(rows) >= 20:
                prices = [float(r["close"]) for r in rows]
                latest = prices[-1]
                ny = int(len(rows) * 0.25)
                context[idx_key] = {
                    "ticker": idx_ticker,
                    "latest": round(latest, 2),
                    "1y_return_pct": round(((latest / prices[-ny]) - 1) * 100, 2),
                    "max_close": round(max(prices), 2),
                    "min_close": round(min(prices), 2),
                    "current_percentile": round(
                        (latest - min(prices)) / (max(prices) - min(prices)) * 100, 1
                    ) if max(prices) != min(prices) else 50,
                    "n_days": len(rows),
                }
        recent = conn.execute(
            "SELECT ticker, COUNT(*) as days, MIN(date) as from_date, MAX(date) as to_date"
            " FROM historical_prices GROUP BY ticker ORDER BY MAX(date) DESC LIMIT 20"
        ).fetchall()
        context["tickers_loaded"] = [dict(r) for r in recent]
        # Volatility snapshot for top 5 loaded tickers
        for t in [r["ticker"] for r in recent[:5]]:
            rows = conn.execute(
                "SELECT close FROM historical_prices WHERE ticker=? ORDER BY date DESC LIMIT 21",
                (t,),
            ).fetchall()
            if len(rows) >= 10:
                closes = [float(r["close"]) for r in rows]
                daily_rets = [(closes[i] - closes[i + 1]) / closes[i + 1] * 100 for i in range(len(closes) - 1)]
                context["volatility"][t] = {
                    "30d_avg_daily_move_pct": round(sum(abs(r) for r in daily_rets) / len(daily_rets), 2),
                    "max_daily_move_pct": round(max(abs(r) for r in daily_rets), 2),
                }
    print(json.dumps(context, indent=2))


def cmd_learn_params(args: argparse.Namespace) -> None:
    if args.subcommand == "set":
        if len(args.kv_pairs) % 2:
            raise ValueError("parameter updates require key/value pairs")
        pairs = iter(args.kv_pairs)
        updates = dict(zip(pairs, pairs))
        with connect() as conn:
            for key, val in updates.items():
                if key not in PARAM_DEFAULTS:
                    raise ValueError(f"unknown strategy parameter: {key}")
                try:
                    number = float(val)
                except ValueError as exc:
                    raise ValueError(f"{key} must be numeric") from exc
                if number < 0:
                    raise ValueError(f"{key} must be non-negative")
                if key.startswith("max_") and key.endswith("weight") and number > 1:
                    raise ValueError(f"{key} must be between 0 and 1")
                if key in {"max_positions", "min_forecasts_for_adaptation"} and not number.is_integer():
                    raise ValueError(f"{key} must be a whole number")
                conn.execute(
                    "INSERT OR REPLACE INTO state(key, value) VALUES (?, ?)",
                    (f"strategy_{key}", str(number)),
                )
            conn.commit()
            result = {key: state_float(conn, f"strategy_{key}", default)
                      for key, default in PARAM_DEFAULTS.items()}
        print(json.dumps(result))
    else:
        with connect() as conn:
            result = {key: state_float(conn, f"strategy_{key}", default)
                      for key, default in PARAM_DEFAULTS.items()}
            result["max_positions"] = int(result["max_positions"])
            result["min_forecasts_for_adaptation"] = int(result["min_forecasts_for_adaptation"])
        print(json.dumps(result, indent=2))


def _write_learning_log(conn: sqlite3.Connection, data: dict) -> None:
    """Synthesize a learning log entry from recent thesis closures and reflections."""
    outcomes = conn.execute(
        """SELECT confidence, outcome FROM theses
           WHERE status IN ('CLOSED','PENDING_RESOLUTION') AND outcome IN ('WIN','LOSS')"""
    ).fetchall()
    forecasts = conn.execute(
        """SELECT confidence, event_outcome, brier_component FROM theses
           WHERE event_outcome IN ('YES','NO') AND brier_component IS NOT NULL"""
    ).fetchall()
    wins = sum(1 for r in outcomes if r["outcome"] == "WIN")
    win_rate = (wins / len(outcomes)) * 100 if outcomes else None
    brier = (sum(float(r["brier_component"]) for r in forecasts) / len(forecasts)
             if forecasts else None)
    avg_conf = (sum(r["confidence"] for r in forecasts) / len(forecasts)
                if forecasts else None)
    event_rate = (sum(r["event_outcome"] == "YES" for r in forecasts) / len(forecasts)
                  if forecasts else None)
    drift = (round(event_rate - avg_conf / 100, 4)
             if event_rate is not None and avg_conf is not None else None)

    # Synthesize lessons from recent closed theses with reflections
    recent_closes = conn.execute(
        "SELECT ticker, outcome, lesson, exit_reason, timing_accuracy"
        " FROM theses WHERE status IN ('CLOSED','PENDING_RESOLUTION') AND closed_at IS NOT NULL"
        " ORDER BY closed_at DESC LIMIT 10"
    ).fetchall()

    lessons_parts = []
    if recent_closes:
        # Per-thesis lessons
        for r in recent_closes:
            ticker_lesson = f"{r['ticker']} ({r['outcome']}): {r['lesson']}"
            if r["exit_reason"]:
                ticker_lesson += f" [exit: {r['exit_reason']}]"
            if r["timing_accuracy"]:
                ticker_lesson += f" [timing: {r['timing_accuracy']}]"
            lessons_parts.append(ticker_lesson)

    # When no theses have closed yet, extract context from journals and active theses
    if not lessons_parts:
        # Latest journal entry
        latest_journal = conn.execute(
            "SELECT entry_type, content, timestamp FROM decision_journal ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if latest_journal:
            content_snip = latest_journal["content"][:300] if latest_journal["content"] else ""
            date_snip = latest_journal["timestamp"][:10] if latest_journal["timestamp"] else ""
            lessons_parts.append(f"Last journal ({date_snip}): {content_snip}")

        # Active theses with their current status
        active_theses = conn.execute(
            "SELECT ticker, direction, confidence, catalyst, invalidation"
            " FROM theses WHERE status='ACTIVE' ORDER BY created_at DESC LIMIT 5"
        ).fetchall()
        if active_theses:
            thesis_lines = []
            for t in active_theses:
                thesis_lines.append(
                    f"{t['ticker']} {t['direction']} (conf:{t['confidence']}): "
                    f"catalyst={t['catalyst']}"
                )
            lessons_parts.append("Active theses: " + " | ".join(thesis_lines))

        # Market context from recent feed
        recent_feed = conn.execute(
            "SELECT source_type, observation FROM market_feed ORDER BY id DESC LIMIT 3"
        ).fetchall()
        if recent_feed:
            feed_lines = [f"{r['source_type']}: {r['observation'][:120]}" for r in recent_feed]
            lessons_parts.append("Market: " + " | ".join(feed_lines))

    # Build a meaningful summary
    if recent_closes:
        summary = "Review"
    elif lessons_parts:
        summary = "Session context"
    else:
        summary = "Auto-review"

    lessons = " | ".join(lessons_parts) if lessons_parts else (
        f"Review: active_return={data.get('active_return_pct')}%, win_rate={win_rate}%,"
        f" brier={round(brier, 4) if brier is not None else None}"
    )

    first_snapshot = conn.execute(
        "SELECT timestamp FROM snapshots ORDER BY id ASC LIMIT 1"
    ).fetchone()

    conn.execute(
        """INSERT INTO learning_log(
               period_start, period_end, summary, alpha_pct, active_return_pct,
               win_rate_pct, brier_score, calibration_drift, lessons, created_at
           ) VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, ?)""",
        (first_snapshot["timestamp"] if first_snapshot else "inception",
         data.get("latest_snapshot_date", now()),
         summary, data.get("active_return_pct"), win_rate,
         round(brier, 4) if brier is not None else None, drift,
         lessons, now()),
    )
def cmd_snapshot(_: argparse.Namespace) -> None:
    with connect() as conn:
        data = portfolio_status(conn)
        benchmark_ticker = _benchmark_ticker(conn)
        benchmark_quote = latest_quote(conn, benchmark_ticker) if benchmark_ticker else None
        conn.execute(
            """INSERT INTO snapshots(
                   cash, holdings_value, total, holdings_json, timestamp, benchmark_price
               ) VALUES (?, ?, ?, ?, ?, ?)""",
            (
                data["cash"],
                data["market_value"],
                data["nav"],
                json.dumps(data["holdings"]),
                now(),
                float(benchmark_quote["price"]) if benchmark_quote else None,
            ),
        )
    print(json.dumps(data))


def cmd_review(_: argparse.Namespace) -> None:
    with connect() as conn:
        data = portfolio_status(conn)
        benchmark_ticker = _benchmark_ticker(conn)
        totals = [float(row["total"]) for row in conn.execute("SELECT total FROM snapshots ORDER BY id")]
        latest_snapshot = conn.execute(
            "SELECT timestamp FROM snapshots ORDER BY id DESC LIMIT 1"
        ).fetchone()
        peak = None
        max_drawdown = 0.0
        for total in totals:
            peak = total if peak is None else max(peak, total)
            if peak:
                max_drawdown = min(max_drawdown, ((total - peak) / peak) * 100)
        benchmark_quote = latest_quote(conn, benchmark_ticker) if benchmark_ticker else None
        benchmark_start = conn.execute(
            """SELECT total, benchmark_price, timestamp FROM snapshots
               WHERE benchmark_price IS NOT NULL ORDER BY id ASC LIMIT 1"""
        ).fetchone()
        benchmark_return = None
        portfolio_period_return = None
        benchmark_status = "MISSING"
        if benchmark_start and benchmark_quote:
            benchmark_age = _quote_age_hours(benchmark_quote)
            max_age = state_float(conn, "strategy_quote_max_age_hours", 0.25)
            benchmark_status = "FRESH" if benchmark_age <= max_age else "STALE"
            if benchmark_status == "FRESH":
                benchmark_initial = float(benchmark_start["benchmark_price"])
                benchmark_return = (
                    (float(benchmark_quote["price"]) - benchmark_initial)
                    / benchmark_initial * 100
                )
                period_start_value = float(benchmark_start["total"])
                if period_start_value:
                    portfolio_period_return = (
                        (float(data["nav"]) - period_start_value) / period_start_value * 100
                    )
        outcomes = conn.execute(
            """SELECT confidence, outcome FROM theses
               WHERE status IN ('CLOSED','PENDING_RESOLUTION')
                 AND outcome IN ('WIN', 'LOSS')"""
        ).fetchall()
        forecasts = conn.execute(
            """SELECT confidence, event_outcome, brier_component FROM theses
               WHERE event_outcome IN ('YES','NO') AND brier_component IS NOT NULL"""
        ).fetchall()
        wins = sum(1 for row in outcomes if row["outcome"] == "WIN")
        win_rate = (wins / len(outcomes)) * 100 if outcomes else None
        brier = (sum(float(row["brier_component"]) for row in forecasts) / len(forecasts)
                 if forecasts else None)
        event_rate = (sum(row["event_outcome"] == "YES" for row in forecasts) / len(forecasts)
                      if forecasts else None)
        base_brier = event_rate * (1 - event_rate) if event_rate is not None else None
        brier_skill = (1 - brier / base_brier
                       if len(forecasts) >= 5 and base_brier and brier is not None else None)
        period_returns = [
            (totals[index] / totals[index - 1]) - 1
            for index in range(1, len(totals)) if totals[index - 1]
        ]
        volatility = None
        sortino = None
        if len(period_returns) >= 2:
            mean_return = sum(period_returns) / len(period_returns)
            variance = sum((value - mean_return) ** 2 for value in period_returns) / (len(period_returns) - 1)
            volatility = math.sqrt(variance)
            downside = [min(value, 0.0) for value in period_returns]
            downside_deviation = math.sqrt(sum(value * value for value in downside) / len(downside))
            sortino = mean_return / downside_deviation if downside_deviation else None
        active_return = (
            portfolio_period_return - benchmark_return
            if portfolio_period_return is not None and benchmark_return is not None else None
        )
        result = {
            "total_value": data["nav"],
            "return": data["return"],
            "return_pct": data["return_pct"],
            "realized_pnl": data["realized_pnl"],
            "gross_realized_pnl": data["gross_realized_pnl"],
            "trading_costs": data["trading_costs"],
            "benchmark": benchmark_ticker,
            "benchmark_status": benchmark_status,
            "active_period_start": benchmark_start["timestamp"] if benchmark_start else None,
            "portfolio_period_return_pct": (
                round(portfolio_period_return, 2)
                if portfolio_period_return is not None else None
            ),
            "benchmark_return_pct": round(benchmark_return, 2) if benchmark_return is not None else None,
            "active_return_pct": round(active_return, 2) if active_return is not None else None,
            "alpha_pct": None,
            "max_drawdown_pct": round(max_drawdown, 2),
            "closed_theses": conn.execute(
                "SELECT COUNT(*) FROM theses WHERE status IN ('CLOSED','PENDING_RESOLUTION')"
            ).fetchone()[0],
            "pending_forecast_resolution": conn.execute(
                "SELECT COUNT(*) FROM theses WHERE status='PENDING_RESOLUTION'"
            ).fetchone()[0],
            "resolved_forecasts": len(forecasts),
            "win_rate_pct": round(win_rate, 2) if win_rate is not None else None,
            "brier_score": round(brier, 4) if brier is not None else None,
            "brier_skill_score": round(brier_skill, 4) if brier_skill is not None else None,
            "period_volatility_pct": round(volatility * 100, 4) if volatility is not None else None,
            "sortino_per_period": round(sortino, 4) if sortino is not None else None,
            "snapshot_count": len(totals),
            "gross_exposure_pct": data["gross_exposure_pct"],
            "net_exposure_pct": data["net_exposure_pct"],
            "latest_snapshot_date": latest_snapshot["timestamp"] if latest_snapshot else None,
            "valuation_status": data["valuation_status"],
            "portfolio_heat_pct": data["portfolio_heat_pct"],
        }
        _write_learning_log(conn, result)
    print(json.dumps(result, indent=2))


def cmd_journal(args: argparse.Namespace) -> None:
    if not args.content.strip():
        raise ValueError("journal content is required")
    with connect() as conn:
        if args.run_id is not None and not conn.execute(
            "SELECT 1 FROM runs WHERE id=?", (args.run_id,)
        ).fetchone():
            raise ValueError(f"run {args.run_id} does not exist")
        conn.execute(
            "INSERT INTO decision_journal(entry_type, content, timestamp, run_id) VALUES (?, ?, ?, ?)",
            (args.entry_type, args.content.strip(), now(), args.run_id),
        )
    print(f"JOURNAL {args.entry_type}")


def cmd_run_finish(args: argparse.Namespace) -> None:
    with connect() as conn:
        row = conn.execute(
            "SELECT status, session_label FROM runs WHERE id=?", (args.run_id,)
        ).fetchone()
        if not row:
            raise ValueError(f"run {args.run_id} does not exist")
        if row["status"] == "COMPLETED":
            raise ValueError(f"run {args.run_id} is already completed")
        intraday_positions = _intraday_positions(conn)
        overdue = [item["ticker"] for item in intraday_positions if item["overdue"]]
        if overdue:
            raise ValueError(
                "overdue intraday positions must be closed before finishing a run: "
                + ", ".join(overdue)
            )
        is_close_session = str(row["session_label"] or "").lower() in {
            "close", "market-close", "closing"
        }
        market_timezone = _market_timezone(conn)
        current = datetime.now(market_timezone)
        current_minute = current.hour * 60 + current.minute
        adapter_schedule = (_get_adapter(conn) or {}).get("session_schedule") or {}
        exit_time = str(adapter_schedule.get("intraday_exit") or DEFAULT_INTRADAY_EXIT)
        exit_minute = _minutes_since_midnight(exit_time, "intraday exit")
        if intraday_positions and (is_close_session or current_minute >= exit_minute):
            raise ValueError(
                "close every INTRADAY position before the close run can finish: "
                + ", ".join(item["ticker"] for item in intraday_positions)
            )
        if is_close_session:
            active_intraday = [row["ticker"] for row in conn.execute(
                "SELECT ticker FROM theses WHERE status='ACTIVE' AND horizon LIKE 'INTRADAY:%'"
            )]
            if active_intraday:
                raise ValueError(
                    "resolve or close every INTRADAY thesis before the close run can finish: "
                    + ", ".join(active_intraday)
                )
        if not conn.execute(
            "SELECT 1 FROM decision_journal WHERE run_id=? LIMIT 1", (args.run_id,)
        ).fetchone():
            raise ValueError(
                "journal what changed, what was learned, and the decision before finishing the run"
            )
        conn.execute(
            "UPDATE runs SET status='COMPLETED', report=?, completed_at=? WHERE id=?",
            (args.report.strip(), now(), args.run_id),
        )
    print(json.dumps({"run_id": args.run_id, "status": "COMPLETED"}))


def cmd_run_start(args: argparse.Namespace) -> None:
    try:
        datetime.strptime(args.market_date, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("market date must be YYYY-MM-DD") from exc
    with connect() as conn:
        session = args.session or ""
        existing = conn.execute(
            """SELECT id, status FROM runs
               WHERE market_date=? AND session_label=? ORDER BY id DESC LIMIT 1""",
            (args.market_date, session),
        ).fetchone()
        if existing:
            print(json.dumps({"created": False, "run_id": existing["id"],
                              "status": existing["status"], "session": session}))
            return
        parameter_version = _parameter_version(conn)
        cursor = conn.execute(
            """INSERT INTO runs(
                   market_date, session_label, status, created_at,
                   decision_model_version, parameter_version, schedule_version
               ) VALUES (?, ?, 'STARTED', ?, ?, ?, ?)""",
            (args.market_date, session, now(), DECISION_MODEL_VERSION,
             parameter_version, OPERATING_SCHEDULE_VERSION),
        )
        result = {
            "created": True, "run_id": cursor.lastrowid, "status": "STARTED",
            "session": session, "decision_model_version": DECISION_MODEL_VERSION,
            "parameter_version": parameter_version,
            "schedule_version": OPERATING_SCHEDULE_VERSION,
        }
    print(json.dumps(result))


# ── Intel sources management ──


def cmd_intel_sources(args: argparse.Namespace) -> None:
    """Manage RSS/feed sources: list, add, remove, toggle."""
    action = args.intel_action
    with connect() as conn:
        if action == "list":
            rows = conn.execute(
                "SELECT id, name, feed_url, source_type, enabled,"
                " total_fetched, unique_count, duplicate_count, ticker_mentions,"
                " last_fetch_at, reason_disabled"
                " FROM intel_sources ORDER BY enabled DESC, total_fetched DESC"
            ).fetchall()
            print(json.dumps([dict(r) for r in rows], indent=2, default=str))

        elif action == "add":
            url = args.url
            name = args.name or urllib.parse.urlparse(url).netloc
            existing = conn.execute(
                "SELECT id FROM intel_sources WHERE feed_url=?", (url,)
            ).fetchone()
            if existing:
                print(f"EXISTS id={existing['id']}")
                return
            cur = conn.execute(
                "INSERT INTO intel_sources (name, feed_url, source_type, enabled, added_at)"
                " VALUES (?, ?, ?, 1, ?)",
                (name, url, args.source_type, now()),
            )
            print(f"ADDED id={cur.lastrowid}")

        elif action == "remove":
            sid = args.source_id
            conn.execute("DELETE FROM intel_sources WHERE id=?", (sid,))
            print(f"REMOVED id={sid}")

        elif action == "toggle":
            sid = args.source_id
            row = conn.execute(
                "SELECT enabled FROM intel_sources WHERE id=?", (sid,)
            ).fetchone()
            if not row:
                print("NOT FOUND")
                return
            new_enabled = 0 if row["enabled"] else 1
            conn.execute(
                "UPDATE intel_sources SET enabled=? WHERE id=?",
                (new_enabled, sid),
            )
            print(f"TOGGLED id={sid} → {'enabled' if new_enabled else 'disabled'}")
        else:
            print("Unknown action. Use: list | add | remove | toggle")


def cmd_intel_patterns(args: argparse.Namespace) -> None:
    """Manage learned relevance patterns."""
    action = args.intel_pattern_action
    with connect() as conn:
        if action == "list":
            rows = conn.execute(
                "SELECT id, pattern, pattern_type, weight, source, match_count,"
                " created_at, last_matched_at"
                " FROM intel_relevance_patterns"
                " ORDER BY weight DESC, match_count DESC"
            ).fetchall()
            if not rows:
                print(json.dumps([], indent=2))
                return
            result = []
            for r in rows:
                d = dict(r)
                d["created_at"] = str(d.get("created_at", ""))
                d["last_matched_at"] = str(d.get("last_matched_at", "")) if d.get("last_matched_at") else None
                result.append(d)
            print(json.dumps(result, indent=2, default=str))

        elif action == "add":
            pattern = args.pattern
            ptype = args.pattern_type
            weight = args.weight
            now = now()
            conn.execute(
                "INSERT OR IGNORE INTO intel_relevance_patterns"
                " (pattern, pattern_type, weight, source, created_at)"
                " VALUES (?, ?, ?, 'manual', ?)",
                (pattern, ptype, weight, now),
            )
            print(f"ADDED pattern='{pattern}' type={ptype} weight={weight}")

        elif action == "remove":
            pid = args.pattern_id
            conn.execute("DELETE FROM intel_relevance_patterns WHERE id=?", (pid,))
            print(f"REMOVED pattern id={pid}")

        else:
            print("Unknown action. Use: list | add | remove")


def cmd_intel_staging(args: argparse.Namespace) -> None:
    """View/manage the staging queue."""
    action = args.intel_staging_action
    with connect() as conn:
        if action == "status":
            total = conn.execute(
                "SELECT COUNT(*) as c FROM intel_relevance_staging WHERE batch_id IS NULL"
            ).fetchone()["c"]
            by_source = conn.execute(
                """SELECT s.name AS source_name, COUNT(*) AS cnt
                   FROM intel_relevance_staging st
                   JOIN intel_sources s ON s.id = st.source_id
                   WHERE st.batch_id IS NULL
                   GROUP BY st.source_id ORDER BY cnt DESC"""
            ).fetchall()
            oldest = conn.execute(
                "SELECT MIN(staged_at) as oldest FROM intel_relevance_staging WHERE batch_id IS NULL"
            ).fetchone()["oldest"]
            last_batch = conn.execute(
                "SELECT id, total_articles, passed, input_tokens, output_tokens, created_at"
                " FROM intel_relevance_batches ORDER BY created_at DESC LIMIT 1"
            ).fetchone()

            health = _intel_queue_health(conn)
            print(json.dumps({
                "staged_total": total,
                "by_source": [dict(r) for r in by_source],
                "oldest_staged": str(oldest) if oldest else None,
                "last_batch": dict(last_batch) if last_batch else None,
                "age_health": health,
            }, indent=2, default=str))

        elif action == "flush":
            count = conn.execute(
                "DELETE FROM intel_relevance_staging WHERE batch_id IS NULL"
            ).rowcount
            print(f"FLUSHED {count} staged articles")


def cmd_dashboard(args: argparse.Namespace) -> None:
    """Report the optional companion dashboard connection without exposing secrets."""
    convex_url = (
        os.environ.get("CONVEX_URL")
        or os.environ.get("NUXT_PUBLIC_CONVEX_URL")
        or _read_convex_url_from_config()
    )
    sync_url = _dashboard_sync_endpoint(convex_url)
    token_present = len(os.environ.get("HARPER_SYNC_TOKEN", "").strip()) >= 32
    status = {
        "optional": True,
        "source_of_truth": str(db_path()),
        "contract_version": DASHBOARD_CONTRACT_VERSION,
        "companion_repository": "https://github.com/balsimpson/harper-dashboard",
        "deployment_guide": "https://github.com/balsimpson/harper-dashboard/blob/main/DEPLOYMENT.md",
        "convex_url": convex_url,
        "sync_url": sync_url,
        "sync_token_configured": token_present,
        "configured": bool(convex_url and sync_url and token_present),
        "next_action": (
            "Review the production target and explicitly approve the first convex-sync."
            if convex_url and sync_url and token_present
            else "Deploy the optional companion dashboard and configure CONVEX_URL, "
                 "HARPER_SYNC_URL, and HARPER_SYNC_TOKEN outside chat."
        ),
    }

    if not args.guide:
        print(json.dumps(status, indent=2))
        return

    def configured_label(value: object) -> str:
        return "Configured" if value else "Not configured"

    print("Harper Dashboard (optional)")
    print(f"Source of truth: {status['source_of_truth']}")
    print(f"Dashboard contract: version {DASHBOARD_CONTRACT_VERSION}")
    print()
    print("Connection checklist")
    print(f"- Convex deployment: {convex_url or 'Not configured'}")
    print(f"- Private sync endpoint: {sync_url or 'Not configured'}")
    print(f"- Sync token: {configured_label(token_present)} (value hidden)")
    print()

    if status["configured"]:
        print("The dashboard connection is ready for review.")
        print("Next: confirm this production target and explicitly approve the first sync:")
        print(f"  {convex_url}")
        print("Then run: python3 scripts/portfolio.py convex-sync")
        return

    print("Next steps")
    print("1. Deploy the dashboard into your own Vercel and Convex accounts:")
    print(f"   {status['deployment_guide']}")
    print("2. In the production Convex deployment, set a unique HARPER_SYNC_TOKEN")
    print("   containing at least 32 characters.")
    print("3. In the Hermes runtime environment, configure CONVEX_URL,")
    print("   HARPER_SYNC_URL, and the same HARPER_SYNC_TOKEN.")
    print("4. Run this command again, review the target, and explicitly approve")
    print("   the first convex-sync before uploading portfolio data.")


def _convex_evidence_source_scores(conn: sqlite3.Connection) -> list[dict]:
    """Map claim accuracy into the existing Convex source-score payload shape."""
    by_domain: dict[str, dict] = {}
    for row in conn.execute(
        """SELECT source_url, status, resolved_at FROM evidence_claims
           WHERE status IN ('ACCURATE','INACCURATE')"""
    ):
        domain = urllib.parse.urlparse(row["source_url"]).netloc.lower()
        if not domain:
            continue
        stat = by_domain.setdefault(
            domain,
            {"domain": domain, "wins": 0, "losses": 0, "flats": 0, "last_updated": ""},
        )
        stat["wins" if row["status"] == "ACCURATE" else "losses"] += 1
        stat["last_updated"] = max(stat["last_updated"], row["resolved_at"] or "")
    return sorted(
        by_domain.values(), key=lambda item: (-(item["wins"] - item["losses"]), item["domain"])
    )


def cmd_usage(_: argparse.Namespace) -> None:
    """Refresh and report Harper's model, token, and cost accounting."""
    with connect() as conn:
        refresh = refresh_llm_usage(conn)
        rows = [dict(row) for row in conn.execute(
            "SELECT * FROM llm_usage ORDER BY started_at DESC, usage_key"
        )]
    print(json.dumps({
        "refreshed": refresh,
        "this_week": _summarize_usage_rows(rows, "week"),
        "this_month": _summarize_usage_rows(rows, "month"),
        "all_time": _summarize_usage_rows(rows, "all"),
    }, indent=2))


def _read_convex_url_from_config() -> str | None:
    """Read CONVEX_URL from config.yaml convex.url key."""
    config_path = (Path.home() / ".hermes" / "config.yaml")
    try:
        text = config_path.read_text()
        in_convex = False
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("convex:"):
                in_convex = True
                continue
            if in_convex and stripped.startswith("url:"):
                val = stripped.split("url:", 1)[1].strip().strip('"').strip("'")
                return val or None
            if in_convex and stripped and not stripped.startswith("  "):
                in_convex = False
        return None
    except Exception:
        return None


def _dashboard_sync_endpoint(convex_url: str | None) -> str | None:
    explicit = os.environ.get("HARPER_SYNC_URL", "").strip()
    if explicit:
        return explicit if explicit.rstrip("/").endswith("/harper-sync") else (
            f"{explicit.rstrip('/')}/harper-sync"
        )
    if not convex_url:
        return None
    parsed = urllib.parse.urlparse(convex_url)
    hostname = parsed.hostname or ""
    if not hostname.endswith(".convex.cloud"):
        return None
    site_hostname = f"{hostname[:-len('.convex.cloud')]}.convex.site"
    return urllib.parse.urlunparse((
        parsed.scheme or "https", site_hostname, "/harper-sync", "", "", ""
    ))


def _dashboard_auto_sync_configured() -> bool:
    convex_url = (
        os.environ.get("CONVEX_URL")
        or os.environ.get("NUXT_PUBLIC_CONVEX_URL")
        or _read_convex_url_from_config()
    )
    return bool(
        _dashboard_sync_endpoint(convex_url)
        and len(os.environ.get("HARPER_SYNC_TOKEN", "").strip()) >= 32
    )


def cmd_convex_sync(_: argparse.Namespace) -> None:
    """Push current dashboard data to Convex cloud backend.

    Reads CONVEX_URL from the environment or Hermes config. Dashboard sync is
    optional and remains disabled until a compatible endpoint is configured.
    """
    convex_url = (
        os.environ.get("CONVEX_URL")
        or os.environ.get("NUXT_PUBLIC_CONVEX_URL")
        or _read_convex_url_from_config()
    )
    if not convex_url:
        raise ValueError(
            "Convex sync is not configured. Set CONVEX_URL only after deploying "
            "a compatible dashboard with an authenticated /harper-sync endpoint."
        )
    endpoint = _dashboard_sync_endpoint(convex_url)
    if not endpoint:
        raise ValueError(
            "Dashboard sync endpoint is unavailable. Set HARPER_SYNC_URL to the "
            "production Convex Site URL ending in /harper-sync."
        )
    sync_token = os.environ.get("HARPER_SYNC_TOKEN", "").strip()
    if len(sync_token) < 32:
        raise ValueError(
            "HARPER_SYNC_TOKEN is missing or too short. Configure the same unique "
            "32-character-or-longer token in Hermes and the Convex deployment."
        )

    with connect() as conn:
        try:
            refresh_llm_usage(conn)
        except (OSError, sqlite3.Error, ValueError) as exc:
            # Keep the last successfully imported ledger available when the
            # Hermes accounting database is momentarily unavailable.
            conn.execute(
                "INSERT OR REPLACE INTO state(key, value) VALUES"
                " ('llm_usage_last_error', ?)",
                (f"{now()} {exc}",),
            )
            conn.commit()
        status_data = portfolio_status(conn)
        snapshots_rows = conn.execute(
            "SELECT cash, holdings_value, total, benchmark_price, timestamp"
            " FROM snapshots ORDER BY id"
        ).fetchall()
        theses_active = [dict(r) for r in conn.execute(
            "SELECT ticker, direction, confidence, horizon, target, catalyst, invalidation,"
            " variant_view, sources_json, created_at, investment_success_probability,"
            " ev_model, scenario_json, expected_return_pct, thesis_type, thesis_contract_json,"
            " review_date FROM theses WHERE status='ACTIVE'"
        )]
        theses_closed = [dict(r) for r in conn.execute(
            "SELECT ticker, direction, confidence, outcome, lesson, exit_reason,"
            " timing_accuracy, was_calibrated, closed_at FROM theses"
            " WHERE status IN ('CLOSED','PENDING_RESOLUTION') ORDER BY closed_at DESC"
        )]
        trades = [dict(r) for r in conn.execute(
            "SELECT ticker, action, shares, price, total, reason, timestamp"
            " FROM trades ORDER BY id DESC LIMIT 20"
        )]
        feed = [dict(r) for r in conn.execute(
            "SELECT source_type, observation, source_urls, created_at"
            " FROM market_feed ORDER BY id DESC LIMIT 20"
        )]
        source_scores = _convex_evidence_source_scores(conn)
        sources = [
            {
                "domain": item["domain"], "wins": item["wins"],
                "losses": item["losses"], "flats": 0,
                "ratio": round(item["wins"] / max(item["wins"] + item["losses"], 1), 3),
            }
            for item in source_scores
        ]
        learning = conn.execute(
            "SELECT win_rate_pct, brier_score, calibration_drift, lessons, created_at"
            " FROM learning_log ORDER BY id DESC LIMIT 1"
        ).fetchone()

        journal_entries = [dict(r) for r in conn.execute(
            "SELECT entry_type, content, timestamp FROM decision_journal"
            " ORDER BY id DESC LIMIT 15"
        )]
        research = [dict(r) for r in conn.execute(
            "SELECT ticker, sector, topic, findings, sources_json, created_at"
            " FROM research_library ORDER BY id DESC LIMIT 10"
        )]

        profile_row = conn.execute(
            "SELECT preferred_name, market, base_currency, initial_cash, user_timezone, updated_at"
            " FROM investor_profile WHERE id = 1"
        ).fetchone()
        adapter = _get_adapter(conn) or {}
        adapter_health = _adapter_health(adapter) if adapter else {}
        adapter_schedule = adapter.get("session_schedule") or {}
        adapter_cost_model = adapter.get("cost_model") or GENERIC_COST_FALLBACK
        benchmark_ticker = adapter.get("benchmark_ticker")

        mkts = {}
        for ticker in ([benchmark_ticker] if benchmark_ticker else []):
            rows = conn.execute(
                "SELECT date, close FROM historical_prices WHERE ticker=? ORDER BY date", (ticker,)
            ).fetchall()
            if rows:
                closes = [r["close"] for r in rows]
                latest = closes[-1]
                low5, high5 = min(closes), max(closes)
                pct = (latest - low5) / (high5 - low5) * 100 if (high5 - low5) else 50
                label = str(ticker)
                mkts[label] = {
                    "ticker": str(ticker),
                    "latest": round(latest, 2),
                    "low5": round(low5, 2),
                    "high5": round(high5, 2),
                    "pct": round(pct, 1),
                    "last_date": rows[-1]["date"],
                }

        intel_sources_count = conn.execute(
            "SELECT COUNT(*) FROM intel_sources WHERE enabled=1"
        ).fetchone()[0]
        intel_disabled = conn.execute(
            "SELECT name, reason_disabled FROM intel_sources WHERE enabled=0 ORDER BY id LIMIT 10"
        ).fetchall()
        intel_articles_stats = conn.execute(
            "SELECT COUNT(*) as total FROM intel_articles"
        ).fetchone()
        intel_dup_ticker_stats = conn.execute(
            "SELECT COALESCE(SUM(duplicate_count), 0) as dups,"
            " COALESCE(SUM(ticker_mentions), 0) as tickers FROM intel_sources"
        ).fetchone()
        intel_source_stats = [dict(r) for r in conn.execute(
            "SELECT id, name, feed_url, source_type, total_fetched, unique_count, duplicate_count,"
            " COALESCE(ticker_mentions, 0) as ticker_mentions,"
            " ROUND(1.0 * COALESCE(duplicate_count, 0) / MAX(total_fetched, 1) * 100, 1) as dup_pct,"
            " last_fetch_at, enabled, reason_disabled,"
            " COALESCE(relevance_pass_rate,0) as relevance_pass_rate,"
            " COALESCE(relevance_checked,0) as relevance_checked,"
            " COALESCE(llm_rescued_count,0) as llm_rescued_count"
            " FROM intel_sources ORDER BY enabled DESC, total_fetched DESC LIMIT 30"
        )]
        data_lifecycle = lifecycle_status(conn)
        decisions = [dict(r) for r in conn.execute(
            "SELECT id, run_id, action, ticker, rationale, evidence_json, cash_reason, timestamp,"
            " decision_model_version, parameter_version"
            " FROM decisions ORDER BY id DESC LIMIT 100"
        )]
        candidate_evaluations = [dict(r) for r in conn.execute(
            """SELECT id, run_id, ticker, thesis_type, research_depth, status,
                      preliminary_score, rank, quote_price, quote_source, quote_asof,
                      benchmark_price, benchmark_source, benchmark_asof,
                      binding_rejection_gate, gate_outcomes_json, sources_json,
                      snapshot_json, evaluated_at, hard_gates_json, hard_gate_pass,
                      score_components_json, weighted_score, scoring_model_version,
                      legacy_result, shadow_recommendation
               FROM candidate_evaluations ORDER BY id DESC LIMIT 500"""
        )]
        candidate_outcomes = [dict(r) for r in conn.execute(
            """SELECT evaluation_id, horizon_sessions, outcome_date,
                      candidate_price, benchmark_price, candidate_return_pct,
                      benchmark_return_pct, active_return_pct, marked_at
               FROM candidate_outcomes ORDER BY id DESC LIMIT 1500"""
        )]
        opportunity_audits = [dict(r) for r in conn.execute(
            """SELECT triggered, sessions_required, sessions_observed,
                      low_exposure_sessions, exposure_threshold_pct,
                      average_exposure_pct, screened_candidates, ranked_candidates,
                      deep_candidates, approved_candidates, rejected_candidates,
                      top_rejection_gate, diagnostics_json, window_start, window_end,
                      generated_at
               FROM opportunity_audits ORDER BY id DESC LIMIT 20"""
        )]

        # Quotes — last 100
        quotes = [dict(r) for r in conn.execute(
            "SELECT ticker, price, source, asof, recorded_at FROM quotes ORDER BY id DESC LIMIT 100"
        )]

        # Historical prices — last 30 rows per held ticker and configured benchmark.
        held_tickers = tuple(
            r[0] for r in conn.execute(
                "SELECT DISTINCT ticker FROM holdings ORDER BY ticker"
            ).fetchall()
        )
        historical_prices = []
        history_tickers = tuple(dict.fromkeys(
            held_tickers + ((str(benchmark_ticker),) if benchmark_ticker else ())
        ))
        for ticker in history_tickers:
            rows = conn.execute(
                "SELECT ticker, date, open, high, low, close, volume"
                " FROM historical_prices WHERE ticker=? ORDER BY date DESC LIMIT 30",
                (ticker,),
            ).fetchall()
            historical_prices.extend(dict(r) for r in rows)

        # Intel articles — last 50
        intel_articles = [dict(r) for r in conn.execute(
            "SELECT source_id, fingerprint, title, link, summary, source_domain, tickers, created_at"
            " FROM intel_articles ORDER BY id DESC LIMIT 50"
        )]

        # Runs — chronological order for Convex insertion so .order('desc') query returns newest first
        runs = [dict(r) for r in conn.execute(
            "SELECT id, market_date, session_label, status, report, created_at, completed_at,"
            " decision_model_version, parameter_version, schedule_version"
            " FROM runs ORDER BY id"
        )]

        latest_journal_raw = conn.execute(
            "SELECT content FROM decision_journal ORDER BY id DESC LIMIT 3"
        ).fetchall()
        latest_thoughts = [r["content"] for r in latest_journal_raw]
        llm_usage = [dict(r) for r in conn.execute(
            "SELECT usage_key, session_id, root_session_id, job_id, job_name, source,"
            " model, provider, task, started_at, ended_at, api_calls, input_tokens,"
            " output_tokens, cache_read_tokens, cache_write_tokens, reasoning_tokens,"
            " estimated_cost_usd, actual_cost_usd, cost_status"
            " FROM llm_usage ORDER BY started_at"
        )]

        initial_cash_row = conn.execute(
            "SELECT value FROM state WHERE key='initial_cash'"
        ).fetchone()
        market_session_payload = {
            "date": _state_text(conn, "market_session_date", ""),
            "status": _state_text(conn, "market_session_status", "UNKNOWN"),
            "open": _state_text(conn, "market_session_open", ""),
            "close": _state_text(conn, "market_session_close", ""),
            "source": _state_text(conn, "market_session_source", ""),
            "confirmed_at": _state_text(conn, "market_session_confirmed_at", ""),
        }

    nav_history = [
        {
            "t": r["timestamp"],
            "v": round(float(r["total"]), 2),
            "cash": round(float(r["cash"]), 2),
            "holdings_value": round(float(r["holdings_value"]), 2),
            **({"benchmark_price": round(float(r["benchmark_price"]), 6)}
               if r["benchmark_price"] is not None else {}),
        }
        for r in snapshots_rows
    ]

    latest_run = runs[-1] if runs else None  # runs[-1] = most recent (chronological order)

    # Build the payload matching the Convex syncDashboard mutation schema
    # Nulls must be stripped from v.optional() fields — Convex rejects null (wants absence)

    initial_cash_val = (
        float(initial_cash_row[0]) if initial_cash_row else status_data["cash"]
    )

    def strip_nulls(d):
        """Recursively remove None values from a dict (Convex optional fields).
        Also removes keys whose value is None, and handles None at the top.
        Works on lists too."""
        if d is None:
            return None
        if isinstance(d, dict):
            return {k: strip_nulls(v) for k, v in d.items() if v is not None}
        if isinstance(d, list):
            return [strip_nulls(v) for v in d]
        return d

    profile_payload = {
        "preferred_name": profile_row["preferred_name"] if profile_row else None,
        "user_timezone": (
            profile_row["user_timezone"]
            if profile_row and profile_row["user_timezone"]
            else "UTC"
        ),
        "portfolio_currency": (
            profile_row["base_currency"] if profile_row else status_data["reporting_currency"]
        ),
        "initial_capital": (
            float(profile_row["initial_cash"])
            if profile_row and profile_row["initial_cash"] is not None
            else initial_cash_val
        ),
    }
    portfolio_config = {
        "market_id": adapter.get("market_id") or status_data.get("market_id") or "UNCONFIGURED",
        "market_label": adapter.get("display_name") or (
            profile_row["market"] if profile_row else "Unconfigured market"
        ),
        "portfolio_currency": status_data["reporting_currency"],
        "benchmark_ticker": benchmark_ticker,
        "benchmark_name": benchmark_ticker,
        "benchmark_mode": adapter_health.get("benchmark_mode", "ABSOLUTE_RETURN_ONLY"),
        "cost_mode": adapter_health.get("cost_mode", "CONSERVATIVE_FALLBACK"),
    }
    dashboard_schedule = dict(adapter_schedule)
    dashboard_sessions = []
    if (adapter.get("market_timezone") and profile_row and profile_row["user_timezone"]):
        market_zone = ZoneInfo(str(adapter["market_timezone"]))
        user_zone = ZoneInfo(str(profile_row["user_timezone"]))
        reference_date = datetime.now(market_zone).date()
        for session in adapter_schedule.get("sessions") or []:
            hour, minute = _parse_hhmm(str(session["time"]), "session time")
            market_dt = datetime.combine(
                reference_date, datetime.min.time(), tzinfo=market_zone
            ).replace(hour=hour, minute=minute)
            local_dt = market_dt.astimezone(user_zone)
            dashboard_sessions.append({
                **session,
                "market_time": market_dt.strftime("%H:%M"),
                "user_time": local_dt.strftime("%H:%M"),
                "user_timezone": str(profile_row["user_timezone"]),
                "user_date_offset_days": (local_dt.date() - market_dt.date()).days,
            })
    else:
        dashboard_sessions = list(adapter_schedule.get("sessions") or [])
    dashboard_schedule["sessions"] = dashboard_sessions
    market_adapter_payload = {
        "market_id": adapter.get("market_id") or portfolio_config["market_id"],
        "display_name": adapter.get("display_name") or portfolio_config["market_label"],
        "status": adapter.get("status") or "DISCOVERY",
        "version": int(adapter.get("version") or 1),
        "market_timezone": adapter.get("market_timezone"),
        "native_currency": adapter.get("native_currency"),
        "benchmark_ticker": benchmark_ticker,
        "session_schedule_json": json.dumps(dashboard_schedule, ensure_ascii=False),
        "cost_model_json": json.dumps(adapter_cost_model, ensure_ascii=False),
        "capabilities_json": json.dumps(adapter.get("capabilities") or {}, ensure_ascii=False),
        "sources_json": json.dumps(adapter.get("sources") or {}, ensure_ascii=False),
        "market_session_json": json.dumps(market_session_payload, ensure_ascii=False),
        "last_validated_at": adapter.get("last_validated_at"),
        "updated_at": adapter.get("updated_at"),
    }
    valuation_payload = {
        "valued_at": snapshots_rows[-1]["timestamp"] if snapshots_rows else now(),
        "status": status_data.get("valuation_status", "UNAVAILABLE"),
        "portfolio_currency": status_data["reporting_currency"],
        "stale_tickers": status_data.get("stale_tickers", []),
        "gross_realized_pnl": status_data.get("gross_realized_pnl", 0),
        "trading_costs": status_data.get("trading_costs", 0),
        "portfolio_heat_pct": status_data.get("portfolio_heat_pct", 0),
        "risk_data_missing": status_data.get("risk_data_missing", []),
    }

    args_payload = {
        "profile": strip_nulls(profile_payload),
        "portfolio_config": strip_nulls(portfolio_config),
        "market_adapter": strip_nulls(market_adapter_payload),
        "valuation": strip_nulls(valuation_payload),
        "sync_metadata": {
            "source_updated_at": profile_row["updated_at"] if profile_row else now(),
            "synced_at": now(),
            "complete": True,
        },
        "status": strip_nulls({
            "reporting_currency": status_data["reporting_currency"],
            "cash": status_data["cash"],
            "initial_cash": initial_cash_val,
            "holdings": [
                strip_nulls({
                    "ticker": h["ticker"],
                    "direction": h["direction"],
                    "shares": abs(h.get("signed_shares", h.get("shares", 0))),
                    "signed_shares": h.get("signed_shares", h["shares"]),
                    "avg_cost_basis": h["avg_cost_basis"],
                    "market_price": h["market_price"],
                    "market_value": h["market_value"],
                    "unrealized_pnl": h["unrealized_pnl"],
                    "quote_source": h.get("quote_source"),
                    "quote_asof": h.get("quote_asof"),
                    "quote_age_hours": h.get("quote_age_hours"),
                    "trade_style": h.get("trade_style"),
                    "opened_at": h.get("opened_at"),
                })
                for h in status_data.get("holdings", [])
            ],
            "holdings_count": status_data.get("holdings_count", 0),
            "market_value": status_data.get("market_value", 0),
            "nav": status_data["nav"],
            "realized_pnl": status_data.get("realized_pnl", 0),
            "gross_exposure_pct": status_data.get("gross_exposure_pct", 0),
            "net_exposure_pct": status_data.get("net_exposure_pct", 0),
            "return": status_data.get("return", 0),
            "return_pct": status_data.get("return_pct", 0),
            "valuation_status": status_data.get("valuation_status", "UNAVAILABLE"),
            "stale_tickers": status_data.get("stale_tickers", []),
            "portfolio_heat_pct": status_data.get("portfolio_heat_pct", 0),
            "risk_data_missing": status_data.get("risk_data_missing", []),
            "gross_realized_pnl": status_data.get("gross_realized_pnl", 0),
            "trading_costs": status_data.get("trading_costs", 0),
            "exposure_regime": status_data.get("exposure_regime"),
            "latest_cash_reason": status_data.get("latest_cash_reason"),
            "latest_run": strip_nulls({
                "id": latest_run["id"],
                "market_date": latest_run["market_date"],
                "session_label": latest_run.get("session_label"),
                "status": latest_run["status"],
                "report": latest_run.get("report"),
                "created_at": latest_run["created_at"],
                "completed_at": latest_run.get("completed_at"),
            }) if latest_run else None,
        }),
        "nav_history": nav_history,
        "theses_active": [strip_nulls(t) for t in theses_active],
        "theses_closed": [strip_nulls(t) for t in theses_closed],
        "trades": trades,
        "feed": feed,
        "learning": strip_nulls(dict(learning)) if learning else None,
        "journal": journal_entries,
        "research": [strip_nulls(r) for r in research],
        "markets": mkts,
        "intel_sources_count": intel_sources_count,
        "intel_disabled": [strip_nulls(dict(r)) for r in intel_disabled],
        "intel_articles_stats": dict(intel_articles_stats) | dict(intel_dup_ticker_stats) if intel_articles_stats and intel_dup_ticker_stats else None,
        "intel_source_stats": [strip_nulls(s) for s in intel_source_stats],
        "data_lifecycle": strip_nulls(data_lifecycle),
        "runs": [strip_nulls(r) for r in runs],
        "latest_thoughts": latest_thoughts,
        "quotes": [strip_nulls(q) for q in quotes],
        "historical_prices": [strip_nulls(p) for p in historical_prices],
        "intel_articles": [strip_nulls(a) for a in intel_articles],
        "source_scores": [strip_nulls(s) for s in source_scores],
        "llm_usage": [strip_nulls(row) for row in llm_usage],
        "decisions": [strip_nulls(row) for row in decisions],
        "candidate_evaluations": [strip_nulls(row) for row in candidate_evaluations],
        "candidate_outcomes": [strip_nulls(row) for row in candidate_outcomes],
        "opportunity_audits": [strip_nulls(row) for row in opportunity_audits],
    }

    # Strip all null keys at every level — Convex v.optional() rejects null, expects absent key
    args_payload = strip_nulls(args_payload)
    args_payload = {k: v for k, v in args_payload.items() if v is not None}

    body = json.dumps({
        "contractVersion": DASHBOARD_CONTRACT_VERSION,
        "payload": args_payload,
    }).encode("utf-8")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {sync_token}",
    }
    req = urllib.request.Request(
        endpoint,
        data=body,
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode())
            if "error" in result:
                raise ValueError(f"Convex sync failed: {result['error']}")
            if result.get("contractVersion") != DASHBOARD_CONTRACT_VERSION:
                raise ValueError("Convex sync returned an incompatible contract version")
            print(json.dumps(result, indent=2))
    except urllib.error.HTTPError as e:
        raise ValueError(
            f"Convex sync failed (HTTP {e.code}): {e.read().decode()[:300]}"
        ) from e
    except urllib.error.URLError as e:
        raise ValueError(f"Cannot reach Convex at {convex_url}: {e.reason}") from e


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.set_defaults(func=cmd_init)

    profile = sub.add_parser(
        "profile", help="Inspect or update Harper's conversational profile"
    )
    profile_sub = profile.add_subparsers(dest="profile_command", required=True)
    profile_show = profile_sub.add_parser(
        "show", help="Read onboarding state and current virtual portfolio context"
    )
    profile_show.set_defaults(func=cmd_profile_show)
    profile_set = profile_sub.add_parser(
        "set", help="Persist confirmed required onboarding details"
    )
    profile_set.add_argument("--preferred-name")
    profile_set.add_argument("--market")
    profile_set.add_argument("--base-currency")
    profile_set.add_argument(
        "--initial-cash",
        type=float,
        help="Confirm the virtual portfolio's starting cash in the reporting currency",
    )
    profile_set.add_argument("--user-timezone")
    profile_set.add_argument(
        "--research-access",
        choices=("NOT_CHECKED", "FULL", "LIMITED", "UNAVAILABLE"),
        help="Persist the result of an observed web-search and extraction check",
    )
    profile_set.add_argument(
        "--automation", choices=("NOT_ASKED", "ENABLED", "SKIPPED")
    )
    profile_set.add_argument(
        "--delivery-target",
        help=(
            "Persist a confirmed target returned by `hermes send --list --json`, "
            "or local for local-only reports"
        ),
    )
    profile_set.add_argument(
        "--dashboard", choices=("NOT_ASKED", "ENABLED", "SKIPPED")
    )
    profile_set.add_argument("--confirm-scope-change")
    profile_set.set_defaults(func=cmd_profile_set)
    profile_preference = profile_sub.add_parser(
        "preference", help="Manage optional preferences separately from portfolio state"
    )
    preference_sub = profile_preference.add_subparsers(
        dest="preference_action", required=True
    )
    preference_set = preference_sub.add_parser("set")
    preference_set.add_argument("key")
    preference_set.add_argument("value")
    preference_set.set_defaults(func=cmd_profile_preference)
    preference_delete = preference_sub.add_parser("delete")
    preference_delete.add_argument("key")
    preference_delete.set_defaults(func=cmd_profile_preference)
    preference_reset = preference_sub.add_parser("reset")
    preference_reset.add_argument("--confirm", required=True)
    preference_reset.set_defaults(func=cmd_profile_preference)

    adapter = sub.add_parser(
        "market-adapter", help="Build and evolve a sourced market capability adapter"
    )
    adapter_sub = adapter.add_subparsers(dest="adapter_command", required=True)
    adapter_show = adapter_sub.add_parser("show")
    adapter_show.add_argument("market", nargs="?")
    adapter_show.set_defaults(func=cmd_market_adapter_show)
    adapter_set = adapter_sub.add_parser("set")
    adapter_set.add_argument("market")
    adapter_set.add_argument("--display-name")
    adapter_set.add_argument("--market-timezone")
    adapter_set.add_argument("--native-currency")
    adapter_set.add_argument("--benchmark-ticker")
    adapter_set.add_argument("--ticker-pattern")
    adapter_set.add_argument("--market-open")
    adapter_set.add_argument("--market-close")
    adapter_set.add_argument("--intraday-exit")
    adapter_set.add_argument("--sessions-json")
    adapter_set.add_argument("--fee-bps", type=float)
    adapter_set.add_argument("--slippage-large-bps", type=float)
    adapter_set.add_argument("--slippage-mid-bps", type=float)
    adapter_set.add_argument("--slippage-small-bps", type=float)
    adapter_set.add_argument("--capabilities-json")
    adapter_set.add_argument(
        "--status", choices=("DISCOVERY", "LIMITED", "OPERATIONAL")
    )
    adapter_set.add_argument(
        "--source-kind",
        choices=("quote", "historical", "calendar", "regulatory", "benchmark", "costs", "primary"),
    )
    adapter_set.add_argument("--source")
    adapter_set.add_argument("--effective-at")
    adapter_set.set_defaults(func=cmd_market_adapter_set)
    adapter_schedule = adapter_sub.add_parser("schedule")
    adapter_schedule.add_argument("market", nargs="?")
    adapter_schedule.add_argument("--user-timezone")
    adapter_schedule.set_defaults(func=cmd_market_adapter_schedule)

    reset = sub.add_parser("reset")
    reset.add_argument("--confirm", required=True)
    reset.set_defaults(func=cmd_reset)

    maintain = sub.add_parser(
        "maintain", help="Archive cold research data and purge expired raw records"
    )
    maintain.add_argument("--dry-run", action="store_true")
    maintain.add_argument("--quiet", action="store_true")
    maintain.set_defaults(func=cmd_maintain)

    market_session = sub.add_parser("market-session")
    market_session_sub = market_session.add_subparsers(
        dest="market_session_command", required=True
    )
    market_confirm = market_session_sub.add_parser("confirm")
    market_confirm.add_argument("market_date")
    market_confirm.add_argument("--status", required=True,
                                choices=("OPEN", "CLOSED", "SPECIAL"))
    market_confirm.add_argument("--source", required=True)
    market_confirm.add_argument("--open-time")
    market_confirm.add_argument("--close-time")
    market_confirm.set_defaults(func=cmd_market_session_confirm)

    quote = sub.add_parser("quote")
    quote.add_argument("ticker")
    quote.add_argument("price", type=float)
    quote.add_argument("source")
    quote.add_argument("--asof")
    quote.set_defaults(func=cmd_quote)

    thesis = sub.add_parser("thesis")
    thesis_sub = thesis.add_subparsers(dest="thesis_command", required=True)
    thesis_set = thesis_sub.add_parser("set")
    thesis_set.add_argument("ticker")
    thesis_set.add_argument("--direction", required=True, choices=("LONG",))
    thesis_set.add_argument("--trade-style", required=True,
                            choices=("INTRADAY", "POSITION"))
    thesis_set.add_argument("--thesis-type", default="CATALYST", choices=CANDIDATE_THESIS_TYPES)
    thesis_set.add_argument("--confidence", required=True, type=int, choices=range(1, 100))
    thesis_set.add_argument("--horizon", required=True)
    thesis_set.add_argument("--target", required=True, type=float)
    thesis_set.add_argument("--invalidation", required=True)
    thesis_set.add_argument("--catalyst", required=True)
    thesis_set.add_argument("--variant", required=True)
    thesis_set.add_argument("--sources", required=True)
    thesis_set.add_argument("--primary-sources", required=True)
    thesis_set.add_argument("--event", help="Binary catalyst event to score")
    thesis_set.add_argument("--resolution-date")
    thesis_set.add_argument("--resolution-source")
    thesis_set.add_argument("--review-date")
    thesis_set.add_argument("--quality-trajectory")
    thesis_set.add_argument("--valuation-gap")
    thesis_set.add_argument("--rerating-condition")
    thesis_set.add_argument("--trend-condition")
    thesis_set.add_argument("--technical-invalidation")
    thesis_set.add_argument("--entry-reference", required=True, type=float)
    thesis_set.add_argument("--invalidation-price", required=True, type=float)
    thesis_set.add_argument("--sector", required=True)
    thesis_set.add_argument("--counter-thesis", required=True)
    thesis_set.add_argument("--financial-summary", required=True)
    thesis_set.add_argument("--investment-success-probability", type=float,
                            help="Probability the investment payoff is positive; separate from event confidence")
    thesis_set.add_argument("--bear-return-pct", type=float)
    thesis_set.add_argument("--base-return-pct", type=float)
    thesis_set.add_argument("--bull-return-pct", type=float)
    thesis_set.add_argument("--bear-probability", type=float)
    thesis_set.add_argument("--base-probability", type=float)
    thesis_set.add_argument("--bull-probability", type=float)
    thesis_set.set_defaults(func=cmd_thesis_set)
    thesis_close = thesis_sub.add_parser("close")
    thesis_close.add_argument("ticker")
    thesis_close.add_argument("--outcome", required=True, choices=("WIN", "LOSS", "FLAT"))
    thesis_close.add_argument("--lesson", required=True)
    thesis_close.add_argument("--exit-reason", required=True, choices=(
        "invalidation_triggered", "catalyst_played_out", "risk_exit", "other"))
    thesis_close.add_argument("--timing", required=True, choices=("early", "on_time", "late"))
    thesis_close.add_argument("--event-outcome",
                              choices=("YES", "NO", "UNRESOLVED"))
    thesis_close.set_defaults(func=cmd_thesis_close)

    trade = sub.add_parser("trade")
    trade.add_argument("action", choices=("BUY", "SELL"))
    trade.add_argument("ticker")
    trade.add_argument("shares", type=float)
    trade.add_argument("price", type=float)
    trade.add_argument("reason")
    trade.add_argument("--liquidity", choices=("large", "mid", "small"), default="large")
    trade.add_argument("--starter", action="store_true", help="Open an initial starter position")
    trade.add_argument("--confirmation-source", help="Public evidence URL required before adding to a starter")
    trade.set_defaults(func=cmd_trade)

    status = sub.add_parser("status")
    status.set_defaults(func=cmd_status)

    snapshot = sub.add_parser("snapshot")
    snapshot.set_defaults(func=cmd_snapshot)
    review = sub.add_parser("review")
    review.set_defaults(func=cmd_review)

    diagnostics = sub.add_parser("diagnostics", help="Inspect effective operating contracts")
    diagnostics_sub = diagnostics.add_subparsers(dest="diagnostics_command", required=True)
    diagnostics_schedule = diagnostics_sub.add_parser("schedule")
    diagnostics_schedule.set_defaults(func=cmd_diagnostics_schedule)
    diagnostics_config = diagnostics_sub.add_parser("config")
    diagnostics_config.set_defaults(func=cmd_diagnostics_config)

    usage = sub.add_parser(
        "usage", help="Report Harper model, token, and cost usage"
    )
    usage.set_defaults(func=cmd_usage)

    dashboard = sub.add_parser(
        "dashboard", help="Inspect the optional companion dashboard connection"
    )
    dashboard.add_argument(
        "--guide",
        action="store_true",
        help="Show a human-readable setup checklist instead of JSON",
    )
    dashboard.set_defaults(func=cmd_dashboard)

    export = sub.add_parser("export")
    export.add_argument("path")
    export.set_defaults(func=cmd_export)
    backup = sub.add_parser("backup")
    backup.add_argument("path")
    backup.set_defaults(func=cmd_backup)

    release = sub.add_parser("release", help="Production-readiness checks and controlled clean-start tooling")
    release_sub = release.add_subparsers(dest="release_command", required=True)
    release_preflight = release_sub.add_parser("preflight", help="Validate database, schema, run state, and release contracts")
    release_preflight.add_argument("--strict", action="store_true", help="Fail on warnings as well as blockers")
    release_preflight.set_defaults(func=cmd_release_preflight)
    release_verify = release_sub.add_parser("verify-backup", help="Validate a Harper SQLite backup")
    release_verify.add_argument("path")
    release_verify.set_defaults(func=cmd_release_verify_backup)
    release_clean = release_sub.add_parser("clean-start", help="Back up and reset the virtual portfolio in one controlled operation")
    release_clean.add_argument("--backup", required=True)
    release_clean.add_argument("--confirm", required=True)
    release_clean.set_defaults(func=cmd_release_clean_start)

    run = sub.add_parser("run")
    run_sub = run.add_subparsers(dest="run_command", required=True)
    run_start = run_sub.add_parser("start")
    run_start.add_argument("market_date")
    run_start.add_argument("--session", default="", help="Session label (e.g. morning, mid-day, close)")
    run_start.set_defaults(func=cmd_run_start)

    run_finish = run_sub.add_parser("finish")
    run_finish.add_argument("run_id", type=int)
    run_finish.add_argument("report")
    run_finish.set_defaults(func=cmd_run_finish)

    journal = sub.add_parser("journal")
    journal.add_argument("entry_type")
    journal.add_argument("content")
    journal.add_argument("--run-id", type=int)
    journal.set_defaults(func=cmd_journal)

    decision = sub.add_parser("decision")
    decision_sub = decision.add_subparsers(dest="decision_command", required=True)
    decision_record = decision_sub.add_parser("record")
    decision_record.add_argument("action", choices=(
        "NO_TRADE", "OPEN", "ADD", "REDUCE", "CLOSE", "INVALIDATE"))
    decision_record.add_argument("--ticker")
    decision_record.add_argument("--rationale", required=True)
    decision_record.add_argument("--sources", required=True)
    decision_record.add_argument("--run-id", type=int)
    decision_record.add_argument("--cash-reason", choices=("NO_QUALIFYING_SETUP", "DEFENSIVE_REGIME", "RISK_CAPACITY", "AWAITING_CONFIRMATION", "OPERATIONAL_CONSTRAINT"))
    decision_record.set_defaults(func=cmd_decision_record)

    rejection_report = decision_sub.add_parser(
        "rejection-report",
        help="Summarize binding gates, near misses, and rejected-candidate outcomes",
    )
    rejection_report.add_argument("--run-id", type=int)
    rejection_report.add_argument("--mark-outcomes", action="store_true")
    rejection_report.add_argument(
        "--refresh", action="store_true",
        help="Refresh rejected-candidate historical prices before marking",
    )
    rejection_report.add_argument("--as-of", help="Outcome cutoff date (YYYY-MM-DD)")
    rejection_report.set_defaults(func=cmd_decision_rejection_report)
    comparison_report = decision_sub.add_parser(
        "comparison-report", help="Compare legacy candidate outcomes with the Phase 2 shadow model"
    )
    comparison_report.add_argument("--run-id", type=int)
    comparison_report.set_defaults(func=cmd_decision_comparison_report)

    regime = sub.add_parser("regime", help="Set or inspect the portfolio exposure regime")
    regime_sub = regime.add_subparsers(dest="regime_command", required=True)
    regime_set = regime_sub.add_parser("set")
    regime_set.add_argument("name", choices=("DEFENSIVE", "NORMAL", "STRONG_OPPORTUNITY"))
    regime_set.add_argument("--reason", required=True)
    regime_set.set_defaults(func=cmd_regime_set)
    regime_show = regime_sub.add_parser("show")
    regime_show.set_defaults(func=cmd_regime_show)

    candidate = sub.add_parser("candidate", help="Persist and review the opportunity funnel")
    candidate_sub = candidate.add_subparsers(dest="candidate_command", required=True)

    candidate_screen = candidate_sub.add_parser(
        "screen", help="Record one candidate or import a JSON-array screen"
    )
    candidate_screen.add_argument("ticker", nargs="?")
    candidate_screen.add_argument("--input", help="JSON file containing candidate objects")
    candidate_screen.add_argument("--run-id", type=int)
    candidate_screen.add_argument("--score", type=float)
    candidate_screen.add_argument("--thesis-type", default="CATALYST",
                                  choices=CANDIDATE_THESIS_TYPES)
    candidate_screen.add_argument("--research-depth", default="SCREENED",
                                  choices=CANDIDATE_DEPTHS)
    candidate_screen.add_argument("--status", default="WATCHLIST",
                                  choices=CANDIDATE_STATUSES)
    candidate_screen.add_argument("--rank", type=int)
    candidate_screen.add_argument("--quote-price", type=float)
    candidate_screen.add_argument("--quote-source")
    candidate_screen.add_argument("--quote-asof")
    candidate_screen.add_argument("--benchmark-price", type=float)
    candidate_screen.add_argument("--benchmark-source")
    candidate_screen.add_argument("--benchmark-asof")
    candidate_screen.add_argument("--binding-rejection-gate")
    candidate_screen.add_argument("--gate-outcomes", default="{}")
    candidate_screen.add_argument("--hard-gates", default="{}")
    candidate_screen.add_argument("--score-components", default="{}")
    candidate_screen.add_argument("--legacy-result", choices=CANDIDATE_STATUSES)
    candidate_screen.add_argument("--sources")
    candidate_screen.add_argument("--snapshot", default="{}")
    candidate_screen.add_argument("--evaluated-at")
    candidate_screen.set_defaults(func=cmd_candidate_screen)

    candidate_rank = candidate_sub.add_parser(
        "rank", help="Rank the latest evaluation for each candidate"
    )
    candidate_rank.add_argument("--run-id", type=int)
    candidate_rank.add_argument("--top", type=int, default=10)
    candidate_rank.set_defaults(func=cmd_candidate_rank)

    candidate_list = candidate_sub.add_parser("list")
    candidate_list.add_argument("--run-id", type=int)
    candidate_list.add_argument("--depth", choices=CANDIDATE_DEPTHS)
    candidate_list.add_argument("--status", choices=CANDIDATE_STATUSES)
    candidate_list.add_argument("--limit", type=int, default=100)
    candidate_list.set_defaults(func=cmd_candidate_list)

    candidate_mark = candidate_sub.add_parser(
        "mark-outcomes", help="Mark 5-, 10-, and 20-session rejected-candidate returns"
    )
    candidate_mark.add_argument("--as-of", help="Outcome cutoff date (YYYY-MM-DD)")
    candidate_mark.add_argument(
        "--refresh", action="store_true",
        help="Refresh rejected-candidate historical prices before marking",
    )
    candidate_mark.set_defaults(func=cmd_candidate_mark_outcomes)

    candidate_audit = candidate_sub.add_parser(
        "opportunity-audit", help="Diagnose sustained low exposure without auto-trading"
    )
    candidate_audit.add_argument("--sessions", type=int, default=5)
    candidate_audit.add_argument("--threshold-pct", type=float, default=25.0)
    candidate_audit.set_defaults(func=cmd_candidate_opportunity_audit)

    evidence = sub.add_parser("evidence")
    evidence_sub = evidence.add_subparsers(dest="evidence_action", required=True)
    evidence_add = evidence_sub.add_parser("add")
    evidence_add.add_argument("--ticker")
    evidence_add.add_argument("--claim", required=True)
    evidence_add.add_argument("--source", required=True)
    evidence_add.add_argument("--tier", required=True, type=int, choices=range(1, 8))
    evidence_add.add_argument("--published-at")
    evidence_add.set_defaults(func=cmd_evidence)
    evidence_resolve = evidence_sub.add_parser("resolve")
    evidence_resolve.add_argument("evidence_id", type=int)
    evidence_resolve.add_argument("--outcome", required=True,
                                  choices=("ACCURATE", "INACCURATE"))
    evidence_resolve.add_argument("--note", required=True)
    evidence_resolve.set_defaults(func=cmd_evidence)
    evidence_list = evidence_sub.add_parser("list")
    evidence_list.add_argument("--limit", type=int, default=50)
    evidence_list.set_defaults(func=cmd_evidence)

    corporate_action = sub.add_parser("corporate-action")
    corporate_action.add_argument("action_type", choices=("DIVIDEND", "SPLIT", "BONUS"))
    corporate_action.add_argument("ticker")
    corporate_action.add_argument("--amount-per-share", type=float)
    corporate_action.add_argument("--ratio", type=float)
    corporate_action.add_argument("--source", required=True)
    corporate_action.add_argument("--ex-date", required=True)
    corporate_action.set_defaults(func=cmd_corporate_action)

    learn = sub.add_parser("learn")
    learn_sub = learn.add_subparsers(dest="learn_command", required=True)

    learn_briefing = learn_sub.add_parser("briefing")
    learn_briefing.set_defaults(func=cmd_learn_briefing)
    learn_report = learn_sub.add_parser("report", help="Combined portfolio, forecast, rejection, cash-drag, and intel learning report")
    learn_report.add_argument("--sessions", type=int, default=20)
    learn_report.set_defaults(func=cmd_learn_report)

    learn_research = learn_sub.add_parser("research")
    learn_research.add_argument("ticker")
    learn_research.add_argument("--sector")
    learn_research.add_argument("--topic", required=True)
    learn_research.add_argument("--findings", required=True)
    learn_research.add_argument("--sources", required=True)
    learn_research.set_defaults(func=cmd_learn_research)

    learn_library = learn_sub.add_parser("library")
    learn_library.set_defaults(func=cmd_learn_library)

    learn_log = learn_sub.add_parser("log")
    learn_log_sub = learn_log.add_subparsers(dest="log_command", required=True)
    learn_log_latest = learn_log_sub.add_parser("latest")
    learn_log_latest.set_defaults(func=cmd_learn_log_latest)

    learn_params = learn_sub.add_parser("params")
    learn_params_sub = learn_params.add_subparsers(dest="param_command")
    learn_params_get = learn_params_sub.add_parser("get")
    learn_params_get.set_defaults(func=cmd_learn_params, subcommand="get")
    learn_params_set = learn_params_sub.add_parser("set")
    learn_params_set.add_argument("kv_pairs", nargs="+")
    learn_params_set.set_defaults(func=cmd_learn_params, subcommand="set")
    learn_params.set_defaults(func=cmd_learn_params, subcommand="get")

    learn_feed = learn_sub.add_parser("feed")
    learn_feed_sub = learn_feed.add_subparsers(dest="feed_command", required=True)
    learn_feed_add = learn_feed_sub.add_parser("add")
    learn_feed_add.add_argument("--type", dest="feed_type", required=True,
                                choices=("rbi_circular", "earnings_transcript", "commodity",
                                         "fx", "macro", "sector_rotation", "other"))
    learn_feed_add.add_argument("--observation", required=True)
    learn_feed_add.add_argument("--sources", required=True)
    learn_feed_add.add_argument("--run-id", type=int)
    learn_feed_add.set_defaults(func=cmd_learn_feed_add)
    learn_feed_latest = learn_feed_sub.add_parser("latest")
    learn_feed_latest.add_argument("--limit", type=int, default=10)
    learn_feed_latest.set_defaults(func=cmd_learn_feed_latest)

    learn_adapt = learn_sub.add_parser("adapt")
    learn_adapt.set_defaults(func=cmd_learn_adapt)

    learn_historical = learn_sub.add_parser("historical")
    learn_historical_sub = learn_historical.add_subparsers(dest="historical_command", required=True)
    hist_fetch = learn_historical_sub.add_parser("fetch")
    hist_fetch.add_argument("ticker")
    hist_fetch.add_argument("--years", type=int, default=5, choices=(1, 2, 5, 10))
    hist_fetch.set_defaults(func=cmd_learn_historical_fetch)
    hist_sim = learn_historical_sub.add_parser("simulate")
    hist_sim.add_argument("ticker")
    hist_sim.add_argument("--direction", required=True, choices=("LONG",))
    hist_sim.add_argument("--entry", required=True)
    hist_sim.add_argument("--exit")
    hist_sim.set_defaults(func=cmd_learn_historical_simulate)
    hist_analyze = learn_historical_sub.add_parser("analyze")
    hist_analyze.add_argument("ticker")
    hist_analyze.set_defaults(func=cmd_learn_historical_analyze)
    hist_ctx = learn_historical_sub.add_parser("context")
    hist_ctx.set_defaults(func=cmd_learn_historical_context)

    # ── Intel sources management ──
    intel = sub.add_parser("intel-sources", help="Manage RSS/feed sources (list|add|remove|toggle)")
    intel_sub = intel.add_subparsers(dest="intel_action", required=True)

    intel_list = intel_sub.add_parser("list", help="Show all sources with stats")
    intel_list.set_defaults(func=cmd_intel_sources)
    intel_quality = intel_sub.add_parser("quality", help="Source relevance, duplicates, downstream value proxy, and queue health")
    intel_quality.set_defaults(func=cmd_intel_quality)

    intel_add = intel_sub.add_parser("add", help="Add a new RSS source")
    intel_add.add_argument("url", help="RSS feed URL")
    intel_add.add_argument("--name", help="Display name (default: domain)")
    intel_add.add_argument("--type", dest="source_type", default="rss",
                          choices=("rss", "twitter", "reddit"), help="Source type")
    intel_add.set_defaults(func=cmd_intel_sources)

    intel_remove = intel_sub.add_parser("remove", help="Remove a source by ID")
    intel_remove.add_argument("source_id", type=int, help="Source ID from list")
    intel_remove.set_defaults(func=cmd_intel_sources)

    intel_toggle = intel_sub.add_parser("toggle", help="Enable/disable a source by ID")
    intel_toggle.add_argument("source_id", type=int, help="Source ID from list")
    intel_toggle.set_defaults(func=cmd_intel_sources)

    intel_patterns = intel_sub.add_parser("patterns", help="Manage learned relevance patterns (list|add|remove)")
    intel_pat_sub = intel_patterns.add_subparsers(dest="intel_pattern_action", required=True)

    intel_pat_list = intel_pat_sub.add_parser("list", help="Show all learned patterns with stats")
    intel_pat_list.set_defaults(func=cmd_intel_patterns)

    intel_pat_add = intel_pat_sub.add_parser("add", help="Add a relevance pattern")
    intel_pat_add.add_argument("pattern", help="Regex or text pattern to match")
    intel_pat_add.add_argument("--type", dest="pattern_type", default="entity",
                               choices=("ticker","entity","keyword","global_event_chain"),
                               help="Pattern category")
    intel_pat_add.add_argument("--weight", type=int, default=10, help="Match weight (higher = stronger signal)")
    intel_pat_add.set_defaults(func=cmd_intel_patterns)

    intel_pat_remove = intel_pat_sub.add_parser("remove", help="Remove a learned pattern by ID")
    intel_pat_remove.add_argument("pattern_id", type=int, help="Pattern ID from list")
    intel_pat_remove.set_defaults(func=cmd_intel_patterns)

    intel_staging = intel_sub.add_parser("staging", help="View staged articles awaiting LLM classification (status|flush)")
    intel_stg_sub = intel_staging.add_subparsers(dest="intel_staging_action", required=True)

    intel_stg_status = intel_stg_sub.add_parser("status", help="Show staging queue size")
    intel_stg_status.set_defaults(func=cmd_intel_staging)

    intel_stg_flush = intel_stg_sub.add_parser("flush", help="Delete all staged articles (for testing)")
    intel_stg_flush.set_defaults(func=cmd_intel_staging)

    # ── Convex cloud sync ──
    convex_sync = sub.add_parser("convex-sync", help="Push dashboard data to Convex cloud backend")
    convex_sync.set_defaults(func=cmd_convex_sync)

    return parser


# Functions whose execution should trigger an auto-sync to the Convex dashboard.
_STATE_CHANGING_FUNCS = frozenset({
    cmd_reset, cmd_maintain,
    cmd_quote, cmd_trade, cmd_thesis_set, cmd_thesis_close,
    cmd_journal, cmd_run_start, cmd_run_finish,
    cmd_learn_research, cmd_learn_feed_add,
    cmd_snapshot,
    cmd_decision_record, cmd_evidence, cmd_corporate_action,
    cmd_candidate_screen, cmd_candidate_rank, cmd_candidate_mark_outcomes,
    cmd_candidate_opportunity_audit,
})


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
        # Auto-sync state changes to the Convex dashboard (Nuxt app)
        evidence_changed = (
            args.func is cmd_evidence and getattr(args, "evidence_action", None) != "list"
        )
        state_changed = (
            (args.func in _STATE_CHANGING_FUNCS and args.func is not cmd_evidence)
            or evidence_changed
            or (args.func is cmd_learn_params and getattr(args, "subcommand", None) == "set")
            or (
                args.func is cmd_decision_rejection_report
                and getattr(args, "mark_outcomes", False)
            )
        )
        if args.func is cmd_maintain and getattr(args, "dry_run", False):
            state_changed = False
        if (
            os.environ.get("VIRTUAL_INVESTOR_DISABLE_SYNC") != "1"
            and state_changed
            and _dashboard_auto_sync_configured()
        ):
            try:
                cmd_convex_sync(args)
            except Exception:
                pass  # Don't fail the main command if the sync flakes
    except (ValueError, sqlite3.Error) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
