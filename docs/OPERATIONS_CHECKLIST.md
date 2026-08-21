# SourceRank Operations Checklist

Use this checklist before pushing refreshed leaderboard artifacts or before
showing the project as a portfolio demo.

## Quick Health Check

```bash
python scheduler.py --preflight
python scheduler.py --preflight --json
python scripts/pipeline_health.py
python scripts/pipeline_health.py --json
python scripts/feed_health.py --status missing --limit 10
python scripts/feed_health.py --status stale --limit 10
```

Expected signals:

- `journalists` is at least 100.
- `active_journalists` is high enough for the next backfill batch.
- `tweets_db_exists` and `claims_db_exists` are true on the local operator machine.
- `pending_claims` is low before a score refresh is published.
- `source_gap_count` is low, or remaining gaps are intentionally deferred.
- `feed_problem_sources`, `feed_missing_sources`, and `feed_stale_sources` are understood before a score refresh.
- `latest_snapshot` points to the newest generated leaderboard snapshot.

## Static Validation

```bash
python scripts/config_check.py
python scripts/journalists_check.py
python scripts/rss_check.py
python scripts/source_balance_check.py
python scripts/site_check.py
python scripts/snapshot_check.py
python scripts/extension_check.py
python -m unittest discover -s tests -v
```

These checks do not scrape X, call Ollama, or hit paid APIs. They are safe to
run in CI and before every push.

RSS source categories must stay within the shared taxonomy:
`wire`, `public_broadcaster`, `newspaper`, `digital_native`,
`nonprofit_investigative`, `tv`, `radio`, and `regional`.

## Data Refresh Loop

```bash
python scrapers/news_scraper.py
python scrapers/news_scraper.py --category wire --dry-run
python scrapers/news_scraper.py --category public_broadcaster --limit 5 --dry-run
python scripts/feed_health.py --status stale --limit 10
python scripts/source_gap_plan.py --limit 5
python scripts/source_gap_plan.py --priority high --csv
python scripts/coverage_plan.py --limit 10
python scrapers/tweet_scraper.py --only-missing --limit 5
OLLAMA_NUM_PARALLEL=4 python pipeline/claim_extractor.py --limit 500
python scripts/claim_review.py --max-confidence 0.5 --csv > claim-review.csv
python pipeline/verifier.py --recheck --limit 100
python pipeline/scorer.py
python scheduler.py --preflight
python scripts/pipeline_health.py
```

After reviewing the health summary, commit updated public artifacts under
`output/site/data/` when the score movement looks reasonable.

## Release Gate

- Pipeline configuration and journalist registry pass static validation.
- RSS registry passes static validation.
- RSS source balance covers core categories and tier-1 country coverage.
- Static site files, local links, and public snapshots pass validation.
- Extension scaffold validates against Manifest V3 expectations.
- Regression tests pass locally.
- README metrics are updated if public counts changed materially.
- `docs/PHASE_PLAN.md` reflects the next real blocker.
