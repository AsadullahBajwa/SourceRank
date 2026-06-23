"""
rss_check.py
Validate the RSS source registry without making network requests.

Usage:
    python scripts/rss_check.py
"""

import csv
import os
import sys
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config


REQUIRED_COLUMNS = ["name", "url", "country", "language", "tier"]
VALID_TIERS = {"1", "2", "3"}
DEAD_HOSTS = {"feeds.reuters.com"}


def read_sources(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader)


def validate_source_rows(rows: list[dict]) -> list[str]:
    errors = []
    seen_names = set()

    for index, row in enumerate(rows, start=2):
        for column in REQUIRED_COLUMNS:
            if not row.get(column, "").strip():
                errors.append(f"Row {index} missing {column}")

        name = row.get("name", "").strip()
        if name:
            lowered = name.lower()
            if lowered in seen_names:
                errors.append(f"Row {index} duplicates source name: {name}")
            seen_names.add(lowered)

        url = row.get("url", "").strip()
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            errors.append(f"Row {index} has invalid URL: {url}")
        if parsed.netloc.lower() in DEAD_HOSTS:
            errors.append(f"Row {index} uses dead RSS host: {parsed.netloc}")

        tier = row.get("tier", "").strip()
        if tier and tier not in VALID_TIERS:
            errors.append(f"Row {index} has invalid tier: {tier}")

    return errors


def validate_sources_file(path: str) -> list[str]:
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != REQUIRED_COLUMNS:
            return [f"rss_sources.csv header must be: {','.join(REQUIRED_COLUMNS)}"]
        return validate_source_rows(list(reader))


def main() -> None:
    errors = validate_sources_file(config.RSS_SOURCES_CSV)
    if errors:
        for error in errors:
            print(error)
        raise SystemExit(1)
    print("RSS source check passed.")


if __name__ == "__main__":
    main()
