"""
extension_check.py
Validate the browser extension scaffold without launching a browser.

Usage:
    python scripts/extension_check.py
"""

import json
import os
import sys


REQUIRED_EXTENSION_FILES = [
    "manifest.json",
    "content.js",
    "overlay.css",
    "options.html",
    "options.js",
    "popup.html",
    "popup.js",
]

REQUIRED_MANIFEST_KEYS = {
    "manifest_version",
    "name",
    "version",
    "permissions",
    "content_scripts",
    "action",
    "options_ui",
}


def missing_extension_files(extension_dir: str) -> list[str]:
    return [
        os.path.join(extension_dir, name)
        for name in REQUIRED_EXTENSION_FILES
        if not os.path.exists(os.path.join(extension_dir, name))
    ]


def load_manifest(extension_dir: str) -> dict:
    with open(os.path.join(extension_dir, "manifest.json"), encoding="utf-8") as f:
        return json.load(f)


def validate_manifest(manifest: dict) -> list[str]:
    errors = []
    missing_keys = REQUIRED_MANIFEST_KEYS - set(manifest)
    for key in sorted(missing_keys):
        errors.append(f"manifest.json missing key: {key}")

    if manifest.get("manifest_version") != 3:
        errors.append("manifest.json must use Manifest V3")
    if "storage" not in manifest.get("permissions", []):
        errors.append("manifest.json permissions must include storage")
    if not manifest.get("content_scripts"):
        errors.append("manifest.json must define at least one content script")
    options_ui = manifest.get("options_ui", {})
    if options_ui.get("page") != "options.html":
        errors.append("manifest.json options_ui.page must point to options.html")

    action = manifest.get("action", {})
    if action.get("default_popup") != "popup.html":
        errors.append("manifest.json action.default_popup must point to popup.html")
    return errors


def validate_extension(extension_dir: str) -> list[str]:
    errors = []
    missing = missing_extension_files(extension_dir)
    errors.extend(f"Missing required extension file: {path}" for path in missing)
    if os.path.exists(os.path.join(extension_dir, "manifest.json")):
        errors.extend(validate_manifest(load_manifest(extension_dir)))
    return errors


def main() -> None:
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    errors = validate_extension(os.path.join(repo_root, "extension"))
    if errors:
        for error in errors:
            print(error)
        raise SystemExit(1)
    print("Extension check passed.")


if __name__ == "__main__":
    main()
