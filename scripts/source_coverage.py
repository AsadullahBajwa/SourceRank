"""
source_coverage.py
Summarize RSS source coverage by country, language, and tier.

Usage:
    python scripts/source_coverage.py
    python scripts/source_coverage.py --json
"""

import argparse
import csv
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config


def load_sources(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def build_source_report(rows: list[dict]) -> dict:
    by_country = Counter(row.get("country", "unknown") for row in rows)
    by_language = Counter(row.get("language", "unknown") for row in rows)
    by_tier = Counter(str(row.get("tier", "unknown")) for row in rows)
    tier1_by_country = Counter(
        row.get("country", "unknown")
        for row in rows
        if str(row.get("tier", "")) == "1"
    )
    return {
        "total_sources": len(rows),
        "by_country": dict(sorted(by_country.items())),
        "by_language": dict(sorted(by_language.items())),
        "by_tier": dict(sorted(by_tier.items())),
        "tier1_by_country": dict(sorted(tier1_by_country.items())),
    }


def print_report(report: dict) -> None:
    print(f"Total RSS sources: {report['total_sources']}")
    print(f"By country       : {report['by_country']}")
    print(f"By language      : {report['by_language']}")
    print(f"By tier          : {report['by_tier']}")
    print(f"Tier-1 by country: {report['tier1_by_country']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize RSS source coverage.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    args = parser.parse_args()

    report = build_source_report(load_sources(config.RSS_SOURCES_CSV))
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_report(report)


if __name__ == "__main__":
    main()
