"""
source_balance_check.py
Validate that RSS coverage is broad enough for claim verification.

Usage:
    python scripts/source_balance_check.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from scripts.source_coverage import EXPECTED_NEWS_COUNTRIES, build_source_report, load_sources


CORE_SOURCE_CATEGORIES = {
    "wire",
    "public_broadcaster",
    "newspaper",
    "tv",
}


def validate_source_balance(
    rows: list[dict],
    minimum_categories: int = 5,
    required_categories: set[str] | None = None,
    required_tier1_countries: set[str] | None = None,
) -> list[str]:
    report = build_source_report(rows)
    categories = set(report["by_category"]) - {"unknown"}
    required_categories = required_categories or CORE_SOURCE_CATEGORIES
    required_tier1_countries = required_tier1_countries or EXPECTED_NEWS_COUNTRIES

    errors = []
    if len(categories) < minimum_categories:
        errors.append(
            f"RSS registry must cover at least {minimum_categories} source categories; "
            f"found {len(categories)}"
        )

    missing_categories = sorted(required_categories - categories)
    if missing_categories:
        errors.append(
            "RSS registry missing core source categories: "
            + ", ".join(missing_categories)
        )

    tier1_countries = set(report["tier1_by_country"])
    missing_tier1_countries = sorted(required_tier1_countries - tier1_countries)
    if missing_tier1_countries:
        errors.append(
            "RSS registry missing tier-1 sources for: "
            + ", ".join(missing_tier1_countries)
        )

    return errors


def main() -> None:
    errors = validate_source_balance(load_sources(config.RSS_SOURCES_CSV))
    if errors:
        for error in errors:
            print(error)
        raise SystemExit(1)
    print("RSS source balance check passed.")


if __name__ == "__main__":
    main()
