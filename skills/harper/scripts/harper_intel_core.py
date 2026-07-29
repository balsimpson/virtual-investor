#!/usr/bin/env python3
"""Shared, standard-library-only helpers for Harper's intel pipeline."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

PASS_THRESHOLD = 10
STATIC_PATTERNS: tuple[tuple[str, str, int], ...] = (
    (r"\b(?:NSE|BSE|SEBI|RBI|IRDAI|NIFTY|SENSEX|NSDL|CDSL|MCX)\b", "entity", 15),
    (r"\b(?:repo rate|CRR|SLR|fiscal deficit|union budget|FPI|FII|DII|demat|IPO)\b", "keyword", 12),
    (r"\b(?:RELIANCE|TCS|HDFCBANK|ICICIBANK|INFY|SBIN|BHARTIARTL|ITC|LT|AXISBANK|KOTAKBANK|MARUTI|SUNPHARMA|TATAMOTORS|HINDUNILVR)\b", "ticker", 20),
    (r"\b(?:Federal Reserve|Fed|OPEC\+?|Brent crude|Treasury yield|tariffs?)\b", "global_event_chain", 8),
)
TICKER_ALIASES = {
    "RELIANCE": "RELIANCE.NS", "TCS": "TCS.NS", "HDFCBANK": "HDFCBANK.NS",
    "ICICIBANK": "ICICIBANK.NS", "INFY": "INFY.NS", "SBIN": "SBIN.NS",
    "BHARTIARTL": "BHARTIARTL.NS", "ITC": "ITC.NS", "LT": "LT.NS",
    "AXISBANK": "AXISBANK.NS", "KOTAKBANK": "KOTAKBANK.NS", "MARUTI": "MARUTI.NS",
    "SUNPHARMA": "SUNPHARMA.NS", "TATAMOTORS": "TATAMOTORS.NS", "HINDUNILVR": "HINDUNILVR.NS",
}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def fingerprint(link: str, title: str) -> str:
    canonical = urllib.parse.urlsplit(link.strip())._replace(query="", fragment="").geturl().lower()
    return hashlib.sha256(f"{canonical}\n{title.strip().lower()}".encode()).hexdigest()


def source_domain(link: str) -> str:
    return urllib.parse.urlparse(link).netloc.lower()


def load_patterns(conn: sqlite3.Connection) -> list[tuple[int | None, re.Pattern[str], str, int]]:
    patterns = [(None, re.compile(p, re.I), typ, weight) for p, typ, weight in STATIC_PATTERNS]
    for row in conn.execute("SELECT id,pattern,pattern_type,weight FROM intel_relevance_patterns"):
        try:
            patterns.append((int(row[0]), re.compile(str(row[1]), re.I), str(row[2]), int(row[3])))
        except re.error:
            continue
    return patterns


@dataclass(frozen=True)
class Relevance:
    score: int
    tickers: tuple[str, ...]
    matched_pattern_ids: tuple[int, ...]


def score_article(text: str, patterns: Iterable[tuple[int | None, re.Pattern[str], str, int]]) -> Relevance:
    total = 0
    tickers: set[str] = set()
    matched_ids: list[int] = []
    for pattern_id, regex, pattern_type, weight in patterns:
        matches = list(regex.finditer(text))
        if not matches:
            continue
        total += weight
        if pattern_id is not None:
            matched_ids.append(pattern_id)
        if pattern_type == "ticker":
            for match in matches:
                token = match.group(0).upper().replace(" ", "")
                if token in TICKER_ALIASES:
                    tickers.add(TICKER_ALIASES[token])
    return Relevance(total, tuple(sorted(tickers)), tuple(matched_ids))


def update_source_relevance(conn: sqlite3.Connection, source_id: int, passed: bool, rescued: bool = False) -> None:
    row = conn.execute(
        "SELECT relevance_checked,relevance_pass_rate,llm_rescued_count FROM intel_sources WHERE id=?",
        (source_id,),
    ).fetchone()
    if not row:
        return
    checked = int(row[0] or 0) + 1
    previous_rate = float(row[1] or 0.0)
    previous_passes = previous_rate * (checked - 1)
    pass_rate = (previous_passes + (1 if passed else 0)) / checked
    rescued_count = int(row[2] or 0) + (1 if rescued else 0)
    enabled, reason = 1, None
    if checked >= 50 and pass_rate < 0.20:
        enabled, reason = 0, f"auto-disabled: relevance pass rate {pass_rate:.1%} after {checked} checks"
    conn.execute(
        "UPDATE intel_sources SET relevance_checked=?,relevance_pass_rate=?,llm_rescued_count=?,enabled=?,reason_disabled=? WHERE id=?",
        (checked, pass_rate, rescued_count, enabled, reason, source_id),
    )


def insert_article(conn: sqlite3.Connection, source_id: int, title: str, link: str, summary: str | None, tickers: Iterable[str]) -> bool:
    fp = fingerprint(link, title)
    cur = conn.execute(
        "INSERT OR IGNORE INTO intel_articles(source_id,fingerprint,title,link,summary,source_domain,tickers,created_at) VALUES(?,?,?,?,?,?,?,?)",
        (source_id, fp, title.strip(), link.strip(), summary, source_domain(link), ",".join(sorted(set(tickers))) or None, utcnow()),
    )
    return cur.rowcount == 1


def parse_classifier_decisions(path: Path) -> dict[int, dict]:
    raw = json.loads(path.read_text())
    items = raw.get("decisions", raw) if isinstance(raw, dict) else raw
    result: dict[int, dict] = {}
    for item in items:
        result[int(item["id"])] = item
    return result
