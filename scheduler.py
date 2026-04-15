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
    python scheduler.py --dry-run    # print what would happen
"""

import subprocess
import argparse
import sys
import os
import datetime
import logging

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
    parser.add_argument("--dry-run", action="store_true", help="Print steps without running")
    args = parser.parse_args()

    start = datetime.datetime.utcnow()
    log.info(f"Pipeline started at {start.isoformat()}")

    steps = [args.step] if args.step else STEP_ORDER

    for step in steps:
        ok = run_step(step, STEPS[step], dry_run=args.dry_run)
        if not ok:
            log.error(f"Pipeline aborted at step '{step}'.")
            sys.exit(1)

    elapsed = (datetime.datetime.utcnow() - start).seconds
    log.info(f"Pipeline completed in {elapsed}s.")


if __name__ == "__main__":
    main()
