"""
config_check.py
Validate pipeline configuration values without running external services.

Usage:
    python scripts/config_check.py
"""

import os
import sys
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config


EXPECTED_SCORE_WEIGHTS = {
    "accuracy_rate",
    "prediction_score",
    "correction_behavior",
    "source_quality",
    "spam_index",
}
EXPECTED_CLAIM_TYPES = {"breaking", "prediction", "exclusive", "statistic", "general"}


def validate_config(settings) -> list[str]:
    errors = []

    weights = getattr(settings, "SCORE_WEIGHTS", {})
    if set(weights) != EXPECTED_SCORE_WEIGHTS:
        errors.append(
            "SCORE_WEIGHTS keys must be: " + ", ".join(sorted(EXPECTED_SCORE_WEIGHTS))
        )
    if any(not isinstance(value, (int, float)) or value < 0 for value in weights.values()):
        errors.append("SCORE_WEIGHTS values must be non-negative numbers")
    if abs(sum(weights.values()) - 1.0) > 1e-9:
        errors.append(f"SCORE_WEIGHTS must sum to 1.0; found {sum(weights.values()):.6f}")

    windows = getattr(settings, "VERIFICATION_WINDOWS", {})
    if set(windows) != EXPECTED_CLAIM_TYPES:
        errors.append(
            "VERIFICATION_WINDOWS keys must be: "
            + ", ".join(sorted(EXPECTED_CLAIM_TYPES))
        )
    if any(not isinstance(days, int) or days <= 0 for days in windows.values()):
        errors.append("VERIFICATION_WINDOWS values must be positive integers")

    if getattr(settings, "INITIAL_SCRAPE_MONTHS", 0) < 1:
        errors.append("INITIAL_SCRAPE_MONTHS must be at least 1")
    if getattr(settings, "SKIP_RECENT_DAYS", -1) < 0:
        errors.append("SKIP_RECENT_DAYS cannot be negative")
    if getattr(settings, "QUICK_SKIP_MIN_WORDS", 0) < 1:
        errors.append("QUICK_SKIP_MIN_WORDS must be at least 1")
    if getattr(settings, "MIN_RESOLVED_CLAIMS_FOR_RANKING", 0) < 1:
        errors.append("MIN_RESOLVED_CLAIMS_FOR_RANKING must be at least 1")

    ollama_host = getattr(settings, "OLLAMA_HOST", "")
    parsed_host = urlparse(ollama_host)
    if parsed_host.scheme not in {"http", "https"} or not parsed_host.netloc:
        errors.append(f"OLLAMA_HOST must be an HTTP URL; found {ollama_host!r}")
    if not getattr(settings, "OLLAMA_MODEL", "").strip():
        errors.append("OLLAMA_MODEL cannot be empty")
    parallel = getattr(settings, "OLLAMA_NUM_PARALLEL", 0)
    if not isinstance(parallel, int) or parallel < 1:
        errors.append("OLLAMA_NUM_PARALLEL must be a positive integer")

    base_dir = os.path.abspath(getattr(settings, "BASE_DIR", ""))
    for name in ("DATA_DIR", "DB_DIR", "OUTPUT_DIR", "HISTORY_DIR"):
        path = os.path.abspath(getattr(settings, name, ""))
        try:
            is_inside = os.path.commonpath([base_dir, path]) == base_dir
        except ValueError:
            is_inside = False
        if not base_dir or not path or not is_inside:
            errors.append(f"{name} must be inside BASE_DIR")

    return errors


def main() -> None:
    errors = validate_config(config)
    if errors:
        for error in errors:
            print(error)
        raise SystemExit(1)
    print("Configuration check passed.")


if __name__ == "__main__":
    main()
