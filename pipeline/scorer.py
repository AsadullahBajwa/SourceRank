"""
scorer.py
Computes credibility scores for each journalist based on their claims history.
Outputs scores.json for the frontend leaderboard.

Usage:
    python pipeline/scorer.py           # score all journalists, write output
    python pipeline/scorer.py --dry-run # print scores without writing JSON
    python pipeline/scorer.py --dry-run --min-resolved 5
"""

import sys
import os
import sqlite3
import argparse
import datetime
import logging
import json
import csv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from time_utils import utc_now, utc_now_iso

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Score computation
# ---------------------------------------------------------------------------

def compute_accuracy_rate(claims: list[dict]) -> float:
    """% of resolved claims that were confirmed."""
    resolved = [c for c in claims if c["verdict"] in ("CONFIRMED", "REFUTED")]
    if not resolved:
        return 0.5  # neutral default when no data
    confirmed = sum(1 for c in resolved if c["verdict"] == "CONFIRMED")
    return confirmed / len(resolved)


def compute_prediction_score(claims: list[dict]) -> float:
    """Accuracy rate weighted towards breaking/exclusive/prediction type claims."""
    high_value = [c for c in claims if c["claim_type"] in ("breaking", "exclusive", "prediction")]
    if not high_value:
        return compute_accuracy_rate(claims)
    resolved = [c for c in high_value if c["verdict"] in ("CONFIRMED", "REFUTED")]
    if not resolved:
        return 0.5
    confirmed = sum(1 for c in resolved if c["verdict"] == "CONFIRMED")
    return confirmed / len(resolved)


def compute_correction_score(claims: list[dict]) -> float:
    """
    Penalize for refuted claims with no follow-up correction.
    Returns a score between 0.0 (many uncorrected refutations) and 1.0 (clean).
    """
    refuted = [c for c in claims if c["verdict"] == "REFUTED"]
    if not refuted:
        return 1.0
    total_claims = max(len(claims), 1)
    penalty_rate = len(refuted) / total_claims
    return max(0.0, 1.0 - (penalty_rate * 2))  # double-weight refutations


def compute_spam_index(handle: str, tweets_conn: sqlite3.Connection) -> float:
    """
    Score based on tweet volume and deletion patterns.
    Returns a penalty-adjusted score between 0.0 (spammy) and 1.0 (clean).
    """
    thirty_days_ago = (utc_now() - datetime.timedelta(days=30)).isoformat()
    row = tweets_conn.execute(
        "SELECT COUNT(*) FROM tweets WHERE handle = ? AND created_at > ? AND is_retweet = 0",
        (handle.lower(), thirty_days_ago),
    ).fetchone()
    tweet_count_30d = row[0] if row else 0

    # Penalize if posting >10 original tweets per day on average
    daily_avg = tweet_count_30d / 30
    if daily_avg <= 5:
        volume_score = 1.0
    elif daily_avg <= 10:
        volume_score = 0.8
    elif daily_avg <= 20:
        volume_score = 0.6
    else:
        volume_score = 0.4

    return volume_score


def compute_source_quality(claims: list[dict]) -> float:
    """
    Proxy: if claims are confirmed by tier-1 sources, source quality is higher.
    Returns a 0.0-1.0 score.
    """
    confirmed = [c for c in claims if c["verdict"] == "CONFIRMED"]
    if not confirmed:
        return 0.5
    tier1_confirmed = sum(
        1 for c in confirmed
        if c.get("verdict_source", "") in config.TIER1_SOURCE_NAMES
    )
    return tier1_confirmed / len(confirmed)


def score_journalist(journalist: dict, claims: list[dict],
                     tweets_conn: sqlite3.Connection,
                     min_resolved_claims: int | None = None) -> dict:
    handle = journalist["handle"].lower()
    weights = config.SCORE_WEIGHTS

    accuracy = compute_accuracy_rate(claims)
    prediction = compute_prediction_score(claims)
    correction = compute_correction_score(claims)
    source_quality = compute_source_quality(claims)
    spam = compute_spam_index(handle, tweets_conn)

    composite = (
        accuracy * weights["accuracy_rate"]
        + prediction * weights["prediction_score"]
        + correction * weights["correction_behavior"]
        + source_quality * weights["source_quality"]
        + spam * weights["spam_index"]
    )

    resolved = [c for c in claims if c["verdict"] in ("CONFIRMED", "REFUTED")]
    final_score = round(composite * 100, 1)
    min_resolved = (
        min_resolved_claims
        if min_resolved_claims is not None
        else getattr(config, "MIN_RESOLVED_CLAIMS_FOR_RANKING", 20)
    )
    eligible = len(resolved) >= min_resolved

    return {
        "handle": handle,
        "name": journalist.get("name", handle),
        "beat": journalist.get("beat", ""),
        "country": journalist.get("country", ""),
        "score": final_score,
        "grade": score_to_grade(final_score),
        "eligible": eligible,
        "rank_status": "ranked" if eligible else "insufficient_data",
        "min_resolved_claims": min_resolved,
        "accuracy_rate": round(accuracy * 100, 1),
        "prediction_score": round(prediction * 100, 1),
        "correction_score": round(correction * 100, 1),
        "source_quality_score": round(source_quality * 100, 1),
        "spam_index_score": round(spam * 100, 1),
        "total_claims": len(claims),
        "confirmed": sum(1 for c in claims if c["verdict"] == "CONFIRMED"),
        "refuted": sum(1 for c in claims if c["verdict"] == "REFUTED"),
        "pending": sum(1 for c in claims if c["verdict"] == "PENDING"),
        "expired": sum(1 for c in claims if c["verdict"] == "EXPIRED"),
        "resolved_claims": len(resolved),
        "scored_at": utc_now_iso(),
    }


def score_to_grade(score: float) -> str:
    if score >= 90: return "A+"
    if score >= 85: return "A"
    if score >= 80: return "A-"
    if score >= 75: return "B+"
    if score >= 70: return "B"
    if score >= 65: return "B-"
    if score >= 60: return "C+"
    if score >= 55: return "C"
    if score >= 50: return "C-"
    if score >= 40: return "D"
    return "F"


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_journalists(csv_path: str) -> list[dict]:
    with open(csv_path, newline="", encoding="utf-8") as f:
        return [j for j in csv.DictReader(f) if j.get("active", "true").lower() == "true"]


def load_claims_for_handle(handle: str, conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM claims WHERE handle = ?",
        (handle.lower(),),
    ).fetchall()
    cols = [d[0] for d in conn.execute("SELECT * FROM claims LIMIT 0").description]
    return [dict(zip(cols, row)) for row in rows]


def write_score_outputs(output: dict) -> None:
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    os.makedirs(config.HISTORY_DIR, exist_ok=True)

    latest_path = os.path.join(config.OUTPUT_DIR, "scores.json")
    with open(latest_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    snapshot_date = output["generated_at"][:10]
    snapshot_name = f"scores_{snapshot_date}.json"
    snapshot_path = os.path.join(config.HISTORY_DIR, snapshot_name)
    with open(snapshot_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    snapshots = sorted(
        name for name in os.listdir(config.HISTORY_DIR)
        if name.startswith("scores_") and name.endswith(".json")
    )
    index = {
        "latest": snapshot_name,
        "snapshots": snapshots,
    }
    with open(os.path.join(config.HISTORY_DIR, "index.json"), "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Score journalists and export leaderboard JSON.")
    parser.add_argument("--dry-run", action="store_true", help="Print scores without writing files")
    parser.add_argument("--min-resolved", type=int,
                        help="Override minimum resolved claims needed for rank eligibility")
    args = parser.parse_args()

    journalists = load_journalists(config.JOURNALISTS_CSV)
    claims_conn = sqlite3.connect(config.CLAIMS_DB)
    tweets_conn = sqlite3.connect(config.TWEETS_DB)

    scores = []
    for j in journalists:
        claims = load_claims_for_handle(j["handle"], claims_conn)
        score = score_journalist(
            j,
            claims,
            tweets_conn,
            min_resolved_claims=args.min_resolved,
        )
        scores.append(score)
        log.info(f"@{j['handle']:30s}  score={score['score']:5.1f}  grade={score['grade']}  "
                 f"claims={score['total_claims']}  confirmed={score['confirmed']}")

    scores.sort(key=lambda x: (x["eligible"], x["score"]), reverse=True)

    output = {
        "generated_at": utc_now_iso(),
        "journalists": scores,
    }

    if args.dry_run:
        print(json.dumps(output, indent=2))
    else:
        write_score_outputs(output)
        log.info(f"Scores written to {os.path.join(config.OUTPUT_DIR, 'scores.json')}")

    claims_conn.close()
    tweets_conn.close()


if __name__ == "__main__":
    main()
