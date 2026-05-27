"""
audit_registry.py
Summarize journalist roster quality and local tweet coverage.

Usage:
    python scripts/audit_registry.py
    python scripts/audit_registry.py --strict
"""

import argparse
import csv
import json
import os
import sqlite3
from collections import Counter, defaultdict

import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config


def load_rows() -> list[dict]:
    with open(config.JOURNALISTS_CSV, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_tweet_counts() -> dict[str, int]:
    if not os.path.exists(config.TWEETS_DB):
        return {}
    conn = sqlite3.connect(config.TWEETS_DB)
    rows = conn.execute(
        "SELECT handle, COUNT(*) FROM tweets GROUP BY handle"
    ).fetchall()
    conn.close()
    return {handle.lower(): count for handle, count in rows}


def duplicate_names(rows: list[dict], active_only: bool = False) -> dict[str, list[str]]:
    handles_by_name: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        if active_only and row.get("active", "true").lower() != "true":
            continue
        handles_by_name[row["name"]].append(row["handle"])
    return {
        name: handles
        for name, handles in handles_by_name.items()
        if len(handles) > 1
    }


def build_report(rows: list[dict], tweet_counts: dict[str, int]) -> dict:
    active = [row for row in rows if row.get("active", "true").lower() == "true"]
    active_by_country = Counter(row["country"] for row in active)
    inactive_by_country = Counter(row["country"] for row in rows if row not in active)
    active_without_rows = [
        row for row in active if tweet_counts.get(row["handle"].lower(), 0) == 0
    ]
    active_without_tweets = [row["handle"] for row in active_without_rows]
    active_with_tweets = len(active) - len(active_without_tweets)
    active_without_by_country = Counter(row["country"] for row in active_without_rows)
    return {
        "total": len(rows),
        "active": len(active),
        "inactive": len(rows) - len(active),
        "active_with_tweets": active_with_tweets,
        "active_coverage_pct": round((active_with_tweets / len(active)) * 100, 1) if active else 0.0,
        "active_by_country": dict(sorted(active_by_country.items())),
        "inactive_by_country": dict(sorted(inactive_by_country.items())),
        "active_without_tweets_by_country": dict(sorted(active_without_by_country.items())),
        "duplicate_names": duplicate_names(rows),
        "active_duplicate_names": duplicate_names(rows, active_only=True),
        "active_without_tweets": sorted(active_without_tweets, key=str.lower),
    }


def print_report(report: dict) -> None:
    print(f"Total journalists : {report['total']}")
    print(f"Active journalists: {report['active']}")
    print(f"Inactive          : {report['inactive']}")
    print(f"Tweet coverage    : {report['active_with_tweets']}/{report['active']} "
          f"({report['active_coverage_pct']}%)")
    print(f"Active by country : {report['active_by_country']}")
    print(f"Inactive by country: {report['inactive_by_country']}")
    print(f"Missing by country: {report['active_without_tweets_by_country']}")
    print(f"Active duplicate names: {len(report['active_duplicate_names'])}")
    for name, handles in sorted(report["active_duplicate_names"].items()):
        print(f"  - {name}: {', '.join(handles)}")
    print(f"All duplicate names   : {len(report['duplicate_names'])}")
    for name, handles in sorted(report["duplicate_names"].items()):
        print(f"  - {name}: {', '.join(handles)}")
    print(f"Active without tweets: {len(report['active_without_tweets'])}")
    for handle in report["active_without_tweets"]:
        print(f"  - @{handle}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit journalist registry coverage and duplicates.")
    parser.add_argument("--strict", action="store_true",
                        help="Exit non-zero when duplicate names or uncovered active handles remain")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    args = parser.parse_args()

    report = build_report(load_rows(), load_tweet_counts())
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_report(report)

    if args.strict and (report["active_duplicate_names"] or report["active_without_tweets"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
