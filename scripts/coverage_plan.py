"""
coverage_plan.py
Print the next active journalist handles that need tweet backfill.

Usage:
    python scripts/coverage_plan.py
    python scripts/coverage_plan.py --country Pakistan --limit 10
"""

import argparse
import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.audit_registry import load_rows, load_tweet_counts
from scripts.audit_registry import normalized_handle, row_is_active

CSV_FIELDS = ["handle", "name", "country", "beat", "followers_tier"]
FOLLOWER_TIER_PRIORITY = {"high": 0, "medium": 1, "low": 2}


def backfill_sort_key(row: dict, priority: str = "handle") -> tuple:
    handle = row.get("handle", "").strip().lower()
    if priority == "tier":
        tier = row.get("followers_tier", "").strip().lower()
        return (FOLLOWER_TIER_PRIORITY.get(tier, 3), handle)
    return (handle,)


def missing_active_rows(rows: list[dict], tweet_counts: dict[str, int],
                        country: str | None = None,
                        limit: int | None = None,
                        priority: str = "handle") -> list[dict]:
    country_filter = country.strip().lower() if country else None
    missing = [
        row
        for row in rows
        if row_is_active(row)
        and (not country_filter or row.get("country", "").strip().lower() == country_filter)
        and tweet_counts.get(normalized_handle(row), 0) == 0
    ]
    missing = sorted(missing, key=lambda row: backfill_sort_key(row, priority=priority))
    return missing[:limit] if limit and limit > 0 else missing


def missing_active_handles(rows: list[dict], tweet_counts: dict[str, int],
                           country: str | None = None,
                           limit: int | None = None,
                           priority: str = "handle") -> list[str]:
    return [
        row["handle"].strip()
        for row in missing_active_rows(
            rows,
            tweet_counts,
            country=country,
            limit=limit,
            priority=priority,
        )
    ]


def build_coverage_plan(rows: list[dict], tweet_counts: dict[str, int],
                        country: str | None = None,
                        limit: int | None = None,
                        priority: str = "handle") -> dict[str, object]:
    handles = missing_active_handles(
        rows,
        tweet_counts,
        country=country,
        limit=limit,
        priority=priority,
    )
    return {
        "country": country or "all",
        "priority": priority,
        "missing_count": len(handles),
        "handles": handles,
    }


def write_csv(rows: list[dict], stream) -> None:
    writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="List active handles that need tweet backfill.")
    parser.add_argument("--country", help="Filter missing handles by country")
    parser.add_argument("--limit", type=int, help="Maximum handles to print")
    parser.add_argument(
        "--priority",
        choices=["handle", "tier"],
        default="handle",
        help="Sort by handle or follower tier before applying --limit",
    )
    output_group = parser.add_mutually_exclusive_group()
    output_group.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    output_group.add_argument("--csv", action="store_true", help="Print spreadsheet-ready CSV")
    args = parser.parse_args()

    rows = load_rows()
    tweet_counts = load_tweet_counts()
    plan = build_coverage_plan(
        rows,
        tweet_counts,
        country=args.country,
        limit=args.limit,
        priority=args.priority,
    )
    if args.json:
        print(json.dumps(plan, indent=2))
        return
    if args.csv:
        write_csv(
            missing_active_rows(
                rows,
                tweet_counts,
                country=args.country,
                limit=args.limit,
                priority=args.priority,
            ),
            sys.stdout,
        )
        return
    for handle in plan["handles"]:
        print(handle)


if __name__ == "__main__":
    main()
