#!/usr/bin/env python3
"""Build a validated candidate-screen JSON array from JSONL on stdin."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse


REQUIRED_FIELDS = {
    "ticker",
    "thesis_type",
    "research_depth",
    "status",
    "preliminary_score",
    "quote_price",
    "quote_source",
    "quote_asof",
    "gate_outcomes",
    "sources",
    "snapshot",
}
THESIS_TYPES = {"CATALYST", "QUALITY", "VALUE", "MOMENTUM"}


def _public_url(value: object) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def validate_row(row: object, line_number: int) -> dict:
    if not isinstance(row, dict):
        raise ValueError(f"line {line_number}: candidate must be a JSON object")
    missing = sorted(REQUIRED_FIELDS - set(row))
    if missing:
        raise ValueError(f"line {line_number}: missing required fields: {', '.join(missing)}")
    ticker = str(row["ticker"]).upper()
    if not ticker.endswith((".NS", ".BO")):
        raise ValueError(f"line {line_number}: ticker must end in .NS or .BO")
    if row["thesis_type"] not in THESIS_TYPES:
        raise ValueError(f"line {line_number}: invalid thesis_type")
    if row["research_depth"] != "SCREENED":
        raise ValueError(f"line {line_number}: batch rows must use SCREENED depth")
    if row["status"] != "WATCHLIST":
        raise ValueError(f"line {line_number}: batch rows must use WATCHLIST status")
    if not isinstance(row["preliminary_score"], (int, float)) or not 0 <= row["preliminary_score"] <= 100:
        raise ValueError(f"line {line_number}: preliminary_score must be between 0 and 100")
    if not isinstance(row["quote_price"], (int, float)) or row["quote_price"] <= 0:
        raise ValueError(f"line {line_number}: quote_price must be positive")
    if not _public_url(row["quote_source"]):
        raise ValueError(f"line {line_number}: quote_source must be a public URL")
    if not isinstance(row["quote_asof"], str) or not row["quote_asof"].strip():
        raise ValueError(f"line {line_number}: quote_asof is required")
    if not isinstance(row["gate_outcomes"], dict):
        raise ValueError(f"line {line_number}: gate_outcomes must be an object")
    if not isinstance(row["sources"], list) or not row["sources"] or not all(_public_url(url) for url in row["sources"]):
        raise ValueError(f"line {line_number}: sources must contain public URLs")
    if not isinstance(row["snapshot"], dict):
        raise ValueError(f"line {line_number}: snapshot must be an object")
    return row


def build_candidate_screen(lines: list[str], output: Path, minimum: int = 40, maximum: int = 100) -> int:
    rows = []
    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"line {line_number}: invalid JSON: {exc.msg}") from exc
        rows.append(validate_row(parsed, line_number))
    if not minimum <= len(rows) <= maximum:
        raise ValueError(f"candidate screen requires {minimum}-{maximum} rows; got {len(rows)}")

    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=output.parent,
            prefix=f".{output.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            json.dump(rows, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temp_path, output)
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink()
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--min", dest="minimum", type=int, default=40)
    parser.add_argument("--max", dest="maximum", type=int, default=100)
    args = parser.parse_args()
    if args.minimum <= 0 or args.maximum < args.minimum:
        parser.error("require 0 < --min <= --max")
    try:
        count = build_candidate_screen(list(sys.stdin), args.output, args.minimum, args.maximum)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps({"output": str(args.output), "rows": count}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
