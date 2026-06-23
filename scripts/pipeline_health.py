"""
pipeline_health.py
Summarize local SourceRank pipeline health without running the pipeline.

Usage:
    python scripts/pipeline_health.py
    python scripts/pipeline_health.py --json
"""

import argparse
import csv
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config


def count_csv_rows(path: str) -> int:
    if not os.path.exists(path):
        return 0
    with open(path, newline="", encoding="utf-8") as f:
        return sum(1 for _ in csv.DictReader(f))


def sqlite_count(path: str, table: str, where: str | None = None) -> int | None:
    if not os.path.exists(path):
        return None
    query = f"SELECT COUNT(*) FROM {table}"
    if where:
        query += f" WHERE {where}"
    try:
        conn = sqlite3.connect(path)
        try:
            return conn.execute(query).fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error:
        return None


def latest_snapshot(history_dir: str) -> str:
    index_path = os.path.join(history_dir, "index.json")
    if not os.path.exists(index_path):
        return ""
    try:
        with open(index_path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return ""
    return data.get("latest", "")


def build_health_report() -> dict:
    active_journalists = 0
    if os.path.exists(config.JOURNALISTS_CSV):
        with open(config.JOURNALISTS_CSV, newline="", encoding="utf-8") as f:
            active_journalists = sum(
                1 for row in csv.DictReader(f)
                if row.get("active", "").strip().lower() == "true"
            )

    return {
        "journalists": count_csv_rows(config.JOURNALISTS_CSV),
        "active_journalists": active_journalists,
        "rss_sources": count_csv_rows(config.RSS_SOURCES_CSV),
        "tweets_db_exists": os.path.exists(config.TWEETS_DB),
        "claims_db_exists": os.path.exists(config.CLAIMS_DB),
        "tweets": sqlite_count(config.TWEETS_DB, "tweets"),
        "original_tweets": sqlite_count(config.TWEETS_DB, "tweets", "is_retweet = 0"),
        "claims": sqlite_count(config.CLAIMS_DB, "claims"),
        "pending_claims": sqlite_count(config.CLAIMS_DB, "claims", "verdict = 'PENDING'"),
        "latest_snapshot": latest_snapshot(config.HISTORY_DIR),
    }


def print_report(report: dict) -> None:
    for key, value in report.items():
        print(f"{key}: {value if value is not None else 'unavailable'}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize local SourceRank pipeline health.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    args = parser.parse_args()

    report = build_health_report()
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print_report(report)


if __name__ == "__main__":
    main()
