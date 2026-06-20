"""
site_check.py
Validate static site files and public JSON artifacts.

Usage:
    python scripts/site_check.py
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config


REQUIRED_SITE_FILES = [
    "index.html",
    "journalist.html",
    "trends.html",
    "coverage.html",
    "methodology.html",
    "dispute.html",
]

REQUIRED_DATA_FILES = [
    "scores.json",
    "claims.json",
    os.path.join("history", "index.json"),
]

LOCAL_HREF_RE = re.compile(r'href=["\']([^"\']+\.html(?:\?[^"\']*)?)["\']')


def missing_site_files(site_dir: str, data_dir: str) -> list[str]:
    missing = [
        os.path.join(site_dir, name)
        for name in REQUIRED_SITE_FILES
        if not os.path.exists(os.path.join(site_dir, name))
    ]
    missing.extend(
        os.path.join(data_dir, name)
        for name in REQUIRED_DATA_FILES
        if not os.path.exists(os.path.join(data_dir, name))
    )
    return missing


def load_json_file(path: str) -> object:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def validate_site(site_dir: str, data_dir: str) -> list[str]:
    errors = []
    missing = missing_site_files(site_dir, data_dir)
    errors.extend(f"Missing required file: {path}" for path in missing)
    if missing:
        return errors

    scores = load_json_file(os.path.join(data_dir, "scores.json"))
    claims = load_json_file(os.path.join(data_dir, "claims.json"))
    history = load_json_file(os.path.join(data_dir, "history", "index.json"))

    if not isinstance(scores.get("journalists"), list):
        errors.append("scores.json must contain a journalists list")
    if not isinstance(claims.get("claims"), list):
        errors.append("claims.json must contain a claims list")
    if not isinstance(history.get("snapshots"), list):
        errors.append("history/index.json must contain a snapshots list")
    errors.extend(validate_local_links(site_dir))
    return errors


def validate_local_links(site_dir: str) -> list[str]:
    errors = []
    for file_name in REQUIRED_SITE_FILES:
        path = os.path.join(site_dir, file_name)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            html = f.read()
        for href in LOCAL_HREF_RE.findall(html):
            target = href.split("?", 1)[0]
            if not os.path.exists(os.path.join(site_dir, target)):
                errors.append(f"{file_name} links to missing local page: {href}")
    return errors


def main() -> None:
    site_dir = os.path.join(config.BASE_DIR, "output", "site")
    errors = validate_site(site_dir, config.OUTPUT_DIR)
    if errors:
        for error in errors:
            print(error)
        raise SystemExit(1)
    print("Static site check passed.")


if __name__ == "__main__":
    main()
