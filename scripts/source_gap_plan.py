"""
source_gap_plan.py
Plan the next RSS source additions by country and source category.

Usage:
    python scripts/source_gap_plan.py
    python scripts/source_gap_plan.py --country Pakistan --limit 5
    python scripts/source_gap_plan.py --json
"""

import argparse
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from scripts.source_coverage import load_sources
from scripts.source_taxonomy import CATEGORY_LABELS, normalize_category

COUNTRY_CATEGORY_TARGETS = {
    "global": {
        "wire": 3,
        "public_broadcaster": 1,
    },
    "Pakistan": {
        "newspaper": 3,
        "tv": 3,
        "public_broadcaster": 1,
        "radio": 1,
        "digital_native": 1,
    },
    "US": {
        "newspaper": 2,
        "tv": 2,
        "public_broadcaster": 1,
        "digital_native": 3,
        "nonprofit_investigative": 1,
        "radio": 1,
    },
}


def source_counts_by_country_category(rows: list[dict]) -> Counter:
    counts = Counter()
    for row in rows:
        country = row.get("country", "").strip() or "unknown"
        category = normalize_category(row.get("category", "")) or "unknown"
        counts[(country, category)] += 1
    return counts


def build_gap_plan(
    rows: list[dict],
    targets: dict[str, dict[str, int]] | None = None,
    country: str | None = None,
) -> list[dict]:
    targets = targets or COUNTRY_CATEGORY_TARGETS
    country_filter = country.strip().lower() if country else None
    counts = source_counts_by_country_category(rows)
    gaps = []

    for target_country, category_targets in targets.items():
        if country_filter and target_country.lower() != country_filter:
            continue
        for category, target in category_targets.items():
            current = counts[(target_country, category)]
            missing = max(target - current, 0)
            if not missing:
                continue
            gaps.append({
                "country": target_country,
                "category": category,
                "category_label": CATEGORY_LABELS.get(category, category),
                "current": current,
                "target": target,
                "missing": missing,
                "priority": "high" if current == 0 else "medium",
            })

    return sorted(
        gaps,
        key=lambda row: (
            0 if row["priority"] == "high" else 1,
            -row["missing"],
            row["country"],
            row["category"],
        ),
    )


def limited_gaps(gaps: list[dict], limit: int | None = None) -> list[dict]:
    if limit and limit > 0:
        return gaps[:limit]
    return gaps


def print_gap_plan(gaps: list[dict]) -> None:
    if not gaps:
        print("No RSS source category gaps found.")
        return
    print("| Country | Category | Current | Target | Missing | Priority |")
    print("|---|---|---:|---:|---:|---|")
    for gap in gaps:
        print(
            f"| {gap['country']} | {gap['category_label']} | "
            f"{gap['current']} | {gap['target']} | {gap['missing']} | {gap['priority']} |"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Plan RSS source additions by country/category.")
    parser.add_argument("--country", help="Filter gaps by country")
    parser.add_argument("--limit", type=int, help="Maximum gaps to print")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    args = parser.parse_args()

    gaps = limited_gaps(
        build_gap_plan(load_sources(config.RSS_SOURCES_CSV), country=args.country),
        limit=args.limit,
    )
    if args.json:
        print(json.dumps({"gaps": gaps}, indent=2))
    else:
        print_gap_plan(gaps)


if __name__ == "__main__":
    main()
