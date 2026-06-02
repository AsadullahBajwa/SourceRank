"""
scheduler.py
Orchestrates the full SourceRank pipeline. Run this daily via Windows Task Scheduler
or GitHub Actions.

Steps:
  1. Fetch latest RSS articles (news corpus refresh)
  2. Scrape new tweets for all active journalists
  3. Extract claims from unprocessed tweets
  4. Verify pending claims against news corpus
  5. Score all journalists and write output JSON

Usage:
    python scheduler.py              # run full pipeline
    python scheduler.py --step news  # run one step only
    python scheduler.py --from-step extract --through-step score
    python scheduler.py --dry-run    # print what would happen
"""

import subprocess
import argparse
import sys
import os
import datetime
import logging
import csv
import json
import sqlite3
from time_utils import utc_now

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [SCHEDULER] %(message)s",
)
log = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

STEPS = {
    "news":    [sys.executable, os.path.join(BASE_DIR, "scrapers", "news_scraper.py")],
    "tweets":  [sys.executable, os.path.join(BASE_DIR, "scrapers", "tweet_scraper.py")],
    "extract": [sys.executable, os.path.join(BASE_DIR, "pipeline", "claim_extractor.py")],
    "verify":  [sys.executable, os.path.join(BASE_DIR, "pipeline", "verifier.py")],
    "score":   [sys.executable, os.path.join(BASE_DIR, "pipeline", "scorer.py")],
}

STEP_ORDER = ["news", "tweets", "extract", "verify", "score"]


def select_steps(step: str | None = None, from_step: str | None = None,
                 through_step: str | None = None) -> list[str]:
    if step:
        return [step]

    start = STEP_ORDER.index(from_step) if from_step else 0
    end = STEP_ORDER.index(through_step) if through_step else len(STEP_ORDER) - 1
    if start > end:
        raise ValueError("--from-step must come before or equal --through-step")
    return STEP_ORDER[start:end + 1]


def _count_rows(db_path: str, query: str) -> int:
    if not os.path.exists(db_path):
        return 0
    conn = sqlite3.connect(db_path)
    try:
        return int(conn.execute(query).fetchone()[0])
    finally:
        conn.close()


def _group_counts(db_path: str, query: str) -> dict[str, int]:
    if not os.path.exists(db_path):
        return {}
    conn = sqlite3.connect(db_path)
    try:
        return {str(key): int(count) for key, count in conn.execute(query).fetchall()}
    finally:
        conn.close()


def pipeline_status() -> dict:
    import config
    from scripts.audit_registry import build_report, load_tweet_counts

    journalist_rows = []
    active_journalists = 0
    if os.path.exists(config.JOURNALISTS_CSV):
        with open(config.JOURNALISTS_CSV, newline="", encoding="utf-8") as f:
            journalist_rows = list(csv.DictReader(f))
        active_journalists = sum(
            1 for row in journalist_rows
            if row.get("active", "true").lower() == "true"
        )
    registry = build_report(journalist_rows, load_tweet_counts()) if journalist_rows else {}

    original_tweets = _count_rows(config.TWEETS_DB, "SELECT COUNT(*) FROM tweets WHERE is_retweet = 0")
    processed_tweets = _count_rows(config.CLAIMS_DB, "SELECT COUNT(*) FROM processed_tweets")
    scores_path = os.path.join(config.OUTPUT_DIR, "scores.json")
    ranked = tracked = 0
    if os.path.exists(scores_path):
        with open(scores_path, encoding="utf-8") as f:
            scores = json.load(f).get("journalists", [])
        tracked = len(scores)
        ranked = sum(1 for row in scores if row.get("eligible"))

    return {
        "active_journalists": active_journalists,
        "registry_coverage_pct": registry.get("active_coverage_pct", 0.0),
        "active_without_tweets": len(registry.get("active_without_tweets", [])),
        "active_duplicate_names": len(registry.get("active_duplicate_names", {})),
        "duplicate_names": len(registry.get("duplicate_names", {})),
        "tweet_handles": _count_rows(config.TWEETS_DB, "SELECT COUNT(DISTINCT handle) FROM tweets"),
        "original_tweets": original_tweets,
        "processed_tweets": processed_tweets,
        "unprocessed_tweets": max(original_tweets - processed_tweets, 0),
        "claims": _count_rows(config.CLAIMS_DB, "SELECT COUNT(*) FROM claims"),
        "verdicts": _group_counts(config.CLAIMS_DB, "SELECT verdict, COUNT(*) FROM claims GROUP BY verdict"),
        "articles": _count_rows(config.CLAIMS_DB, "SELECT COUNT(*) FROM articles"),
        "leaderboard_tracked": tracked,
        "leaderboard_ranked": ranked,
    }


def print_status(as_json: bool = False) -> None:
    status = pipeline_status()
    if as_json:
        print(json.dumps(status, indent=2))
        return
    log.info("Pipeline status:")
    for key, value in status.items():
        log.info(f"  {key}: {value}")


def run_step(name: str, cmd: list[str], dry_run: bool = False) -> bool:
    log.info(f"--- Step: {name.upper()} ---")
    if dry_run:
        log.info(f"[DRY RUN] Would run: {' '.join(cmd)}")
        return True
    result = subprocess.run(cmd, cwd=BASE_DIR)
    if result.returncode != 0:
        log.error(f"Step '{name}' failed with exit code {result.returncode}")
        return False
    return True


def main():
    parser = argparse.ArgumentParser(description="Run the SourceRank pipeline.")
    parser.add_argument("--step", choices=STEP_ORDER, help="Run a single step only")
    parser.add_argument("--from-step", choices=STEP_ORDER, help="Run from this step through the end")
    parser.add_argument("--through-step", choices=STEP_ORDER, help="Stop after this step")
    parser.add_argument("--dry-run", action="store_true", help="Print steps without running")
    parser.add_argument("--status", action="store_true", help="Print local pipeline status and exit")
    parser.add_argument("--json", action="store_true", help="Print --status output as JSON")
    args = parser.parse_args()

    if args.json and not args.status:
        parser.error("--json can only be used with --status")
    if args.step and (args.from_step or args.through_step):
        parser.error("--step cannot be combined with --from-step or --through-step")

    if args.status:
        print_status(as_json=args.json)
        return

    start = utc_now()
    log.info(f"Pipeline started at {start.isoformat()}")

    try:
        steps = select_steps(args.step, args.from_step, args.through_step)
    except ValueError as e:
        parser.error(str(e))

    for step in steps:
        ok = run_step(step, STEPS[step], dry_run=args.dry_run)
        if not ok:
            log.error(f"Pipeline aborted at step '{step}'.")
            sys.exit(1)

    elapsed = (utc_now() - start).seconds
    log.info(f"Pipeline completed in {elapsed}s.")


if __name__ == "__main__":
    main()
