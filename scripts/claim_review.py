"""
claim_review.py
List claims that deserve manual review before scores are treated as final.

Usage:
    python scripts/claim_review.py
    python scripts/claim_review.py --verdict UNVERIFIED --limit 25
    python scripts/claim_review.py --json
"""

import argparse
import csv
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config


DEFAULT_VERDICTS = ["UNVERIFIED", "EXPIRED", "PENDING"]
CSV_FIELDS = [
    "id",
    "handle",
    "claim_text",
    "claim_type",
    "verdict",
    "confidence",
    "verdict_source",
    "verdict_url",
    "tweet_created_at",
]


def review_candidates(conn: sqlite3.Connection, verdicts: list[str],
                      max_confidence: float | None = None,
                      limit: int | None = None) -> list[dict]:
    conn.row_factory = sqlite3.Row
    placeholders = ",".join("?" for _ in verdicts)
    query = f"""
        SELECT id, handle, claim_text, claim_type, verdict, confidence,
               verdict_source, verdict_url, tweet_created_at
        FROM claims
        WHERE verdict IN ({placeholders})
    """
    params: list[object] = list(verdicts)
    if max_confidence is not None:
        query += " OR confidence <= ?"
        params.append(max_confidence)
    query += " ORDER BY confidence ASC, tweet_created_at DESC"
    if limit and limit > 0:
        query += " LIMIT ?"
        params.append(limit)
    return [dict(row) for row in conn.execute(query, params).fetchall()]


def print_table(rows: list[dict]) -> None:
    if not rows:
        print("No review candidates found.")
        return
    print("| Handle | Verdict | Confidence | Claim |")
    print("|---|---|---:|---|")
    for row in rows:
        claim = row.get("claim_text", "").replace("|", "\\|")
        if len(claim) > 120:
            claim = claim[:117] + "..."
        print(
            f"| @{row.get('handle', '')} | {row.get('verdict', '')} | "
            f"{row.get('confidence', 0):.2f} | {claim} |"
        )


def write_csv(rows: list[dict], stream) -> None:
    writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="List claims that deserve manual review.")
    parser.add_argument("--verdict", action="append", choices=["PENDING", "UNVERIFIED", "EXPIRED", "CONFIRMED", "REFUTED"],
                        help="Verdict to include. Can be passed multiple times.")
    parser.add_argument("--max-confidence", type=float,
                        help="Also include claims at or below this confidence")
    parser.add_argument("--limit", type=int, default=50, help="Maximum rows to return")
    output_group = parser.add_mutually_exclusive_group()
    output_group.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    output_group.add_argument("--csv", action="store_true", help="Print spreadsheet-ready CSV")
    args = parser.parse_args()

    conn = sqlite3.connect(config.CLAIMS_DB)
    rows = review_candidates(
        conn,
        verdicts=args.verdict or DEFAULT_VERDICTS,
        max_confidence=args.max_confidence,
        limit=args.limit,
    )
    conn.close()

    if args.json:
        print(json.dumps({"claims": rows}, indent=2))
    elif args.csv:
        write_csv(rows, sys.stdout)
    else:
        print_table(rows)


if __name__ == "__main__":
    main()
