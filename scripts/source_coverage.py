"""
source_coverage.py
Summarize RSS source coverage by country, language, and tier.

Usage:
    python scripts/source_coverage.py
    python scripts/source_coverage.py --category wire
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
from scripts.source_taxonomy import normalize_category

EXPECTED_NEWS_COUNTRIES = {"Pakistan", "US", "global"}


def load_sources(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def filter_sources(rows: list[dict], category: str | None = None) -> list[dict]:
    if not category:
        return rows
    normalized = normalize_category(category)
    return [
        row for row in rows
        if normalize_category(row.get("category", "")) == normalized
    ]


def build_source_report(rows: list[dict]) -> dict:
    by_country = Counter(row.get("country", "unknown").strip() or "unknown" for row in rows)
    by_language = Counter(row.get("language", "unknown").strip() or "unknown" for row in rows)
    by_tier = Counter(str(row.get("tier", "unknown")).strip() or "unknown" for row in rows)
    by_category = Counter(
        normalize_category(row.get("category", "")) or "unknown"
        for row in rows
    )
    tier1_by_country = Counter(
        row.get("country", "unknown").strip() or "unknown"
        for row in rows
        if str(row.get("tier", "")).strip() == "1"
    )
    languages_by_country: dict[str, set[str]] = {}
    categories_by_country: dict[str, set[str]] = {}
    for row in rows:
        country = row.get("country", "unknown").strip() or "unknown"
        language = row.get("language", "unknown").strip() or "unknown"
        category = normalize_category(row.get("category", "")) or "unknown"
        languages_by_country.setdefault(country, set()).add(language)
        categories_by_country.setdefault(country, set()).add(category)
    return {
        "total_sources": len(rows),
        "by_country": dict(sorted(by_country.items())),
        "by_language": dict(sorted(by_language.items())),
        "by_tier": dict(sorted(by_tier.items())),
        "by_category": dict(sorted(by_category.items())),
        "tier1_by_country": dict(sorted(tier1_by_country.items())),
        "tier1_missing_countries": sorted(EXPECTED_NEWS_COUNTRIES - set(tier1_by_country)),
        "languages_by_country": {
            country: sorted(languages)
            for country, languages in sorted(languages_by_country.items())
        },
        "categories_by_country": {
            country: sorted(categories)
            for country, categories in sorted(categories_by_country.items())
        },
    }


def print_report(report: dict) -> None:
    print(f"Total RSS sources: {report['total_sources']}")
    print(f"By country       : {report['by_country']}")
    print(f"By language      : {report['by_language']}")
    print(f"By tier          : {report['by_tier']}")
    print(f"By category      : {report['by_category']}")
    print(f"Tier-1 by country: {report['tier1_by_country']}")
    print(f"Tier-1 gaps      : {report['tier1_missing_countries']}")
    print(f"Languages/country: {report['languages_by_country']}")
    print(f"Categories/country: {report['categories_by_country']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize RSS source coverage.")
    parser.add_argument("--category", help="Filter report by source category")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    args = parser.parse_args()

    report = build_source_report(
        filter_sources(load_sources(config.RSS_SOURCES_CSV), category=args.category)
    )
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_report(report)


if __name__ == "__main__":
    main()
