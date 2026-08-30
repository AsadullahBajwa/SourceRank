"""
coverage_plan.py
Print the next active journalist handles that need tweet backfill.

Usage:
    python scripts/coverage_plan.py
    python scripts/coverage_plan.py --country Pakistan --limit 10
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.audit_registry import load_rows, load_tweet_counts
from scripts.audit_registry import normalized_handle, row_is_active


def missing_active_handles(rows: list[dict], tweet_counts: dict[str, int],
                           country: str | None = None,
                           limit: int | None = None) -> list[str]:
    country_filter = country.strip().lower() if country else None
    missing = [
        row["handle"].strip()
        for row in rows
        if row_is_active(row)
        and (not country_filter or row.get("country", "").strip().lower() == country_filter)
        and tweet_counts.get(normalized_handle(row), 0) == 0
    ]
    missing = sorted(missing, key=str.lower)
    return missing[:limit] if limit and limit > 0 else missing


def build_coverage_plan(rows: list[dict], tweet_counts: dict[str, int],
                        country: str | None = None,
                        limit: int | None = None) -> dict[str, object]:
    handles = missing_active_handles(
        rows,
        tweet_counts,
        country=country,
        limit=limit,
    )
    return {
        "country": country or "all",
        "missing_count": len(handles),
        "handles": handles,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="List active handles that need tweet backfill.")
    parser.add_argument("--country", help="Filter missing handles by country")
    parser.add_argument("--limit", type=int, help="Maximum handles to print")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    args = parser.parse_args()

    plan = build_coverage_plan(
        load_rows(),
        load_tweet_counts(),
        country=args.country,
        limit=args.limit,
    )
    if args.json:
        print(json.dumps(plan, indent=2))
        return
    for handle in plan["handles"]:
        print(handle)


if __name__ == "__main__":
    main()
