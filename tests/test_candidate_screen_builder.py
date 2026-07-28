import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_candidate_screen.py"
SPEC = importlib.util.spec_from_file_location("build_candidate_screen", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def candidate(index: int) -> dict:
    return {
        "ticker": f"TEST{index}.NS",
        "thesis_type": "QUALITY",
        "research_depth": "SCREENED",
        "status": "WATCHLIST",
        "preliminary_score": 60 + index / 10,
        "quote_price": 100 + index,
        "quote_source": f"https://quotes.example/test{index}",
        "quote_asof": "2026-07-28T09:20:00+05:30",
        "gate_outcomes": {"liquidity": "PASS"},
        "sources": [f"https://sources.example/test{index}"],
        "snapshot": {"sector": "Industrials"},
    }


def test_builds_atomic_json_array(tmp_path: Path) -> None:
    output = tmp_path / "candidates.json"
    lines = [json.dumps(candidate(index)) for index in range(40)]
    assert MODULE.build_candidate_screen(lines, output) == 40
    rows = json.loads(output.read_text())
    assert len(rows) == 40
    assert rows[0]["ticker"] == "TEST0.NS"


def test_rejects_missing_required_field(tmp_path: Path) -> None:
    output = tmp_path / "candidates.json"
    rows = [candidate(index) for index in range(40)]
    del rows[3]["quote_source"]
    with pytest.raises(ValueError, match="missing required fields: quote_source"):
        MODULE.build_candidate_screen([json.dumps(row) for row in rows], output)
    assert not output.exists()


def test_rejects_short_screen(tmp_path: Path) -> None:
    output = tmp_path / "candidates.json"
    with pytest.raises(ValueError, match="requires 40-100 rows; got 3"):
        MODULE.build_candidate_screen([json.dumps(candidate(index)) for index in range(3)], output)
