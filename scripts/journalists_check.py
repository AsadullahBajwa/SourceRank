"""
journalists_check.py
Validate the journalist registry without scraping X.

Usage:
    python scripts/journalists_check.py
"""

import csv
import os
import re
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config


REQUIRED_COLUMNS = [
    "handle",
    "name",
    "beat",
    "country",
    "followers_tier",
    "verified",
    "active",
]
VALID_COUNTRIES = {"Pakistan", "US"}
VALID_FOLLOWER_TIERS = {"low", "medium", "high"}
VALID_BOOLEANS = {"true", "false"}
HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{1,30}$")


def validate_journalist_rows(rows: list[dict], minimum_total: int = 100) -> list[str]:
    errors = []
    seen_handles = set()
    country_counts = Counter()

    if len(rows) < minimum_total:
        errors.append(
            f"journalists.csv must contain at least {minimum_total} rows; found {len(rows)}"
        )

    for index, row in enumerate(rows, start=2):
        for column in REQUIRED_COLUMNS:
            if not row.get(column, "").strip():
                errors.append(f"Row {index} missing {column}")

        handle = row.get("handle", "").strip()
        lowered_handle = handle.lower()
        if handle and not HANDLE_RE.fullmatch(handle):
            errors.append(f"Row {index} has invalid X handle: {handle}")
        if lowered_handle in seen_handles:
            errors.append(f"Row {index} duplicates handle: {handle}")
        if lowered_handle:
            seen_handles.add(lowered_handle)

        country = row.get("country", "").strip()
        if country:
            country_counts[country] += 1
            if country not in VALID_COUNTRIES:
                errors.append(f"Row {index} has unsupported country: {country}")

        follower_tier = row.get("followers_tier", "").strip().lower()
        if follower_tier and follower_tier not in VALID_FOLLOWER_TIERS:
            errors.append(f"Row {index} has invalid followers_tier: {follower_tier}")

        for column in ("verified", "active"):
            value = row.get(column, "").strip().lower()
            if value and value not in VALID_BOOLEANS:
                errors.append(f"Row {index} has invalid {column} value: {value}")

    if rows and set(country_counts) == VALID_COUNTRIES:
        difference = abs(country_counts["Pakistan"] - country_counts["US"])
        if difference > 10:
            errors.append(
                "journalists.csv country balance differs by more than 10 rows: "
                f"Pakistan={country_counts['Pakistan']}, US={country_counts['US']}"
            )

    return errors


def validate_journalists_file(path: str) -> list[str]:
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != REQUIRED_COLUMNS:
            return [f"journalists.csv header must be: {','.join(REQUIRED_COLUMNS)}"]
        return validate_journalist_rows(list(reader))


def main() -> None:
    errors = validate_journalists_file(config.JOURNALISTS_CSV)
    if errors:
        for error in errors:
            print(error)
        raise SystemExit(1)
    print("Journalist registry check passed.")


if __name__ == "__main__":
    main()
