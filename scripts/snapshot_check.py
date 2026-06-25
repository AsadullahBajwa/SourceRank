"""
snapshot_check.py
Validate public leaderboard snapshots and the history index.

Usage:
    python scripts/snapshot_check.py
"""

import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config


COUNT_FIELDS = {
    "total_claims",
    "confirmed",
    "refuted",
    "pending",
    "expired",
    "resolved_claims",
}


def load_json(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def validate_scores_payload(payload: dict, label: str = "scores.json") -> list[str]:
    errors = []
    generated_at = payload.get("generated_at")
    try:
        datetime.datetime.fromisoformat(generated_at)
    except (TypeError, ValueError):
        errors.append(f"{label} generated_at must be an ISO timestamp")

    journalists = payload.get("journalists")
    if not isinstance(journalists, list):
        return errors + [f"{label} must contain a journalists list"]

    seen_handles = set()
    ranked_scores = []
    for index, row in enumerate(journalists, start=1):
        prefix = f"{label} journalist {index}"
        handle = str(row.get("handle", "")).strip().lower()
        if not handle:
            errors.append(f"{prefix} missing handle")
        elif handle in seen_handles:
            errors.append(f"{prefix} duplicates handle: {handle}")
        seen_handles.add(handle)

        score = row.get("score")
        if not isinstance(score, (int, float)) or not 0 <= score <= 100:
            errors.append(f"{prefix} score must be between 0 and 100")

        for field in COUNT_FIELDS:
            value = row.get(field)
            if not isinstance(value, int) or value < 0:
                errors.append(f"{prefix} {field} must be a non-negative integer")

        if all(isinstance(row.get(field), int) for field in ("confirmed", "refuted", "resolved_claims")):
            expected_resolved = row["confirmed"] + row["refuted"]
            if row["resolved_claims"] != expected_resolved:
                errors.append(
                    f"{prefix} resolved_claims must equal confirmed + refuted"
                )

        eligible = row.get("eligible")
        rank_status = row.get("rank_status")
        if eligible is True:
            ranked_scores.append(score)
            if rank_status != "ranked":
                errors.append(f"{prefix} eligible row must have rank_status=ranked")
        elif eligible is False and rank_status == "ranked":
            errors.append(f"{prefix} ineligible row cannot have rank_status=ranked")
        elif not isinstance(eligible, bool):
            errors.append(f"{prefix} eligible must be a boolean")

    numeric_ranked_scores = [
        score for score in ranked_scores if isinstance(score, (int, float))
    ]
    if numeric_ranked_scores != sorted(numeric_ranked_scores, reverse=True):
        errors.append(f"{label} eligible journalists must be sorted by score descending")
    return errors


def validate_history(data_dir: str) -> list[str]:
    history_dir = os.path.join(data_dir, "history")
    index_path = os.path.join(history_dir, "index.json")
    try:
        index = load_json(index_path)
    except (OSError, json.JSONDecodeError) as exc:
        return [f"Cannot read history/index.json: {exc}"]

    snapshots = index.get("snapshots")
    latest = index.get("latest")
    if not isinstance(snapshots, list) or not snapshots:
        return ["history/index.json must contain a non-empty snapshots list"]

    errors = []
    if len(snapshots) != len(set(snapshots)):
        errors.append("history/index.json snapshots must be unique")
    if snapshots != sorted(snapshots):
        errors.append("history/index.json snapshots must be sorted")
    if latest not in snapshots:
        errors.append("history/index.json latest must appear in snapshots")
    elif latest != snapshots[-1]:
        errors.append("history/index.json latest must be the final snapshot")

    for snapshot in snapshots:
        path = os.path.join(history_dir, snapshot)
        if not os.path.exists(path):
            errors.append(f"Missing history snapshot: {snapshot}")
            continue
        try:
            errors.extend(validate_scores_payload(load_json(path), snapshot))
        except json.JSONDecodeError as exc:
            errors.append(f"Invalid JSON in {snapshot}: {exc}")
    return errors


def validate_public_snapshots(data_dir: str) -> list[str]:
    scores_path = os.path.join(data_dir, "scores.json")
    try:
        errors = validate_scores_payload(load_json(scores_path))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"Cannot read scores.json: {exc}"]
    errors.extend(validate_history(data_dir))
    return errors


def main() -> None:
    errors = validate_public_snapshots(config.OUTPUT_DIR)
    if errors:
        for error in errors:
            print(error)
        raise SystemExit(1)
    print("Public snapshot check passed.")


if __name__ == "__main__":
    main()
