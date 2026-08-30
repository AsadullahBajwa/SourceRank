"""
feed_health.py
Summarize RSS feed fetch freshness and failures from claims.db.

Usage:
    python scripts/feed_health.py
    python scripts/feed_health.py --status missing --limit 10
    python scripts/feed_health.py --strict
    python scripts/feed_health.py --json
"""

import argparse
import datetime
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from scripts.source_coverage import load_sources
from time_utils import parse_utc, utc_now


def load_feed_logs(db_path: str) -> list[dict]:
    if not os.path.exists(db_path):
        return []
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """
                SELECT source_name, fetched_at, status, entry_count, new_count, error
                FROM feed_fetch_log
                ORDER BY fetched_at DESC
                """
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return []
    return [dict(row) for row in rows]


def latest_logs_by_source(log_rows: list[dict]) -> dict[str, dict]:
    latest = {}
    for row in log_rows:
        source_name = row.get("source_name", "")
        fetched_at = row.get("fetched_at", "")
        if not source_name:
            continue
        current = latest.get(source_name)
        if current is None or fetched_at > current.get("fetched_at", ""):
            latest[source_name] = row
    return latest


def _age_hours(fetched_at: str, now: datetime.datetime) -> float | None:
    try:
        return round((now - parse_utc(fetched_at)).total_seconds() / 3600, 1)
    except (TypeError, ValueError):
        return None


def build_feed_health_report(
    source_rows: list[dict],
    log_rows: list[dict],
    stale_after_hours: int = 24,
    now: datetime.datetime | None = None,
) -> dict:
    now = now or utc_now()
    latest = latest_logs_by_source(log_rows)
    sources = [row.get("name", "").strip() for row in source_rows if row.get("name", "").strip()]
    problems = []
    status_counts = {"ok": 0, "warning": 0, "error": 0, "missing": 0, "stale": 0}

    for source_name in sorted(sources, key=str.lower):
        log_row = latest.get(source_name)
        if not log_row:
            status_counts["missing"] += 1
            problems.append({
                "source_name": source_name,
                "status": "missing",
                "is_stale": False,
                "age_hours": None,
                "entry_count": 0,
                "new_count": 0,
                "error": "No fetch log recorded",
            })
            continue

        status = str(log_row.get("status", "")).strip().lower() or "unknown"
        if status in status_counts:
            status_counts[status] += 1
        age_hours = _age_hours(log_row.get("fetched_at", ""), now)
        is_stale = age_hours is None or age_hours > stale_after_hours
        if is_stale:
            status_counts["stale"] += 1
        if status != "ok" or is_stale:
            problems.append({
                "source_name": source_name,
                "status": status,
                "is_stale": is_stale,
                "age_hours": age_hours,
                "entry_count": int(log_row.get("entry_count") or 0),
                "new_count": int(log_row.get("new_count") or 0),
                "error": log_row.get("error", ""),
            })

    return {
        "total_sources": len(sources),
        "fetched_sources": len(sources) - status_counts["missing"],
        "stale_after_hours": stale_after_hours,
        "status_counts": status_counts,
        "problem_count": len(problems),
        "problems": problems,
    }


def filter_problems(
    problems: list[dict],
    status: str | None = None,
    limit: int | None = None,
) -> list[dict]:
    selected = problems
    if status:
        normalized = status.strip().lower()
        if normalized == "stale":
            selected = [problem for problem in selected if problem.get("is_stale")]
        else:
            selected = [
                problem for problem in selected
                if problem.get("status") == normalized
            ]
    if limit and limit > 0:
        selected = selected[:limit]
    return selected


def has_feed_problems(report: dict) -> bool:
    return int(report.get("problem_count") or 0) > 0


def print_report(report: dict) -> None:
    print(f"Total sources : {report['total_sources']}")
    print(f"Fetched sources: {report['fetched_sources']}")
    print(f"Problem sources: {report['problem_count']}")
    print(f"Status counts : {report['status_counts']}")
    for problem in report["problems"]:
        age = problem["age_hours"] if problem["age_hours"] is not None else "unknown"
        print(
            f"- {problem['source_name']}: {problem['status']} "
            f"age={age}h entries={problem['entry_count']} new={problem['new_count']}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize RSS feed fetch health.")
    parser.add_argument("--stale-hours", type=int, default=24,
                        help="Hours after which a feed log is considered stale")
    parser.add_argument("--status", choices=["missing", "stale", "warning", "error"],
                        help="Filter problem feeds by status")
    parser.add_argument("--limit", type=int, help="Maximum problem feeds to print")
    parser.add_argument("--strict", action="store_true",
                        help="Exit non-zero when any feed problem is present")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    args = parser.parse_args()

    report = build_feed_health_report(
        load_sources(config.RSS_SOURCES_CSV),
        load_feed_logs(config.CLAIMS_DB),
        stale_after_hours=args.stale_hours,
    )
    report["problems"] = filter_problems(
        report["problems"],
        status=args.status,
        limit=args.limit,
    )
    report["shown_problem_count"] = len(report["problems"])
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print_report(report)
    if args.strict and has_feed_problems(report):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
